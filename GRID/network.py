# modules/network.py
import asyncio
import logging
from typing import Dict

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

log = logging.getLogger('Network')


class Node(BaseModel):
    node_id: str
    ws: WebSocket
    services: Dict[str, Dict[str, str]] = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        for ws in self.active_connections:
            await ws.send_text(message)


class NodesManager:
    def __init__(self, conn_manager: ConnectionManager):
        self.conn_manager = conn_manager
        self.nodes: Dict[str, Node] = {}

    def register(self, node_id: str, websocket: WebSocket) -> bool:
        self.nodes[node_id] = Node(node_id=node_id, ws=websocket)
        log.info(f'Node {node_id} registered')
        return True  # here will be a secure lvl

    def remove(self, node_id: str):
        self.nodes.pop(node_id, None)
        log.info(f'Node {node_id} removed')


class NetworkModule:
    def __init__(self, host: str = "0.0.0.0", port: int = 9000):
        self.host = host
        self.port = port
        self.app = FastAPI()

        self.conn_manager = ConnectionManager()
        self.nodes_manager = NodesManager(self.conn_manager)

        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None

        self._register_routes()

    def _register_routes(self):
        app = self.app

        @app.websocket("/ws/{node_id}")
        async def websocket_endpoint(websocket: WebSocket, node_id: str):
            await self.conn_manager.connect(websocket)
            self.nodes_manager.register(node_id, websocket)
            try:
                while True:
                    data = await websocket.receive_text()
                    await self.conn_manager.send(f"You wrote: {data}", websocket)
                    await self.conn_manager.broadcast(f"Client #{node_id} says: {data}")
            except WebSocketDisconnect:
                self.nodes_manager.remove(node_id)
                self.conn_manager.disconnect(websocket)
                await self.conn_manager.broadcast(f"Client #{node_id} left the chat")

    async def start(self):
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="info")
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())
        log.info(f'[network] started on {self.host}:{self.port}')

    async def stop(self):
        if self._server:
            self._server.should_exit = True
        if self._task:
            await self._task
        log.info('[network] stopped')