# GRID/sessions.py — управление ожидающими запросами

import asyncio
import logging
from typing import Any, Dict

log = logging.getLogger('Sessions')


class RPCTimeout(Exception):
    def __init__(self, label, timeout):
        super().__init__(f'timeout {timeout}s label={label[:8]}')


class SessionTable:
    def __init__(self):
        self._table: Dict[str, asyncio.Future | asyncio.Queue] = {}

    def register_single(self, label: str) -> asyncio.Future:
        f = asyncio.get_event_loop().create_future()
        self._table[label] = f
        return f

    def register_stream(self, label: str) -> asyncio.Queue:
        q = asyncio.Queue()
        self._table[label] = q
        return q

    def has(self, label: str) -> bool:
        return label in self._table

    def resolve(self, label: str, data: Any):
        session = self._table.get(label)
        if isinstance(session, asyncio.Future) and not session.done():
            session.set_result(data)
            self._table.pop(label)
        elif isinstance(session, asyncio.Queue):
            session.put_nowait(data)

    def close_stream(self, label: str):
        session = self._table.get(label)
        if isinstance(session, asyncio.Queue):
            session.put_nowait(None)  # sentinel
            self._table.pop(label)

    def cancel(self, label: str):
        session = self._table.pop(label, None)
        if isinstance(session, asyncio.Future) and not session.done():
            session.cancel()