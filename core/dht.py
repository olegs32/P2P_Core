import asyncio
import hashlib
import json
import random
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from kademlia.network import Server as KademliaServer
import logging

logger = logging.getLogger(__name__)


@dataclass
class DHTNode:
    node_id: str
    host: str
    port: int
    last_seen: float = None

    def __post_init__(self):
        if self.last_seen is None:
            self.last_seen = time.time()


class AsyncDHT:
    """Асинхронная DHT на основе Kademlia"""

    def __init__(self, host: str = "127.0.0.1", port: int = 5678):
        self.host = host
        self.port = port
        self.node_id = self._generate_node_id()
        self.server = KademliaServer()
        self.bootstrap_nodes = []
        self.local_data = {}

    def _generate_node_id(self) -> str:
        """Генерация уникального ID узла"""
        return hashlib.sha256(f"{self.host}:{self.port}:{time.time()}".encode()).hexdigest()

    async def start(self, bootstrap_nodes: List[Tuple[str, int]] = None):
        """Запуск DHT узла"""
        await self.server.listen(self.port)
        logger.info(f"DHT node started on {self.host}:{self.port}")

        if bootstrap_nodes:
            self.bootstrap_nodes = bootstrap_nodes
            await self.server.bootstrap(bootstrap_nodes)
            logger.info(f"Bootstrapped with {len(bootstrap_nodes)} nodes")

    async def stop(self):
        """Остановка DHT узла"""
        self.server.stop()

    async def store(self, key: str, value: dict) -> bool:
        """Сохранение данных в DHT"""
        try:
            serialized_value = json.dumps(value)
            await self.server.set(key, serialized_value)
            self.local_data[key] = value
            logger.debug(f"Stored {key} in DHT")
            return True
        except Exception as e:
            logger.error(f"Failed to store {key}: {e}")
            return False

    async def retrieve(self, key: str) -> Optional[dict]:
        """Получение данных из DHT"""
        try:
            value = await self.server.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"Failed to retrieve {key}: {e}")
            return None

    async def register_service(self, service_name: str, service_info: dict):
        """Регистрация сервиса в DHT"""
        key = f"service:{service_name}:{self.node_id}"
        service_data = {
            "node_id": self.node_id,
            "host": self.host,
            "port": self.port,
            "timestamp": time.time(),
            **service_info
        }
        await self.store(key, service_data)

    async def discover_services(self, service_name: str) -> List[dict]:
        """Поиск сервисов в DHT"""
        services = []
        # В реальной реализации нужен префиксный поиск
        # Пока используем простой подход
        for i in range(10):  # Поиск в нескольких узлах
            key = f"service:{service_name}:{i}"
            service = await self.retrieve(key)
            if service:
                services.append(service)
        return services

    def get_node_info(self) -> DHTNode:
        """Получение информации о текущем узле"""
        return DHTNode(
            node_id=self.node_id,
            host=self.host,
            port=self.port,
            last_seen=time.time()
        )