# tests/test_update_core.py — ядро обновления _update.exe

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.internal_modules.update import (
    UPDATER_SUFFIX,
    get_updater_exe_path,
    _sha256,
    _replace_binary,
    _parse_args,
    is_updater_mode,
    _kill_streamlit_processes,
    _wait_old_process_gone,
)


def test_get_updater_exe_path():
    p = Path(r"C:\Core\Node_P2P_Core.exe")
    assert get_updater_exe_path(p) == Path(r"C:\Core\Node_P2P_Core_update.exe")
    # без суффикса .exe? fallback
    p2 = Path(r"C:\Core\Node_P2P_Core")
    assert str(get_updater_exe_path(p2)).endswith(UPDATER_SUFFIX)


def test_is_updater_mode():
    assert is_updater_mode(["--updater", "--old-pid", "123"]) is True
    assert is_updater_mode(["--other"]) is False


def test_parse_args():
    ns = _parse_args(["--updater", "--old-pid", "1234", "--old-exe", r"C:\a.exe", "--staged", r"C:\b.exe"])
    assert ns.old_pid == 1234
    assert ns.old_exe == r"C:\a.exe"
    assert ns.staged == r"C:\b.exe"
    assert ns.health_confirm_sec == 90
    ns2 = _parse_args(["--updater", "--old-pid", "1", "--old-exe", "a", "--staged", "b", "--health-confirm-sec", "5"])
    assert ns2.health_confirm_sec == 5


def test_sha256(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello world")
    import hashlib
    assert _sha256(f) == hashlib.sha256(b"hello world").hexdigest()


def test_replace_binary_success(tmp_path):
    old_exe = tmp_path / "Node_P2P_Core.exe"
    staged = tmp_path / "staged.exe"
    updater = tmp_path / "updater.exe"
    old_exe.write_bytes(b"old content")
    staged.write_bytes(b"new content")
    updater.write_bytes(b"updater old")
    ok, err = _replace_binary(old_exe, staged, updater)
    assert ok, err
    assert old_exe.read_bytes() == b"new content"
    assert (tmp_path / "Node_P2P_Core.exe.old").read_bytes() == b"old content"


def test_replace_binary_no_staged(tmp_path):
    old_exe = tmp_path / "Node_P2P_Core.exe"
    staged = tmp_path / "missing.exe"
    updater = tmp_path / "updater.exe"
    old_exe.write_bytes(b"old")
    ok, err = _replace_binary(old_exe, staged, updater)
    assert not ok
    assert "не найден" in err


def test_replace_binary_rollback_on_hash_mismatch(tmp_path, monkeypatch):
    old_exe = tmp_path / "Node_P2P_Core.exe"
    staged = tmp_path / "staged.exe"
    updater = tmp_path / "updater.exe"
    old_exe.write_bytes(b"old")
    staged.write_bytes(b"new")
    updater.write_bytes(b"up")
    # подменить _sha256 чтобы после копирования хеши не совпали
    import src.internal_modules.update as upd
    orig = upd._sha256
    def fake_sha(p):
        if p == old_exe:
            return "aaa"
        if p == staged:
            return "bbb"
        return orig(p)
    monkeypatch.setattr(upd, "_sha256", fake_sha)
    ok, err = _replace_binary(old_exe, staged, updater)
    assert not ok
    assert "не совпал" in err
    # должен откатиться на old
    assert old_exe.read_bytes() == b"old"


def test_wait_old_process_gone_already_dead():
    # pid 999999 точно не существует
    assert _wait_old_process_gone(999999, "nonexistent.exe", timeout=1) is True


def test_wait_old_process_gone_with_psutil_mock(monkeypatch):
    fake_psutil = MagicMock()
    fake_psutil.pid_exists.return_value = True
    proc = MagicMock()
    proc.name.return_value = "Node_P2P_Core.exe"
    fake_psutil.Process.return_value = proc
    fake_psutil.process_iter.return_value = []
    import src.internal_modules.update as upd
    monkeypatch.setattr(upd, "psutil", fake_psutil)
    # должен ждать таймаут и вернуть False (процесс не ушёл)
    start = time.time()
    res = _wait_old_process_gone(1234, "Node_P2P_Core.exe", timeout=1)
    elapsed = time.time() - start
    assert res is False
    assert elapsed >= 1


def test_kill_streamlit_processes_mock(monkeypatch):
    fake_psutil = MagicMock()
    cur_pid = 999
    with patch("src.internal_modules.update.os.getpid", return_value=cur_pid):
        p1 = MagicMock()
        p1.info = {"pid": 100, "cmdline": ["C:\\a.exe", "-m", "streamlit", "run"], "name": "a.exe"}
        p1.is_running.return_value = True
        p2 = MagicMock()
        p2.info = {"pid": 101, "cmdline": ["python", "other"], "name": "b.exe"}
        fake_psutil.process_iter.return_value = [p1, p2]
        fake_psutil.net_connections.return_value = []
        import src.internal_modules.update as upd
        monkeypatch.setattr(upd, "psutil", fake_psutil)
        killed = _kill_streamlit_processes()
        assert killed >= 1
        p1.terminate.assert_called()


def test_updater_main_parse_fail():
    from src.internal_modules.update import updater_main
    # без required args — должен вернуть 1
    rc = updater_main(["--updater"])
    assert rc != 0
