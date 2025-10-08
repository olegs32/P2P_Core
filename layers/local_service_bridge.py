# local_service_bridge.py - Локальный мост сервисов (БЕЗ глобальных переменных)

import asyncio
import logging
from typing import Dict, Any, Callable


class SimpleLocalProxy:
    """Простой локальный прокси для вызова методов"""

    def __init__(self, method_registry: Dict[str, Callable]):
        self.method_registry = method_registry
        self.logger = logging.getLogger("LocalProxy")

    async def call(self, method_path: str, **kwargs):
        """Вызов метода через локальный реестр"""
        if method_path not in self.method_registry:
            available_methods = list(self.method_registry.keys())
            raise ValueError(
                f"Method {method_path} not found. Available: {available_methods[:10]}"
            )

        method = self.method_registry[method_path]

        if asyncio.iscoroutinefunction(method):
            return await method(**kwargs)
        else:
            return method(**kwargs)


class LocalServiceBridge:
    """Локальный мост для вызова сервисов без сетевых запросов"""

    def __init__(self, method_registry: Dict[str, Callable], service_manager):
        self.local_proxy = None
        self.method_registry = method_registry
        self.service_manager = service_manager
        self.registry = self.service_manager.registry
        self.logger = logging.getLogger("ServiceBridge")

    async def initialize(self):
        """Инициализация локального моста с диагностикой"""
        self.logger.info("Initializing service bridge...")

        available_methods = list(self.method_registry.keys())
        self.logger.info(f"Service bridge initialized with {len(available_methods)} methods")

        if not available_methods:
            self.logger.warning("⚠️  Method registry is EMPTY! This will cause 'method not found' errors.")

        self.local_proxy = SimpleLocalProxy(self.method_registry)
        self.logger.info("Service bridge initialized")

    def get_proxy(self, remote_client=None):
        """Получить локальный прокси"""
        return self.local_proxy

    async def call_method_direct(self, service_name: str, method_name: str, **kwargs):
        """Прямой вызов метода через реестр"""
        method_path = f"{service_name}/{method_name}"

        if method_path not in self.method_registry:
            available_methods = list(self.method_registry.keys())
            raise ValueError(
                f"Method {method_path} not found. Available: {available_methods[:10]}"
            )

        method = self.method_registry[method_path]

        if asyncio.iscoroutinefunction(method):
            return await method(**kwargs)
        else:
            return method(**kwargs)


def create_local_service_bridge(method_registry: Dict[str, Callable], service_manager):
    """
    Factory для создания локального моста сервисов

    Args:
        method_registry: Словарь зарегистрированных методов из ServiceManager
        service_manager: Экземпляр ServiceManager

    Returns:
        LocalServiceBridge: Инициализированный мост
    """
    return LocalServiceBridge(method_registry, service_manager)