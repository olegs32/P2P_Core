# GRID/transport.py — всё что знает про WS

import logging
from typing import Protocol
from fastapi import WebSocket
from GRID.protocol import MsgPack

log = logging.getLogger('Transport')


class ITransport(Protocol):
    """Абстракция транспорта — можно заменить на TCP/IPC."""
    async def send(self, pack: MsgPack): ...


class WebSocketTransport:
    def __init__(self, websocket: WebSocket):
        self.ws = websocket

    async def send(self, pack: MsgPack):
        await self.ws.send_json(pack.model_dump())
        log.debug(f'→ {pack.type} [{pack.label[:8]}] to {pack.dst}')