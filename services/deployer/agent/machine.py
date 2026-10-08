# services/deployer/agent/machine.py
"""Конечный автомат deploy-агента.

Открытый флоу: детект и запись на диск делегированы host-адаптеру (Task 8/9),
формат пакета и проверка подписи — agent/package.py. run() возвращает
exit-code и не бросает.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path
from typing import Protocol

from services.deployer.agent.package import (
    AgentConf, BUSY, PreConfigError, validate_pre_config,
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
                if not await self.host.pid_alive(self._pid):
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
