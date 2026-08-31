# src/internal_modules/update.py — ядро обновления (watchdog + замена бинарника)
#
# Выделено из services/updater чтобы работать до инициализации узла.
# Запускается как frozen копия _update.exe с ключами (--updater ...).
# Отвечает за: ожидание завершения старого процесса, убийство streamlit,
# атомарную замену exe, запуск новой версии, watchdog health_confirm_sec,
# откат копированием себя при неудаче.

import argparse
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore

UPDATER_SUFFIX = "_update.exe"
UPDATER_FLAG = "--updater"

log = logging.getLogger("UpdaterCore")

# polling intervals
POLL_SEC = 0.5
KILL_TIMEOUT_SEC = 3


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(UPDATER_FLAG, action="store_true")
    p.add_argument("--old-pid", type=int, required=True)
    p.add_argument("--old-exe", type=str, required=True, help="путь к старому exe (цель замены)")
    p.add_argument("--staged", type=str, required=True, help="путь к новому скачанному exe")
    p.add_argument("--work-dir", type=str, default=".")
    p.add_argument("--health-confirm-sec", type=int, default=90)
    p.add_argument("--from-version", type=str, default="")
    p.add_argument("--to-version", type=str, default="")
    return p.parse_args(argv)


def is_updater_mode(argv: list[str] | None = None) -> bool:
    a = argv if argv is not None else sys.argv
    return UPDATER_FLAG in a


def _wait_old_process_gone(old_pid: int, old_exe_name: str, timeout: float = 30) -> bool:
    """Ждать исчезновения старого процесса по pid и имени. True если ушёл."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        gone = True
        if psutil is not None:
            try:
                if psutil.pid_exists(old_pid):
                    try:
                        proc = psutil.Process(old_pid)
                        # pid reuse защита: имя должно совпасть
                        name = proc.name().lower() if proc.name() else ""
                        if old_exe_name.lower() in name.lower() or name == "":
                            gone = False
                        else:
                            # pid занят другим процессом — считаем что старый ушёл
                            gone = True
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        gone = True
                # доп. проверка: жив ли процесс с таким exe именем (кроме нас)
                if gone:
                    # убедиться что нет других процессов с тем же exe путем
                    # ищем по имени среди всех, кроме нашего pid
                    cur_pid = os.getpid()
                    for proc in psutil.process_iter(["pid", "name", "exe"]):
                        try:
                            if proc.info["pid"] in (cur_pid, old_pid):
                                continue
                            n = (proc.info.get("name") or "").lower()
                            exe = (proc.info.get("exe") or "").lower()
                            if old_exe_name.lower() == n.lower() or old_exe_name.lower() in exe:
                                # ещё жив старый инстанс (дубль)
                                gone = False
                                break
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue
            except Exception:
                # fallback к простому pid_exists
                gone = not psutil.pid_exists(old_pid)
        else:
            # без psutil — только попытка открыть pid (Windows: OpenProcess)
            try:
                import ctypes
                handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, old_pid)  # PROCESS_QUERY_LIMITED_INFORMATION
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    gone = False
                else:
                    err = ctypes.GetLastError()
                    # 87 or 5?  If fails, assume gone
                    gone = True
            except Exception:
                gone = True
        if gone:
            return True
        time.sleep(POLL_SEC)
    return False


def _kill_streamlit_processes() -> int:
    """Найти и убить streamlit-дочки (тот же exe с -m streamlit). Возвращает число убитых."""
    killed = 0
    if psutil is None:
        return 0
    # также через порт 8501
    try:
        # 1. по cmdline
        cur_pid = os.getpid()
        for proc in psutil.process_iter(["pid", "cmdline", "name"]):
            try:
                if proc.info["pid"] == cur_pid:
                    continue
                cmd = proc.info.get("cmdline") or []
                cmd_s = " ".join(cmd).lower() if isinstance(cmd, list) else str(cmd).lower()
                if "streamlit" in cmd_s:
                    proc.terminate()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        # 2. по порту 8501
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr and getattr(conn.laddr, "port", None) == 8501 and conn.pid:
                    if conn.pid == cur_pid:
                        continue
                    try:
                        p = psutil.Process(conn.pid)
                        p.terminate()
                        killed += 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
        except (psutil.AccessDenied, NotImplementedError):
            pass
        # дождаться завершения
        deadline = time.time() + KILL_TIMEOUT_SEC
        while time.time() < deadline:
            alive = 0
            for proc in psutil.process_iter(["pid", "cmdline"]):
                try:
                    cmd = proc.info.get("cmdline") or []
                    cmd_s = " ".join(cmd).lower() if isinstance(cmd, list) else str(cmd).lower()
                    if "streamlit" in cmd_s and proc.info["pid"] != cur_pid and proc.is_running():
                        alive += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if alive == 0:
                break
            time.sleep(POLL_SEC)
        # force kill остатки
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = proc.info.get("cmdline") or []
                cmd_s = " ".join(cmd).lower() if isinstance(cmd, list) else str(cmd).lower()
                if "streamlit" in cmd_s and proc.info["pid"] != cur_pid:
                    try:
                        if proc.is_running():
                            proc.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as e:
        log.warning(f"kill streamlit failed: {e}")
    return killed


def _replace_binary(old_exe: Path, staged: Path, updater_exe: Path) -> tuple[bool, str]:
    """Атомарно заменить old_exe на staged. updater_exe — наша копия (старая версия)."""
    try:
        if not staged.is_file():
            return False, f"staged не найден: {staged}"
        old_old = old_exe.with_name(old_exe.name + ".old")
        # удалить предыдущий .old
        try:
            old_old.unlink(missing_ok=True)
        except OSError:
            pass
        # old -> .old (переименовать запущенный exe можно, но сейчас он уже должен быть мёртв)
        if old_exe.is_file():
            try:
                os.replace(old_exe, old_old)
            except OSError as e:
                # если файл ещё занят — пробуем через psutil ожидание ещё раз
                return False, f"не удалось переименовать старый exe: {e}"
        # staged -> old_exe
        try:
            shutil.copyfile(staged, old_exe)
        except OSError as e:
            # попытаться откатить
            try:
                if old_old.is_file() and not old_exe.is_file():
                    os.replace(old_old, old_exe)
            except OSError:
                pass
            return False, f"copy staged -> exe failed: {e}"
        # проверка целостности
        try:
            if _sha256(old_exe) != _sha256(staged):
                # откат
                try:
                    os.replace(old_old, old_exe)
                except OSError:
                    pass
                return False, "sha256 нового exe не совпал после копирования"
        except OSError as e:
            return False, f"sha256 check failed: {e}"
        return True, ""
    except Exception as e:
        return False, str(e)


def _launch_new_version(exe_path: Path, work_dir: Path) -> int | None:
    """Запустить новую версию. Новая консоль видима, pid отслеживаем."""
    try:
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        # close_fds=False чтобы новая консоль не закрывалась, shell=False — прямой запуск
        proc = subprocess.Popen(
            [str(exe_path)],
            cwd=str(work_dir),
            creationflags=creationflags,
            close_fds=False,
        )
        return proc.pid
    except OSError as e:
        log.error(f"launch new version failed: {e}")
        return None


def _is_process_alive(pid: int) -> bool:
    if psutil is not None:
        try:
            return psutil.pid_exists(pid) and psutil.Process(pid).is_running()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
    else:
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                # GetExitCodeProcess
                exit_code = ctypes.c_ulong()
                ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                ctypes.windll.kernel32.CloseHandle(handle)
                # STILL_ACTIVE = 259
                return exit_code.value == 259
            return False
        except Exception:
            return False


def _rollback(updater_exe: Path, target_exe: Path, work_dir: Path) -> bool:
    try:
        shutil.copyfile(updater_exe, target_exe)
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        subprocess.Popen(
            [str(target_exe), "--update-failed"],
            cwd=str(work_dir),
            creationflags=creationflags,
            close_fds=False,
        )
        return True
    except Exception as e:
        log.error(f"rollback failed: {e}")
        return False


def updater_main(argv: list[str] | None = None) -> int:
    """Точка входа _update.exe. Возвращает код выхода."""
    if argv is None:
        argv = sys.argv[1:]
    # убрать сам флаг --updater из argv если есть, но argparse его ждёт
    # используем sys.argv напрямую
    try:
        args = _parse_args(argv if UPDATER_FLAG in argv else [UPDATER_FLAG] + argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 1
    except Exception as e:
        print(f"updater arg parse failed: {e}", file=sys.stderr)
        return 1

    work_dir = Path(args.work_dir) if args.work_dir else Path(".")
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        log_dir = work_dir / "updates"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / "updater.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
        # sys.stdout может быть None в windowed/scheduler запуске
        stream = sys.stdout if sys.stdout else sys.stderr
        sh = logging.StreamHandler(stream) if stream else logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
        root = logging.getLogger()
        # очистить старые хендлеры чтобы не дублировать
        for h in list(root.handlers):
            try:
                root.removeHandler(h)
            except Exception:
                pass
        root.addHandler(fh)
        root.addHandler(sh)
        root.setLevel(logging.INFO)
    except Exception:
        pass

    old_exe = Path(args.old_exe)
    staged = Path(args.staged)
    updater_exe = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__).resolve()

    log.info(f"Updater started: old_pid={args.old_pid} old_exe={old_exe} staged={staged} updater={updater_exe} health={args.health_confirm_sec}")

    # 1. ждать смерти старого процесса
    log.info(f"Waiting for old process {args.old_pid} ({old_exe.name}) to exit...")
    ok = _wait_old_process_gone(args.old_pid, old_exe.name, timeout=30)
    if not ok:
        log.warning(f"Old pid {args.old_pid} still alive after 30s — forcing? Continue anyway")
        # не фатально, но попробуем ещё
        time.sleep(2)

    # 2. убить streamlit
    killed = _kill_streamlit_processes()
    if killed:
        log.info(f"Killed {killed} streamlit processes")

    # 3. замена бинарника
    ok, err = _replace_binary(old_exe, staged, updater_exe)
    if not ok:
        log.error(f"Replace failed: {err}")
        # не можем заменить — пытаемся просто запустить старый обратно?
        return 2

    log.info(f"Binary replaced: {old_exe} <- {staged}")

    # 4. запуск новой версии
    new_pid = _launch_new_version(old_exe, work_dir)
    if new_pid is None:
        log.error("Failed to launch new version — rollback")
        _rollback(updater_exe, old_exe, work_dir)
        return 3

    log.info(f"Launched new version pid={new_pid}, waiting {args.health_confirm_sec}s health...")

    # 5. watchdog
    deadline = time.time() + max(5, int(args.health_confirm_sec))
    failed = False
    while time.time() < deadline:
        if not _is_process_alive(new_pid):
            log.error(f"New process {new_pid} died before health confirm — rollback")
            failed = True
            break
        time.sleep(POLL_SEC)

    if not failed:
        # финальная проверка жива ли
        if not _is_process_alive(new_pid):
            failed = True
            log.error("New process not alive at health deadline — rollback")

    if failed:
        # откат: копируем себя в дефолтное имя и запускаем с флагом failed
        _rollback(updater_exe, old_exe, work_dir)
        log.info("Rollback launched")
        # удалить staged? оставить для диагностики
        return 4

    log.info(f"Update success: {args.from_version} -> {args.to_version} pid={new_pid} alive")
    # успех — самоудаление _update.exe? Попытаться удалить себя отложенно
    try:
        # отложенное удаление через cmd (себя нельзя удалить пока живы)
        if os.name == "nt":
            creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            creationflags |= 0x08000000
            subprocess.Popen(
                ["cmd", "/c", f'timeout /t 2 /nobreak >nul & del /f /q "{updater_exe}"'],
                creationflags=creationflags,
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass

    # обновить state файл как boot_ok?
    try:
        import json
        state_path = work_dir / "update_state.json"
        if state_path.is_file():
            st = json.loads(state_path.read_text(encoding="utf-8"))
            st["boot_ok"] = True
            st["confirmed_at"] = time.time()
            state_path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    return 0


def get_updater_exe_path(current_exe: Path) -> Path:
    """Путь к _update.exe рядом с текущим exe."""
    return current_exe.with_name(current_exe.stem + UPDATER_SUFFIX) if current_exe.suffix else Path(str(current_exe) + UPDATER_SUFFIX)
