# local_service_bridge.py - Локальный мост сервисов с поддержкой таргетинга узлов
import asyncio
import logging
from typing import Dict, Any, Optional


class LocalServiceBridge:
    """Локальный мост для вызова сервисов без сетевых запросов"""

    def __init__(self, method_registry: Dict[str, Any], service_manager):
        self.method_registry = method_registry
        self.service_manager = service_manager
        self.logger = logging.getLogger("LocalServiceBridge")

    async def initialize(self):
        """Инициализация локального моста"""
        self.logger.info("Initializing local service bridge...")
        self.local_proxy = SimpleLocalProxy(self.method_registry)
        self.logger.info("Local service bridge initialized")

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

    def __init__(self, method_registry: Dict[str, Any]):
        self.method_registry = method_registry
        self.logger = logging.getLogger("SimpleLocalProxy")

    def __getattr__(self, service_name: str):
        """Получить прокси для сервиса"""
        return ServiceMethodProxy(service_name, self.method_registry)


class ServiceMethodProxy:
    """Прокси для методов сервиса с поддержкой таргетинга узлов"""

    def __init__(self, service_name: str, method_registry: Dict[str, Any], target_node: str = None):
        self.service_name = service_name
        self.method_registry = method_registry
        self.target_node = target_node
        self.logger = logging.getLogger(f"ServiceProxy.{service_name}")

    def __getattr__(self, attr_name: str):
        """Получить callable для метода или прокси для узла"""

        # Список известных узлов/ролей для таргетинга
        known_targets = [
            'coordinator', 'worker', 'worker1', 'worker2', 'worker3',
            'node1', 'node2', 'host1', 'host2', 'pc1', 'pc2',
            'localhost', 'local', 'remote'
        ]

        # Если это похоже на имя узла - создаем таргетированный прокси
        if attr_name in known_targets or attr_name.startswith(('node_', 'host_', 'pc_', 'worker_')):
            self.logger.debug(f"Creating targeted proxy for {self.service_name}.{attr_name}")
            return ServiceMethodProxy(
                service_name=self.service_name,
                method_registry=self.method_registry,
                target_node=attr_name
            )

        # Иначе это имя метода
        return MethodCaller(
            service_name=self.service_name,
            method_name=attr_name,
            method_registry=self.method_registry,
            target_node=self.target_node
        )


class MethodCaller:
    """Вызов метода через реестр с поддержкой таргетинга"""

    def __init__(self, service_name: str, method_name: str, method_registry: Dict[str, Any], target_node: str = None):
        self.service_name = service_name
        self.method_name = method_name
        self.method_registry = method_registry
        self.target_node = target_node
        self.logger = logging.getLogger(f"Method.{service_name}.{method_name}")

    async def __call__(self, **kwargs):
        """Выполнение локального или таргетированного вызова"""
        method_path = f"{self.service_name}/{self.method_name}"

        if method_path not in self.method_registry:
            # Если метод не найден локально и указан целевой узел
            if self.target_node:
                raise RuntimeError(
                    f"Method {method_path} not found in local registry. "
                    f"Remote calls to '{self.target_node}' not implemented in local bridge. "
                    f"Available methods: {list(self.method_registry.keys())}"
                )
            else:
                raise RuntimeError(f"Method {method_path} not found in local registry")

        # Логируем тип вызова
        if self.target_node:
            self.logger.debug(f"Targeted call to {self.target_node}: {method_path}")
        else:
            self.logger.debug(f"Local call: {method_path}")

        method = self.method_registry[method_path]
        return await method(**kwargs)


def create_local_service_bridge(method_registry: Dict[str, Any], service_manager):
    """Создание локального моста сервисов"""
    return LocalServiceBridge(method_registry, service_manager)