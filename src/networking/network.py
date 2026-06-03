# GRID/network.py

import asyncio
import logging
import uuid

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from typing import Dict

from src.internal_modules.base import ModuleGeneric
from src.internal_modules.exceptions import RoutingRequired
from src.networking.neighbor_table import PROTOCOL_VERSION, NeighborTable
from src.networking.protocol import MsgPack, PackType
from src.networking.router import Router
from src.networking.transport import WebSocketTransport

log = logging.getLogger('Network')


class Node:
    def __init__(self, node_id: str, ws: WebSocket):
        self.node_id = node_id
        self.ws = ws


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, pack: MsgPack):
        for ws in self.active_connections:
            await ws.send_json(pack.model_dump())


class NodesManager:
    def __init__(self):
        self.nodes: Dict[str, Node] = {}

    def register(self, node_id: str, websocket: WebSocket) -> Node:
        node = Node(node_id=node_id, ws=websocket)
        self.nodes[node_id] = node
        log.info(f'Node {node_id} registered')
        return node

    def remove(self, node_id: str):
        self.nodes.pop(node_id, None)
        log.info(f'Node {node_id} removed')

    def get(self, node_id: str) -> Node | None:
        return self.nodes.get(node_id)

    def get_nodes(self, count):
        if count > len(self.nodes):
            log.warning(f"Requested nodes count not existing in network, use {len(self.nodes)} of {count} requested")
            count = len(self.nodes)
        try:
            nodes = list(self.nodes.values())[:count]
        except Exception as e:
            log.error(f"Couldn't slice requested nodes scope: {e}")
            raise e
        return nodes


class NetworkModule(ModuleGeneric):
    def __init__(self, name: str, context, host: str = "0.0.0.0", port: int = 9000):
        super().__init__(name, context)
        self.host = host
        self.port = port
        self.app = FastAPI()
        self.conn_manager = ConnectionManager()
        self.nodes_manager: NodesManager = NodesManager()
        self.neighbor_table = NeighborTable(own_node_id=context.NODE)
        self.router = Router(self.nodes_manager, context)
        self._server        = None
        self._task          = None
        self._gossip_task   = None
        self._announce_task = None
        self._register_routes()

    def _register_routes(self):
        app = self.app

        @app.websocket("/ws/{node_id}")
        async def websocket_endpoint(websocket: WebSocket, node_id: str):
            await self.conn_manager.connect(websocket)
            transport = WebSocketTransport(websocket)

            try:
                # ждём HELLO первым пакетом
                raw  = await asyncio.wait_for(websocket.receive_json(), timeout=10)
                pack = MsgPack(**raw)

                if pack.dst != self.ctx.NODE:
                    await transport.send(MsgPack(
                        type=PackType.HELLO_REJECT,
                        source=self.ctx.NODE,
                        dst=node_id,
                        data={'reason': f'Routing update required to reach {pack.dst}'},
                    ))
                    return
                    # raise RoutingRequired(pack.dst)

                if pack.type != PackType.HELLO:
                    await transport.send(MsgPack(
                        type   = PackType.HELLO_REJECT,
                        source = self.ctx.NODE,
                        dst    = node_id,
                        data   = {'reason': 'expected HELLO'},
                    ))
                    return

                # проверить дубликат
                if self.nodes_manager.get(node_id):
                    await transport.send(MsgPack(
                        type   = PackType.HELLO_REJECT,
                        source = self.ctx.NODE,
                        dst    = node_id,
                        data   = {'reason': f'connection already exists: {node_id}'},
                    ))
                    self.conn_manager.disconnect(websocket)
                    return

                # принять
                hello_data = pack.data or {}
                session_id = str(uuid.uuid4())

                self.nodes_manager.register(node_id, websocket)
                self.neighbor_table.register_connected(
                    node_id    = node_id,
                    host       = hello_data.get('host', ''),
                    port       = hello_data.get('port', 9000),
                    session_id = session_id,
                    version    = hello_data.get('version', PROTOCOL_VERSION),
                    services   = hello_data.get('services', []),
                )

                # ответить HELLO_ACK с текущей таблицей соседей и сервисами
                await transport.send(MsgPack(
                    type   = PackType.HELLO_ACK,
                    source = self.ctx.NODE,
                    dst    = node_id,
                    data   = {
                        'host':       self.host,
                        'port':       self.port,
                        'version':    PROTOCOL_VERSION,
                        'session_id': session_id,
                        'services':   list(self.ctx.services.services.keys()),
                        'neighbors':  self.neighbor_table.to_gossip(),
                    }
                ))
                self.log.info(f'Node {node_id} accepted (session={session_id[:8]})')

                # основной цикл
                while True:
                    data = await websocket.receive_json()
                    pack = MsgPack(**data)

                    # обновить last_ts при любом трафике
                    self.neighbor_table.touch(pack.source)

                    await self.router.handle(pack, transport)

            except asyncio.TimeoutError:
                self.log.warning(f'HELLO timeout from {node_id}')
            except WebSocketDisconnect:
                self.nodes_manager.remove(node_id)
                self.conn_manager.disconnect(websocket)
                self.neighbor_table.mark_unreachable(node_id)
                self.log.info(f'Node {node_id} disconnected')

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self):
        config = uvicorn.Config(
            self.app, host=self.host, port=self.port, log_level="warning"
        )
        self._server = uvicorn.Server(config)
        self._task   = asyncio.create_task(self._server.serve())
        self._gossip_task   = asyncio.create_task(self._gossip_loop())
        self._announce_task = asyncio.create_task(self._announce_loop())
        self.log.info(f'Started on {self.host}:{self.port}')

    async def stop(self):
        for task in (self._gossip_task, self._announce_task):
            if task:
                task.cancel()
        if self._server:
            self._server.should_exit = True
        if self._task:
            await self._task
        self.log.info('Stopped')

    # ------------------------------------------------------------------ #
    #  Периодические рассылки
    # ------------------------------------------------------------------ #

    async def _gossip_loop(self):
        """Каждые 30с рассылать таблицу соседей всем connected нодам."""
        while True:
            await asyncio.sleep(30)
            neighbors = self.neighbor_table.to_gossip()
            if not neighbors:
                continue
            pack = MsgPack(
                type   = PackType.GOSSIP,
                source = self.ctx.NODE,
                data   = {'neighbors': neighbors, 'from': self.ctx.NODE},
            )
            for node in self.neighbor_table.connected():
                ws_node = self.nodes_manager.get(node.node_id)
                if ws_node:
                    transport = WebSocketTransport(ws_node.ws)
                    try:
                        await transport.send(pack)
                    except Exception as e:
                        self.log.error(f'Gossip to {node.node_id} failed: {e}')

    async def _announce_loop(self):
        """Каждые 60с рассылать список сервисов всем connected нодам."""
        while True:
            await asyncio.sleep(60)
            services = list(self.ctx.services.services.keys())
            pack = MsgPack(
                type   = PackType.ANNOUNCE,
                source = self.ctx.NODE,
                data   = {'services': services, 'from': self.ctx.NODE},
            )
            for node in self.neighbor_table.connected():
                ws_node = self.nodes_manager.get(node.node_id)
                if ws_node:
                    transport = WebSocketTransport(ws_node.ws)
                    try:
                        await transport.send(pack)
                    except Exception as e:
                        self.log.error(f'Announce to {node.node_id} failed: {e}')

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    async def call(self, dst: str, service: str, method: str, data=None, timeout: int = 10):
        """Single RPC вызов."""
        return await self.router.call(dst, service, method, data, timeout)

    async def stream(self, dst: str, service: str, method: str, data=None):
        """Stream вызов — возвращает async generator."""
        return await self.router.stream(dst, service, method, data)


