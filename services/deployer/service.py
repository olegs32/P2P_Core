# services/deployer/service.py
# Deployer — базовый функционал для open (compile + selective services + versioned save / mass deploy)
# SecureDeployer наследует и расширяет генерацией сертификата (см. src/se/services/deployer/service.py)

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import yaml

from src.internal_modules.base import ModuleGeneric
from services.rpc import rpc

log = logging.getLogger("Deployer")

ROOT = Path(__file__).resolve().parents[2]  # P2P_Core/
DIST = ROOT / "dist"
DEVICES_TXT = ROOT / "devices.txt"

# Транзитивные зависимости сервисов — UI чекбокс тянет зависимости
SERVICE_DEPENDS: dict[str, list[str]] = {
    "updater": ["files"],        # updater.check/download → files.find/read
    "deployer": ["system"],      # деплой — system.node_detail + psexec
    "eyesauron": ["files"],      # spool → files при желании шарить кадры
    "audit": ["files"],          # audit может шарить логи через files
}

# Допустимые packer'ы — pyarmor дефолт (согласовано)
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
        s = raw.strip()
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


def _gen_per_node_config(target_node: str, base_cfg_path: Path | None = None) -> dict:
    """Сгенерировать config dict для целевого узла на основе текущего конфига админа.
    node/alias подменяются на target_node, peers — без self.
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

    # Peers — можно оставить как есть, но убрать self если был
    peers = base_dict["local"].get("peers") or []
    # Фильтруем запись где node_id == canon
    peers = [p for p in peers if _canon_node(p.get("node_id", "")) != canon]
    base_dict["local"]["peers"] = peers

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

    # ------------------------------------------------------------------ #
    #  Helpers — deploy
    # ------------------------------------------------------------------ #

    def _remote_path_for(self, target: str) -> str:
        """Remote path из LocalConfig.full_path цели (если доступна через RPC), иначе C:\\Core\\Node_P2P_Core.exe"""
        default = r"C:\Core\Node_P2P_Core.exe"
        try:
            # Пытаемся спросить цель через system.node_detail (если онлайн)
            # Синхронно нельзя — пробуем кэш neighbor_table
            # Для MVP — дефолт, UI позволяет поправить
            return default
        except Exception:
            return default

    async def _deploy_one(self, target: str, version: str, exe_src: Path, config_dict: dict, remote_path: str | None = None, cert_pem: bytes | None = None) -> dict:
        """Деплой одного узла: SMB copy + psexec. Возвращает статус."""
        # remote_path: полный путь к exe на цели, e.g. C:\Core\Node_P2P_Core.exe
        if not remote_path:
            remote_path = self._remote_path_for(target)
        # Нормализуем UNC
        # \\target\C$\Core\Node_P2P_Core.exe  — из C:\Core\... → C$\Core\...
        # target может быть host или node_id — пробуем резолвить host из neighbor_table
        host = target
        try:
            # если target — node_id, ищем host в neighbor_table
            info = self.ctx.network.neighbor_table.get(target)
            if info and info.host:
                host = info.host
        except Exception:
            pass

        # Для локального теста: если host == localhost/127.0.0.1 или target == self.NODE — копируем локально
        is_local = host in ("127.0.0.1", "localhost", self.ctx.NODE) or target.strip().lower() == self.ctx.NODE.strip().lower()

        # Директория назначения
        if ":" in remote_path:
            # C:\Core\Node_P2P_Core.exe → dir
            remote_dir = str(Path(remote_path).parent)
            exe_name = Path(remote_path).name
        else:
            remote_dir = remote_path
            exe_name = "Node_P2P_Core.exe"
            remote_path = str(Path(remote_dir) / exe_name)

        # UNC путь
        if is_local:
            unc_exe = Path(remote_path)
            unc_cfg = unc_exe.parent / "config.yaml"
            unc_cert_dir = unc_exe.parent / "certs"
        else:
            # C:\Core → \\host\C$\Core
            try:
                drive, tail = remote_dir.split(":", 1)
                tail = tail.lstrip("\\/")
                unc_base = f"\\\\{host}\\{drive}$\\{tail}"
            except Exception:
                unc_base = f"\\\\{host}\\C$\\Core"
            unc_exe = Path(unc_base) / exe_name
            unc_cfg = Path(unc_base) / "config.yaml"
            unc_cert_dir = Path(unc_base) / "certs"

        result: dict[str, Any] = {"target": target, "host": host, "remote_path": remote_path, "unc_exe": str(unc_exe)}
        try:
            # 1. Создать директории
            unc_exe.parent.mkdir(parents=True, exist_ok=True)

            # 2. Копирование exe (с версионированием на цели — бэкап .old)
            if not exe_src.exists():
                return {**result, "ok": False, "error": f"build artifact not found: {exe_src}"}
            # бэкап существующего
            if unc_exe.exists():
                try:
                    backup = unc_exe.with_suffix(".exe.old")
                    shutil.copy2(unc_exe, backup)
                except Exception:
                    pass
            shutil.copy2(exe_src, unc_exe)
            # hash verify
            h_src = hashlib.sha256(exe_src.read_bytes()).hexdigest()
            h_dst = hashlib.sha256(unc_exe.read_bytes()).hexdigest()
            if h_src != h_dst:
                return {**result, "ok": False, "error": "hash mismatch after copy"}

            # 3. Конфиг пер-нодовый
            unc_cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg_text = yaml.dump(config_dict, allow_unicode=True, default_flow_style=False, sort_keys=False)
            unc_cfg.write_text(cfg_text, encoding="utf-8")

            # 4. Сертификат если есть (SE)
            if cert_pem:
                unc_cert_dir.mkdir(parents=True, exist_ok=True)
                # cert уже в config? кладём рядом как node.pem для ручной установки
                (unc_cert_dir / f"{target}.pem").write_bytes(cert_pem if isinstance(cert_pem, bytes) else cert_pem.encode())

            # 5. Запуск через psexec -s \\host remote_path
            # В админ-контексте psexec доступен; если нет — логируем как done (MVP без реального запуска)
            psexec = shutil.which("psexec")
            if psexec and not is_local:
                try:
                    cmd = [psexec, "-s", f"\\\\{host}", remote_path]
                    # Не блокируем надолго
                    proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=30)
                    out = (proc.stdout or b"").decode(errors="ignore")[:500]
                    err = (proc.stderr or b"").decode(errors="ignore")[:500]
                    result["psexec_out"] = out
                    result["psexec_err"] = err
                    result["psexec_code"] = proc.returncode
                    if proc.returncode != 0:
                        log.warning(f"psexec {target} code {proc.returncode}: {err}")
                except Exception as e:
                    result["psexec_error"] = str(e)
            elif is_local:
                result["note"] = "local copy — psexec skipped (same host)"

            result["ok"] = True
            return result
        except Exception as e:
            result["ok"] = False
            result["error"] = f"{type(e).__name__}: {e}"
            result["trace"] = traceback.format_exc()[-1000:]
            return result

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

    @rpc
    async def get_status(self, data: dict) -> dict:
        # История сборок из dist
        builds = []
        if DIST.exists():
            for p in DIST.iterdir():
                if p.is_dir() and (p / "manifest.json").exists():
                    try:
                        m = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
                        builds.append({"version": m.get("version", p.name), "path": str(p), "manifest": m, "mtime": p.stat().st_mtime})
                    except Exception:
                        builds.append({"version": p.name, "path": str(p)})
        builds = sorted(builds, key=lambda x: x.get("mtime", 0), reverse=True)
        # Включаем build_status для UI polling после смены вкладки
        return {"ok": True, "builds": builds[:20], "last_build": self._last_build, "build_status": dict(self._build_status), "node": self.ctx.NODE}

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
        remote_path_tpl = data.get("remote_path") or data.get("remote_path_template") or ""
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
                return {**result, "ok": False, "error": "targets required for deploy", "deploy": []}
            # Mass deploy — exe один, конфиги пер-нодовые
            deploy_results = []
            # Генерируем пер-нодовые конфиги заранее
            base_cfg_path = ROOT / "config.yaml"
            for tgt in targets:
                cfg_dict = _gen_per_node_config(tgt, base_cfg_path if base_cfg_path.exists() else None)
                # remote_path per target — если шаблон содержит {node}
                rp = remote_path_tpl
                if rp and "{node}" in rp:
                    rp = rp.replace("{node}", tgt)
                # cert_pem — пока None, SecureDeployer переопределит
                res = await self._deploy_one(tgt, version, exe_src, cfg_dict, remote_path=rp or None)
                deploy_results.append(res)

            result["deploy"] = deploy_results
            result["note"] = f"deployed to {len([r for r in deploy_results if r.get('ok')])}/{len(targets)}"
            # success если хотя бы один ok? Но возвращаем полный список
            return result

        return {"ok": False, "error": f"unknown action {action!r}"}

    # Совместимость: отдельный deploy для уже собранного build_id
    @rpc
    async def deploy(self, data: dict) -> dict:
        """Деплой уже собранной версии (без пересборки). Для mass — передавайте targets[]."""
        version = data.get("version") or (self._last_build or {}).get("version")
        if not version:
            return {"ok": False, "error": "version required (no last build)"}
        # Найти exe
        exe_src = DIST / version / "Node_P2P_Core.exe"
        if not exe_src.exists():
            exe_src = DIST / "Node_P2P_Core.exe"
            if not exe_src.exists():
                return {"ok": False, "error": f"artifact not found for {version}"}
        targets = data.get("targets") or ([data.get("target")] if data.get("target") else [])
        if isinstance(targets, str):
            targets = [targets]
        if not targets:
            return {"ok": False, "error": "targets required"}
        remote_path_tpl = data.get("remote_path") or ""
        base_cfg_path = ROOT / "config.yaml"
        results = []
        for tgt in targets:
            cfg_dict = _gen_per_node_config(tgt, base_cfg_path if base_cfg_path.exists() else None)
            rp = remote_path_tpl.replace("{node}", tgt) if remote_path_tpl and "{node}" in remote_path_tpl else (remote_path_tpl or None)
            res = await self._deploy_one(tgt, version, exe_src, cfg_dict, remote_path=rp)
            results.append(res)
        return {"ok": True, "version": version, "deploy": results}
