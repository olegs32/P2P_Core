# services/deployer/agent/machine.py
"""Конечный автомат deploy-агента.

Открытый флоу: детект и запись на диск делегированы host-адаптеру (Task 8/9),
формат пакета и проверка подписи — agent/package.py. run() возвращает
exit-code и не бросает.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import os
import secrets
import shutil
import subprocess
import sys
import time
import uuid as _uuid
from pathlib import Path
from typing import Protocol

import websockets

from src.networking.neighbor_table import PROTOCOL_VERSION
from src.networking.protocol import (
    MAX_FRAME_SIZE, MsgPack, PackType, decode_pack, encode_pack,
)
from services.deployer.agent.package import (
    AgentConf, AUTH_FAILED, BUSY, EnvelopeError, PreConfigError,
    open_envelope, sign_request, validate_pre_config,
)

log = logging.getLogger("deploy.agent")

EXIT_DONE = 0
EXIT_AUTH = 10
EXIT_ADMIN = 11
EXIT_BUSINESS = 12
EXIT_FAIL = 13
EXIT_NEED_SYSTEM = 14

FETCH_CHUNK = 1024 * 1024        # cap из ТЗ: <= 4MB, берём 1MB
FETCH_PAUSE = 0.1                # пауза между чанками (лимит скорости)
FETCH_ATTEMPTS = 2               # sha-retry: полная перекачка с нуля
REPORT_RETRIES = 3
PID_POLL = 2.0
NODE_SEEN_POLL = 3.0
ROLLBACK_SEEN_TIMEOUT = 60.0


class DeployMachineError(Exception):
    exit_code = EXIT_FAIL


class AuthRejected(DeployMachineError):
    exit_code = EXIT_AUTH


class AdminUnreachable(DeployMachineError):
    exit_code = EXIT_ADMIN


class BusinessError(DeployMachineError):
    exit_code = EXIT_BUSINESS


class NeedSystem(DeployMachineError):
    exit_code = EXIT_NEED_SYSTEM


class ApiClientProto(Protocol):
    async def connect(self) -> None: ...
    async def ping(self) -> float: ...
    async def pre_config(self, version: str) -> dict: ...
    async def fetch_chunk(self, offset: int, size: int) -> bytes: ...
    async def node_seen(self) -> bool: ...
    async def report(self, data: dict) -> None: ...


class HostProto(Protocol):
    def detect(self, pc: dict) -> dict: ...
    async def ensure_system(self) -> str: ...
    async def stop_node(self, exe_name: str) -> None: ...
    async def backup(self, deploy_id: str, pc: dict, plan: dict) -> str | None: ...
    async def write(self, pc: dict, plan: dict) -> None: ...
    async def launch(self, pc: dict) -> int: ...
    async def pid_alive(self, pid: int) -> bool: ...
    async def restore(self, backup_dir: str, pc: dict) -> None: ...
    async def self_terminate(self, code: int) -> None: ...


class DeployMachine:
    def __init__(self, conf: AgentConf, client: ApiClientProto, host: HostProto) -> None:
        self.conf, self.client, self.host = conf, client, host
        self.pc: dict | None = None
        self.deploy_id: str | None = None
        self._started = time.time()
        self._server_ts = 0.0
        self._wrote_disk = False
        self._backup: str | None = None
        self._plan: dict = {}
        self._pid: int | None = None

    # ------------------------------------------------------------------ run

    async def run(self) -> int:
        code = EXIT_FAIL
        error: str | None = None
        try:
            await self.client.connect()                      # CONNECT
            self._server_ts = await self.client.ping()       # PING (auth)
            self._started = time.time()

            # ENSURE_SYSTEM ДО PRE_CONFIG (Delta 1: иначе relaunch-инстанс
            # натыкается на busy-lock, который держит его же родитель)
            res = await self.host.ensure_system()
            if res == "relaunched":
                log.info("relaunched as SYSTEM - handoff, пакет не трогаем")
                return EXIT_DONE                              # handoff: 0 без cleanup
            if res == "denied":
                raise NeedSystem("need admin/SYSTEM")

            await self._stage_pre_config()                    # PRE_CONFIG + validate
            self._plan = self.host.detect(self.pc)            # DETECT
            await self._stage_fetch()                         # FETCH_EXE -> .new
            await self.host.stop_node(self.pc["install"]["exe_name"])   # STOP_NODE
            self._backup = await self.host.backup(self.deploy_id, self.pc, self._plan)
            await self.host.write(self.pc, self._plan)        # WRITE (os.replace .new)
            self._wrote_disk = True
            try:
                self._pid = await self.host.launch(self.pc)   # LAUNCH
            except Exception as e:
                # запись на диске уже сделана — откат тем же путём, что WATCH-fail
                err = str(e) or type(e).__name__
                await self._rollback_and_report(err)
                return await self._finish(EXIT_FAIL, err)

            ok, error = await self._watch()                   # WATCH
            if ok:
                code = EXIT_DONE
                await self._terminal("done", True)
            else:
                code = EXIT_FAIL
                await self._rollback_and_report(error)
        except AuthRejected as e:
            code, error = e.exit_code, str(e)
            # 10/11/busy: отчёт НЕ шлём (админ либо недоступен, либо отверг)
        except AdminUnreachable as e:
            code, error = e.exit_code, str(e)
        except BusinessError as e:
            code, error = e.exit_code, str(e)
            if error != BUSY:
                await self._terminal("failed", False, error=error)
        except NeedSystem as e:
            code, error = e.exit_code, str(e)
        except (PreConfigError,) as e:
            # протухший exp / чужой node -> 10, пакет сохраняем
            code, error = EXIT_AUTH, str(e)
        except Exception as e:                                # DeployMachineError и прочее
            code = getattr(e, "exit_code", EXIT_FAIL)
            error = str(e) or type(e).__name__
            if code == EXIT_FAIL:
                await self._terminal("failed", False, error=error)

        if code == EXIT_FAIL and self.deploy_id and not error:
            await self._terminal("failed", False, error="deploy failed")
        return await self._finish(code, error)

    async def _finish(self, code: int, error: str | None) -> int:
        # Cleanup-гейт (Delta 4): только после записи на диск или успеха;
        # 10/11/12/14 -> пакет остаётся для повторного запуска оператором.
        cleanup = code == EXIT_DONE or (code == EXIT_FAIL and self._wrote_disk)
        if cleanup:
            try:
                await self.host.self_terminate(code)
            except Exception as e:
                log.warning("self_terminate failed: %s", e)
        else:
            log.warning("пакет сохранён для повтора (exit=%d, error=%s)", code, error)
        return code

    # --------------------------------------------------------------- stages

    async def _stage_pre_config(self) -> None:
        pc = await self.client.pre_config(self.conf.version)
        validate_pre_config(pc, self.conf.node_id, now=time.time())
        if pc.get("mode") != self.conf.mode:                  # mismatch conf/сборки
            raise BusinessError(f"mode mismatch: conf={self.conf.mode} pc={pc.get('mode')}")
        self.pc = pc
        self.deploy_id = pc["deploy_id"]
        await self._report("pre_config", True)                # Delta 6: первая отправка

    async def _stage_fetch(self) -> None:
        exe = self.pc["exe"]
        target = Path(str(self.pc["install"]["full_path"]) + ".new")
        size = int(exe["size"])
        expect = bytes.fromhex(exe["sha256"])
        target.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, FETCH_ATTEMPTS + 1):
            digest = hashlib.sha256()
            offset = 0
            with open(target, "wb") as f:
                while offset < size:
                    n = min(FETCH_CHUNK, size - offset)
                    chunk = await self.client.fetch_chunk(offset, n)
                    if not chunk:
                        raise DeployMachineError("пустой чанк exe")
                    f.write(chunk)
                    digest.update(chunk)
                    offset += len(chunk)
                    await asyncio.sleep(FETCH_PAUSE)
            if digest.digest() == expect:
                return
            log.warning("sha256 mismatch (попытка %d/%d)", attempt, FETCH_ATTEMPTS)
        raise DeployMachineError("exe sha256 mismatch после перекачки")

    async def _watch(self) -> tuple[bool, str | None]:
        timeout = float(self.conf.deploy_timeout or 120)
        deadline = time.monotonic() + timeout
        next_pid = next_seen = 0.0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_pid:
                next_pid = now + PID_POLL
                # HostOpsWindows.pid_alive — sync (ctypes), фейки Task 7 — async:
                # принимаем оба варианта контракта HostProto
                alive = self.host.pid_alive(self._pid)
                if inspect.isawaitable(alive):
                    alive = await alive
                if not alive:
                    return False, "process died after launch"
            if now >= next_seen:
                next_seen = now + NODE_SEEN_POLL
                if await self.client.node_seen():
                    return True, None                          # успех = node_seen
            await asyncio.sleep(0.5)
        return False, "running, HELLO not seen"

    async def _rollback(self) -> bool:
        try:
            await self.host.stop_node(self.pc["install"]["exe_name"])
            await self.host.restore(self._backup, self.pc)
            await self.host.launch(self.pc)
            deadline = time.monotonic() + ROLLBACK_SEEN_TIMEOUT
            while time.monotonic() < deadline:
                if await self.client.node_seen():
                    return True
                await asyncio.sleep(NODE_SEEN_POLL)
        except Exception as e:
            log.warning("rollback failed: %s", e)
        return False

    async def _rollback_and_report(self, error: str) -> None:
        # сам rollback не пробрасывает исключения (см. _rollback) —
        # исходный код выхода не маскируется
        rolled = await self._rollback() if self._backup else False
        await self._terminal("rolled_back" if rolled else "failed", False, error=error)

    async def _report(self, stage: str, ok: bool, error: str | None = None,
                      pid: int | None = None) -> None:
        if self.deploy_id is None:                             # Delta 6: до pre_config не шлём
            return
        payload = {"stage": stage, "ok": ok, "error": error,
                   "pid": pid if pid is not None else self._pid,
                   "duration": round(time.time() - self._started, 3),
                   "deploy_id": self.deploy_id}
        for i in range(REPORT_RETRIES):                        # best-effort, админ мог упасть
            try:
                await self.client.report(payload)
                return
            except Exception as e:
                log.debug("report %s retry %d: %s", stage, i + 1, e)
                await asyncio.sleep(0.5 * (i + 1))

    async def _terminal(self, stage: str, ok: bool, error: str | None = None) -> None:
        await self._report(stage, ok, error=error)


# ------------------------------------------------------------------ #
#  Реальный WS-клиент агента (ApiClientProto)
# ------------------------------------------------------------------ #

class AgentApiClient:
    """Самодостаточный WS-клиент агента (NodeRPC не переиспользуем — §6 ТЗ)."""

    def __init__(self, conf: AgentConf, retries: int = 3, retry_delay: float = 2.0):
        self.conf = conf
        self.retries, self.retry_delay = retries, retry_delay
        self.ws = None
        self._pending: dict[str, asyncio.Future] = {}
        self._recv_task = None
        self._ts_offset = 0.0            # server_ts - local_ts при ping
        self.src = f"deploy_{conf.node_id}"

    async def connect(self):
        uri = self.conf.admin_ws.rstrip("/") + "/ws/" + self.src
        last = None
        for i in range(self.retries):
            try:
                self.ws = await websockets.connect(uri, max_size=MAX_FRAME_SIZE)
                break
            except Exception as e:
                last = e
                await asyncio.sleep(self.retry_delay * (i + 1))
        else:
            raise AdminUnreachable(f"connect failed: {last}")
        await self._handshake()                    # recv_loop — ТОЛЬКО после ACK

    async def _handshake(self):
        hello = MsgPack(type=PackType.HELLO, source=self.src,
                        dst=self.conf.admin_node,
                        data={"host": "", "port": 0, "version": PROTOCOL_VERSION,
                              "role": "client", "enc": "msgpack", "services": []},
                        label=str(_uuid.uuid4()))
        await self.ws.send(encode_pack(hello))
        # HELLO_ACK читаем напрямую: recv_loop не запущен и не «съест» его
        try:
            raw = await asyncio.wait_for(self.ws.recv(), 10)
        except Exception as e:
            raise AdminUnreachable(f"handshake timeout: {e}")
        pack = decode_pack(raw)
        if pack.type == PackType.HELLO_REJECT:
            raise AdminUnreachable(pack.error or "HELLO_REJECT")
        if pack.type != PackType.HELLO_ACK:
            raise AdminUnreachable(f"unexpected handshake pack {pack.type}")
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def _recv_loop(self):
        try:
            async for raw in self.ws:
                pack = decode_pack(raw)
                fut = self._pending.pop(pack.label, None)
                if fut and not fut.done():
                    fut.set_result(pack)
        except Exception:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(AdminUnreachable("socket closed"))
            self._pending.clear()

    async def _call(self, op: str, body: dict | None = None, timeout=15) -> dict:
        """REQUEST deployer.agent_api -> открытый envelope-plaintext."""
        label = str(_uuid.uuid4())
        ts = time.time() + self._ts_offset
        nonce = secrets.token_hex(16)
        req = {"op": op, "node_id": self.conf.node_id, "ts": ts, "nonce": nonce,
               "sig": sign_request(self.conf.key, op, self.conf.node_id, ts, nonce),
               "body": body or {}}
        pack = MsgPack(type=PackType.REQUEST, source=self.src,
                       dst=self.conf.admin_node, service="deployer",
                       method="agent_api", data=req, label=label)
        fut = asyncio.get_running_loop().create_future()
        self._pending[label] = fut
        try:
            await self.ws.send(encode_pack(pack))
            resp = await asyncio.wait_for(fut, timeout)
        except (asyncio.TimeoutError, TimeoutError):
            self._pending.pop(label, None)
            raise AdminUnreachable(f"timeout waiting {op}")
        except AdminUnreachable:
            raise
        except Exception as e:
            raise AdminUnreachable(str(e))
        if resp.type == PackType.ERROR:
            raise AdminUnreachable(resp.error or "ERROR packet")
        if resp.type != PackType.RESPONSE:
            raise AdminUnreachable(f"unexpected pack {resp.type}")
        plain = self._open(resp.data)              # envelope -> dict
        if not plain.get("ok", True):
            err = str(plain.get("error") or "error")
            if err == AUTH_FAILED:
                raise AuthRejected(AUTH_FAILED)
            raise BusinessError(err)
        return plain

    def _open(self, data) -> dict:
        try:
            plain = open_envelope(self.conf.key, self.conf.node_id, data)
        except EnvelopeError as e:
            raise AuthRejected(str(e))
        if plain.get("node") and plain["node"] != self.conf.node_id:
            raise AuthRejected("node mismatch")
        return plain

    async def ping(self) -> float:
        local = time.time()
        plain = await self._call("ping")
        server_ts = float(plain["server_ts"])
        self._ts_offset = server_ts - local         # clock skew: ts = server + elapsed
        return server_ts

    async def pre_config(self, version: str) -> dict:
        # ответ админа — сам plaintext pre_config (без обёртки), Task 4 `_op_pre_config`
        return await self._call("pre_config", {"version": version})

    async def fetch_chunk(self, offset: int, size: int) -> bytes:
        plain = await self._call("fetch_chunk", {"offset": offset, "size": size},
                                 timeout=30)
        return bytes(plain["data"])

    async def node_seen(self) -> bool:
        try:
            plain = await self._call("node_seen", timeout=5)
            return bool(plain.get("seen"))
        except (AdminUnreachable, BusinessError):
            return False

    async def report(self, data: dict) -> None:
        await self._call("report", data, timeout=5)

    async def close(self):
        if self._recv_task:
            self._recv_task.cancel()
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass


# ------------------------------------------------------------------ #
#  Хост-операции (HostProto, open-ветка)
# ------------------------------------------------------------------ #

CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008


class HostOpsWindows:
    """Хост-операции на Windows: schtasks/taskkill/бэкап/запуск/terminate."""

    def __init__(self, conf, conf_path, system_run=False, runner=None,
                 is_system_fn=None):
        self.conf, self.conf_path = conf, Path(conf_path)
        self.system_run = system_run
        self._runner = runner or self._default_runner
        self._is_system = is_system_fn or self._sid_is_system
        self.task_name = f"P2P_Deploy_{conf.node_id}"

    async def _run(self, args: list[str]):
        """Единая точка вызова runner: тестовый runner может быть sync-лямбдой."""
        res = self._runner(args)
        if inspect.isawaitable(res):
            res = await res
        return res

    # ---- инфраструктура
    @staticmethod
    async def _default_runner(args):
        def _run():
            p = subprocess.run(args, capture_output=True, text=True, timeout=60)
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        return await asyncio.to_thread(_run)          # D6: блокирующий I/O -> thread

    @staticmethod
    def _sid_is_system() -> bool:
        """Токен процесса == S-1-5-18 (SYSTEM). ctypes, без pywin32."""
        import ctypes
        import ctypes.wintypes as wt
        TOKEN_QUERY, TokenUser = 0x0008, 1

        class SID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wt.DWORD)]

        class TOKEN_USER(ctypes.Structure):
            _fields_ = [("User", SID_AND_ATTRIBUTES)]

        advapi, kernel = ctypes.windll.advapi32, ctypes.windll.kernel32
        token = wt.HANDLE()
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), TOKEN_QUERY,
                                       ctypes.byref(token)):
            return False
        try:
            size = wt.DWORD(0)
            advapi.GetTokenInformation(token, TokenUser, None, 0, ctypes.byref(size))
            buf = ctypes.create_string_buffer(size.value)
            if not advapi.GetTokenInformation(token, TokenUser, buf, size.value,
                                              ctypes.byref(size)):
                return False
            sid = ctypes.cast(buf, ctypes.POINTER(TOKEN_USER)).contents.User.Sid
            sid_str = ctypes.c_wchar_p()
            if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_str)):
                return False
            return sid_str.value == "S-1-5-18"
        finally:
            kernel.CloseHandle(token)

    def pid_alive(self, pid: int | None) -> bool:
        """Delta 10: только ctypes OpenProcess/GetExitCodeProcess —
        прямой kill по pid на Windows убивает процесс, он запрещён."""
        if not pid:
            return False
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE = 0x1000, 259
        kernel = ctypes.windll.kernel32
        h = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel.CloseHandle(h)

    def exe_running(self, exe_name: str) -> bool:
        p = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH"],
                           capture_output=True, text=True)
        out = (p.stdout or "") + (p.stderr or "")
        return exe_name.lower() in out.lower()

    # ---- стадии
    def detect(self, pc: dict) -> dict:
        # open: storage-логики нет; SE-стратегии (bootstrap/redeploy/rebuild)
        # реализуются в Task 9 через lazy import src.se
        return {"strategy": pc.get("mode", "open")}

    async def ensure_system(self) -> str:
        if self._is_system():
            return "ok"
        if self.system_run:
            return "denied"                     # повтор без SYSTEM -> сразу 14
        own = sys.executable if getattr(sys, "frozen", False) else \
            f'"{sys.executable}" "{Path(sys.argv[0]).resolve()}"'
        tr = f'{own} --conf "{self.conf_path}" --system-run'
        code, _ = await self._run(["schtasks", "/create", "/tn", self.task_name,
                                   "/ru", "SYSTEM", "/rl", "HIGHEST",
                                   "/tr", tr, "/f"])
        if code != 0:
            return "denied"
        code, _ = await self._run(["schtasks", "/run", "/tn", self.task_name])
        if code != 0:
            return "denied"
        return "relaunched"                     # родитель: exit 0, без cleanup

    async def stop_node(self, exe_name: str) -> None:
        await self._run(["taskkill", "/IM", exe_name, "/F"])
        for _ in range(20):                     # поллинг выхода (D6: tasklist в thread)
            if not await asyncio.to_thread(self.exe_running, exe_name):
                return
            await asyncio.sleep(0.5)

    async def backup(self, deploy_id, pc, plan) -> str | None:
        install = Path(pc["install"]["full_path"])
        src_files = [install, install.parent / "config.yaml"]
        if pc.get("mode") == "se":
            src_files.append(Path(pc["install"]["work_dir"]) / "p2p_secure.bin")
        existing = [p for p in src_files if p.is_file()]
        if not existing:
            return None
        broot = Path(pc["install"]["work_dir"]) / ".deploy_bak"
        bdir = broot / deploy_id
        bdir.mkdir(parents=True, exist_ok=True)
        for p in existing:
            shutil.copy2(p, bdir / p.name)
        dirs = sorted(broot.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
        for old in dirs[3:]:                    # ротация: хранить последние 3
            shutil.rmtree(old, ignore_errors=True)
        return str(bdir)

    async def write(self, pc: dict, plan: dict) -> None:
        install = Path(pc["install"]["full_path"])
        new = Path(str(install) + ".new")
        if new.is_file():
            os.replace(new, install)            # атомарно, пока нода остановлена
        cfg = install.parent / "config.yaml"    # пишется ВСЕГДА (оба mode, §6)
        tmp = install.parent / "config.yaml.tmp"
        tmp.write_text(pc["config"], encoding="utf-8")
        os.replace(tmp, cfg)
        if pc.get("mode") == "se":
            await self._write_storage(pc, plan)     # Task 9; до неё — ImportError

    async def _write_storage(self, pc: dict, plan: dict) -> None:
        raise NotImplementedError("SE storage — Task 9")

    async def launch(self, pc: dict) -> int:
        target = Path(pc["install"]["full_path"])

        def _spawn() -> int:
            proc = subprocess.Popen([str(target)], cwd=str(target.parent),
                                    creationflags=CREATE_NEW_PROCESS_GROUP)
            return proc.pid
        return await asyncio.to_thread(_spawn)   # D6: Popen не в event loop

    async def restore(self, backup_dir: str, pc: dict) -> None:
        bdir = Path(backup_dir)
        install = Path(pc["install"]["full_path"])
        for name, dst in ((install.name, install),
                          ("config.yaml", install.parent / "config.yaml"),
                          ("p2p_secure.bin",
                           Path(pc["install"]["work_dir"]) / "p2p_secure.bin")):
            src = bdir / name
            if src.is_file():
                shutil.copy2(src, dst)

    async def self_terminate(self, code: int) -> None:
        own = Path(sys.executable if getattr(sys, "frozen", False)
                   else Path(sys.argv[0]).resolve())
        pkg_dir = self.conf_path.parent           # пакет = каталог conf+exe
        try:
            own.replace(str(own) + ".purging")
        except OSError:
            pass
        self.conf_path.unlink(missing_ok=True)
        await self._run(["schtasks", "/delete", "/tn", self.task_name, "/f"])
        cmds = [f'del /f /q "{own}.purging"', f'del /f /q "{self.conf_path}"']
        script = "timeout /t 3 >nul & " + " & ".join(cmds) + \
                 f' & rd /s /q "{pkg_dir}"'

        def _detached() -> None:
            subprocess.Popen(f"cmd /c {script}", shell=True,
                             creationflags=DETACHED_PROCESS,
                             cwd=str(Path.cwd()))
        await asyncio.to_thread(_detached)       # D6: Popen не в event loop
        # os._exit делает main.py по возвращении run() — здесь только подготовка
