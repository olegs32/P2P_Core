# services/eyesauron/_session_helper.py — хелпер захвата в сессии пользователя
#
# Запускается сервисом eyesauron внутри активной интерактивной сессии
# (через WTS-инъекцию, см. _wts.py) — из session 0 рабочий стол недоступен.
# Хелпер максимально глуп: захватил кадр → проверил дедуп → положил в spool.
# Разбором spool и отправкой коллектору занимается процесс узла.
#
# Запуск:
#   frozen:  Node_P2P_Core.exe --eye-sauron-helper --spool <dir> [--interval S]
#   dev:     python -m services.eyesauron._session_helper --spool <dir> ...
# Точка перехвата argv для frozen — хук в начале main.py.
#
# Завершение: сессия разлогинилась (свой session id пропал из активных) либо
# сервис убьёт процесс при остановке/исчезновении сессии.

import argparse
import ctypes
import logging
import logging.handlers
import socket
import sys
import time
from pathlib import Path

if __package__ in (None, ''):
    # Запуск как файл (dev-режим WTS-инъекции): поднимаем корень репозитория
    # в sys.path, иначе package-импорты services.eyesauron.* не резолвятся
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.eyesauron._capture_core import (
    avg_hash, frame_timestamp, grab_frame, hash_distance, png_bytes,
    spool_write, window_title,
)
from services.eyesauron._wts import get_active_sessions, current_session_id

MUTEX_NAME = r'Local\EyeSauronCaptureMutex'   # Local\ = namespace сессии:
                                              # в разных сессиях — свои хелперы
LOG_MAX_BYTES = 1 * 1024 * 1024
SESSION_CHECK_EVERY = 10                      # сек между проверками своей сессии


def _setup_logging(spool_dir: Path):
    handler = logging.handlers.RotatingFileHandler(
        spool_dir / 'helper.log', maxBytes=LOG_MAX_BYTES, backupCount=1,
        encoding='utf-8')
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s'))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def acquire_mutex() -> bool:
    """Анти-размножение: второй хелпер той же сессии немедленно выходит."""
    handle = ctypes.windll.kernel32.CreateMutexW(None, True, MUTEX_NAME)
    return not (handle and ctypes.windll.kernel32.GetLastError() == 183)


def run(spool_dir: Path, interval: float):
    if not acquire_mutex():
        logging.info('уже запущен в этой сессии — выходим')
        return
    hostname = socket.gethostname()
    last_hash = None
    last_session_check = 0.0
    failures = 0
    logging.info('хелпер запущен: spool=%s interval=%.1fs', spool_dir, interval)

    while True:
        now = time.monotonic()

        # периодическая проверка: наша сессия ещё жива?
        if now - last_session_check >= SESSION_CHECK_EVERY:
            last_session_check = now
            mine = current_session_id()
            if mine != 0 and mine not in get_active_sessions():
                logging.info('сессия %s завершена — выходим', mine)
                return

        try:
            img = grab_frame()
            data = png_bytes(img) if img is not None else None
            if data is None:
                # чёрный экран/локскрин/мусор — не спамим лог, просто ждём
                time.sleep(interval)
                continue

            h = avg_hash(img)
            if last_hash is None or hash_distance(h, last_hash) > 0:
                last_hash = h
                meta = {
                    'hostname': hostname,
                    'timestamp': frame_timestamp(),
                    'title': window_title(),
                }
                path = spool_write(spool_dir, data, meta)
                logging.info('кадр: %s (%d bytes)', path.name, len(data))
            failures = 0
        except Exception:
            failures += 1
            if failures <= 3 or failures % 20 == 0:
                logging.exception('ошибка цикла захвата (серия %s)', failures)

        time.sleep(max(1.0, interval))


def main():
    parser = argparse.ArgumentParser(description='EyeSauron capture helper')
    parser.add_argument('--eye-sauron-helper', action='store_true',
                        help=argparse.SUPPRESS)
    parser.add_argument('--spool', required=True)
    parser.add_argument('--interval', type=float, default=5.0)
    args, _unknown = parser.parse_known_args()

    spool_dir = Path(args.spool)
    spool_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(spool_dir)

    try:
        run(spool_dir, args.interval)
    except Exception:
        logging.exception('фатальная ошибка хелпера')
        sys.exit(1)


if __name__ == '__main__':
    main()
