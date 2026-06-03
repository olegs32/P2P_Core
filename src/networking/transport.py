# GRID/transport.py

import logging

import websockets

from src.networking.protocol import MsgPack, PackType

log = logging.getLogger('Transport')


class WebSocketTransport:
    """
    Универсальный транспорт — работает с обоими типами WS:
    - FastAPI WebSocket (server-side) — имеет send_json()
    - websockets ClientConnection (client-side) — имеет только send()
    """
    def __init__(self, websocket):
        self.ws = websocket
        # определяем тип один раз при создании
        self._is_fastapi = hasattr(websocket, 'send_json')

    async def send(self, pack: MsgPack):
        data = pack.model_dump_json()
        if self._is_fastapi:
            await self.ws.send_json(pack.model_dump())
        else:
            await self.ws.send(data)
        log.debug(f'→ {pack.type} [{pack.label[:8]}] to {pack.dst}')


async def send_ack(src: str, dst:str, ws: websockets, label: str, buff: int):
    pack = MsgPack(
        type=PackType.STREAM_ACK,
        source=src,
        dst=dst,
        label=label,
        data=buff,
    )
    transport = WebSocketTransport(ws)  # сам разберётся с типом
    await transport.send(pack)
    log.debug(f'ACK → {label[:8]} buff={buff}')