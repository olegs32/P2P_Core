# GRID/node_connector.py

import asyncio
import json
import logging
import time
import uuid

import websockets

from src.internal_modules.base import ModuleGeneric
from src.networking.neighbor_table import PROTOCOL_VERSION
from src.networking.protocol import MsgPack, PackType
from src.networking.transport import WebSocketTransport
log = logging.getLogger('NodeConnector')

KEEPALIVE_INTERVAL = 20   # сек между проверками
KEEPALIVE_TIMEOUT  = 60   # сек без трафика → ping
DEAD_TIMEOUT       = 90   # сек без трафика → unreachable


class NodeConnector(ModuleGeneric):
    """Исходящее подключение к другой ноде."""

    def __init__(self, name: str, context, peer_node_id: str,
                 target_uri: str):
        super().__init__(name, context)
        self.peer_node_id = peer_node_id
        self.target_uri   = target_uri   # ws://host:port/ws/{own_node_id}
        self._ws          = None
        self._connect_task   = None
        self._keepalive_task = None

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self):
        if not self._should_connect():
            self.log.info(
                f'Skip connect to {self.peer_node_id} '
                f'(lexicographic rule: {self.ctx.NODE} > {self.peer_node_id})'
            )
            return
        self._connect_task   = asyncio.create_task(self._connect_loop())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        self.log.info(f'Connector started → {self.target_uri}')

    async def stop(self):
        if self._connect_task:
            self._connect_task.cancel()
        if self._keepalive_task:
            self._keepalive_task.cancel()
        if self._ws:
            await self._ws.close()
        self.log.info(f'Connector stopped ({self.peer_node_id})')

    # ------------------------------------------------------------------ #
    #  Правило подключения
    # ------------------------------------------------------------------ #

    def _should_connect(self) -> bool:
        """
        Подключаться только если наш node_id лексикографически меньше.
        Исключает взаимоподключение.
        """
        return self.ctx.NODE < self.peer_node_id

    def _already_connected(self) -> bool:
        return self.ctx.network.nodes_manager.get(self.peer_node_id) is not None

    # ------------------------------------------------------------------ #
    #  Подключение
    # ------------------------------------------------------------------ #

    async def _connect_loop(self):
        while True:
            if self._already_connected():
                await asyncio.sleep(5)
                continue
            try:
                async with websockets.connect(self.target_uri) as ws:
                    self._ws = ws
                    self.ctx.network.router.upstream_ws = ws

                    # handshake
                    accepted = await self._handshake(ws)
                    if not accepted:
                        self.log.warning(f'Handshake rejected by {self.peer_node_id}')
                        await asyncio.sleep(10)
                        continue

                    self.log.info(f'Connected to {self.peer_node_id}')

                    async for raw in ws:
                        data = json.loads(raw)
                        pack = MsgPack(**data)

                        # обновить last_ts при любом входящем трафике
                        self.ctx.network.neighbor_table.touch(pack.source)

                        transport = WebSocketTransport(ws)
                        await self.ctx.network.router.handle(pack, transport)

            except websockets.exceptions.ConnectionClosedError as e:
                self.log.warning(f'Connection to {self.peer_node_id} closed: {e}')
            except Exception as e:
                self.log.error(f'Connector error ({self.peer_node_id}): {e}')
            finally:
                self._ws = None
                self.ctx.network.neighbor_table.mark_unreachable(self.peer_node_id)
                await asyncio.sleep(5)

    async def _handshake(self, ws) -> bool:
        """Отправить HELLO, дождаться HELLO_ACK или HELLO_REJECT."""
        cfg  = self.ctx.config
        hello = MsgPack(
            type   = PackType.HELLO,
            source = self.ctx.NODE,
            dst    = self.peer_node_id,
            data   = {
                'node_id':    self.ctx.NODE,
                'host':       cfg.network.host,
                'port':       cfg.network.port,
                'version':    PROTOCOL_VERSION,
                'session_id': str(uuid.uuid4()),
                'services':   list(self.ctx.services.services.keys()),
            }
        )
        await ws.send(hello.model_dump_json())

        try:
            raw  = await asyncio.wait_for(ws.recv(), timeout=10)
            pack = MsgPack(**json.loads(raw))

            if pack.type == PackType.HELLO_ACK:
                await self._on_hello_ack(pack)
                return True
            elif pack.type == PackType.HELLO_REJECT:
                self.log.warning(
                    f'HELLO_REJECT from {self.peer_node_id}: '
                    f'{pack.data.get("reason")}'
                )
                return False
        except asyncio.TimeoutError:
            self.log.error(f'Handshake timeout with {self.peer_node_id}')
        return False

    async def _on_hello_ack(self, pack: MsgPack):
        """Обработать HELLO_ACK — смержить соседей и сервисы."""
        data = pack.data or {}

        # зарегистрировать ноду как connected
        self.ctx.network.neighbor_table.register_connected(
            node_id    = self.peer_node_id,
            host       = data.get('host', ''),
            port       = data.get('port', 9000),
            session_id = data.get('session_id', ''),
            version    = data.get('version', PROTOCOL_VERSION),
            services   = data.get('services', []),
        )

        # смержить таблицу соседей
        neighbors = data.get('neighbors', [])
        self.ctx.network.neighbor_table.merge_gossip(
            neighbors, from_node=self.peer_node_id
        )
        self.log.info(
            f'HELLO_ACK from {self.peer_node_id}: '
            f'+{len(neighbors)} neighbors'
        )

    # ------------------------------------------------------------------ #
    #  Keepalive
    # ------------------------------------------------------------------ #

    async def _keepalive_loop(self):
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            info = self.ctx.network.neighbor_table.get(self.peer_node_id)
            if not info:
                continue

            elapsed = time.time() - info.last_ts

            if elapsed > DEAD_TIMEOUT:
                self.log.warning(
                    f'{self.peer_node_id} no traffic {elapsed:.0f}s → unreachable'
                )
                self.ctx.network.neighbor_table.mark_unreachable(self.peer_node_id)

            elif elapsed > KEEPALIVE_TIMEOUT and self._ws:
                self.log.debug(f'Ping {self.peer_node_id} (no traffic {elapsed:.0f}s)')
                try:
                    transport = WebSocketTransport(self._ws)
                    await transport.send(MsgPack(
                        type   = PackType.PING,
                        source = self.ctx.NODE,
                        dst    = self.peer_node_id,
                    ))
                except Exception as e:
                    self.log.error(f'Keepalive ping failed: {e}')