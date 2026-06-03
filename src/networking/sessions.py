# GRID/sessions.py — управление ожидающими запросами

import asyncio
import logging
from typing import Any, Dict

log = logging.getLogger('Sessions')



class SessionMeta:
    def __init__(self, service: str, method: str):
        self.service = service
        self.method  = method


class SessionTable:
    def __init__(self):
        self._table: Dict[str, asyncio.Future | asyncio.Queue] = {}
        self._meta:  Dict[str, SessionMeta] = {}

    def register_single(self, label: str, service: str = '', method: str = '') -> asyncio.Future:
        f = asyncio.get_event_loop().create_future()
        self._table[label] = f
        self._meta[label]  = SessionMeta(service, method)  # ← новое
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
        self._meta.pop(label, None)
        session = self._table.pop(label, None)
        if isinstance(session, asyncio.Future) and not session.done():
            session.cancel()

    def cancel_by_service(self, service_name: str) -> int:
        targets = [
            label for label, meta in self._meta.items()
            if meta.service == service_name
        ]
        for label in targets:
            self.cancel(label)
        return len(targets)

