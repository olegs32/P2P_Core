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
        self.dht = AsyncDHT(host, port + 2000)  # DHT на другом порту
        self.auth = P2PAuth()
        self.peers: Dict[str, PeerInfo] = {}
        self.services: Dict[str, dict] = {}
        self.task_handlers: Dict[str, Callable] = {}
        self.active_tasks: Dict[str, dict] = {}
        self.websocket_connections: Set[WebSocket] = set()

        # События
        self.on_peer_joined: Optional[Callable] = None
        self.on_peer_left: Optional[Callable] = None
        self.on_task_completed: Optional[Callable] = None

    async def start(self, bootstrap_nodes: List[tuple] = None):
        """Запуск P2P узла"""
        await self.dht.start(bootstrap_nodes)
        await self._register_node()

        # Запуск фоновых задач
        asyncio.create_task(self._peer_discovery_loop())
        asyncio.create_task(self._health_check_loop())
        asyncio.create_task(self._task_processor_loop())

        logger.info(f"P2P node started: {self.dht.node_id}")

    async def stop(self):
        """Остановка P2P узла"""
        await self._unregister_node()
        await self.dht.stop()

    async def _register_node(self):
        """Регистрация узла в DHT"""
        node_info = {
            "host": self.host,
            "port": self.port,
            "capabilities": list(self.services.keys()),
            "load": 0,
            "status": "active"
        }
        await self.dht.register_service("p2p_node", node_info)
        print(f"Node registered: {node_info}")

    async def _unregister_node(self):
        """Отмена регистрации узла"""
        # Уведомление других узлов об уходе
        await self.broadcast_message({
            "type": "node_leaving",
            "node_id": self.dht.node_id
        })

    async def _peer_discovery_loop(self):
        """Цикл обнаружения пиров"""
        while True:
            # try:
            services = await self.dht.discover_services("p2p_node")
            print('services', services)
            for service in services:
                for node in service:
                    print('node', node, 'service', service)
                    print(node, dict(service)[node], service, )
                    if node != self.dht.node_id:
                        await self._add_peer(node, service[node])
                        logger.info(f'Peer detected: {node}')
            await asyncio.sleep(30)  # Поиск каждые 30 сек
            # except Exception as e:
            #     logger.error(f"Peer discovery error: {e}")
            #     await asyncio.sleep(60)

    async def _health_check_loop(self):
        """Цикл проверки здоровья пиров"""
        while True:
            try:
                dead_peers = []
                for peer_id, peer in self.peers.items():
                    if time.time() - peer.last_contact > 120:  # 2 минуты таймаут
                        if not await self._ping_peer(peer):
                            dead_peers.append(peer_id)

                for peer_id in dead_peers:
                    await self._remove_peer(peer_id)

                await asyncio.sleep(60)  # Проверка каждую минуту
            except Exception as e:
                logger.error(f"Health check error: {e}")
                await asyncio.sleep(60)

    async def _task_processor_loop(self):
        """Цикл обработки задач"""
        while True:
            try:
                # Обработка локальных задач
                for task_id, task in list(self.active_tasks.items()):
                    if task["status"] == "pending":
                        await self._process_task(task)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Task processor error: {e}")
                await asyncio.sleep(5)

    async def _add_peer(self, peer_id, service_info: dict):
        """Добавление пира"""
        # peer_id = service_info["node_id"]
        if peer_id not in self.peers:
            peer = PeerInfo(
                node_id=peer_id,
                host=service_info["host"],
                port=service_info["port"],
                last_contact=time.time()
            )
            self.peers[peer_id] = peer

            if self.on_peer_joined:
                await self.on_peer_joined(peer)

            logger.info(f"Added peer: {peer_id}")

    async def _remove_peer(self, peer_id: str):
        """Удаление пира"""
        if peer_id in self.peers:
            peer = self.peers.pop(peer_id)

            if self.on_peer_left:
                await self.on_peer_left(peer)

            logger.info(f"Removed peer: {peer_id}")

    async def _ping_peer(self, peer: PeerInfo) -> bool:
        """Пинг пира"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"http://{peer.host}:{peer.port}/health")
                if response.status_code == 200:
                    peer.last_contact = time.time()
                    return True
        except Exception as e:
            logger.debug(f"Ping failed for {peer.node_id}: {e}")
        return False

    async def send_message(self, peer_id: str, message: dict) -> Optional[dict]:
        """Отправка сообщения пиру"""
        if peer_id not in self.peers:
            logger.error(f"Unknown peer: {peer_id}")
            return None

        peer = self.peers[peer_id]
        try:
            token = self.auth.generate_token(self.dht.node_id)
            headers = {"Authorization": f"Bearer {token}"}

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"http://{peer.host}:{peer.port}/p2p/message",
                    json=message,
                    headers=headers
                )

                if response.status_code == 200:
                    peer.last_contact = time.time()
                    return response.json()
                else:
                    logger.error(f"Message failed: {response.status_code}")

        except Exception as e:
            logger.error(f"Failed to send message to {peer_id}: {e}")

        return None

    async def broadcast_message(self, message: dict) -> List[str]:
        """Рассылка сообщения всем пирам"""
        successful_sends = []
        tasks = []

        for peer_id in self.peers:
            task = asyncio.create_task(self.send_message(peer_id, message))
            tasks.append((peer_id, task))

        for peer_id, task in tasks:
            try:
                result = await task
                if result:
                    successful_sends.append(peer_id)
            except Exception as e:
                logger.error(f"Broadcast to {peer_id} failed: {e}")

        return successful_sends

    def register_service(self, service_name: str, handler: Callable):
        """Регистрация сервиса"""
        self.services[service_name] = {
            "handler": handler,
            "registered_at": time.time()
        }
        logger.info(f"Registered service: {service_name}")

    def register_task_handler(self, task_type: str, handler: Callable):
        """Регистрация обработчика задач"""
        self.task_handlers[task_type] = handler
        logger.info(f"Registered task handler: {task_type}")

    async def submit_task(self, task_type: str, task_data: dict, target_node: str = None) -> str:
        """Отправка задачи"""
        task_id = f"task_{int(time.time() * 1000)}"
        task = {
            "id": task_id,
            "type": task_type,
            "data": task_data,
            "status": "pending",
            "created_at": time.time(),
            "submitted_by": self.dht.node_id
        }

        if target_node and target_node in self.peers:
            # Отправка на конкретный узел
            response = await self.send_message(target_node, {
                "type": "task_assignment",
                "task": task
            })
            if response and response.get("status") == "accepted":
                return task_id

        # Локальная обработка
        self.active_tasks[task_id] = task
        return task_id

    async def _process_task(self, task: dict):
        """Обработка задачи"""
        task_type = task["type"]
        if task_type not in self.task_handlers:
            task["status"] = "failed"
            task["error"] = f"No handler for task type: {task_type}"
            return

        try:
            task["status"] = "running"
            handler = self.task_handlers[task_type]
            result = await handler(task["data"])

            task["status"] = "completed"
            task["result"] = result
            task["completed_at"] = time.time()

            if self.on_task_completed:
                await self.on_task_completed(task)

        except Exception as e:
            task["status"] = "failed"
            task["error"] = str(e)
            logger.error(f"Task {task['id']} failed: {e}")

    async def get_network_status(self) -> dict:
        """Получение статуса сети"""
        return {
            "node_id": self.dht.node_id,
            "host": self.host,
            "port": self.port,
            "peers_count": len(self.peers),
            "peers": [asdict(peer) for peer in self.peers.values()],
            "services": list(self.services.keys()),
            "active_tasks": len([t for t in self.active_tasks.values() if t["status"] == "running"]),
            "total_tasks": len(self.active_tasks)
        }

    async def add_websocket(self, websocket: WebSocket):
        """Добавление WebSocket соединения"""
        self.websocket_connections.add(websocket)

    async def remove_websocket(self, websocket: WebSocket):
        """Удаление WebSocket соединения"""
        self.websocket_connections.discard(websocket)

    async def broadcast_to_websockets(self, message: dict):
        """Рассылка сообщения через WebSocket"""
        if not self.websocket_connections:
            return

        disconnected = set()
        for ws in self.websocket_connections:
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                disconnected.add(ws)

        # Удаление отключенных соединений
        for ws in disconnected:
            self.websocket_connections.discard(ws)