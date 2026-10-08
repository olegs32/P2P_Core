# tests/test_agent_machine.py
"""Конечный автомат агента: happy/откаты/busy/auth/админ недоступен/sha-retry."""
import asyncio
import hashlib
import time
import uuid

import pytest

from services.deployer.agent.package import AgentConf

CONF_D = {
    "protocol": 1, "mode": "open", "admin_ws": "ws://10.0.0.1:9000",
    "admin_node": "node0", "node_id": "node7", "key": "ab" * 32,
    "version": "1.0.0", "deploy_timeout": 5,
}
PC = {
    "v": 1, "mode": "open", "node": "node7", "deploy_id": str(uuid.uuid4()),
    "exp": time.time() + 3600, "config": "node: node7\n",
    "install": {"work_dir": r"C:\Core", "exe_name": "Node_P2P_Core.exe",
                "full_path": r"C:\Core\Node_P2P_Core.exe"},
    # sha256 согласован с фейковым payload b"NODE" (в плане стоял "ab"*32
    # от ключа — happy-path падал бы на mismatch; отклонение от фикстуры)
    "exe": {"name": "Node_P2P_Core.exe", "sha256": hashlib.sha256(b"NODE").hexdigest(),
            "size": 4, "version": "1.0.0"},
}


class FakeClient:
    # op -> значение или Exception; pre_config/node_seen переопределяем гибко.
    def __init__(self, pc=None, fail=None, trace=None):
        self.pc = pc if pc is not None else dict(PC)
        self.fail = fail            # Exception, который кидает connect/ping
        self.reports = []
        self.chunk_calls = 0
        self.node_seen_value = True
        self.pre_config_calls = 0
        self.trace = trace if trace is not None else []

    async def connect(self):
        if self.fail and self.fail.__class__.__name__ == "AdminDown":
            raise _admin_down()

    async def ping(self):
        if self.fail and self.fail.__class__.__name__ == "AdminDown":
            raise _admin_down()
        return time.time()

    async def pre_config(self, version):
        self.trace.append("pre_config")
        self.pre_config_calls += 1
        if isinstance(self.fail, str) and self.fail.startswith("op:"):
            name = self.fail[3:]
            if name == "pre_config_busy":
                raise _business("deploy in progress")
            if name == "pre_config_auth":
                raise _auth()
        return dict(self.pc)

    async def fetch_chunk(self, offset, size):
        self.chunk_calls += 1
        return b"NODE" [offset:offset + size]

    async def node_seen(self):
        return self.node_seen_value

    async def report(self, data):
        self.reports.append(dict(data))


class FakeHost:
    def __init__(self, system="ok", backup=True, die_after_launch=False, trace=None):
        self.system = system
        self.backup_ok = backup
        self.die = die_after_launch
        self.calls = []
        self.alive = True
        self.written = False
        self.trace = trace if trace is not None else []

    def detect(self, pc):
        self.calls.append("detect")
        return {"strategy": "open"}

    async def ensure_system(self):
        self.calls.append("ensure_system")
        self.trace.append("ensure_system")
        return self.system

    async def stop_node(self, exe_name):
        self.calls.append(("stop", exe_name))

    async def backup(self, deploy_id, pc, plan):
        self.calls.append("backup")
        return "C:\\Core\\.deploy_bak\\" + deploy_id if self.backup_ok else None

    async def write(self, pc, plan):
        self.calls.append("write")
        self.written = True

    async def launch(self, pc):
        self.calls.append("launch")
        self.alive = not self.die
        return 4242

    async def pid_alive(self, pid):
        return self.alive

    async def restore(self, backup_dir, pc):
        self.calls.append(("restore", backup_dir))

    async def self_terminate(self, code):
        self.calls.append(("terminate", code))


def _admin_down():
    from services.deployer.agent.machine import AdminUnreachable
    return AdminUnreachable("admin down")


def _business(msg):
    from services.deployer.agent.machine import BusinessError
    return BusinessError(msg)


def _auth():
    from services.deployer.agent.machine import AuthRejected
    return AuthRejected("auth failed")


def _run(conf, client, host):
    from services.deployer.agent.machine import DeployMachine
    return asyncio.run(DeployMachine(conf, client, host).run())


def _conf(**over):
    d = dict(CONF_D)
    d.update(over)
    return AgentConf.from_dict(d)


def test_happy_path_reports_done_and_terminates():
    trace: list = []
    client, host = FakeClient(trace=trace), FakeHost(trace=trace)
    code = _run(_conf(), client, host)
    assert code == 0
    stages = [r["stage"] for r in client.reports]
    assert stages[0] == "pre_config" and stages[-1] == "done"      # Delta 6: до pre_config отчётов нет
    assert ("terminate", 0) in host.calls and "write" in host.calls
    # Delta 1: ENSURE_SYSTEM строго ДО PRE_CONFIG (иначе deadlock busy-lock)
    assert trace.index("ensure_system") < trace.index("pre_config")
    assert client.pre_config_calls == 1


def test_handoff_no_cleanup():
    host = FakeHost(system="relaunched")
    code = _run(_conf(), FakeClient(), host)
    assert code == 0
    assert not any(c for c in host.calls if isinstance(c, tuple) and c[0] == "terminate")


def test_need_system_exit14_keeps_package():
    host = FakeHost(system="denied")
    code = _run(_conf(), FakeClient(), host)
    assert code == 14
    assert not any(c for c in host.calls if isinstance(c, tuple) and c[0] == "terminate")
    assert host.written is False


def test_process_dies_triggers_rollback():
    client, host = FakeClient(), FakeHost(die_after_launch=True, backup=True)
    code = _run(_conf(), client, host)
    assert code == 13
    assert any(isinstance(c, tuple) and c[0] == "restore" for c in host.calls)
    assert client.reports[-1]["stage"] in ("rolled_back", "failed")
    assert ("terminate", 13) in host.calls            # запись была -> cleanup


def test_no_backup_no_rollback_keeps_old_state():
    host = FakeHost(die_after_launch=True, backup=False)
    code = _run(_conf(), FakeClient(), host)
    assert code == 13
    assert not any(isinstance(c, tuple) and c[0] == "restore" for c in host.calls)
    assert any(isinstance(c, tuple) and c[0] == "terminate" for c in host.calls)


def test_busy_is_business_12_and_keeps_package():
    host = FakeHost()
    code = _run(_conf(), FakeClient(fail="op:pre_config_busy"), host)
    assert code == 12
    assert not any(c for c in host.calls if isinstance(c, tuple) and c[0] == "terminate")


def test_admin_down_is_11_and_keeps_package():
    host = FakeHost()
    code = _run(_conf(), FakeClient(fail=type("AdminDown", (), {})()), host)
    assert code == 11
    assert not any(c for c in host.calls if isinstance(c, tuple) and c[0] == "terminate")


def test_auth_rejected_is_10():
    code = _run(_conf(), FakeClient(fail="op:pre_config_auth"), FakeHost())
    assert code == 10


def test_exp_node_tamper_is_10():
    bad = dict(PC, node="node8")
    code = _run(_conf(), FakeClient(pc=bad), FakeHost())
    assert code == 10


def test_mode_mismatch_is_12():
    bad = dict(PC, mode="se")
    code = _run(_conf(), FakeClient(pc=bad), FakeHost())
    assert code == 12


def test_sha_mismatch_refetches_then_fails():
    class ShaClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.payload = b"NOPE"          # не совпадает с sha256(b"NODE")
        async def fetch_chunk(self, offset, size):
            self.chunk_calls += 1
            return self.payload[offset:offset + size]
    client = ShaClient()
    code = _run(_conf(), client, FakeHost())
    assert code == 13
    assert client.chunk_calls == 2               # 2 попытки по 1 чанк -> перекачка была


def test_watch_timeout_live_process_rolls_back(monkeypatch):
    import services.deployer.agent.machine as M
    monkeypatch.setattr(M, "ROLLBACK_SEEN_TIMEOUT", 1.0)   # иначе фейк ждал бы 60с

    class NeverSeen(FakeClient):
        async def node_seen(self):
            return False
    host = FakeHost(backup=True)
    code = _run(_conf(deploy_timeout=2), NeverSeen(), host)
    assert code == 13
    assert any(isinstance(c, tuple) and c[0] == "restore" for c in host.calls)
