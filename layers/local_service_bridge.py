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

        # Проверяем является ли это именем узла/роли для таргетинга
        # 1. Специальные роли (всегда считаются узлами)
        special_roles = ['coordinator', 'worker', 'local', 'remote']

        # 2. Паттерны имен узлов
        node_patterns = ('node_', 'host_', 'pc_', 'worker_', 'worker-', 'coord-', 'node-')

        # 3. Динамический список из node_registry (если доступен)
        known_nodes = set()
        try:
            network = self.context.get_shared('network')
            if network and hasattr(network, 'gossip') and hasattr(network.gossip, 'node_registry'):
                known_nodes = set(network.gossip.node_registry.keys())
        except:
            pass

        # Проверяем: это узел или метод?
        is_target_node = (
            attr_name in special_roles or
            attr_name in known_nodes or
            attr_name.startswith(node_patterns)
        )

        if is_target_node:
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

        # Если указан target_node - проверяем, это локальный узел или удаленный
        if self.target_node:
            # Получаем ID текущего узла
            current_node_id = None
            if self.context and hasattr(self.context, 'config'):
                current_node_id = self.context.config.node_id

            # Если target_node НЕ совпадает с текущим узлом - это удаленный вызов
            if current_node_id and self.target_node != current_node_id:
                self.logger.debug(f"Remote call to {self.target_node}: {method_path}")
                return await self._call_remote(**kwargs)
            else:
                # Target node совпадает с текущим - локальный вызов
                self.logger.debug(f"Local targeted call: {method_path}")

        # Если метод не найден в локальном registry
        if method_path not in self.method_registry:
            # Если был указан target_node, но метода все равно нет - пробуем удаленный вызов
            if self.target_node:
                return await self._call_remote(**kwargs)
            else:
                raise RuntimeError(f"Method {method_path} not found in local registry")

        # Локальный вызов
        self.logger.debug(f"Local call: {method_path}")
        method = self.method_registry[method_path]
        return await method(**kwargs)

    async def _call_remote(self, **kwargs):
        """Выполнить удаленный RPC вызов через HTTP"""
        if not self.context:
            raise RuntimeError("Context not available for remote calls")

        # Определяем адрес целевого узла
        node_address = None

        # Специальная обработка для 'coordinator' - берем адрес из конфигурации
        if self.target_node == 'coordinator':
            if hasattr(self.context, 'config') and hasattr(self.context.config, 'coordinator_addresses'):
                coordinator_addresses = self.context.config.coordinator_addresses
                if coordinator_addresses and len(coordinator_addresses) > 0:
                    # Берем первый координатор из списка
                    node_address = coordinator_addresses[0]
                    self.logger.debug(f"Using coordinator address from config: {node_address}")

        # Если адрес не найден в конфиге, ищем в node_registry
        if not node_address:
            network = self.context.get_shared('network')
            if not network:
                raise RuntimeError("Network component not available")

            # Получаем информацию об узле
            node_info = network.gossip.node_registry.get(self.target_node)
            if not node_info:
                raise RuntimeError(f"Target node '{self.target_node}' not found in network registry or config")

            node_address = node_info.get('address')
            if not node_address:
                raise RuntimeError(f"Address not available for node '{self.target_node}'")

        # Используем стандартный /rpc endpoint
        url = f"https://{node_address}/rpc"
        method_path = f"{self.service_name}/{self.method_name}"

        self.logger.debug(f"Remote RPC call to {self.target_node}: {method_path}")

        # Создаем SSL context для безопасного соединения
        verify_ssl = False
        if hasattr(self.context, 'config'):
            # Проверяем есть ли CA сертификат
            ca_cert_file = getattr(self.context.config, 'ssl_ca_cert_file', None)
            if ca_cert_file:
                try:
                    from layers.ssl_helper import create_client_ssl_context
                    verify_ssl = create_client_ssl_context(verify=True, ca_cert_file=ca_cert_file, context=self.context)
                    self.logger.debug(f"Using SSL verification with CA cert: {ca_cert_file}")
                except Exception as e:
                    self.logger.warning(f"Failed to create SSL context, using no verification: {e}")
                    verify_ssl = False

        # Формируем RPC request
        rpc_payload = {
            "method": method_path,
            "params": kwargs,
            "id": 1
        }

        # Делаем HTTP POST запрос
        async with httpx.AsyncClient(verify=verify_ssl, timeout=30.0) as client:
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