# local_service_bridge.py - Мост сервисов с поддержкой локальных и удаленных RPC вызовов
import asyncio
import logging
from typing import Dict, Any, Optional

from layers.application_context import P2PApplicationContext


class LocalServiceBridge:
    """Мост для вызова локальных и удаленных сервисов через P2P архитектуру"""

    def __init__(self, method_registry: Dict[str, Any], service_manager, context: P2PApplicationContext):
        self.method_registry = method_registry
        self.service_manager = service_manager
        self.context = context
        self.logger = logging.getLogger("ServiceBridge")

    async def initialize(self):
        """Инициализация моста сервисов"""
        self.logger.info("Initializing service bridge...")
        self.local_proxy = SimpleLocalProxy(self.method_registry, self.context)
        self.logger.info("Service bridge initialized")

    def get_proxy(self, remote_client=None):
        """Получить прокси для вызова сервисов"""
        return self.local_proxy

    async def call_method_direct(self, service_name: str, method_name: str, **kwargs):
        """Прямой вызов локального метода через реестр"""
        method_path = f"{service_name}/{method_name}"
        if method_path not in self.method_registry:
            raise ValueError(f"Method {method_path} not found in registry")
        method = self.method_registry[method_path]
        return await method(**kwargs)


class SimpleLocalProxy:
    """Прокси для вызова локальных и удаленных методов сервисов"""

    def __init__(self, method_registry: Dict[str, Any], context: P2PApplicationContext):
        self.method_registry = method_registry
        self.context = context
        self.logger = logging.getLogger("SimpleLocalProxy")

    def __getattr__(self, service_name: str):
        """Получить прокси для сервиса"""
        return ServiceMethodProxy(
            service_name=service_name,
            method_registry=self.method_registry,
            context=self.context
        )


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

        # Получаем node_registry для проверки известных узлов
        network = self.context.get_shared('network')
        known_targets = {}

        if network and hasattr(network, 'gossip') and hasattr(network.gossip, 'node_registry'):
            known_targets = network.gossip.node_registry

            # Также проверяем роли узлов (coordinator, worker)
            # Если attr_name это 'coordinator', ищем узел с role='coordinator'
            if attr_name in ['coordinator', 'worker']:
                for node_id, node_info in known_targets.items():
                    if hasattr(node_info, 'role') and node_info.role == attr_name:
                        # Нашли узел с нужной ролью, создаем прокси с его node_id
                        self.logger.debug(f"Resolved role '{attr_name}' to node '{node_id}'")
                        return ServiceMethodProxy(
                            service_name=self.service_name,
                            method_registry=self.method_registry,
                            target_node=node_id,
                            context=self.context
                        )

        # Если это конкретный node_id из registry - создаем таргетированный прокси
        if attr_name in known_targets or attr_name.startswith(('node_', 'host_', 'pc_', 'worker_')):
            self.logger.debug(f"Creating targeted proxy for {self.service_name}.{attr_name}")
            return ServiceMethodProxy(
                service_name=self.service_name,
                method_registry=self.method_registry,
                target_node=attr_name,
                context=self.context
            )

        # Иначе это имя метода - создаем MethodCaller
        return MethodCaller(
            service_name=self.service_name,
            method_name=attr_name,
            method_registry=self.method_registry,
            target_node=self.target_node,
            context=self.context
        )


class MethodCaller:
    """Вызов локального или удаленного метода через P2P архитектуру"""

    def __init__(self, service_name: str, method_name: str, method_registry: Dict[str, Any],
                 target_node: str = None, context: P2PApplicationContext = None):
        self.service_name = service_name
        self.method_name = method_name
        self.method_registry = method_registry
        self.target_node = target_node
        self.context = context
        self.logger = logging.getLogger(f"Method.{service_name}.{method_name}")

    async def __call__(self, **kwargs):
        """Выполнение локального или удаленного RPC вызова"""
        method_path = f"{self.service_name}/{self.method_name}"

        # Если указан целевой узел - делаем удаленный RPC вызов
        if self.target_node:
            return await self._remote_call(method_path, **kwargs)

        # Иначе делаем локальный вызов
        return await self._local_call(method_path, **kwargs)

    async def _local_call(self, method_path: str, **kwargs):
        """Локальный вызов метода через registry"""
        if method_path not in self.method_registry:
            raise RuntimeError(
                f"Method {method_path} not found in local registry. "
                f"Available: {list(self.method_registry.keys())[:5]}"
            )

        self.logger.debug(f"Local call: {method_path}")
        method = self.method_registry[method_path]
        return await method(**kwargs)

    async def _remote_call(self, method_path: str, **kwargs):
        """Удаленный RPC вызов через P2P архитектуру"""
        self.logger.debug(f"Remote call to {self.target_node}: {method_path}")

        if not self.context:
            raise RuntimeError("Context not available for remote calls")

        # Получаем network layer
        network = self.context.get_shared('network')
        if not network:
            raise RuntimeError("Network layer not available")

        # Получаем информацию о целевом узле из node_registry
        node_registry = network.gossip.node_registry
        if self.target_node not in node_registry:
            raise RuntimeError(
                f"Target node '{self.target_node}' not found in node registry. "
                f"Available nodes: {list(node_registry.keys())}"
            )

        node_info = node_registry[self.target_node]

        # Получаем URL целевого узла
        # Проверяем если HTTPS включен
        https_enabled = True
        if hasattr(self.context.config, 'https_enabled'):
            https_enabled = self.context.config.https_enabled

        node_url = node_info.get_url(https=https_enabled)

        # Подготавливаем RPC запрос
        import uuid
        rpc_request = {
            "jsonrpc": "2.0",
            "method": method_path,
            "params": kwargs,
            "id": str(uuid.uuid4())
        }

        # Выполняем HTTP запрос через connection_manager
        try:
            client = await network.connection_manager.get_client(node_url)

            response = await client.post(
                "/rpc",
                json=rpc_request,
                headers={"Content-Type": "application/json"},
                timeout=30.0
            )

            if response.status_code != 200:
                raise RuntimeError(
                    f"HTTP error {response.status_code} from {self.target_node}: {response.text}"
                )

            result = response.json()

            # Debug: log response
            self.logger.debug(f"RPC response from {self.target_node}: {result}")

            # Обработка RPC ответа
            # Проверяем на реальную ошибку (не None)
            if "error" in result and result["error"] is not None:
                raise RuntimeError(f"RPC error from {self.target_node}: {result['error']}")

            # Если есть result - возвращаем его (даже если None)
            if "result" in result:
                self.logger.debug(f"Remote call successful: {method_path} -> {self.target_node}")
                return result["result"]

            # Если нет ни error ни result - это невалидный ответ
            raise RuntimeError(f"Invalid RPC response from {self.target_node}: {result}")

        except Exception as e:
            self.logger.error(f"Remote call failed to {self.target_node}: {e}")
            raise RuntimeError(f"Remote RPC call to {self.target_node} failed: {e}") from e


def create_local_service_bridge(method_registry: Dict[str, Any], service_manager):
    """Создание локального моста сервисов"""
    return LocalServiceBridge(method_registry, service_manager)