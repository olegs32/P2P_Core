# local_service_layer.py - Локальный слой сервисов без P2PClient

import asyncio
import inspect
import logging
import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime
import uuid
from layers.service_framework import BaseService, ServiceRegistry, ServiceManager


class LocalServiceProxy:
    """Локальный прокси для вызова сервисов без сетевых запросов"""

    def __init__(self, service_registry: ServiceRegistry, remote_client=None):
        self.local_registry = service_registry
        self.remote_client = remote_client  # Только для удаленных вызовов
        self.logger = logging.getLogger("LocalServiceProxy")

    def __getattr__(self, service_name: str):
        """Получить прокси для конкретного сервиса"""
        return LocalServiceMethodProxy(
            service_name=service_name,
            local_registry=self.local_registry,
            remote_client=self.remote_client
        )


class LocalServiceMethodProxy:
    """Прокси для вызова методов сервиса с приоритетом локальных вызовов"""

    def __init__(self, service_name: str, local_registry: ServiceRegistry, remote_client=None):
        self.service_name = service_name
        self.local_registry = local_registry
        self.remote_client = remote_client
        self.logger = logging.getLogger(f"ServiceMethod.{service_name}")

    def __getattr__(self, method_name: str):
        """Получить callable для метода"""
        return LocalMethodCaller(
            service_name=self.service_name,
            method_name=method_name,
            local_registry=self.local_registry,
            remote_client=self.remote_client
        )


class LocalMethodCaller:
    """Вызов метода с умной маршрутизацией (локальный vs удаленный)"""

    def __init__(self, service_name: str, method_name: str,
                 local_registry: ServiceRegistry, remote_client=None):
        self.service_name = service_name
        self.method_name = method_name
        self.local_registry = local_registry
        self.remote_client = remote_client
        self.logger = logging.getLogger(f"MethodCaller.{service_name}.{method_name}")

    async def __call__(self, **kwargs):
        """Выполнение вызова метода"""

        # Приоритет 1: Попытка локального вызова
        local_result = await self._try_local_call(**kwargs)
        if local_result is not None:
            return local_result

        # Приоритет 2: Удаленный вызов (если есть remote_client)
        if self.remote_client:
            return await self._try_remote_call(**kwargs)

        # Если ничего не сработало
        raise RuntimeError(f"Service method {self.service_name}.{self.method_name} not available locally or remotely")

    async def _try_local_call(self, **kwargs):
        """Попытка локального вызова"""
        try:
            # Получаем локальный сервис
            service = self.local_registry.get_service(self.service_name)
            if not service:
                self.logger.debug(f"Service {self.service_name} not found locally")
                return None

            # Проверяем наличие метода
            if not hasattr(service, self.method_name):
                self.logger.debug(f"Method {self.method_name} not found in local service {self.service_name}")
                return None

            method = getattr(service, self.method_name)

            # Проверяем, что это публичный метод сервиса
            if not (hasattr(method, '_service_method') and method._service_public):
                self.logger.debug(f"Method {self.method_name} is not a public service method")
                return None

            # Выполняем локальный вызов
            self.logger.debug(f"Local call: {self.service_name}.{self.method_name}")
            result = await method(**kwargs)
            return result

        except Exception as e:
            self.logger.error(f"Local call failed for {self.service_name}.{self.method_name}: {e}")
            return None

    async def _try_remote_call(self, **kwargs):
        """Попытка удаленного вызова через P2P"""
        try:
            method_path = f"{self.service_name}/{self.method_name}"
            self.logger.debug(f"Remote call: {method_path}")

            # Используем существующий P2P клиент для удаленного вызова
            result = await self.remote_client.rpc_call(
                method_path=method_path,
                params=kwargs
            )
            return result

        except Exception as e:
            self.logger.error(f"Remote call failed for {self.service_name}.{self.method_name}: {e}")
            raise


class DirectRegistryInvoker:
    """Прямой вызов методов через method_registry без сетевых запросов"""

    def __init__(self, method_registry: Dict[str, Any]):
        self.method_registry = method_registry
        self.logger = logging.getLogger("DirectRegistryInvoker")

    async def invoke_method(self, method_path: str, params: Dict[str, Any]) -> Any:
        """Прямой вызов метода из реестра"""

        if method_path not in self.method_registry:
            raise ValueError(f"Method {method_path} not found in registry")

        try:
            method = self.method_registry[method_path]

            # Логируем вызов
            self.logger.debug(f"Direct registry call: {method_path}")

            # Выполняем метод
            if asyncio.iscoroutinefunction(method):
                result = await method(**params)
            else:
                result = method(**params)

            return result

        except Exception as e:
            self.logger.error(f"Direct registry call failed for {method_path}: {e}")
            raise

    def list_available_methods(self) -> List[str]:
        """Список доступных методов в реестре"""
        return list(self.method_registry.keys())


class EnhancedLocalServiceLayer:
    """Улучшенный локальный слой сервисов"""

    def __init__(self, method_registry: Dict[str, Any]):
        self.method_registry = method_registry
        self.service_registry = None  # Будет установлен позже
        self.direct_invoker = DirectRegistryInvoker(method_registry)
        self.logger = logging.getLogger("EnhancedLocalServiceLayer")

    def set_service_registry(self, service_registry: ServiceRegistry):
        """Установка реестра сервисов"""
        self.service_registry = service_registry

    def create_local_proxy(self, remote_client=None) -> LocalServiceProxy:
        """Создание локального прокси"""
        if not self.service_registry:
            raise RuntimeError("Service registry not set")

        return LocalServiceProxy(self.service_registry, remote_client)

    async def call_service_method_direct(self, service_name: str, method_name: str, **kwargs) -> Any:
        """Прямой вызов метода сервиса через реестр"""
        method_path = f"{service_name}/{method_name}"
        return await self.direct_invoker.invoke_method(method_path, kwargs)

    def get_service_info(self, service_name: str) -> Optional[Dict[str, Any]]:
        """Получить информацию о сервисе"""
        if not self.service_registry:
            return None

        service = self.service_registry.get_service(service_name)
        if service:
            return {
                "name": service.service_name,
                "status": service.status.value,
                "methods": service.info.exposed_methods,
                "description": service.info.description,
                "version": service.info.version
            }
        return None

    def list_all_services(self) -> Dict[str, Dict[str, Any]]:
        """Список всех локальных сервисов"""
        if not self.service_registry:
            return {}

        return self.service_registry.list_services()

    def list_registry_methods(self) -> List[str]:
        """Список методов в реестре"""
        return self.direct_invoker.list_available_methods()


# Модификация для интеграции с существующим кодом
def create_enhanced_service_layer(method_registry: Dict[str, Any]) -> EnhancedLocalServiceLayer:
    """Создание улучшенного слоя сервисов"""
    return EnhancedLocalServiceLayer(method_registry)


# Интеграционные функции для service.py
def create_local_service_bridge(method_registry: Dict[str, Any], service_manager: ServiceManager):
    """Создание моста локальных сервисов"""

    class LocalServiceBridge:
        def __init__(self):
            self.local_layer = EnhancedLocalServiceLayer(method_registry)
            self.service_manager = service_manager
            self.logger = logging.getLogger("LocalServiceBridge")

        async def initialize(self):
            """Инициализация моста"""
            # Устанавливаем ссылку на реестр сервисов
            self.local_layer.set_service_registry(self.service_manager.registry)

            # Создаем локальный прокси без P2P клиента
            local_proxy = self.local_layer.create_local_proxy()

            # Устанавливаем прокси для всех существующих сервисов
            for service_name, service in self.service_manager.registry.services.items():
                service.proxy = local_proxy
                self.logger.info(f"Updated proxy for service: {service_name}")

            # Устанавливаем прокси для менеджера сервисов
            self.service_manager.proxy_client = local_proxy

            self.logger.info("Local service bridge initialized")

        def get_proxy(self, remote_client=None):
            """Получить прокси (с опциональным удаленным клиентом)"""
            return self.local_layer.create_local_proxy(remote_client)

        async def call_method_direct(self, service_name: str, method_name: str, **kwargs):
            """Прямой вызов метода"""
            return await self.local_layer.call_service_method_direct(service_name, method_name, **kwargs)

    return LocalServiceBridge()


# Пример использования в новой архитектуре
async def example_local_service_usage():
    """Пример использования локальных сервисов без P2P клиента"""

    from service import method_registry
    from service_framework import ServiceManager

    # Создаем менеджер сервисов
    service_manager = ServiceManager(None)  # RPC methods будет установлен позже

    # Создаем локальный мост
    local_bridge = create_local_service_bridge(method_registry, service_manager)
    await local_bridge.initialize()

    # Получаем прокси для локальных вызовов
    proxy = local_bridge.get_proxy()

    # Теперь можем делать вызовы без сетевых запросов
    try:
        # Получаем информацию о системе (локально)
        system_info = await proxy.system.get_system_info()
        print(f"System info: {system_info}")

        # Вызываем метод файл-менеджера (локально)
        file_list = await proxy.file_manager.list_directory(path="./")
        print(f"Files: {file_list}")

    except Exception as e:
        print(f"Local call failed: {e}")


# Модификация для RPCMethods в service.py
class EnhancedRPCMethods:
    """Улучшенная версия RPCMethods с поддержкой локального моста"""

    def __init__(self, method_registry: Dict[str, Any]):
        self.method_registry = method_registry
        self.services_path = Path("services")
        self.registered_services = set()
        self.local_bridge = None
        self.logger = logging.getLogger("EnhancedRPCMethods")

        # Определяем путь к сервисам
        if os.path.exists("services"):
            self.services_path = Path("services")
        else:
            self.services_path = Path("../services")

    def set_local_bridge(self, local_bridge):
        """Установка локального моста"""
        self.local_bridge = local_bridge

    async def register_rpc_methods(self, path: str, methods_instance):
        """Регистрация RPC методов с поддержкой локального вызова"""
        # Обычная регистрация через inspect
        for name, method in inspect.getmembers(methods_instance, predicate=inspect.ismethod):
            if not name.startswith('_'):
                method_path = f"{path}/{name}"
                self.method_registry[method_path] = method
                self.logger.info(f"Registered RPC method: {method_path}")

        # Если есть локальный мост, обновляем прокси для сервиса
        if self.local_bridge and hasattr(methods_instance, 'proxy'):
            new_proxy = self.local_bridge.get_proxy()
            methods_instance.proxy = new_proxy
            self.logger.info(f"Updated local proxy for service: {path}")

    async def call_local_method(self, method_path: str, params: Dict[str, Any]) -> Any:
        """Прямой локальный вызов метода"""
        if self.local_bridge:
            service_name, method_name = method_path.split('/', 1)
            return await self.local_bridge.call_method_direct(service_name, method_name, **params)
        else:
            # Fallback на обычный вызов через реестр
            if method_path in self.method_registry:
                method = self.method_registry[method_path]
                return await method(**params)
            else:
                raise ValueError(f"Method {method_path} not found")