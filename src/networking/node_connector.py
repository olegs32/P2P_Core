# GRID/node_connector.py
# Node1 подключается к Node0 как WS клиент

import asyncio
import json
import logging
import websockets

from src.internal_modules.base import ModuleGeneric
from protocol import MsgPack, PackType

log = logging.getLogger('NodeConnector')


class NodeConnector(ModuleGeneric):
    """
    Исходящее подключение к другой ноде.
    Получает пакеты и передаёт в локальный Router.
    """

    def __init__(self, name: str, context, target_uri: str):
        super().__init__(name, context)
        self.target_uri = target_uri
        self._ws = None
        self._task = None

    async def start(self):
        self._task = asyncio.create_task(self._connect_loop())
        self.log.info(f'Connecting to {self.target_uri}')

    async def stop(self):
        if self._task:
            self._task.cancel()
        if self._ws:
            await self._ws.close()
        self.log.info('Disconnected')

    async def _connect_loop(self):
        while True:
            try:
                async with websockets.connect(self.target_uri) as ws:
                    self._ws = ws
                    self.ctx.network.router.upstream_ws = ws
                    self.log.info(f'Connected to {self.target_uri}')

                    # пробросить ws в Router чтобы consumer мог слать ACK
                    self.ctx.network.router.upstream_ws = ws

                    async for raw in ws:
                        data = json.loads(raw)
                        pack = MsgPack(**data)

                        # EOF помечаем в stream_info для consumer
                        if pack.type == PackType.STREAM_EOF:
                            self.ctx.network.router.stream_registry._streams.get(pack.label) and \
                            setattr(
                                self.ctx.network.router
                                .stream_registry._streams[pack.label],
                                'eof', True
                            )

                        from transport import WebSocketTransport
                        transport = WebSocketTransport(ws)
                        await self.ctx.network.router.handle(pack, transport)

            except Exception as e:
                self.log.error(f'Connection error: {e} — retry in 3s')
                await asyncio.sleep(3)
