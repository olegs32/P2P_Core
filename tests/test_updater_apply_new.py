# tests/test_updater_apply_new.py — apply через _update.exe

import sys
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from src.internal_modules.config import Config, UpdateConfig, UpdateSource
from src.internal_modules.updater import Updater


def make_ctx(tmp_path):
    cfg = Config(update=UpdateConfig(
        sources=[UpdateSource(node='Admin', share='releases')],
        health_confirm_sec=5,
        auto_check=False,
    ))
    cfg.local.work_dir = tmp_path / "work"
    cfg.local.exe_name = "Node_P2P_Core.exe"
    ctx = MagicMock()
    ctx.NODE = "NodeSelf"
    ctx.config = cfg
    ctx.config.local = cfg.local
    ctx.network = MagicMock()
    ctx.network.neighbor_table.all.return_value = []
    ctx.services = MagicMock()
    return ctx


@pytest.mark.asyncio
async def test_apply_creates_updater_and_exits(tmp_path):
    ctx = make_ctx(tmp_path)
    files_dir = tmp_path / "work" / "downloads"
    files_dir.mkdir(parents=True, exist_ok=True)
    staged = files_dir / "9.9.9_Node_P2P_Core.exe"
    staged.write_bytes(b"new exe content")
    fake_exe = tmp_path / "Node_P2P_Core.exe"
    fake_exe.write_bytes(b"old exe content")

    svc = Updater("updater", ctx)
    svc.ctx = ctx
    svc._known = {
        "9.9.9": {"manifest": {"exe_sha256": __import__("hashlib").sha256(b"new exe content").hexdigest(), "exe_name": "Node_P2P_Core.exe"}, "node": "Admin", "share": "releases", "exe_rel": "9.9.9/Node_P2P_Core.exe"}
    }
    with patch.object(sys, "frozen", True, create=True), \
         patch.object(sys, "executable", str(fake_exe)), \
         patch("src.internal_modules.updater_verify.verify_signature", return_value=(True, "ok")), \
         patch("subprocess.Popen") as mock_popen, \
         patch("os._exit") as mock_exit, \
         patch("src.internal_modules.updater.asyncio.sleep", new=AsyncMock()):
        res = await svc.apply({"version": "9.9.9"})
        assert res["ok"] is True, res

    import asyncio as _aio
    await _aio.sleep(0)
    await _aio.sleep(0)
    updater_path = fake_exe.with_name(fake_exe.stem + "_update.exe")
    assert updater_path.is_file()
    assert updater_path.read_bytes() == b"old exe content"
    assert mock_popen.called
    args = mock_popen.call_args[0][0]
    assert "--updater" in args
    assert "--old-pid" in args
    assert str(staged) in args
    st = svc._load_state()
    assert st["to"] == "9.9.9"
    assert st["pending_boot_confirm"] is True
    for t in list(_aio.all_tasks()):
        if not t.done() and t is not _aio.current_task():
            t.cancel()
            try:
                await t
            except BaseException:
                pass
