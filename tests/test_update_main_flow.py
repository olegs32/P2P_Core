# tests/test_update_main_flow.py — полный поток updater_main

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.internal_modules.update import updater_main


def test_updater_main_success_flow(tmp_path):
    work = tmp_path / "work"
    work.mkdir(parents=True)
    old_exe = tmp_path / "Node_P2P_Core.exe"
    staged = tmp_path / "staged.exe"
    updater_exe = tmp_path / "Node_P2P_Core_update.exe"

    old_exe.write_bytes(b"old")
    staged.write_bytes(b"new")
    updater_exe.write_bytes(b"old")  # updater is old version

    # state file
    import json
    state = {"from": "1.0.0", "to": "1.0.1", "pending_boot_confirm": True, "boot_ok": False}
    (work / "update_state.json").write_text(json.dumps(state), encoding="utf-8")

    # mock sys.executable to updater_exe
    with patch.object(sys, "executable", str(updater_exe)), \
         patch.object(sys, "frozen", True, create=True), \
         patch("src.internal_modules.update._wait_old_process_gone", return_value=True) as mock_wait, \
         patch("src.internal_modules.update._kill_streamlit_processes", return_value=1) as mock_kill, \
         patch("src.internal_modules.update._launch_new_version", return_value=99999) as mock_launch, \
         patch("src.internal_modules.update._is_process_alive", return_value=True), \
         patch("subprocess.Popen") as mock_popen:

        rc = updater_main([
            "--updater",
            "--old-pid", "1234",
            "--old-exe", str(old_exe),
            "--staged", str(staged),
            "--work-dir", str(work),
            "--health-confirm-sec", "1",
            "--from-version", "1.0.0",
            "--to-version", "1.0.1",
        ])
        assert rc == 0
        mock_wait.assert_called()
        mock_kill.assert_called()
        mock_launch.assert_called()
        # old_exe should have been replaced with staged content
        assert old_exe.read_bytes() == b"new"
        assert (old_exe.with_name(old_exe.name + ".old")).read_bytes() == b"old"
        # state boot_ok
        st = json.loads((work / "update_state.json").read_text(encoding="utf-8"))
        assert st["boot_ok"] is True


def test_updater_main_rollback_on_dead(tmp_path):
    work = tmp_path / "work"
    work.mkdir(parents=True)
    old_exe = tmp_path / "Node_P2P_Core.exe"
    staged = tmp_path / "staged.exe"
    updater_exe = tmp_path / "Node_P2P_Core_update.exe"

    old_exe.write_bytes(b"old")
    staged.write_bytes(b"new")
    updater_exe.write_bytes(b"old")

    with patch.object(sys, "executable", str(updater_exe)), \
         patch.object(sys, "frozen", True, create=True), \
         patch("src.internal_modules.update._wait_old_process_gone", return_value=True), \
         patch("src.internal_modules.update._kill_streamlit_processes", return_value=0), \
         patch("src.internal_modules.update._launch_new_version", return_value=11111), \
         patch("src.internal_modules.update._is_process_alive", return_value=False), \
         patch("src.internal_modules.update._rollback", return_value=True) as mock_rb:

        rc = updater_main([
            "--updater",
            "--old-pid", "9999",
            "--old-exe", str(old_exe),
            "--staged", str(staged),
            "--work-dir", str(work),
            "--health-confirm-sec", "1",
        ])
        assert rc == 4
        mock_rb.assert_called()
