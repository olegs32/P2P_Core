# service_framework.py - Фреймворк для сервисов P2P системы

import asyncio
import inspect
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Callable, Type
from pathlib import Path
import importlib.util
import sys
from dataclasses import dataclass, field
from enum import Enum


class ServiceStatus(Enum):
    """Статусы сервиса"""
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class ServiceInfo:
    """Информация о сервисе"""
    name: str
    version: str = "1.0.0"
    description: str = ""
    dependencies: List[str] = field(default_factory=list)
    exposed_methods: List[str] = field(default_factory=list)
    status: ServiceStatus = ServiceStatus.STOPPED
    node_id: str = ""
    domain: str = "default"
    metadata: Dict[str, Any] = field(default_factory=dict)


def service_method(
        description: str = "",
        public: bool = True,
        cache_ttl: int = 0,
        requires_auth: bool = True
):
    """Декоратор для пометки методов сервиса"""

    def decorator(func):
        func._service_method = True
        func._service_description = description
        func._service_public = public
        func._service_cache_ttl = cache_ttl
        func._service_requires_auth = requires_auth
        return func

    return decorator


class BaseService(ABC):
    """Базовый класс для всех сервисов"""

    def __init__(self, service_name: str, proxy_client=None):
        self.service_name = service_name
        self.proxy = proxy_client  # Инжектированный universal proxy
        self.logger = logging.getLogger(f"Service.{service_name}")
        self.status = ServiceStatus.STOPPED
        self.info = ServiceInfo(name=service_name)
        self._extract_service_info()

    def _extract_service_info(self):
        """Извлекает информацию о сервисе из методов класса"""
        methods = []
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if hasattr(method, '_service_method') and method._service_public:
                methods.append(name)

        self.info.exposed_methods = methods
        self.info.description = self.__class__.__doc__ or ""

    @abstractmethod
    async def initialize(self):
        """Инициализация сервиса (переопределяется наследниками)"""
        pass

    @abstractmethod
    async def cleanup(self):
        """Очистка ресурсов при остановке"""
        pass

    async def start(self):
        """Запуск сервиса"""
        try:
            self.status = ServiceStatus.STARTING
            self.logger.info(f"Starting service {self.service_name}")
            await self.initialize()
            self.status = ServiceStatus.RUNNING
            self.logger.info(f"Service {self.service_name} started successfully")
        except Exception as e:
            self.status = ServiceStatus.ERROR
            self.logger.error(f"Failed to start service {self.service_name}: {e}")
            raise

    async def stop(self):
        """Остановка сервиса"""
        try:
            self.status = ServiceStatus.STOPPING
            self.logger.info(f"Stopping service {self.service_name}")
            await self.cleanup()
            self.status = ServiceStatus.STOPPED
            self.logger.info(f"Service {self.service_name} stopped")
        except Exception as e:
            self.status = ServiceStatus.ERROR
            self.logger.error(f"Error stopping service {self.service_name}: {e}")
            raise

    @service_method(description="Get service information", public=True)
    async def get_service_info(self) -> Dict[str, Any]:
        """Получить информацию о сервисе"""
        return {
            "name": self.info.name,
            "version": self.info.version,
            "description": self.info.description,
            "status": self.status.value,
            "exposed_methods": self.info.exposed_methods,
            "dependencies": self.info.dependencies,
            "domain": self.info.domain,
            "metadata": self.info.metadata
        }

    @service_method(description="Health check", public=True)
    async def health_check(self) -> Dict[str, Any]:
        """Проверка состояния сервиса"""
        return {
            "status": "healthy" if self.status == ServiceStatus.RUNNING else "unhealthy",
            "service": self.service_name,
            "uptime": 0,  # TODO: реализовать подсчет uptime
            "last_check": asyncio.get_event_loop().time()
        }


class ServiceRegistry:
    """Реестр сервисов для автоматической регистрации и управления"""

    def __init__(self, rpc_methods_instance):
        self.services: Dict[str, BaseService] = {}
        self.service_classes: Dict[str, Type[BaseService]] = {}
        self.rpc_methods = rpc_methods_instance
        self.logger = logging.getLogger("ServiceRegistry")

    async def register_service_class(self, service_class: Type[BaseService], proxy_client=None):
        """Регистрация класса сервиса"""
        service_name = getattr(service_class, 'SERVICE_NAME', service_class.__name__.lower())
        self.service_classes[service_name] = service_class
        self.logger.info(f"Registered service class: {service_name}")

        # Создаем экземпляр и запускаем
        service_instance = service_class(service_name, proxy_client)
        await self.start_service(service_name, service_instance)

    async def start_service(self, service_name: str, service_instance: BaseService):
        """Запуск сервиса и регистрация его методов в RPC"""
        try:
            await service_instance.start()
            self.services[service_name] = service_instance

            # Регистрируем все публичные методы сервиса в RPC
            await self.rpc_methods.register_rpc_methods(service_name, service_instance)

            self.logger.info(f"Service {service_name} started and registered")

        except Exception as e:
            self.logger.error(f"Failed to start service {service_name}: {e}")
            raise

    async def stop_service(self, service_name: str):
        """Остановка сервиса"""
        if service_name in self.services:
            service = self.services[service_name]
            await service.stop()
            del self.services[service_name]
            self.logger.info(f"Service {service_name} stopped and unregistered")

    async def reload_service(self, service_name: str):
        """Перезагрузка сервиса"""
        if service_name in self.services:
            await self.stop_service(service_name)

        if service_name in self.service_classes:
            service_class = self.service_classes[service_name]
            await self.register_service_class(service_class)

    def get_service(self, service_name: str) -> Optional[BaseService]:
        """Получить экземпляр сервиса"""
        return self.services.get(service_name)

    def list_services(self) -> Dict[str, Dict[str, Any]]:
        """Список всех сервисов"""
        return {
            name: {
                "status": service.status.value,
                "info": service.info.__dict__,
                "methods": service.info.exposed_methods
            }
            for name, service in self.services.items()
        }


class ServiceLoader:
    """Загрузчик сервисов из файловой системы"""

    def __init__(self, services_directory: Path, registry: ServiceRegistry):
        self.services_dir = services_directory
        self.registry = registry
        self.logger = logging.getLogger("ServiceLoader")

    async def discover_and_load_services(self, proxy_client=None):
        """Обнаружение и загрузка всех сервисов"""
        if not self.services_dir.exists():
            self.logger.warning(f"Services directory not found: {self.services_dir}")
            return

        for service_dir in self.services_dir.iterdir():
            if not service_dir.is_dir():
                continue

            await self.load_service_from_directory(service_dir, proxy_client)

    async def load_service_from_directory(self, service_dir: Path, proxy_client=None):
        """Загрузка сервиса из директории"""
        service_name = service_dir.name
        main_file = service_dir / "main.py"

        if not main_file.exists():
            self.logger.warning(f"No main.py found in service directory: {service_dir}")
            return

        try:
            # Загружаем модуль
            spec = importlib.util.spec_from_file_location(f"service_{service_name}", main_file)
            module = importlib.util.module_from_spec(spec)
            sys.modules[f"service_{service_name}"] = module
            spec.loader.exec_module(module)

            # Ищем класс Run
            if hasattr(module, 'Run'):
                run_class = module.Run

                # Проверяем, наследуется ли от BaseService
                if not issubclass(run_class, BaseService):
                    self.logger.error(f"Service {service_name}: Run class must inherit from BaseService")
                    return

                # Регистрируем сервис
                await self.registry.register_service_class(run_class, proxy_client)
                self.logger.info(f"Successfully loaded service: {service_name}")

            else:
                self.logger.error(f"No Run class found in {main_file}")

        except Exception as e:
            self.logger.error(f"Failed to load service {service_name}: {e}")


class ServiceManager:
    """Менеджер сервисов - интегрирует все компоненты"""

    def __init__(self, rpc_methods_instance, services_directory: str = "services"):
        self.registry = ServiceRegistry(rpc_methods_instance)
        self.loader = ServiceLoader(Path(services_directory), self.registry)
        self.logger = logging.getLogger("ServiceManager")
        self.proxy_client = None

    def set_proxy_client(self, proxy_client):
        """Установка universal proxy для инжекции в сервисы"""
        self.proxy_client = proxy_client

    async def initialize_all_services(self):
        """Инициализация всех сервисов"""
        self.logger.info("Initializing service management system")
        await self.loader.discover_and_load_services(self.proxy_client)
        self.logger.info(f"Loaded {len(self.registry.services)} services")

    async def shutdown_all_services(self):
        """Остановка всех сервисов"""
        self.logger.info("Shutting down all services")
        for service_name in list(self.registry.services.keys()):
            await self.registry.stop_service(service_name)
        self.logger.info("All services stopped")

    def get_registry(self) -> ServiceRegistry:
        """Получить реестр сервисов"""
        return self.registry


# Пример использования в существующем коде:

"""
# В p2p_admin.py добавить:

from service_framework import ServiceManager

class P2PAdminSystem:
    def __init__(self, ...):
        # ... существующий код ...

        # Добавляем менеджер сервисов
        self.service_manager = ServiceManager(self.rpc)

    async def start(self, ...):
        # ... существующий код ...

        # Настраиваем proxy для сервисов  
        from universal_proxy import create_universal_client
        proxy_client = create_universal_client(self.transport)
        self.service_manager.set_proxy_client(proxy_client)

        # Инициализируем все сервисы
        await self.service_manager.initialize_all_services()

# Пример сервиса (services/example_service/main.py):

from service_framework import BaseService, service_method

class Run(BaseService):
    SERVICE_NAME = "example_service"

    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.info.version = "1.2.0"
        self.info.description = "Example service for demonstration"
        self.info.dependencies = ["system"]

    async def initialize(self):
        # Инициализация сервиса
        self.logger.info("Example service initializing...")
        # Можем вызывать другие сервисы через self.proxy
        # system_info = await self.proxy.system.get_system_info()

    async def cleanup(self):
        # Очистка ресурсов
        self.logger.info("Example service cleaning up...")

    @service_method(description="Process some data", public=True)
    async def process_data(self, data: dict) -> dict:
        # Публичный метод сервиса
        result = {"processed": data, "service": self.service_name}

        # Можем вызвать другой сервис
        if self.proxy:
            other_result = await self.proxy.other_service.some_method(data)
            result["other_service_response"] = other_result

        return result

    @service_method(description="Internal helper", public=False)  
    async def _internal_helper(self):
        # Приватный метод (не будет доступен через RPC)
        pass
"""