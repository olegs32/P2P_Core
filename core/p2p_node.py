import asyncio
import json
import time
from typing import Dict, List, Set, Optional, Callable
from dataclasses import dataclass, asdict
import httpx
from fastapi import FastAPI, WebSocket
import logging

from .dht import AsyncDHT, DHTNode
from .auth import P2PAuth
from .rpc_proxy import RPCProxy

logger = logging.getLogger(__name__)


@dataclass
class PeerInfo:
    node_id: str
    host: str
    port: int
    last_contact: float
    status: str = "active"


class P2PNode:
    """Основной P2P узел"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8000):
        self.host = host
        self.port = port
        # Используем новый AsyncDHT с префиксным поиском на порту +2000
        self.dht = AsyncDHT(host, port + 2000)
        self.auth = P2PAuth()
        self.peers: Dict[str, PeerInfo] = {}
        self.services: Dict[str, dict] = {}
        self.task_handlers: Dict[str, Callable] = {}
        self.active_tasks: Dict[str, dict] = {}
        self.websocket_connections: Set[WebSocket] = set()

        # события
        self.on_peer_joined: Optional[Callable] = None
        self.on_peer_left: Optional[Callable] = None
        self.on_task_completed: Optional[Callable] = None

    async def start(self, bootstrap_nodes: List[tuple] = None):
        await self.dht.start(bootstrap_nodes)
        await self._register_node()

        asyncio.create_task(self._peer_discovery_loop())
        asyncio.create_task(self._health_check_loop())
        asyncio.create_task(self._task_processor_loop())

        logger.info(f"P2P node started: {self.dht.node_id}")

    async def stop(self):
        await self._unregister_node()
        await self.dht.stop()

    async def _register_node(self):
        """Регистрация узла в DHT"""
        node_info = {
            "node_id": self.dht.node_id,
            "host": self.host,
            "port": self.port,
            "capabilities": list(self.services.keys()),
            "load": 0,
            "status": "active"
        }
        await self.dht.register_service("p2p_node", node_info)
        logger.info(f"Node registered in DHT: {node_info}")

    async def _unregister_node(self):
        # just broadcast leaving message
        await self.broadcast_message({
            "type": "node_leaving",
            "node_id": self.dht.node_id
        })

    async def _peer_discovery_loop(self):
        """Цикл обнаружения пиров"""
        while True:
            try:
                services = await self.dht.discover_services_mask("p2p_node")
                for svc in services:
                    peer_id = svc.get("node_id")
                    if peer_id and peer_id != self.dht.node_id:
                        await self._add_peer(peer_id, svc)
                        logger.info(f"Peer detected: {peer_id}")
            except Exception as e:
                logger.error(f"Peer discovery error: {e}")
            await asyncio.sleep(30)

    async def _health_check_loop(self):
        """Цикл проверки здоровья пиров"""
        while True:
            try:
                dead = []
                for peer_id, peer in list(self.peers.items()):
                    if time.time() - peer.last_contact > 120:
                        alive = await self._ping_peer(peer)
                        if not alive:
                            dead.append(peer_id)
                for pid in dead:
                    await self._remove_peer(pid)
            except Exception as e:
                logger.error(f"Health check error: {e}")
            await asyncio.sleep(60)

    async def _task_processor_loop(self):
        """Цикл обработки задач"""
        while True:
            try:
                for task in list(self.active_tasks.values()):
                    if task["status"] == "pending":
                        await self._process_task(task)
            except Exception as e:
                logger.error(f"Task processor error: {e}")
            await asyncio.sleep(1)

    async def _add_peer(self, peer_id: str, info: dict):
        if peer_id not in self.peers:
            peer = PeerInfo(
                node_id=peer_id,
                host=info.get("host"),
                port=info.get("port"),
                last_contact=time.time()
            )
            self.peers[peer_id] = peer
            if self.on_peer_joined:
                await self.on_peer_joined(peer)

    async def _remove_peer(self, peer_id: str):
        if peer_id in self.peers:
            peer = self.peers.pop(peer_id)
            if self.on_peer_left:
                await self.on_peer_left(peer)

    async def _ping_peer(self, peer: PeerInfo) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"http://{peer.host}:{peer.port}/health")
                if r.status_code == 200:
                    peer.last_contact = time.time()
                    return True
        except Exception:
            pass
        return False

    async def send_message(self, peer_id: str, message: dict) -> Optional[dict]:
        peer = self.peers.get(peer_id)
        if not peer:
            logger.error(f"Unknown peer: {peer_id}")
            return None
        token = self.auth.generate_token(self.dht.node_id)
        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"http://{peer.host}:{peer.port}/p2p/message",
                    json=message,
                    headers=headers
                )
                if r.status_code == 200:
                    peer.last_contact = time.time()
                    return r.json()
        except Exception as e:
            logger.error(f"Failed to send to {peer_id}: {e}")
        return None

    async def broadcast_message(self, message: dict) -> List[str]:
        tasks = [self.send_message(pid, message) for pid in self.peers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [pid for pid, res in zip(self.peers, results) if not isinstance(res, Exception) and res]

    def register_service(self, name: str, handler: Callable):
        self.services[name] = {"handler": handler, "registered_at": time.time()}

    def register_task_handler(self, task_type: str, handler: Callable):
        self.task_handlers[task_type] = handler

    async def submit_task(self, task_type: str, data: dict, target: str = None) -> str:
        tid = f"task_{int(time.time()*1000)}"
        task = {"id": tid, "type": task_type, "data": data, "status": "pending", "created_at": time.time(), "submitted_by": self.dht.node_id}
        if target and target in self.peers:
            resp = await self.send_message(target, {"type": "task_assignment", "task": task})
            if resp and resp.get("status") == "accepted":
                return tid
        self.active_tasks[tid] = task
        return tid

    async def _process_task(self, task: dict):
        ttype = task.get("type")
        handler = self.task_handlers.get(ttype)
        if not handler:
            task.update({"status": "failed", "error": f"No handler for {ttype}"})
            return
        try:
            task["status"] = "running"
            res = await handler(task["data"])
            task.update({"status": "completed", "result": res, "completed_at": time.time()})
            if self.on_task_completed:
                await self.on_task_completed(task)
        except Exception as e:
            task.update({"status": "failed", "error": str(e)})

    async def get_network_status(self) -> dict:
        return {
            "node_id": self.dht.node_id,
            "host": self.host,
            "port": self.port,
            "peers_count": len(self.peers),
            "peers": [asdict(p) for p in self.peers.values()],
            "services": list(self.services.keys()),
            "active_tasks": sum(1 for t in self.active_tasks.values() if t["status"] == "running"),
            "total_tasks": len(self.active_tasks)
        }

    async def add_websocket(self, ws: WebSocket):
        self.websocket_connections.add(ws)

    async def remove_websocket(self, ws: WebSocket):
        self.websocket_connections.discard(ws)

    async def broadcast_to_websockets(self, message: dict):
        if not self.websocket_connections:
            return
        to_remove = []
        for ws in self.websocket_connections:
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                to_remove.append(ws)
        for ws in to_remove:
            self.websocket_connections.discard(ws)
