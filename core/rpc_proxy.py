import asyncio
import httpx
import json
from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class RPCProxy:
    """Прокси для прозрачных RPC вызовов"""

    def __init__(self, target_host: str, target_port: int, auth_token: str = None):
        self.target_host = target_host
        self.target_port = target_port
        self.auth_token = auth_token
        self.base_url = f"http://{target_host}:{target_port}"

    def __getattr__(self, service_name: str):
        return ServiceProxy(self, service_name)


class ServiceProxy:
    """Прокси для сервиса"""

    def __init__(self, rpc_proxy: RPCProxy, service_name: str):
        self.rpc_proxy = rpc_proxy
        self.service_name = service_name

    def __getattr__(self, domain_name: str):
        return DomainProxy(self.rpc_proxy, self.service_name, domain_name)


class DomainProxy:
    """Прокси для домена"""

    def __init__(self, rpc_proxy: RPCProxy, service_name: str, domain_name: str):
        self.rpc_proxy = rpc_proxy
        self.service_name = service_name
        self.domain_name = domain_name

    def __getattr__(self, method_name: str):
        return MethodProxy(self.rpc_proxy, self.service_name, self.domain_name, method_name)


class MethodProxy:
    """Прокси для метода"""

    def __init__(self, rpc_proxy: RPCProxy, service_name: str, domain_name: str, method_name: str):
        self.rpc_proxy = rpc_proxy
        self.service_name = service_name
        self.domain_name = domain_name
        self.method_name = method_name

    async def __call__(self, *args, **kwargs) -> Any:
        """Выполнение удаленного вызова"""

        # Формирование RPC запроса
        rpc_request = {
            "service": self.service_name,
            "domain": self.domain_name,
            "method": self.method_name,
            "args": args,
            "kwargs": kwargs
        }

        try:
            headers = {}
            if self.rpc_proxy.auth_token:
                headers["Authorization"] = f"Bearer {self.rpc_proxy.auth_token}"

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.rpc_proxy.base_url}/rpc",
                    json=rpc_request,
                    headers=headers
                )

                if response.status_code == 200:
                    result = response.json()
                    if result.get("status") == "success":
                        return result.get("data")
                    else:
                        raise Exception(f"RPC error: {result.get('error')}")
                else:
                    raise Exception(f"HTTP error: {response.status_code}")

        except Exception as e:
            logger.error(f"RPC call failed: {self.service_name}.{self.domain_name}.{self.method_name}: {e}")
            raise


# Менеджер RPC соединений
class RPCManager:
    """Менеджер RPC соединений"""

    def __init__(self):
        self.connections: Dict[str, RPCProxy] = {}

    def connect(self, node_id: str, host: str, port: int, auth_token: str = None) -> RPCProxy:
        """Создание RPC соединения"""
        if node_id not in self.connections:
            self.connections[node_id] = RPCProxy(host, port, auth_token)
        return self.connections[node_id]

    def disconnect(self, node_id: str):
        """Закрытие RPC соединения"""
        if node_id in self.connections:
            del self.connections[node_id]

    def get_connection(self, node_id: str) -> Optional[RPCProxy]:
        """Получение RPC соединения"""
        return self.connections.get(node_id)


# Глобальный менеджер
rpc_manager = RPCManager()


# Удобная функция для создания прокси
async def create_service_proxy(node_id: str, host: str, port: int, auth_token: str = None) -> RPCProxy:
    """Создание прокси для сервиса"""
    return rpc_manager.connect(node_id, host, port, auth_token)

# Пример использования:
# proxy = await create_service_proxy("node1", "192.168.1.100", 8000, "auth_token")
# result = await proxy.system.process.list_processes()