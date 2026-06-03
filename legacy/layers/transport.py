import asyncio
import httpx
from typing import Dict, Optional, Any
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import uuid


@dataclass
class TransportConfig:
    """Конфигурация транспортного уровня"""
    max_keepalive_connections: int = 50
    max_connections: int = 200
    keepalive_expiry: float = 30.0
    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    verify_ssl: bool = False
    http2_enabled: bool = True


class P2PTransportLayer:
    """Оптимизированный транспортный уровень для P2P коммуникации"""

    def __init__(self, config: TransportConfig):
        self.config = config
        self.clients: Dict[str, httpx.AsyncClient] = {}
        self.connection_semaphore = asyncio.Semaphore(100)

    def create_optimized_client(self, node_url: str) -> httpx.AsyncClient:
        """Создание оптимизированного HTTP клиента для P2P узла"""

        limits = httpx.Limits(
            max_keepalive_connections=self.config.max_keepalive_connections,
            max_connections=self.config.max_connections,
            keepalive_expiry=self.config.keepalive_expiry
        )

        timeout = httpx.Timeout(
            connect=self.config.connect_timeout,
            read=self.config.read_timeout,
            write=10.0,
            pool=5.0
        )

        transport = httpx.AsyncHTTPTransport(
            limits=limits,
            verify=self.config.verify_ssl,
            http2=self.config.http2_enabled,
            retries=3
        )

        return httpx.AsyncClient(
            base_url=node_url,
            timeout=timeout,
            transport=transport
        )

    def get_client(self, node_url: str) -> httpx.AsyncClient:
        """Получение или создание клиента для узла"""
        if node_url not in self.clients:
            self.clients[node_url] = self.create_optimized_client(node_url)
        return self.clients[node_url]

    @asynccontextmanager
    async def get_connection(self, node_url: str):
        """Контекст-менеджер для управления соединениями"""
        async with self.connection_semaphore:
            client = self.get_client(node_url)
            try:
                yield client
            finally:
                pass  # Соединение возвращается в пул автоматически

    async def send_request(self, node_url: str, endpoint: str,
                           data: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        """Отправка HTTP запроса с оптимизациями P2P"""
        async with self.get_connection(node_url) as client:
            response = await client.post(endpoint, json=data, headers=headers)
            response.raise_for_status()
            return response.json()

    async def close_all(self):
        """Закрытие всех соединений"""
        for client in self.clients.values():
            await client.aclose()
        self.clients.clear()