# local_service_bridge.py - Локальный мост сервисов с поддержкой таргетинга узлов
import asyncio
import logging
import httpx
from typing import Dict, Any, Optional

from layers.application_context import P2PApplicationContext


class LocalServiceBridge:
    """Локальный мост для вызова сервисов без сетевых запросов"""

    def __init__(self, method_registry: Dict[str, Any], service_manager, context: P2PApplicationContext):
        self.method_registry = method_registry
        self.service_manager = service_manager
        self.context = context
        self.logger = logging.getLogger("ServiceBridge")

    async def initialize(self):
        """Инициализация локального моста"""
        self.logger.info("Initializing  service bridge...")
        self.local_proxy = SimpleLocalProxy(self.method_registry, self.context)
        self.logger.info("Service bridge initialized")

    def get_proxy(self, remote_client=None):
        """Получить локальный прокси"""
        return self.local_proxy

    async def call_method_direct(self, service_name: str, method_name: str, **kwargs):
        """Прямой вызов метода через реестр"""
        method_path = f"{service_name}/{method_name}"
        if method_path not in self.method_registry:
            raise ValueError(f"Method {method_path} not found in registry")
        method = self.method_registry[method_path]
        return await method(**kwargs)


class SimpleLocalProxy:
    """Простой локальный прокси для вызова методов"""

    def __init__(self, method_registry: Dict[str, Any], context: P2PApplicationContext):
        self.method_registry = method_registry
        self.context = context
        self.logger = logging.getLogger("SimpleLocalProxy")

    def __getattr__(self, service_name: str):
        """Получить прокси для сервиса"""
        return ServiceMethodProxy(service_name, self.method_registry, context=self.context)


class ServiceMethodProxy:
    """Прокси для методов сервиса с поддержкой таргетинга узлов"""

    def __init__(self, service_name: str, method_registry: Dict[str, Any], context: P2PApplicationContext,
                 target_node: str = None):
        self.context = context
        self.service_name = service_name
        self.method_registry = method_registry
        self.target_node = target_node
        self.logger = logging.getLogger(f"Proxy.{service_name}")

    def __getattr__(self, attr_name: str):
        """Получить callable для метода или прокси для узла"""

        # Список известных узлов/ролей для таргетинга
        # known_targets = [
        #     'coordinator', 'worker', 'worker1', 'worker2', 'worker3',
        #     'node1', 'node2', 'host1', 'host2', 'pc1', 'pc2',
        #     'localhost', 'local', 'remote'
        #
        # ]
        known_targets = self.context.get_shared('network').gossip.node_registry

        # Если это похоже на имя узла - создаем таргетированный прокси
        if attr_name in known_targets or attr_name.startswith(('node_', 'host_', 'pc_', 'worker_')):
            self.logger.debug(f"Creating targeted proxy for {self.service_name}.{attr_name}")
            return ServiceMethodProxy(
                service_name=self.service_name,
                method_registry=self.method_registry,
                target_node=attr_name,
                context=self.context
            )

        # Иначе это имя метода
        return MethodCaller(
            service_name=self.service_name,
            method_name=attr_name,
            method_registry=self.method_registry,
            target_node=self.target_node,
            context=self.context
        )


class MethodCaller:
    """Вызов метода через реестр с поддержкой таргетинга"""

    def __init__(self, service_name: str, method_name: str, method_registry: Dict[str, Any], target_node: str = None, context: P2PApplicationContext = None):
        self.service_name = service_name
        self.method_name = method_name
        self.method_registry = method_registry
        self.target_node = target_node
        self.context = context
        self.logger = logging.getLogger(f"Method.{service_name}.{method_name}")

    async def __call__(self, **kwargs):
        """Выполнение локального или таргетированного вызова"""
        method_path = f"{self.service_name}/{self.method_name}"

        # Если метод не найден локально и указан целевой узел - делаем удаленный вызов
        if method_path not in self.method_registry and self.target_node:
            return await self._call_remote(**kwargs)

        # Если метод не найден вообще
        if method_path not in self.method_registry:
            raise RuntimeError(f"Method {method_path} not found in local registry")

        # Логируем тип вызова
        if self.target_node:
            self.logger.debug(f"Targeted call to {self.target_node}: {method_path}")
        else:
            self.logger.debug(f"Local call: {method_path}")

        method = self.method_registry[method_path]
        return await method(**kwargs)

    async def _call_remote(self, **kwargs):
        """Выполнить удаленный RPC вызов через HTTP"""
        if not self.context:
            raise RuntimeError("Context not available for remote calls")

        # Получаем network layer для доступа к node registry
        network = self.context.get_shared('network')
        if not network:
            raise RuntimeError("Network component not available")

        # Получаем информацию об узле
        node_info = network.gossip.node_registry.get(self.target_node)
        if not node_info:
            raise RuntimeError(f"Target node '{self.target_node}' not found in network registry")

        # Формируем URL для RPC вызова
        node_address = node_info.get('address')
        if not node_address:
            raise RuntimeError(f"Address not available for node '{self.target_node}'")

        # Используем стандартный /rpc endpoint
        url = f"https://{node_address}/rpc"
        method_path = f"{self.service_name}/{self.method_name}"

        self.logger.debug(f"Remote RPC call to {self.target_node}: {method_path}")

        # Получаем SSL context из transport
        transport = self.context.get_shared('transport')
        ssl_context = transport.client_ssl_context if transport else None

        # Формируем RPC request
        rpc_payload = {
            "method": method_path,
            "params": kwargs,
            "id": 1
        }

        # Делаем HTTP POST запрос
        async with httpx.AsyncClient(verify=ssl_context, timeout=30.0) as client:
            try:
                response = await client.post(url, json=rpc_payload)
                response.raise_for_status()
                rpc_response = response.json()

                # Проверяем формат RPC ответа
                if 'error' in rpc_response and rpc_response['error']:
                    raise RuntimeError(f"Remote RPC call failed: {rpc_response['error']}")

                return rpc_response.get('result')

            except httpx.HTTPStatusError as e:
                self.logger.error(f"HTTP error calling {url}: {e.response.status_code} - {e.response.text}")
                raise RuntimeError(f"Remote call failed with HTTP {e.response.status_code}")
            except httpx.RequestError as e:
                self.logger.error(f"Request error calling {url}: {e}")
                raise RuntimeError(f"Remote call failed: {e}")
            except Exception as e:
                self.logger.error(f"Unexpected error calling {url}: {e}")
                raise


def create_local_service_bridge(method_registry: Dict[str, Any], service_manager, context: P2PApplicationContext):
    """Создание локального моста сервисов"""
    return LocalServiceBridge(method_registry, service_manager, context)