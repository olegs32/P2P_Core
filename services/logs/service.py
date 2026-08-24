# services/logs/service.py
# =============================================================================
#  Сервис просмотра логов консоли узла.
#
#  Как это работает:
#    * RingBufferHandler цепляется к root logger в start() и копирует каждую
#      запись, дошедшую до корня (т.е. всё то же, что видно в консоли),
#      в ограниченную очередь в памяти (deque с maxlen);
#    * панель опрашивает узел RPC-методом get_logs() и передаёт since_id —
#      идентификатор последней уже полученной записи, поэтому по сети ходит
#      только дельта, а не весь буфер;
#    * id сквозной и монотонный: по нему же детектируется «обрыв» буфера
#      (если между опросами записей пришло больше, чем влезло в deque).
#
#  Ограничение: видны только записи, доходящие до root logger (уровень
#  задаётся config.yaml → logging.level). Логгеры с propagate=False и логи
#  самого Streamlit-процесса в буфер не попадают.
# =============================================================================

import itertools
import logging
import re
import threading
from collections import deque

from services.rpc import rpc
from src.internal_modules.base import ModuleGeneric

_MAX_MSG = 4000        # обрезка одного сообщения
_MAX_TB = 2000         # обрезка traceback (хвост, чтобы сохранить исключение)


class RingBufferHandler(logging.Handler):
    """Кольцевой буфер лог-записей фиксированной ёмкости.

    Каждая запись получает сквозной монотонный id (itertools.count) —
    по нему клиент делает инкрементальный поллинг.
    """

    def __init__(self, maxlen: int = 2000):
        super().__init__()
        self.buffer = deque(maxlen=maxlen)
        self._ids = itertools.count()
        # Handler.emit вызывается под внутренним lock'ом logging,
        # но читают буфер и из RPC-потока — страхуемся своим замком
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord):
        try:
            entry = {
                'id': next(self._ids),
                'ts': record.created,          # epoch float
                'level': record.levelname,
                'logger': record.name,
                'msg': record.getMessage()[:_MAX_MSG],
            }
            if record.exc_info:
                tb = self.formatException(record.exc_info)
                if len(tb) > _MAX_TB:
                    tb = '...' + tb[-_MAX_TB:]
                entry['msg'] += '\n' + tb

            with self._lock:
                self.buffer.append(entry)
        except Exception:
            self.handleError(record)

    def snapshot(self) -> list:
        with self._lock:
            return list(self.buffer)

    def clear(self):
        with self._lock:
            self.buffer.clear()


class Logs(ModuleGeneric):
    """Просмотр консольных логов узла через веб-панель."""

    MAXLEN = 2000

    def __init__(self, name, context):
        super().__init__(name, context)
        self._handler: RingBufferHandler | None = None

    # ------------------------------------------------------------------ #
    #  Жизненный цикл: подключение/отключение обработчика к root logger
    # ------------------------------------------------------------------ #

    async def start(self):
        root = logging.getLogger()
        if self._handler is None:
            self._handler = RingBufferHandler(maxlen=self.MAXLEN)
        if self._handler not in root.handlers:
            root.addHandler(self._handler)
            self.log.info(f'Log buffer attached (cap={self.MAXLEN})')

    async def stop(self):
        if self._handler:
            logging.getLogger().removeHandler(self._handler)
            self.log.info('Log buffer detached')
            # self._handler не затираем: после hot-reload новый экземпляр
            # создаст свой, а этот не даст задвоить добавление

    # ------------------------------------------------------------------ #
    #  RPC API
    # ------------------------------------------------------------------ #

    @rpc
    def get_logs(self, data: dict) -> dict:
        """Вернуть записи буфера с фильтрами.

        data:
          since_id: int       — вернуть только записи с id > since_id
                                (0 = весь буфер)
          levels: list[str]   — какие severity оставить (None = все)
          search: str         — подстрока в сообщении (без регистра)
          regex: str          — python-regex по сообщению (приоритетнее search;
                                невалидный паттерн → {'ok': False, ...})
          loggers: list[str]  — оставить записи перечисленных логгеров
          since_ts / until_ts — границы по времени (epoch float)
          limit: int          — не более N СВЕЖИХ записей после фильтрации

        Ответ: entries[], last_id (новейший id в буфере — новый курсор для
        поллинга), total_matched, buffer_size, gap (True — между опросами
        часть записей вытеснилась из буфера).
        """
        h = self._handler
        if h is None:
            return {'ok': False, 'error': 'буфер логов не запущен'}

        d = data or {}
        since_id = int(d.get('since_id') or 0)
        levels = {str(l).upper() for l in (d.get('levels') or [])} or None
        loggers = set(d.get('loggers') or []) or None
        search = (d.get('search') or '').lower()
        regex_src = d.get('regex') or ''
        limit = max(1, min(int(d.get('limit') or 500), h.buffer.maxlen))
        since_ts = d.get('since_ts')
        until_ts = d.get('until_ts')

        pattern = None
        if regex_src:
            try:
                pattern = re.compile(regex_src)
            except re.error as e:
                return {'ok': False, 'error': f'неверный regex: {e}'}

        matched = []
        for e in h.snapshot():
            if e['id'] <= since_id:
                continue
            if levels and e['level'] not in levels:
                continue
            if loggers and e['logger'] not in loggers:
                continue
            if since_ts and e['ts'] < since_ts:
                continue
            if until_ts and e['ts'] > until_ts:
                continue
            if pattern is not None:
                if not pattern.search(e['msg']):
                    continue
            elif search and search not in e['msg'].lower():
                continue
            matched.append(e)

        # «обрыв» буфера: самая старая новая запись дальше, чем since_id+1 —
        # значит часть записей вытеснилась из deque между опросами
        gap = since_id > 0 and bool(matched) and matched[0]['id'] > since_id + 1

        entries = matched[-limit:]
        snapshot = h.snapshot()
        last_id = snapshot[-1]['id'] if snapshot else since_id

        return {
            'ok': True,
            'entries': entries,
            'last_id': last_id,
            'total_matched': len(matched),
            'buffer_size': len(snapshot),
            'gap': gap,
        }

    @rpc
    def get_loggers(self, data: dict) -> dict:
        """Имена логгеров, встретившихся в буфере (для фильтра в панели)."""
        h = self._handler
        if h is None:
            return {'ok': False, 'error': 'буфер логов не запущен'}
        names = sorted({e['logger'] for e in h.snapshot()})
        return {'ok': True, 'loggers': names}

    @rpc
    def clear_buffer(self, data: dict) -> dict:
        """Очистить кольцевой буфер."""
        h = self._handler
        if h is None:
            return {'ok': False, 'error': 'буфер логов не запущен'}
        h.clear()
        self.log.info('Log buffer cleared via web panel')
        return {'ok': True}
