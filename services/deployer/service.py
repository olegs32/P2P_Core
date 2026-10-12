# services/deployer/service.py
# Deployer — базовый функционал для open (compile + selective services + versioned save / mass deploy)

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import yaml

from src.internal_modules.base import ModuleGeneric
from services.rpc import rpc
from services.deployer.agent.package import seal, AUTH_FAILED, BUSY, PRE_CONFIG_TTL
from services.deployer.agent_api import AgentAuth, AgentSessions, BusyError, FETCH_MAX
from services.deployer.key_registry import DeployKeyRegistry, canon_node_id

log = logging.getLogger("Deployer")

ROOT = Path(__file__).resolve().parents[2]  # P2P_Core/
DIST = ROOT / "dist"
DEVICES_TXT = ROOT / "devices.txt"


class _Business(Exception):
    """Осознанный бизнес-отказ внутри op -> RESPONSE {'ok': False, 'error': ...}."""
    def __init__(self, error: str):
        super().__init__(error)
        self.error = error


def _sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
            size += len(block)
    return h.hexdigest(), size

# Транзитивные зависимости сервисов — UI чекбокс тянет зависимости
SERVICE_DEPENDS: dict[str, list[str]] = {
    "updater": ["files"],        # updater.check/download → files.find/read
    "deployer": ["system"],      # деплой — system.node_detail
    "eyesauron": ["files"],      # spool → files при желании шарить кадры
    "audit": ["files"],          # audit может шарить логи через files
}

# Допустимые packer'ы — pyarmor дефолт
PACKERS = ["pyarmor", "pyinstaller"]
DEFAULT_PACKER = "pyarmor"

# Whitelist для extra_args — защита от инъекции в pack -e
EXTRA_ARGS_SAFE_RE = re.compile(r"^[\w\s\-\.\/\\=:_]*$")


def _expand_services(selected: list[str]) -> list[str]:
    """Транзитивное расширение зависимостей."""
    expanded = set(s.strip() for s in selected if s.strip())
    stack = list(expanded)
    while stack:
        cur = stack.pop()
        for dep in SERVICE_DEPENDS.get(cur, []):
            if dep not in expanded:
                expanded.add(dep)
                stack.append(dep)
    return sorted(expanded)


def _available_services() -> list[dict]:
    """Сканирование services/ на админ-ноде."""
    base = ROOT / "services"
    out = []
    if not base.exists():
        return out
    for p in base.iterdir():
        if p.is_dir() and not p.name.startswith("_") and p.name != "__pycache__":
            has_svc = (p / "service.py").exists()
            out.append({
                "name": p.name,
                "has_service": has_svc,
                "path": str(p),
                "web_ui": (p / "web_ui.py").exists(),
            })
    return sorted(out, key=lambda x: x["name"])


def _read_devices() -> list[str]:
    """MVP devices.txt — построчный список хостов/node_id, # комментарий."""
    if not DEVICES_TXT.exists():
        return []
    try:
        lines = DEVICES_TXT.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out = []
    for raw in lines:
        s = raw.strip().lower()
        if not s or s.startswith("#"):
            continue
        # формат: host или "node_id host" — берём первое слово как id
        out.append(s.split()[0])
    return out


def _make_version_txt() -> str:
    """Делегирует compile.make_version_txt — единый источник версии."""
    import compile as comp
    return comp.make_version_txt()


def _build_args_for_services(services: list[str], ui: bool = False, extra_args: str = "") -> list[str]:
    """Deprecated — теперь compile.build принимает services напрямую. Оставлен для совместимости."""
    try:
        import compile as comp  # noqa
        return [f"# compile.build(services={services}, ui={ui}, extra_args={extra_args!r})"]
    except Exception:
        return []


def _dummy_build(version: str, services: list[str]) -> Path:
    """DEV fallback когда PyInstaller недоступен или в тесте — создаёт dummy exe + manifest."""
    ver_dir = DIST / version
    ver_dir.mkdir(parents=True, exist_ok=True)
    exe_path = ver_dir / "Node_P2P_Core.exe"
    # dummy: 1KB + версия
    exe_path.write_bytes((f"P2P dummy {version} services={','.join(services)}\n".encode() + b"\x00" * 1024))
    h = hashlib.sha256(exe_path.read_bytes()).hexdigest()
    manifest = {
        "version": version,
        "exe_name": exe_path.name,
        "exe_sha256": h,
        "size": exe_path.stat().st_size,
        "services": services,
    }
    (ver_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # также кладём в dist корень для совместимости с make_manifest логикой
    try:
        (DIST / "Node_P2P_Core.exe").write_bytes(exe_path.read_bytes())
    except Exception:
        pass
    return exe_path


def _peer_uri(uri: str) -> str:
    """Канон local.peers[].uri = ws://h:port/ws/ — main.py склеивает
    f'{peer.uri}{ctx.NODE}', поэтому bare ws://h:port даёт ws://h:portnode7.

    Идемпотентна к уже-каноничным значениям (.../ws/ -> .../ws/, без .../ws//ws/).
    Нормализация нужна ТОЛЬКО при записи в local.peers: в agent.conf admin_ws
    остаётся bare (агент сам дописывает /ws/<src>, machine.py:302).
    """
    s = str(uri).rstrip("/")
    if not s.endswith("/ws"):
        s += "/ws"
    return s + "/"


def _gen_per_node_config(target_node: str, base_cfg_path: Path | None = None,
                         admin_node: str | None = None, admin_ws: str | None = None,
                         port: int | None = None) -> dict:
    """Сгенерировать config dict для целевого узла на основе текущего конфига админа.
    node/alias подменяются на target_node, peers — без self + обязательный
    админ-пир (bootstrap ранее неизвестной ноды без ручного peer на админе
    не даёт node_seen), port — per-target override (два узла на одном хосте).
    """
    from src.internal_modules.config import Config, _deep_fill, _default_config_dict, _canon_node
    # Базовый cfg — из текущего ctx или из файла
    base_dict = {}
    if base_cfg_path and base_cfg_path.exists():
        try:
            import yaml as _yaml
            base_dict = _yaml.safe_load(base_cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:
            base_dict = {}
    else:
        # из ctx админа — берём уже загруженный cfg, но здесь без ctx fallback к дефолту
        base_dict = _default_config_dict()

    # Подмена node/alias
    canon = _canon_node(target_node)
    base_dict["node"] = canon
    if "local" not in base_dict or not isinstance(base_dict["local"], dict):
        base_dict["local"] = {}
    base_dict["local"]["alias"] = canon
    # name остаётся как был (имя задачи планировщика) — не трогаем
    # work_dir/full_path — оставляем дефолт C:\Core, но UI может переопределить remote_path

    # Peers — убрать self, затем гарантировать админ-пир (bootstrap жертвы -> админ)
    peers = base_dict["local"].get("peers") or []
    if not isinstance(peers, list):
        peers = []
    # Фильтруем запись где node_id == canon (не дублируем саму жертву)
    peers = [p for p in peers if isinstance(p, dict)
             and _canon_node(p.get("node_id", "")) != canon]
    if admin_node and admin_ws and _canon_node(str(admin_node)) != canon:
        admin_canon = _canon_node(str(admin_node))
        uri = _peer_uri(admin_ws)          # канон для local.peers[].uri
        for p in peers:   # уже есть — не дублируем, только актуализируем uri
            if _canon_node(p.get("node_id", "")) == admin_canon:
                # обе стороны нормализованы перед сравнением: корректный
                # существующий peer не затирается сломанным bare-адресом
                cur = _peer_uri(p.get("uri") or "")
                if cur != uri:
                    p["uri"] = uri        # адрес устарел/другой — актуализируем
                elif p.get("uri") != cur:
                    p["uri"] = cur        # то же значение, но запись не-канонична
                break
        else:
            peers.append({"node_id": admin_canon, "uri": uri})
    base_dict["local"]["peers"] = peers

    # Per-target port override (два узла на одном хосте -> bind-конфликт без него)
    if port is not None:
        if not isinstance(base_dict.get("network"), dict):
            base_dict["network"] = {}
        base_dict["network"]["port"] = int(port)

    # Валидация + backfill
    _deep_fill(base_dict, _default_config_dict())
    # Проверка pydantic
    try:
        cfg = Config(**base_dict)
        return cfg.model_dump(mode="json")
    except Exception:
        return base_dict


class Deployer(ModuleGeneric):
    """Базовый Deployer — open функционал."""

    def __init__(self, name: str, context):
        super().__init__(name, context)
        self._last_build: dict | None = None
        self._build_lock = asyncio.Lock()
        self._build_status: dict = {"state": "idle", "version": None, "logs": [], "started_at": None, "finished_at": None, "result": None}
        self._build_task: asyncio.Task | None = None
        # Фоновая сборка DeployAgent (кнопка в UI вместо ручного
        # `compile.py --type agent`; пакет встраивает копию exe на момент
        # генерации, поэтому пересборка ноды её не обновляет):
        self._agent_build_status: dict = {"state": "idle", "flavor": None, "logs": [], "started_at": None, "finished_at": None, "result": None}
        self._agent_build_task: asyncio.Task | None = None
        # Фоновая доставка пакетов (аналог _build_status, Task 15):
        # node → {state, version, logs, started_at, finished_at, result}
        self._delivery_status: dict[str, dict] = {}
        # guard от двойного запуска: node → живая фоновая задача доставки
        self._deliver_tasks: dict[str, asyncio.Task] = {}
        # последняя запущенная задача доставки (gen создаёт одну на батч)
        self._deliver_task: asyncio.Task | None = None
        work = Path(getattr(self.ctx.config.local, "work_dir", None) or "data")
        self._agent_registry = DeployKeyRegistry(work / "deploy")
        self._agent_auth = AgentAuth(self._agent_registry)
        self._agent_sessions = AgentSessions()
        # validate() из to_thread — сериализация nonce/registry-состояния
        # (в синхронном виде из brief'а атомарность давал сам event loop)
        self._agent_auth_guard = threading.Lock()

    # ------------------------------------------------------------------ #
    #  RPC
    # ------------------------------------------------------------------ #

    @rpc
    async def list_services(self, data: dict) -> dict:
        svcs = _available_services()
        # meta для UI
        try:
            from services.webpanel.service_meta import SERVICE_META
            for s in svcs:
                meta = SERVICE_META.get(s["name"])
                if meta:
                    s["icon"], s["group"], s["desc"] = meta
        except Exception:
            pass
        return {"ok": True, "services": svcs, "depends": SERVICE_DEPENDS, "packers": PACKERS, "default_packer": DEFAULT_PACKER}

    @rpc
    async def list_devices(self, data: dict) -> dict:
        devs = _read_devices()
        # Дополняем живыми нодами из mesh для подсказки
        try:
            mesh_nodes = [n.node_id for n in self.ctx.network.neighbor_table.all()]
            for m in mesh_nodes:
                if m not in devs:
                    devs.append(m)
        except Exception:
            pass
        # peers из конфига
        try:
            for p in self.ctx.config.local.peers:
                if p.node_id not in devs:
                    devs.append(p.node_id)
        except Exception:
            pass
        return {"ok": True, "devices": sorted(set(devs)), "source": str(DEVICES_TXT)}

    @rpc
    async def list_packers(self, data: dict) -> dict:
        return {"ok": True, "packers": PACKERS, "default": DEFAULT_PACKER}

    def _list_builds(self) -> list[dict]:
        """Версионные сборки из dist, свежие впереди (mtime desc)."""
        builds = []
        if DIST.exists():
            for p in DIST.iterdir():
                if p.is_dir() and (p / "manifest.json").exists():
                    try:
                        m = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
                        builds.append({"version": m.get("version", p.name), "path": str(p),
                                       "manifest": m, "mtime": p.stat().st_mtime})
                    except Exception:
                        builds.append({"version": p.name, "path": str(p), "mtime": p.stat().st_mtime})
        builds.sort(key=lambda x: x.get("mtime", 0), reverse=True)
        return builds

    def _latest_version(self) -> str | None:
        b = self._list_builds()
        return b[0]["version"] if b else None

    def _artifact_for(self, version: str) -> Path | None:
        p = DIST / str(version) / "Node_P2P_Core.exe"
        return p if p.is_file() else None

    def _admin_mode(self) -> str:
        return "se" if (hasattr(self.ctx, "se_identity")
                        or hasattr(self.ctx, "secure_storage")) else "open"

    def _agent_ws(self) -> str:
        """Адрес для agent.conf: куда стучится агент.

        Base = mesh-порт (open: mTLS нет, bootstrap не нужен).
        SE перекрывает: отдельный bootstrap-порт без клиентского серта.
        """
        return f"ws://{self.ctx.NODE}:{self.ctx.config.network.port}"

    def _mesh_admin_ws(self, admin_ws: str) -> str:
        """Адрес админа для local.peers жертвы (mesh-порт, mTLS).

        Base = как есть. SE перекрывает: хост из agent_ws + mesh-порт,
        иначе жертва получит пира на bootstrap-порт и не войдёт в mesh
        (bootstrap принимает только deploy_*-агентов).
        """
        return str(admin_ws)

    def _agent_exe_info(self) -> dict:
        """Какой агент сейчас лежит в dist (для UI: свежесть артефакта)."""
        name = self._agent_exe_name(self._admin_mode())
        p = DIST / name
        try:
            if p.is_file():
                st = p.stat()
                return {"name": name, "exists": True, "size": st.st_size,
                        "mtime": st.st_mtime, "path": str(p)}
        except OSError:
            pass
        return {"name": name, "exists": False, "path": str(p)}

    @rpc
    async def get_status(self, data: dict) -> dict:
        # История сборок из dist (блокирующий обход — в потоке, не в event loop)
        builds = (await asyncio.to_thread(self._list_builds))[:20]
        # Включаем build_status для UI polling после смены вкладки
        return {"ok": True, "builds": builds, "last_build": self._last_build, "build_status": dict(self._build_status), "node": self.ctx.NODE}

    @rpc
    async def build(self, data: dict) -> dict:
        """Сборка — теперь фоновая, чтобы не таймаутить RPC (build может идти минуты).

        Возвращает сразу {"ok":True,"started":True,"version":...}, логи — через build_status/get_status.
        При targets>1 exe собирается единожды, конфиги пер-нодовые.
        """
        # Если уже идёт сборка — не запускать вторую
        if self._build_task and not self._build_task.done():
            return {"ok": False, "error": "build already running", "status": self._build_status}
        # Быстрая валидация перед стартом фона
        packer = (data.get("packer") or DEFAULT_PACKER).strip().lower() if isinstance(data, dict) else DEFAULT_PACKER
        if packer not in PACKERS:
            return {"ok": False, "error": f"unknown packer {packer!r}"}
        # Запуск фона
        # Версия генерируется внутри _run_build, но для ответа сразу генерим preview
        self._build_status = {"state": "running", "version": None, "logs": [f"queued packer={packer}"], "started_at": time.time(), "finished_at": None, "result": None}
        self._build_task = asyncio.create_task(self._run_build(dict(data or {})))
        # Вернём started сразу — UI будет поллить build_status
        return {"ok": True, "started": True, "state": "running", "logs": self._build_status["logs"]}

    @rpc
    async def build_status(self, data: dict) -> dict:
        """Статус фоновой сборки — для polling UI (1с)."""
        st = dict(self._build_status)
        # Добавляем last_build для удобства
        if self._last_build:
            st["last_build"] = self._last_build
        # Если таск завершился с исключением — пробросить
        if self._build_task and self._build_task.done() and self._build_task.exception():
            st["error"] = str(self._build_task.exception())
        return {"ok": True, **st}

    async def _run_build(self, data: dict):
        """Фоновый воркер — выполняет _do_build под локом и обновляет статус."""
        async with self._build_lock:
            try:
                self._build_status["logs"].append("build started")
                res = await self._do_build(data)
                self._build_status["result"] = res
                self._build_status["state"] = "done" if res.get("ok") else "failed"
                self._build_status["finished_at"] = time.time()
                # Дополняем логи
                if res.get("logs"):
                    self._build_status["logs"].extend(res["logs"][-50:])
                if res.get("version"):
                    self._build_status["version"] = res["version"]
                # Логи всегда доступны через get_status/last_build
                self.log.info(f"Deployer build finished: {res.get('ok')} v{res.get('version')}")
            except Exception as e:
                self._build_status["state"] = "failed"
                self._build_status["finished_at"] = time.time()
                self._build_status["logs"].append(f"exception: {e}")
                self._build_status["result"] = {"ok": False, "error": str(e), "trace": traceback.format_exc()[-2000:]}
                self.log.exception("Deployer _run_build failed")

    @rpc
    async def build_agent_exe(self, data: dict) -> dict:
        """Пересборка DeployAgent — фоновая (PyInstaller идёт минуты).

        flavor: "open" | "se", дефолт — _admin_mode() (агент обязан совпадать
        с режимом админа: gen_agent_package пакует именно этот вариант).
        Возвращает сразу {"ok": True, "started": True}; статус — через
        agent_build_status (и живьём в agent_status → UI-фрагмент).
        PyInstaller один за раз: делит _build_lock со сборкой ноды.
        """
        data = data if isinstance(data, dict) else {}
        flavor = str(data.get("flavor") or self._admin_mode()).strip().lower()
        if flavor not in ("open", "se"):
            return {"ok": False, "error": f"bad flavor {flavor!r} — expected open/se"}
        if self._agent_build_task and not self._agent_build_task.done():
            return {"ok": False, "error": "agent build already running",
                    "status": dict(self._agent_build_status)}
        if self._build_task and not self._build_task.done():
            return {"ok": False, "error": "node build already running — агент ждёт",
                    "status": dict(self._agent_build_status)}
        self._agent_build_status = {"state": "running", "flavor": flavor,
                                    "logs": [f"queued flavor={flavor}"],
                                    "started_at": time.time(),
                                    "finished_at": None, "result": None}
        self._agent_build_task = asyncio.create_task(self._run_agent_build(flavor))
        return {"ok": True, "started": True, "state": "running", "flavor": flavor,
                "logs": self._agent_build_status["logs"]}

    @rpc
    async def agent_build_status(self, data: dict) -> dict:
        """Статус фоновой сборки агента — для polling UI."""
        st = dict(self._agent_build_status)
        if self._agent_build_task and self._agent_build_task.done() \
                and self._agent_build_task.exception():
            st["error"] = str(self._agent_build_task.exception())
        return {"ok": True, **st}

    async def _run_agent_build(self, flavor: str):
        """Фоновый воркер — compile.build_agent под общим _build_lock."""
        async with self._build_lock:
            st = self._agent_build_status
            try:
                st["logs"].append("agent build started")
                import logging as _logging

                class _ListHandler(_logging.Handler):
                    def emit(self, record):
                        try:
                            st["logs"].append(self.format(record))
                        except Exception:
                            pass

                _handler = _ListHandler()
                _handler.setFormatter(_logging.Formatter(
                    "%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S"))
                _handler.setLevel(logging.INFO)
                _loggers = [logging.getLogger("PyInstaller"),
                            logging.getLogger("Compiler"),
                            logging.getLogger("Deployer")]
                for _lg in _loggers:
                    _lg.addHandler(_handler)
                    _lg.setLevel(logging.INFO)
                try:
                    import compile as comp
                    ok = await asyncio.to_thread(comp.build_agent, flavor)
                finally:
                    for _lg in _loggers:
                        try:
                            _lg.removeHandler(_handler)
                        except Exception:
                            pass
                exe = DIST / self._agent_exe_name(flavor)
                if ok and exe.is_file():
                    st["result"] = {"ok": True, "flavor": flavor,
                                    "exe": str(exe), "size": exe.stat().st_size,
                                    "mtime": exe.stat().st_mtime}
                    st["state"] = "done"
                    st["logs"].append(f"agent built: {exe} ({exe.stat().st_size} B)")
                else:
                    st["result"] = {"ok": False, "flavor": flavor,
                                    "error": f"compile.build_agent({flavor!r}) failed — см. логи выше"}
                    st["state"] = "failed"
                st["finished_at"] = time.time()
                self.log.info(f"Deployer agent build finished: {st['state']} flavor={flavor}")
            except Exception as e:
                st["state"] = "failed"
                st["finished_at"] = time.time()
                st["logs"].append(f"exception: {e}")
                st["result"] = {"ok": False, "flavor": flavor, "error": str(e),
                                "trace": traceback.format_exc()[-2000:]}
                self.log.exception("Deployer _run_agent_build failed")

    async def _do_build(self, data: dict) -> dict:
        packer = (data.get("packer") or DEFAULT_PACKER).strip().lower()
        if packer not in PACKERS:
            return {"ok": False, "error": f"unknown packer {packer!r}, expected {PACKERS}"}
        services = data.get("services") or []
        if isinstance(services, str):
            services = [s.strip() for s in services.split(",") if s.strip()]
        expanded = _expand_services(services)
        # extra_args может прийти как строка или список (чекбоксы)
        extra_raw = data.get("extra_args") or data.get("profile_args") or data.get("extra_list") or ""
        if isinstance(extra_raw, list):
            extra_args = " ".join(extra_raw)
        else:
            extra_args = str(extra_raw)
        build_webui = bool(data.get("build_webui", True))  # по умолчанию галка есть
        action = (data.get("action") or "save").strip().lower()
        targets: list[str] = data.get("targets") or []
        if isinstance(targets, str):
            targets = [t.strip() for t in targets.split(",") if t.strip()]
        version_notes = data.get("version_notes") or data.get("notes") or ""

        # Валидация extra_args
        if extra_args and not EXTRA_ARGS_SAFE_RE.match(extra_args):
            return {"ok": False, "error": "extra_args содержит недопустимые символы"}

        # 1. Версия
        version = _make_version_txt()
        self.log.info(f"Deployer build v{version} packer={packer} services={expanded} action={action} targets={targets}")
        # Связываем build_logs с _build_status для live polling UI (1с)
        self._build_status["version"] = version
        self._build_status["logs"].append(f"version={version} packer={packer} services={expanded}")

        # 2. Сборка exe — делегируем compile.build (единый источник, соответствует deployer)
        exe_src: Path | None = None
        build_logs = self._build_status["logs"]  # live-лог для build_status/get_status
        # Перехватываем логи PyInstaller/compile в build_logs для UI (иначе только в консоли)
        import logging as _logging
        class _ListHandler(_logging.Handler):
            def emit(self, record):
                try:
                    msg = self.format(record)
                    build_logs.append(msg)
                except Exception:
                    pass
        _handler = _ListHandler()
        _handler.setFormatter(_logging.Formatter("%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S"))
        _handler.setLevel(logging.INFO)
        _loggers = [logging.getLogger("PyInstaller"), logging.getLogger("compile"), logging.getLogger("Compiler"), logging.getLogger("Deployer")]
        for _lg in _loggers:
            _lg.addHandler(_handler)
            _lg.setLevel(logging.INFO)
        try:
            ui_flag = "webpanel" in expanded
            extra_list = extra_args.strip().split() if isinstance(extra_args, str) and extra_args.strip() else (list(extra_args) if extra_args else [])
            build_logs.append(f"packer={packer} services={expanded} ui={ui_flag} extra={extra_list}")
            use_dummy = os.environ.get("P2P_DUMMY_BUILD") == "1"
            if not use_dummy:
                import compile as comp
                # compile.build теперь принимает services/extra_args/packer напрямую
                ok = await asyncio.to_thread(comp.build, "Node_P2P_Core", ui_flag, expanded, extra_list, packer)
                if ok:
                    # sign — compile.build не подписывает, делаем здесь как раньше
                    try:
                        from sign.signer import sign_exe as _sign
                        exe_candidate = DIST / "Node_P2P_Core.exe"
                        if exe_candidate.exists():
                            _sign(exe_candidate, DIST)
                            if (DIST / "signed_Node_P2P_Core.exe").exists():
                                try:
                                    os.remove(exe_candidate)
                                except Exception:
                                    pass
                                shutil.move(str(DIST / "signed_Node_P2P_Core.exe"), str(exe_candidate))
                    except Exception as e:
                        build_logs.append(f"sign skip: {e}")
                    try:
                        comp.make_manifest(version, services=expanded, packer=packer)
                        exe_src = DIST / version / "Node_P2P_Core.exe"
                    except Exception as e:
                        build_logs.append(f"manifest skip: {e}")
                        for cand in [DIST / version / "Node_P2P_Core.exe", DIST / "Node_P2P_Core.exe"]:
                            if cand.exists():
                                exe_src = cand
                                break
                else:
                    # Строго: что выбрано — то и пакует, без fallback на pyinstaller/dummy
                    # P2P_DUMMY_BUILD только для тестов; в проде — ошибка
                    if os.environ.get("P2P_DUMMY_BUILD") == "1":
                        build_logs.append("compile.build returned False — dummy fallback (test mode)")
                        use_dummy = True
                    else:
                        return {"ok": False, "error": f"packer '{packer}' build failed — pyarmor not found or build error", "logs": build_logs, "trace": "see logs"}
            if use_dummy or exe_src is None or not exe_src.exists():
                if use_dummy:
                    exe_src = _dummy_build(version, expanded)
                    build_logs.append(f"dummy build at {exe_src}")
                else:
                    return {"ok": False, "error": f"build failed (packer={packer}) — artifact not found", "logs": build_logs}

            # Параллельная сборка Web-UI NODE если галка
            webui_exe: Path | None = None
            if build_webui and not use_dummy:
                try:
                    import compile as comp2
                    # ui=True, те же сервисы (webpanel нужен для UI)
                    ok2 = await asyncio.to_thread(comp2.build, "WebUI_P2P_Core", True, expanded if "webpanel" in expanded else None, extra_list, packer)
                    if ok2:
                        # sign webui
                        try:
                            from sign.signer import sign_exe as _sign2
                            cand2 = DIST / "WebUI_P2P_Core.exe"
                            if cand2.exists():
                                _sign2(cand2, DIST)
                                if (DIST / "signed_WebUI_P2P_Core.exe").exists():
                                    try:
                                        os.remove(cand2)
                                    except Exception:
                                        pass
                                    shutil.move(str(DIST / "signed_WebUI_P2P_Core.exe"), str(cand2))
                        except Exception as e:
                            build_logs.append(f"webui sign skip: {e}")
                        # Копируем webui в версионную папку рядом с Node
                        try:
                            ver_dir = DIST / version
                            ver_dir.mkdir(parents=True, exist_ok=True)
                            src_w = DIST / "WebUI_P2P_Core.exe"
                            if src_w.exists():
                                dst_w = ver_dir / "WebUI_P2P_Core.exe"
                                shutil.copy2(src_w, dst_w)
                                webui_exe = dst_w
                                build_logs.append(f"webui build at {dst_w}")
                        except Exception as e:
                            build_logs.append(f"webui copy skip: {e}")
                    else:
                        build_logs.append("webui compile.build returned False")
                except Exception as e:
                    build_logs.append(f"webui build skip: {e}")
            elif build_webui and use_dummy:
                # dummy webui
                try:
                    ver_dir = DIST / version
                    ver_dir.mkdir(parents=True, exist_ok=True)
                    webui_exe = ver_dir / "WebUI_P2P_Core.exe"
                    webui_exe.write_bytes((f"P2P dummy WebUI {version}\n".encode() + b"\x00" * 512))
                    build_logs.append(f"dummy webui at {webui_exe}")
                except Exception as e:
                    build_logs.append(f"dummy webui skip: {e}")

        except Exception as e:
            return {"ok": False, "error": f"build failed: {e}", "trace": traceback.format_exc()[-2000:], "logs": build_logs}
        finally:
            for _lg in _loggers:
                try:
                    _lg.removeHandler(_handler)
                except Exception:
                    pass

        # exe_src теперь указывает на версионный exe
        if exe_src is None or not exe_src.exists():
            # fallback — dummy
            exe_src = _dummy_build(version, expanded)

        # Сохраняем last_build с логами для UI автообновления
        self._last_build = {"version": version, "exe": str(exe_src), "services": expanded, "packer": packer, "at": time.time(), "webui_exe": str(webui_exe) if webui_exe else None, "build_webui": build_webui, "logs": build_logs[-200:]}
        result: dict[str, Any] = {"ok": True, "version": version, "exe": str(exe_src), "services": expanded, "packer": packer, "logs": build_logs, "build_webui": build_webui, "webui_exe": str(webui_exe) if webui_exe else None}

        # 3. Действие save vs deploy
        if action == "save":
            result["note"] = f"сохранено версионно dist/{version}/"
            return result

        if action == "deploy":
            if not targets:
                return {**result, "ok": False,
                        "error": "targets required for deploy", "deploy": []}
            # Деплой = генерация agent-пакетов (push/SMB удалён, Task 5)
            pkg = await self.gen_agent_package(
                {**data, "version": version, "targets": targets})
            if pkg.get("ok"):
                result["deploy"] = pkg["packages"]
            else:
                result.update(ok=False, error=pkg.get("error"), deploy=[])
            return result

        return {"ok": False, "error": f"unknown action {action!r}"}

    # ------------------------------------------------------------------ #
    #  Agent packages (safe deploy)
    # ------------------------------------------------------------------ #

    def _agent_exe_name(self, mode: str) -> str:
        return "DeployAgent_se.exe" if mode == "se" else "DeployAgent.exe"

    def _agent_exe_src(self, mode: str) -> Path | None:
        p = DIST / self._agent_exe_name(mode)
        if p.is_file():
            return p
        if os.environ.get("P2P_DUMMY_BUILD") == "1":
            DIST.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"MZ dummy agent (P2P_DUMMY_BUILD)")
            return p
        return None

    def _cfg_hint(self) -> dict:
        """Подсказки для генерации конфига жертвы (pre_config идёт отдельным
        вызовом ПОСЛЕ gen_agent_package): admin-адрес для админ-пира в
        local.peers (bare admin_ws; канон .../ws/ применяет _gen_per_node_config)
        + per-target port overrides (скоуп gen-сессии — каждый gen их перезаписывает).

        Создание через getattr — SE-инстансы в тестах собираются через __new__
        и не проходят __init__.
        """
        hint = getattr(self, "_agent_cfg_hint", None)
        if hint is None:
            hint = {"admin_ws": None, "admin_node": None, "ports": {}}
            self._agent_cfg_hint = hint
        return hint

    def _delivery_copy(self, pkg_dir: Path, dest: Path) -> None:
        """Копирование пакета в целевой каталог (блокирует — только из to_thread)."""
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(pkg_dir, dest, dirs_exist_ok=True)

    # ------------------------------------------------------------------ #
    #  Фоновая доставка (Task 15): статусы + guard + воркер
    # ------------------------------------------------------------------ #

    def _deliv_map(self) -> dict:
        """Ленивая карта _delivery_status (SE-инстансы собираются через __new__)."""
        m = getattr(self, "_delivery_status", None)
        if not isinstance(m, dict):
            m = {}
            self._delivery_status = m
        return m

    def _deliver_guard(self) -> dict:
        """Ленивая карта guard'ов _deliver_tasks (см. _deliv_map)."""
        g = getattr(self, "_deliver_tasks", None)
        if not isinstance(g, dict):
            g = {}
            self._deliver_tasks = g
        return g

    def _dlog(self, node: str, msg: str) -> None:
        """Лог доставки для UI: append в _delivery_status[node]["logs"]."""
        st = self._deliv_map().get(node)
        if not isinstance(st, dict):
            return
        logs = st.setdefault("logs", [])
        logs.append(msg)
        if len(logs) > 200:                     # кольцевой потолок, не растём вечно
            del logs[: len(logs) - 200]

    def _delivery_queued(self, node: str, version: str, pkg_dir: Path) -> None:
        """Свежая запись queued (перетирает предыдущую — вызывается только
        при отсутствии живой задачи, см. _delivery_busy)."""
        self._deliv_map()[node] = {
            "state": "queued", "version": str(version),
            "logs": [f"queued: pkg {pkg_dir}"],
            "started_at": time.time(), "finished_at": None, "result": None,
        }

    def _delivery_busy(self, node: str) -> bool:
        """Живая фоновая задача уже ведёт эту ноду? (guard от двойного запуска)."""
        t = self._deliver_guard().get(node)
        return t is not None and not t.done()

    def _start_deliveries(self, version: str, nodes: list[str]) -> None:
        """Одна фоновая задача на батч нод; guard пишется на каждую ноду,
        чистится per-node в воркере (finally) + done-callback'ом (cancel/skip)."""
        task = asyncio.create_task(self._run_deliveries(str(version), list(nodes)))
        guard = self._deliver_guard()
        for n in nodes:
            guard[n] = task

        def _cleanup(t, ns=tuple(nodes)):
            g = self._deliver_guard()
            for n in ns:
                if g.get(n) is t:
                    g.pop(n, None)

        task.add_done_callback(_cleanup)
        self._deliver_task = task

    async def _run_deliveries(self, version: str, nodes: list[str]) -> None:
        r"""Фоновый воркер доставки (аналог _run_build): последовательно по нодам.

        Ошибка/исключение одной ноды не роняет остальные — state=error и переход
        к следующей. Логи ноды (start/copy/error) — в _delivery_status[node].
        Блокирующий I/O остаётся в _deliver_agent (D6: to_thread).

        Отмена батча (рестарт узла) помечает ВСЕ ещё queued/running ноды как
        error (см. _abort_leftovers) — иначе они навсегда остаются «⏳ queued»
        без живой задачи: guard уже снят done-callback'ом, а retry-кнопка в UI
        есть только в ветке state=error.
        """
        cur = asyncio.current_task()
        guard = self._deliver_guard()
        skipped: set[str] = set()              # ноды, ведомые чужой живой задачей
        try:
            for node in nodes:
                busy = guard.get(node)
                if busy is not None and busy is not cur and not busy.done():
                    # живая чужая задача уже ведёт эту ноду — пропуск с логом
                    self.log.info(f"delivery skip {node}: уже выполняется")
                    skipped.add(node)
                    continue
                st = self._deliv_map().get(node)
                if not isinstance(st, dict):
                    st = {"state": "queued", "version": str(version), "logs": []}
                    self._deliv_map()[node] = st
                st["state"] = "running"
                st.setdefault("version", str(version))
                st.setdefault("logs", [])
                st.setdefault("started_at", time.time())
                pkg_dir = DIST / str(version) / "agent" / node
                try:
                    self._dlog(node, f"start: pkg {pkg_dir}")
                    try:
                        res = await self._deliver_agent(
                            node, pkg_dir,
                            on_log=lambda msg, n=node: self._dlog(n, msg))
                    except asyncio.CancelledError:
                        # задачу гасят (рестарт/тесты) — статус не должен врать «running»
                        st["state"] = "error"
                        st["result"] = {"ok": False, "error": "delivery cancelled"}
                        st["finished_at"] = time.time()
                        self._dlog(node, "cancelled")
                        raise
                    except Exception as e:
                        # D9: сбой одной ноды — словарь, остальные ноды продолжаются
                        res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                        self._dlog(node, f"exception: {e}")
                        self.log.exception(f"Deployer delivery failed for {node}")
                    st["result"] = res
                    st["finished_at"] = time.time()
                    st["state"] = "done" if res.get("ok") else "error"
                    if res.get("ok"):
                        self._dlog(node, f"done: {res.get('unc') or res.get('host') or ''}")
                    else:
                        self._dlog(node, f"error: {res.get('error')}")
                    self.log.info(f"delivery {node}: {st['state']}")
                finally:
                    if guard.get(node) is cur:      # отпускаем ноду для retry/gen
                        guard.pop(node, None)
        except asyncio.CancelledError:
            # пробрасываем дальше — shutdown/тесты ждут отмену задачи
            self.log.info(f"delivery batch прерван: {len(nodes)} нод(ы) в батче")
            raise
        finally:
            # общий финал (в т.ч. отмена): недоеденные ноды батча — error,
            # иначе зависшая очередь «⏳ queued» без пути retry (review 6yy.15)
            self._abort_leftovers(nodes, skipped, cur)

    def _abort_leftovers(self, nodes: list[str], skipped: set, task) -> None:
        r"""Недоеденные ноды батча → `state=error` (отмена/остановка воркера).

        Помечает только queued/running записи этого батча: текущая нода уже
        терминальна (per-node finally), пропущенные (skipped) ведёт чужая задача.
        Отмена прерывает батч, но не врёт UI: у каждой ноды есть finished_at,
        result.ok=False и retry-кнопка (ветка state=error).
        """
        if not nodes:
            return
        guard = self._deliver_guard()
        msg = "доставка прервана (остановка узла)"
        for n in nodes:
            if n in skipped:
                continue
            g = guard.get(n)
            if g is not None and g is not task and not g.done():
                continue                          # чужая живая задача ведёт ноду
            st = self._deliv_map().get(n)
            if not isinstance(st, dict) or st.get("state") not in ("queued", "running"):
                continue                          # уже терминальна (done/error)
            st["state"] = "error"
            st["result"] = {"ok": False, "error": msg}
            st["finished_at"] = st.get("finished_at") or time.time()
            self._dlog(n, f"aborted: {msg}")
            self.log.info(f"delivery {n}: aborted — {msg}")

    @staticmethod
    def _node_segment_error(node: str) -> str | None:
        r"""Отказ для node как одного path-компонента (текст D9-ответа) либо None.

        canon_node_id отсекает только `/\:` — `.`, `..` и `\x00` проходят мимо
        и ломают path-guard: `(DIST/ver/"agent"/"..").resolve()` остаётся внутри
        dist, а копия уходит в `\\host\C$\Core\agent\..` (= `C:\Core` цели).
        """
        if node in (".", "..") or "\x00" in node:
            return f"bad node {node!r} — недопустимый идентификатор узла"
        return None

    async def _deliver_agent(self, node: str, pkg_dir: Path, on_log=None) -> dict:
        """Авто-доставка agent-пакета: локальная копия или UNC (SMB) + psexec.

        Порядок — copy-then-psexec: копирование идёт первым, поэтому при
        отсутствии PsTools пакет уже лежит на цели (ответ ok=False с подсказкой,
        D9-словарь, не исключение). Хост резолвится из neighbor_table (info.host),
        локальная цель копируется в <work_dir>/agent/<node> без psexec.

        `on_log(msg)` — callback живых логов доставки (копирование/psexec) для
        _delivery_status[node]["logs"]; вызывается только из фонового воркера
        (_run_deliveries), при прямых вызовах допустим None.

        Семантика ok=True: «скопировано и запущено», НЕ «завершено» — psexec
        идёт с -d (detached) и возвращается сразу после старта агента, поэтому
        локальный timeout покрывает только запуск. Финальный статус деплоя —
        live-фрагмент прогресса (agent_status / _agent_live в web_ui).
        """
        def _log(msg: str) -> None:
            if on_log is None:
                return
            try:
                on_log(msg)
            except Exception:
                pass

        pkg_dir = Path(pkg_dir)
        host = str(node)
        try:
            info = self.ctx.network.neighbor_table.get(node)
            if info is not None and getattr(info, "host", None):
                host = str(info.host)
        except Exception:
            pass
        is_local = (host in ("127.0.0.1", "localhost", str(self.ctx.NODE))
                    or str(node).strip().lower() == str(self.ctx.NODE).strip().lower())
        result: dict[str, Any] = {"ok": False, "host": host, "unc": None,
                                  "psexec_code": None, "error": None,
                                  "local": bool(is_local),
                                  "launched": False, "remote_pid": None}
        try:
            if is_local:
                # та же машина (или резолв в себя) — копия локально, без psexec
                work = Path(getattr(self.ctx.config.local, "work_dir", None) or "data")
                dest = work / "agent" / str(node)
                _log(f"copy → {dest}")
                await asyncio.to_thread(self._delivery_copy, pkg_dir, dest)
                result.update(ok=True, unc=str(dest))
                return result
            # C:\Core\agent\<node> → \\host\C$\Core\agent\<node> (admin share)
            remote_dir = rf"C:\Core\agent\{node}"
            drive, tail = remote_dir.split(":", 1)
            dest = Path(f"\\\\{host}\\{drive}$\\{tail.lstrip(chr(92))}")
            _log(f"copy → {dest}")
            await asyncio.to_thread(self._delivery_copy, pkg_dir, dest)
            result["unc"] = str(dest)
            psexec = shutil.which("psexec")
            if not psexec:
                _log("psexec не найден (PsTools) — копирование выполнено")
                result["error"] = ("psexec не найден на админ-ноде (установите PsTools) "
                                   "— доставьте папку вручную")
                return result
            # -w: рабочий каталог на цели (нужен для любых относительных
            # операций агента); agent.conf сам резолвится рядом с exe
            # (agent/main.py:28-31), поэтому доп. аргументы (--conf) не нужны
            # -d: detached — psexec не ждёт завершения агента (он работает
            # дольше локального таймаута), возвращается сразу после запуска
            remote_exe = str(Path(remote_dir) / "agent.exe")
            cmd = [psexec, "-s", "-d", f"\\\\{host}", "-w", remote_dir, remote_exe]
            _log(f"psexec → \\\\{host}")
            proc = await asyncio.to_thread(subprocess.run, cmd,
                                           capture_output=True, timeout=30)
            result["psexec_code"] = proc.returncode
            _log(f"psexec code {proc.returncode}")
            # Полный вывод (без [:300]): хвост несёт причину, обрезка
            # посреди IP лишь маскировала её в UI
            err = (proc.stderr or b"").decode(errors="ignore").strip()
            out = (proc.stdout or b"").decode(errors="ignore").strip()
            result["psexec_stdout"] = out
            result["psexec_stderr"] = err
            # PsExec -d кладёт в errorlevel PID запущенного процесса
            # («... started on HOST with process ID N»), а не код ошибки —
            # «started on» = запуск удался при ЛЮБОМ returncode.
            # Прогресс (включая started-строку) PsExec пишет в STDERR,
            # баннер — в stdout: ищем по обоим потокам
            combined = f"{out}\n{err}"
            launched = "started on" in combined.lower()
            result["launched"] = launched
            if launched:
                m = re.search(r"process ID (\d+)", combined, re.IGNORECASE)
                result["remote_pid"] = int(m.group(1)) if m else None
                result["ok"] = True
                _log("agent started" + (f" pid={result['remote_pid']}"
                                        if result["remote_pid"] else ""))
                return result
            if proc.returncode != 0:
                hint = (f"psexec не смог запустить агента "
                        f"(код {proc.returncode})")
                detail = err or out
                result["error"] = (f"{hint}: ...{detail[-500:]}"
                                   if detail else hint)
                _log(result["error"])
                return result
            result["ok"] = True
            return result
        except Exception as e:
            # D9: отказ доставки — словарь, не исключение
            result["error"] = f"{type(e).__name__}: {e}"
            _log(f"{type(e).__name__}: {e}")
            return result

    @rpc
    async def gen_agent_package(self, data: dict) -> dict:
        """Пакет = папка dist/<ver>/agent/<node_id>/ (agent.conf + копия agent.exe).

        Доставка фоновая (Task 15): ответ несёт delivery={"state": "queued"},
        копирование идёт в _run_deliveries, статус/логи — agent_status→deliveries.
        """
        data = data if isinstance(data, dict) else {}
        targets = data.get("targets") or []
        if isinstance(targets, str):
            targets = [targets]
        try:
            targets = [canon_node_id(t) for t in targets if str(t).strip()]
        except ValueError as e:
            # D9: невалидный node_id — бизнес-отказ, не исключение
            return {"ok": False, "error": str(e)}
        for t in targets:
            # тот же path-guard, что в deliver_agent: ".." создал бы пакет в
            # dist/<ver>/ и доставил его в C:\Core\agent\.. на цели
            bad = self._node_segment_error(t)
            if bad:
                return {"ok": False, "error": bad}
        if not targets:
            return {"ok": False, "error": "targets required"}
        # per-target port override (опционально): {node: port} — см. скоуп ниже
        ports_raw = data.get("ports") or {}
        if not isinstance(ports_raw, dict):
            return {"ok": False,
                    "error": f"bad ports {ports_raw!r} — expected dict {{node: port}}"}
        port_overrides: dict[str, int] = {}
        for raw_node, raw_port in ports_raw.items():
            try:
                pnode = canon_node_id(str(raw_node))
            except ValueError as e:
                return {"ok": False, "error": str(e)}
            try:
                pval = int(raw_port)
            except (TypeError, ValueError):
                return {"ok": False, "error": f"bad port {raw_port!r} for {raw_node!r}"}
            if not 1 <= pval <= 65535:
                return {"ok": False,
                        "error": f"bad port {raw_port!r} for {raw_node!r} (1-65535)"}
            port_overrides[pnode] = pval
        # скоуп ports — gen-сессия: каждый gen задаёт набор override'ов заново
        # (не накапливается instance-level); пустой -> дефолтный порт config.yaml
        self._cfg_hint()["ports"] = dict(port_overrides)
        version = str(data.get("version") or (await asyncio.to_thread(self._latest_version)) or "")
        if not version or await asyncio.to_thread(self._artifact_for, version) is None:
            return {"ok": False, "error": "no artifact for version — соберите ноду сначала"}
        mode = self._admin_mode()
        agent_src = await asyncio.to_thread(self._agent_exe_src, mode)
        if agent_src is None:
            return {"ok": False,
                    "error": f"agent artifact not found: {self._agent_exe_name(mode)} — "
                             f"соберите: python compile.py --type agent"}
        admin_ws = data.get("admin_ws") or self._agent_ws()
        admin_node = data.get("admin_node") or self.ctx.NODE
        # адрес админа нужен позже в _build_pre_config (админ-пир конфига жертвы)
        hint = self._cfg_hint()
        hint["admin_ws"] = str(admin_ws)
        hint["admin_node"] = str(admin_node)
        try:
            deploy_timeout = int(data.get("deploy_timeout") or 120)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"bad deploy_timeout {data.get('deploy_timeout')!r}"}

        def _write_packages() -> list[dict]:
            pkgs = []
            for t in targets:
                # реестр из потока — внешняя сериализация (инвариант key_registry)
                with self._agent_auth_guard:
                    key, created = self._agent_registry.get_or_create(t)
                pkg_dir = DIST / version / "agent" / t
                pkg_dir.mkdir(parents=True, exist_ok=True)
                conf = {"protocol": 1, "mode": mode, "admin_ws": str(admin_ws),
                        "admin_node": str(admin_node), "node_id": t, "key": key.hex(),
                        "version": version, "deploy_timeout": deploy_timeout}
                # Вариант A: пакет = agent.conf + agent.exe, без key material.
                # Certs — только в шифрованном pre_config (агент пишет из RAM
                # в DPAPI, на диске цели ключей нет).
                (pkg_dir / "agent.conf").write_text(
                    json.dumps(conf, ensure_ascii=False, indent=2), encoding="utf-8")
                shutil.copy2(agent_src, pkg_dir / "agent.exe")
                pkgs.append({"node": t, "path": str(pkg_dir),
                             "key_status": "created" if created else "existing"})
            return pkgs

        try:
            pkgs = await asyncio.to_thread(_write_packages)
        except Exception as e:
            # D9: отказ генерации пакета — словарь, не исключение
            return {"ok": False, "error": f"package write failed: {e}"}
        # авто-доставка — ФОНОВАЯ (Task 15, паттерн build()): RPC отвечает
        # сразу, SMB-копирование + psexec идут в _run_deliveries, live-статус
        # и логи — в _delivery_status[node] (agent_status → deliveries → UI).
        # Отказ доставки не валит генерацию: пакет уже создан, в карточку
        # кладётся только marker состояния (совместимость UI).
        queued: list[str] = []
        for pkg in pkgs:
            node = pkg["node"]
            if self._delivery_busy(node):
                # живая задача уже ведёт ноду — запись и логи не перетираем
                self.log.info(f"delivery skip {node}: уже выполняется (gen)")
                pkg["delivery"] = {"state": self._deliv_map().get(node, {})
                                   .get("state", "queued")}
                continue
            self._delivery_queued(node, version, Path(pkg["path"]))
            pkg["delivery"] = {"state": "queued"}
            queued.append(node)
        if queued:
            # одна фоновая задача на весь батч нод
            self._start_deliveries(version, queued)
        return {"ok": True, "version": version, "mode": mode,
                "admin_ws": str(admin_ws), "packages": pkgs}

    @rpc
    async def deliver_agent(self, data: dict) -> dict:
        r"""Повторная доставка уже сгенерированного пакета: dist/<ver>/agent/<node>.

        Path-guard: node — ровно один сегмент пути; `.`, `..`, пустая строка и
        `\x00` отклоняются ДО resolve (иначе guard видит путь внутри dist и
        пропускает копию в `\\host\C$\Core\agent\..` на цели).

        Контракт started (Task 15): мгновенный {"ok": True, "started": True},
        доставка идёт в фоне; при живой задаче — D9-отказ «уже выполняется».
        """
        data = data if isinstance(data, dict) else {}
        try:
            node = canon_node_id(data.get("node") or "")
        except ValueError as e:
            # D9: невалидный node_id — бизнес-отказ, не исключение
            return {"ok": False, "error": str(e)}
        if not node:
            return {"ok": False, "error": "node required"}
        bad = self._node_segment_error(node)
        if bad:
            return {"ok": False, "error": bad}
        version = str(data.get("version")
                      or (await asyncio.to_thread(self._latest_version)) or "")
        if not version:
            return {"ok": False, "error": "no builds"}
        # path traversal guard: каталог пакета обязан лежать внутри dist/
        dist_root = DIST.resolve()
        try:
            pkg_dir = (DIST / version / "agent" / node).resolve()
            in_dist = pkg_dir == dist_root or dist_root in pkg_dir.parents
            is_pkg_dir = pkg_dir.is_dir()
        except (ValueError, OSError) as e:
            # D9: некорректный сегмент пути (например \x00 в version)
            return {"ok": False, "error": f"bad version {version!r} — {e}"}
        if not in_dist:
            return {"ok": False, "error": f"bad version {version!r} — путь вне dist/"}
        if not is_pkg_dir:
            return {"ok": False,
                    "error": f"пакет не найден: {version}/agent/{node} — "
                             f"сгенерируйте сначала"}
        if self._delivery_busy(node):
            # D9: живая задача уже ведёт ноду — двойной запуск запрещён
            return {"ok": False, "error": "доставка уже выполняется",
                    "state": self._deliv_map().get(node, {}).get("state")}
        self._delivery_queued(node, version, pkg_dir)
        self._start_deliveries(version, [node])
        return {"ok": True, "started": True, "state": "queued",
                "node": node, "version": version}

    @rpc
    async def rotate_agent_key(self, data: dict) -> dict:
        data = data if isinstance(data, dict) else {}
        try:
            node = canon_node_id(data.get("node") or data.get("target") or "")
        except ValueError as e:
            # D9: невалидный node_id — бизнес-отказ, не исключение
            return {"ok": False, "error": str(e)}
        if not node:
            return {"ok": False, "error": "node required"}

        def _rotate() -> None:
            with self._agent_auth_guard:
                self._agent_registry.rotate(node)

        await asyncio.to_thread(_rotate)
        return {"ok": True, "node": node, "rotated": True}

    @rpc
    async def deploy(self, data: dict) -> dict:
        """Деплой = генерация agent-пакетов для targets[] (без сборки)."""
        data = data if isinstance(data, dict) else {}
        version = data.get("version") or (self._last_build or {}).get("version") \
            or (await asyncio.to_thread(self._latest_version))
        if not version:
            return {"ok": False, "error": "no builds"}
        targets = data.get("targets") or ([data.get("target")] if data.get("target") else [])
        if not targets:
            return {"ok": False, "error": "targets required"}
        res = await self.gen_agent_package({**data, "version": version, "targets": targets})
        if res.get("ok"):
            res["note"] = f"packages for {len(res['packages'])} target(s)"
        return res

    # ------------------------------------------------------------------ #
    #  Agent API (safe deploy)
    # ------------------------------------------------------------------ #

    async def _pre_config_certs(self, node_id: str) -> dict:
        return {}

    async def _build_pre_config(self, node_id: str, version: str, exe: Path) -> dict:
        hint = self._cfg_hint()
        admin_node = hint.get("admin_node") or str(self.ctx.NODE)
        admin_ws = self._mesh_admin_ws(
            hint.get("admin_ws") or self._agent_ws())
        try:
            port_override = hint.get("ports", {}).get(canon_node_id(node_id))
        except ValueError:
            port_override = None

        def _gen_cfg() -> tuple[dict, str]:
            base = ROOT / "config.yaml"
            cfg_dict = _gen_per_node_config(
                node_id, base if base.exists() else None,
                admin_node=admin_node, admin_ws=admin_ws, port=port_override)
            return cfg_dict, yaml.dump(cfg_dict, allow_unicode=True, sort_keys=False)

        cfg_dict, config_text = await asyncio.to_thread(_gen_cfg)
        local = cfg_dict.get("local") or {}
        work_dir = local.get("work_dir") or r"C:\Core"
        exe_name = local.get("exe_name") or "Node_P2P_Core.exe"
        full_path = local.get("full_path") or str(Path(work_dir) / exe_name)
        sha, size = await asyncio.to_thread(_sha256_file, exe)
        pc = {"v": 1, "mode": self._admin_mode(), "node": canon_node_id(node_id),
              "deploy_id": str(uuid.uuid4()), "exp": time.time() + PRE_CONFIG_TTL,
              "config": config_text,
              "install": {"work_dir": work_dir, "exe_name": exe_name, "full_path": full_path},
              "exe": {"name": exe.name, "sha256": sha, "size": size, "version": version}}
        certs = await self._pre_config_certs(node_id)   # certs: ca_cert/node_cert/node_key
        pc.update(certs)
        return pc

    async def _op_pre_config(self, node_id: str, body: dict) -> dict:
        version = body.get("version") or await asyncio.to_thread(self._latest_version)
        exe = await asyncio.to_thread(self._artifact_for, str(version)) if version else None
        if exe is None:
            raise _Business("no artifact")
        pc = await self._build_pre_config(node_id, str(version), exe)
        try:
            self._agent_sessions.open(node_id, pc["deploy_id"], str(version), str(exe))
        except BusyError:
            raise _Business(BUSY)
        return pc

    async def _op_fetch_chunk(self, node_id: str, body: dict) -> dict:
        sess = self._agent_sessions.get(node_id)
        if not sess:
            raise _Business("no session")
        offset, size = body.get("offset"), body.get("size")
        if (not isinstance(offset, int) or isinstance(offset, bool)
                or not isinstance(size, int) or isinstance(size, bool)
                or offset < 0 or size <= 0 or size > FETCH_MAX):
            raise _Business("bad chunk request")

        def _read() -> bytes | None:
            p = Path(sess["exe_path"])
            if not p.is_file():
                return None
            fsize = p.stat().st_size
            if offset >= fsize:
                return None
            with open(p, "rb") as f:
                f.seek(offset)
                return f.read(min(size, fsize - offset))

        data = await asyncio.to_thread(_read)
        if data is None:
            raise _Business("out of range")
        return {"ok": True, "offset": offset, "data": data}

    def _op_node_seen(self, node_id: str) -> dict:
        info = self.ctx.network.neighbor_table.get(canon_node_id(node_id))
        status = getattr(info, "status", None)
        value = getattr(status, "value", status)
        return {"ok": True, "seen": bool(info is not None and value == "connected")}

    def _op_report(self, node_id: str, body: dict) -> dict:
        if not isinstance(body, dict) or not isinstance(body.get("stage"), str):
            raise _Business("bad report")
        self._agent_sessions.record_report(node_id, body)
        return {"ok": True}

    @rpc
    async def agent_api(self, data: dict) -> dict:
        """Единственный вход агента. Ответ — envelope; plaintext только для
        неизвестного узла ({'ok': False, 'error': 'auth failed'})."""
        req = data if isinstance(data, dict) else {}

        def _validate() -> tuple[bytes | None, str | None]:
            with self._agent_auth_guard:
                return self._agent_auth.validate(req)

        key, err = await asyncio.to_thread(_validate)
        raw_node = req.get("node_id") if isinstance(req.get("node_id"), str) else ""
        try:
            node = canon_node_id(raw_node)
        except ValueError:
            node = None
        if err or not node:
            # ключ известен (и id валиден) -> отказ внутри envelope,
            # иначе — единообразный plaintext auth failed
            if key is not None and node:
                return await asyncio.to_thread(seal, key, node, {"ok": False, "error": err})
            return {"ok": False, "error": AUTH_FAILED}

        op = req["op"]
        body = req.get("body") or {}
        try:
            if not isinstance(body, dict):
                raise _Business("bad body")
            if op == "ping":
                payload = {"ok": True, "server_ts": time.time()}
            elif op == "pre_config":
                payload = await self._op_pre_config(node, body)
            elif op == "fetch_chunk":
                payload = await self._op_fetch_chunk(node, body)
            elif op == "node_seen":
                payload = self._op_node_seen(node)
            elif op == "report":
                payload = self._op_report(node, body)
            else:
                payload = {"ok": False, "error": f"unknown op {op!r}"}
        except _Business as e:
            payload = {"ok": False, "error": e.error}
        # любое действие с валидной подписью продлевает busy-grace
        self._agent_sessions.extend(node)
        return await asyncio.to_thread(seal, key, node, payload)

    @rpc
    async def agent_status(self, data: dict) -> dict:
        data = data if isinstance(data, dict) else {}
        node = data.get("node")
        try:
            sess_node = canon_node_id(node) if node else None
        except ValueError:
            return {"ok": False, "error": "bad node id"}
        def _list_keys() -> dict:
            with self._agent_auth_guard:
                return self._agent_registry.list()

        return {"ok": True,
                "statuses": self._agent_sessions.status(sess_node),
                "keys": await asyncio.to_thread(_list_keys),
                "mode": self._admin_mode(),
                # Вариант A: bootstrap-порт SE-админа (без mTLS) — агент идёт
                # сюда; на open-ноде совпадает с mesh-портом.
                "agent_ws": self._agent_ws(),
                # name-based адрес: HELLO dst сверяется с NODE сервера,
                # имя ноды вместо IP (ip-резолв здесь больше не нужен)
                "admin_ws": f"ws://{self.ctx.NODE}:{self.ctx.config.network.port}",
                "admin_port": self.ctx.config.network.port,
                "admin_node": str(self.ctx.NODE),
                # живые записи фоновой доставки пакетов (Task 15):
                # node → {state, version, logs, started_at, finished_at, result}
                "deliveries": {n: {**st, "logs": list(st.get("logs") or [])}
                               for n, st in self._deliv_map().items()},
                # статус фоновой сборки DeployAgent + свежесть артефакта
                # (пакет встраивает копию exe на момент генерации)
                "agent_build": dict(self._agent_build_status),
                "agent_exe": self._agent_exe_info()}
