# tests/test_agent_build_exe.py
"""Фоновая сборка DeployAgent из UI: build_agent_exe + agent_build_status.

Мотивация: пакет встраивает КОПИЮ dist/DeployAgent.exe на момент генерации,
поэтому после правок агента нужна явная пересборка (пересборка ноды её не
даёт) — кнопка в UI вместо ручного `compile.py --type agent`.
"""
import asyncio
import threading

import pytest

from test_agent_api_integration import make_deployer, run


def _patch_build(monkeypatch, dist, seen, release=None, ok=True):
    import compile as comp_mod

    def _fake(flavor):
        seen.append(flavor)
        if release is not None:
            assert release.wait(timeout=30), "воркер завис без release"
        (dist / ("DeployAgent_se.exe" if flavor == "se" else "DeployAgent.exe")
         ).write_bytes(b"MZ-agent")
        return ok

    monkeypatch.setattr(comp_mod, "build_agent", _fake)


def test_build_agent_exe_background_and_status(tmp_path, monkeypatch):
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    seen: list = []
    _patch_build(monkeypatch, dist, seen)

    async def _drive():
        res = await svc.build_agent_exe({})
        assert res["ok"] is True and res["started"] is True
        assert res["flavor"] == "open"          # дефолт = _admin_mode()
        mid = await svc.agent_build_status({})
        assert mid["state"] in ("running", "done")
        await svc._agent_build_task
        return await svc.agent_build_status({})

    st = asyncio.run(_drive())
    assert seen == ["open"]
    assert st["state"] == "done"
    assert st["result"]["ok"] is True
    assert st["result"]["exe"].endswith("DeployAgent.exe")
    assert st["result"]["size"] == len(b"MZ-agent")


def test_build_agent_exe_bad_flavor(tmp_path, monkeypatch):
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    res = run(svc.build_agent_exe({"flavor": "win"}))
    assert res["ok"] is False and "flavor" in res["error"]


def test_build_agent_exe_guard_while_running(tmp_path, monkeypatch):
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    seen: list = []
    release = threading.Event()
    _patch_build(monkeypatch, dist, seen, release=release)

    async def _drive():
        r1 = await svc.build_agent_exe({"flavor": "open"})
        assert r1.get("started") is True
        r2 = await svc.build_agent_exe({"flavor": "open"})
        assert r2["ok"] is False and "already running" in r2["error"]
        release.set()
        await svc._agent_build_task
        st = await svc.agent_build_status({})
        assert st["state"] == "done"
        assert seen == ["open"]                # второй запуск не стартовал

    asyncio.run(_drive())


def test_build_agent_exe_guard_while_node_build(tmp_path, monkeypatch):
    """PyInstaller один за раз: сборка ноды блокирует и сборку агента."""
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)

    async def _drive():
        async def _never():
            await asyncio.sleep(3600)

        svc._build_task = asyncio.create_task(_never())
        try:
            r = await svc.build_agent_exe({})
            assert r["ok"] is False and "already running" in r["error"]
        finally:
            svc._build_task.cancel()
            try:
                await svc._build_task
            except (asyncio.CancelledError, Exception):
                pass
            svc._build_task = None

    asyncio.run(_drive())


def test_agent_status_reports_agent_build_and_exe(tmp_path, monkeypatch):
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    info = run(svc.agent_status({}))
    assert isinstance(info.get("agent_build"), dict)
    assert info["agent_build"]["state"] == "idle"
    exe = info.get("agent_exe") or {}
    assert exe.get("name") == "DeployAgent.exe" and exe.get("exists") is False
    (dist / "DeployAgent.exe").write_bytes(b"MZ-agent")
    info2 = run(svc.agent_status({}))
    assert info2["agent_exe"]["exists"] is True
    assert info2["agent_exe"]["size"] == len(b"MZ-agent")
    # вариант A: адрес агента (open — mesh-порт, SE — bootstrap-порт)
    assert info2["agent_ws"].startswith("ws://")
