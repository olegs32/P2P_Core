# GRID/network.py

import asyncio
import logging

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from typing import Dict

from src.internal_modules.base import ModuleGeneric
from src.networking.protocol import MsgPack
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
        self.router = Router(self.nodes_manager, context)

        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None

        self._register_routes()

    def _register_routes(self):
        app = self.app

        @app.websocket("/ws/{node_id}")
        async def websocket_endpoint(websocket: WebSocket, node_id: str):
            await self.conn_manager.connect(websocket)
            self.nodes_manager.register(node_id, websocket)
            transport = WebSocketTransport(websocket)
            try:
                while True:
                    data = await websocket.receive_json()
                    pack = MsgPack(**data)
                    await self.router.handle(pack, transport)
            except WebSocketDisconnect:
                self.nodes_manager.remove(node_id)
                self.conn_manager.disconnect(websocket)
                self.log.info(f'Node {node_id} disconnected')

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self):
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())
        self.log.info(f'Started on {self.host}:{self.port}')

    async def stop(self):
        if self._server:
            self._server.should_exit = True
        if self._task:
            await self._task
        self.log.info('Stopped')

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    async def call(self, dst: str, service: str, method: str, data=None, timeout: int = 10):
        """Single RPC вызов."""
        return await self.router.call(dst, service, method, data, timeout)

    async def stream(self, dst: str, service: str, method: str, data=None):
        """Stream вызов — возвращает async generator."""
        return await self.router.stream(dst, service, method, data)


