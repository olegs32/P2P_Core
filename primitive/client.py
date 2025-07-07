# client.py - RPC клиент
import httpx
import asyncio
from typing import Any, Dict, List


class RPCClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self.client = httpx.AsyncClient()

    async def call(self, service: str, method: str, *args, **kwargs) -> Any:
        """Базовый метод для RPC вызовов"""
        payload = {
            "service": service,
            "method": method,
            "args": list(args),
            "kwargs": kwargs
        }

        try:
            response = await self.client.post(f"{self.base_url}/rpc", json=payload)
            response.raise_for_status()

            data = response.json()
            if data.get("error"):
                raise Exception(f"RPC Error: {data['error']}")

            return data.get("result")

        except httpx.HTTPError as e:
            raise Exception(f"Network error: {e}")

    async def close(self):
        await self.client.aclose()


class ServiceProxy:
    """Прокси для сервиса"""

    def __init__(self, client: RPCClient, service_name: str):
        self.client = client
        self.service_name = service_name

    def __getattr__(self, method_name: str):
        async def method_call(*args, **kwargs):
            return await self.client.call(self.service_name, method_name, *args, **kwargs)

        return method_call


class NodeProxy:
    """Прокси для узла (домена)"""

    def __init__(self, base_url: str):
        self.client = RPCClient(base_url)

    def __getattr__(self, service_name: str):
        return ServiceProxy(self.client, service_name)

    async def close(self):
        await self.client.close()


class RPCDomain:
    """Основной класс для работы с RPC"""

    def __init__(self):
        self.nodes = {}

    def add_node(self, node_name: str, base_url: str):
        """Добавление узла"""
        self.nodes[node_name] = NodeProxy(base_url)

    def __getattr__(self, node_name: str):
        if node_name not in self.nodes:
            raise AttributeError(f"Node '{node_name}' not found")
        return self.nodes[node_name]

    async def close_all(self):
        """Закрытие всех соединений"""
        for node in self.nodes.values():
            await node.close()


# Пример использования
async def main():
    # Создание домена
    rpc = RPCDomain()

    # Добавление узлов
    rpc.add_node("node1", "http://localhost:8000")
    rpc.add_node("node2", "http://localhost:8001")

    try:
        # Вызов: service.node.domain(args)
        # В нашем случае: rpc.node1.example.calculate(5, 3, operation="add")
        result = await rpc.node1.example.calculate(5, 3, operation="add")
        print(f"Результат: {result}")

        # Другой пример
        info = await rpc.node1.example.get_info()
        print(f"Информация о сервисе: {info}")

    except Exception as e:
        print(f"Ошибка: {e}")

    finally:
        await rpc.close_all()


# Синхронная обёртка для удобства
class SyncRPCClient:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def call(self, service: str, method: str, *args, **kwargs):
        async def _call():
            client = RPCClient(self.base_url)
            try:
                return await client.call(service, method, *args, **kwargs)
            finally:
                await client.close()

        return asyncio.run(_call())


class SyncServiceProxy:
    def __init__(self, client: SyncRPCClient, service_name: str):
        self.client = client
        self.service_name = service_name

    def __getattr__(self, method_name: str):
        def method_call(*args, **kwargs):
            return self.client.call(self.service_name, method_name, *args, **kwargs)

        return method_call


class SyncNodeProxy:
    def __init__(self, base_url: str):
        self.client = SyncRPCClient(base_url)

    def __getattr__(self, service_name: str):
        return SyncServiceProxy(self.client, service_name)


class SyncRPCDomain:
    """Синхронная версия RPC домена"""

    def __init__(self):
        self.nodes = {}

    def add_node(self, node_name: str, base_url: str):
        self.nodes[node_name] = SyncNodeProxy(base_url)

    def __getattr__(self, node_name: str):
        if node_name not in self.nodes:
            raise AttributeError(f"Node '{node_name}' not found")
        return self.nodes[node_name]


# Пример синхронного использования
def sync_example():
    rpc = SyncRPCDomain()
    rpc.add_node("node1", "http://localhost:8000")

    # Простой синхронный вызов
    result = rpc.node1.example.calculate(10, 5, operation="multiply")
    print(f"Синхронный результат: {result}")


if __name__ == "__main__":
    # Запуск асинхронного примера
    asyncio.run(main())

    # Синхронный пример
    # sync_example()