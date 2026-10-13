# tests/test_agent_host.py
"""HostOpsWindows: файловые операции, бэкап/ротация, ensure_system, pid_alive, CLI."""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from services.deployer.agent.package import AgentConf

CONF_D = {
    "protocol": 1, "mode": "open", "admin_ws": "ws://127.0.0.1:9000",
    "admin_node": "admin0", "node_id": "node7", "key": "ab" * 32,
    "version": "1.0.0", "deploy_timeout": 5,
}


def _conf(**over):
    d = dict(CONF_D)
    d.update(over)
    return AgentConf.from_dict(d)


def _host(tmp_path, conf, **kw):
    from services.deployer.agent.machine import HostOpsWindows
    kw.setdefault("runner", lambda args: (0, ""))
    kw.setdefault("is_system_fn", lambda: True)
    return HostOpsWindows(conf, conf_path=tmp_path / "agent.conf", **kw)


def _pc(tmp_path, mode="open"):
    target = tmp_path / "Node_P2P_Core.exe"
    return {
        "v": 1, "mode": mode, "node": "node7", "deploy_id": "d-1",
        "exp": 2 ** 31, "config": "node: node7\n",
        "install": {"work_dir": str(tmp_path), "exe_name": "Node_P2P_Core.exe",
                    "full_path": str(target)},
        "exe": {"name": "Node_P2P_Core.exe", "sha256": "ab", "size": 4,
                "version": "1.0.0"},
    }


def test_write_replaces_new_and_writes_config(tmp_path):
    host, pc = _host(tmp_path, _conf()), _pc(tmp_path)
    target = Path(pc["install"]["full_path"])
    target.write_bytes(b"OLDNODE")
    Path(str(target) + ".new").write_bytes(b"NEWNODE")
    asyncio.run(host.write(pc, {"strategy": "open"}))
    assert target.read_bytes() == b"NEWNODE"
    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == pc["config"]
    assert not Path(str(target) + ".new").exists()


def test_backup_restore_and_rotation(tmp_path):
    host, pc = _host(tmp_path, _conf()), _pc(tmp_path)
    target = Path(pc["install"]["full_path"])
    target.write_bytes(b"OLD")
    (tmp_path / "config.yaml").write_text("old-cfg", encoding="utf-8")
    for i in range(5):                                  # ротация оставляет 3
        d = asyncio.run(host.backup(f"d{i}", pc, {"strategy": "open"}))
        assert d and Path(d).is_dir()
    baks = sorted((tmp_path / ".deploy_bak").iterdir())
    assert len(baks) == 3
    asyncio.run(host.restore(d, pc))
    assert target.read_bytes() == b"OLD"


def test_backup_none_when_no_prior_state(tmp_path):
    host, pc = _host(tmp_path, _conf()), _pc(tmp_path)
    assert asyncio.run(host.backup("d1", pc, {"strategy": "open"})) is None


def test_ensure_system_paths(tmp_path):
    calls = []
    runner = lambda args: (calls.append(args) or (0, ""))     # noqa: E731
    # уже SYSTEM
    h = _host(tmp_path, _conf(), is_system_fn=lambda: True, runner=runner)
    assert asyncio.run(h.ensure_system()) == "ok" and not calls
    # не SYSTEM -> create+run -> relaunched (родитель выйдет с 0)
    h = _host(tmp_path, _conf(), is_system_fn=lambda: False, runner=runner)
    assert asyncio.run(h.ensure_system()) == "relaunched"
    assert calls[0][:3] == ["schtasks", "/create", "/tn"] and calls[1][0] == "schtasks"
    assert "--system-run" in " ".join(calls[0])
    # /sc обязателен для schtasks /create (прод 13.10: без него exit=14)
    assert "/sc" in calls[0] and "ONCE" in calls[0]
    # повторный запуск уже с --system-run, всё ещё не SYSTEM -> сразу denied (14)
    h = _host(tmp_path, _conf(), is_system_fn=lambda: False,
              system_run=True, runner=runner)
    assert asyncio.run(h.ensure_system()) == "denied"
    # нет прав на создание задачи -> denied
    runner_bad = lambda args: (1, "Access is denied")          # noqa: E731
    h = _host(tmp_path, _conf(), is_system_fn=lambda: False, runner=runner_bad)
    assert asyncio.run(h.ensure_system()) == "denied"


def test_sid_check_does_not_crash():
    """Дымовой тест _sid_is_system: обязан вернуть bool, не упасть.

    Прод 13.10: без argtypes ctypes резал 64-битный SID-указатель до c_int
    → ConvertSidToStringSidW падал → всегда False даже под SYSTEM.
    Прямую True-ветку в CI не проверить (нужен SYSTEM), но smoke
    на win32 ловит регрессию сигнатур/падений.
    """
    import sys as _sys
    if not getattr(_sys, "winver", None) and __import__("os").name != "nt":
        import pytest as _pt
        _pt.skip("SID check — только Windows")
    from services.deployer.agent.machine import HostOpsWindows
    assert isinstance(HostOpsWindows._sid_is_system(), bool)


def test_pid_alive_self_and_dead():
    from services.deployer.agent.machine import HostOpsWindows
    h = HostOpsWindows(_conf(), conf_path=Path("x"), runner=lambda a: (0, ""),
                       is_system_fn=lambda: True)
    assert h.pid_alive(os.getpid()) is True
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait(timeout=30)
    assert h.pid_alive(p.pid) is False


def test_no_os_kill_anywhere():
    src = Path("services/deployer/agent/machine.py").read_text(encoding="utf-8")
    assert "os.kill" not in src                        # Delta 10: ctypes только


def test_cli_conf_errors(tmp_path):
    from services.deployer.agent.main import main
    assert main(["--conf", str(tmp_path / "missing.json")]) == 12
    bad = tmp_path / "agent.conf"
    bad.write_text("{not json", encoding="utf-8")
    assert main(["--conf", str(bad)]) == 12
