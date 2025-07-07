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
    """Асинхронная DHT на основе Kademlia с поддержкой префиксного поиска"""

    IDX_NAMES = "index:service_names"
    IDX_KEYS_TPL = "index:service:{service}:keys"

    def __init__(self, host: str = "127.0.0.1", port: int = 5678):
        self.host = host
        self.port = port
        self.node_id = self._generate_node_id()
        self.server = KademliaServer()
        self.bootstrap_nodes = []
        self.local_data = {}

    def _generate_node_id(self) -> str:
        return hashlib.sha256(f"{self.host}:{self.port}:{time.time()}".encode()).hexdigest()

    async def start(self, bootstrap_nodes: List[Tuple[str, int]] = None):
        await self.server.listen(self.port)
        logger.info(f"DHT node started on {self.host}:{self.port}")

        if bootstrap_nodes:
            self.bootstrap_nodes = bootstrap_nodes
            await self.server.bootstrap(bootstrap_nodes)
            logger.info(f"Bootstrapped with {len(bootstrap_nodes)} nodes")

    async def stop(self):
        self.server.stop()

    async def store(self, key: str, value) -> bool:
        try:
            serial = json.dumps(value)
            await self.server.set(key, serial)
            self.local_data[key] = value
            logger.debug(f"Stored {key}")
            return True
        except Exception as e:
            logger.error(f"Failed to store {key}: {e}")
            return False

    async def retrieve(self, key: str):
        try:
            val = await self.server.get(key)
            return json.loads(val) if val else None
        except Exception as e:
            logger.error(f"Failed to retrieve {key}: {e}")
            return None

    async def _update_index(self, idx_key: str, element) -> None:
        """Подтягиваем список из DHT, добавляем элемент и заново сохраняем."""
        lst = await self.retrieve(idx_key) or []
        if element not in lst:
            lst.append(element)
            await self.store(idx_key, lst)

    async def register_service(self, service_name: str, service_info: dict):
        """Регистрируем сервис и обновляем оба индекса."""
        key = f"service:{service_name}:{self.node_id}"
        data = {
            "node_id": self.node_id,
            "host": self.host,
            "port": self.port,
            "timestamp": time.time(),
            **service_info
        }
        # 1) Сохраняем сам сервис
        await self.store(key, data)

        # 2) Обновляем список ключей для этого сервиса
        idx_keys = self.IDX_KEYS_TPL.format(service=service_name)
        await self._update_index(idx_keys, key)

        # 3) Обновляем список всех имён сервисов
        await self._update_index(self.IDX_NAMES, service_name)

    async def discover_services(self, service_name: str) -> List[dict]:
        """Поиск всех инстансов сервисов по точному имени."""
        result = []
        idx_keys = self.IDX_KEYS_TPL.format(service=service_name)
        keys = await self.retrieve(idx_keys) or []
        for k in keys:
            svc = await self.retrieve(k)
            if svc:
                result.append(svc)
        return result

    async def discover_services_mask(self, mask: str) -> List[dict]:
        """Префиксный поиск по именам сервисов."""
        found = []
        # 1) Скачиваем все зарегистрированные имена
        all_names = await self.retrieve(self.IDX_NAMES) or []

        # 2) Фильтруем по префиксу
        matched = [name for name in all_names if name.startswith(mask)]

        # 3) Для каждого подходящего имени — достаём все его ключи и данные
        for name in matched:
            idx_keys = self.IDX_KEYS_TPL.format(service=name)
            keys = await self.retrieve(idx_keys) or []
            for k in keys:
                svc = await self.retrieve(k)
                if svc:
                    found.append(svc)
        return found


def get_node_info(self) -> DHTNode:
    return DHTNode(
        node_id=self.node_id,
        host=self.host,
        port=self.port,
        last_seen=time.time()
    )
