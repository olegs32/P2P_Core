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
        self._proxy_set_callback = None
        self.service_name = service_name
        self.proxy = proxy_client  # Инжектированный proxy
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

    def set_proxy(self, proxy_client):
        """
        ИСПРАВЛЕНИЕ: Улучшенный метод установки proxy
        """
        old_proxy = self.proxy
        self.proxy = proxy_client

        if old_proxy is None and proxy_client is not None:
            self.logger.info(f"Proxy successfully set for service: {self.service_name}")

            # Вызываем callback если он был установлен
            if self._proxy_set_callback:
                try:
                    if asyncio.iscoroutinefunction(self._proxy_set_callback):
                        asyncio.create_task(self._proxy_set_callback())
                    else:
                        self._proxy_set_callback()
                except Exception as e:
                    self.logger.error(f"Error in proxy set callback: {e}")

        return proxy_client is not None

    def on_proxy_set(self, callback):
        """Установить callback который вызывается когда proxy установлен"""
        self._proxy_set_callback = callback


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
    """Менеджер сервисов с улучшенной инжекцией proxy"""

    def __init__(self, rpc_handler):
        self.rpc = rpc_handler
        self.services = {}
        self.registry = ServiceRegistry(rpc_handler)
        self.proxy_client = None
        self.logger = logging.getLogger("ServiceManager")

        # Устанавливаем как глобальный менеджер
        set_global_service_manager(self)

    def set_proxy_client(self, proxy_client):
        """Улучшенная установка proxy клиента"""
        self.proxy_client = proxy_client
        self.logger.info("Setting proxy client for all services...")

        # ИСПРАВИТЬ: Инжектируем proxy во все уже созданные сервисы
        for service_name, service_instance in self.services.items():
            try:
                if hasattr(service_instance, 'set_proxy'):
                    service_instance.set_proxy(proxy_client)
                    self.logger.info(f"Proxy injected into service: {service_name}")
                elif hasattr(service_instance, 'proxy'):
                    service_instance.proxy = proxy_client
                    self.logger.info(f"Proxy set directly for service: {service_name}")
            except Exception as e:
                self.logger.error(f"Failed to inject proxy into {service_name}: {e}")

    async def load_service(self, service_path: Path) -> Optional[BaseService]:
        service_name = service_path.name
        main_file = service_path / "main.py"

        self.logger.info(f"Loading service {service_name} from {main_file}")

        try:
            # Создаем уникальное имя модуля
            module_name = f"service_{service_name}_{hash(str(main_file))}"
            self.logger.debug(f"Module name: {module_name}")

            # Динамическая загрузка модуля
            spec = importlib.util.spec_from_file_location(module_name, main_file)
            if not spec:
                self.logger.error(f"Failed to create spec for {main_file}")
                return None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.logger.debug(f"Module {module_name} loaded")

            # Ищем класс Run
            if not hasattr(module, 'Run'):
                self.logger.error(f"Service {service_name}: Class 'Run' not found in module")
                return None

            self.logger.debug(f"Found Run class in {service_name}")

            # Создаем экземпляр сервиса
            RunClass = module.Run
            service_instance = RunClass(service_name, None)

            self.logger.info(f"Service instance created for {service_name}")
            return service_instance

        except Exception as e:
            self.logger.error(f"Failed to load service {service_name}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None

    async def initialize_service(self, service_instance: BaseService):
        """
        ИСПРАВЛЕНИЕ: Инициализация сервиса с проверкой proxy
        """
        try:
            # Еще одна проверка proxy перед инициализацией
            if self.proxy_client and not service_instance.proxy:
                if hasattr(service_instance, 'set_proxy'):
                    service_instance.set_proxy(self.proxy_client)
                else:
                    service_instance.proxy = self.proxy_client
                self.logger.info(f"Last chance proxy injection for: {service_instance.service_name}")

            # Вызываем инициализацию
            if hasattr(service_instance, 'initialize'):
                await service_instance.initialize()

            # Регистрируем публичные методы
            await self._register_service_methods(service_instance)

            self.logger.info(f"Service initialized: {service_instance.service_name}")

        except Exception as e:
            self.logger.error(f"Failed to initialize service {service_instance.service_name}: {e}")
            import traceback
            traceback.print_exc()

    # Остальные методы ServiceManager остаются без изменений...

    async def _register_service_methods(self, service_instance: BaseService):
        """Регистрация методов сервиса в RPC"""
        service_name = service_instance.service_name

        for method_name in dir(service_instance):
            method = getattr(service_instance, method_name)

            if (hasattr(method, '_service_method') and
                    getattr(method, '_service_public', False)):

                rpc_path = f"{service_name}/{method_name}"

                if hasattr(self.rpc, 'register_method'):
                    await self.rpc.register_method(rpc_path, method)
                elif hasattr(self.rpc, 'method_registry'):
                    self.rpc.method_registry[rpc_path] = method

                self.logger.info(f"Registered method: {rpc_path}")

    async def initialize_all_services(self):
        """Инициализация всех найденных сервисов"""
        services_dir = Path("services")


        """Получить директорию exe файла"""
        if getattr(sys, 'frozen', False):
            exe_dir =  Path(sys.executable).parent
        else:
            exe_dir = Path(__file__).parent

        services_dir = exe_dir / "services"
        log = logging.getLogger('Path')
        log.info(services_dir)
        if not services_dir.exists():
            services_dir.mkdir(exist_ok=True)

        if not services_dir.exists():
            services_path = Path.cwd() / "services"
            log.info(services_path)
            services_dir.mkdir(exist_ok=True)



        if not services_dir.exists():
            self.logger.info("Services directory not found, skipping service initialization")
            return

        # ДОБАВИТЬ ОТЛАДКУ:
        self.logger.info(f"Scanning services directory: {services_dir.absolute()}")

        for service_path in services_dir.iterdir():
            self.logger.info(f"Found item: {service_path.name} (is_dir: {service_path.is_dir()})")

            if service_path.is_dir():
                main_file = service_path / "main.py"
                self.logger.info(f"Checking main.py in {service_path.name}: exists={main_file.exists()}")

                if main_file.exists():
                    self.logger.info(f"Attempting to load service: {service_path.name}")
                    service_instance = await self.load_service(service_path)

                    if service_instance:
                        self.logger.info(f"Service {service_path.name} loaded successfully")
                        await self.initialize_service(service_instance)
                    else:
                        self.logger.error(f"Failed to load service: {service_path.name}")

        self.logger.info(f"Initialized {len(self.services)} services")

    async def shutdown_all_services(self):
        """Остановка всех сервисов"""
        for service_name, service_instance in self.services.items():
            try:
                if hasattr(service_instance, 'cleanup'):
                    await service_instance.cleanup()
                self.logger.info(f"Service {service_name} shutdown completed")
            except Exception as e:
                self.logger.error(f"Error shutting down service {service_name}: {e}")

        self.services.clear()
        self.logger.info("All services shutdown completed")


_global_service_manager = None


def set_global_service_manager(manager):
    """Установить глобальный менеджер сервисов"""
    global _global_service_manager
    _global_service_manager = manager


def get_global_service_manager():
    """Получить глобальный менеджер сервисов"""
    return _global_service_manager


def diagnose_proxy_issues(service_instance):
    """Диагностика проблем с proxy для отладки"""
    issues = []

    if not hasattr(service_instance, 'proxy'):
        issues.append("Service doesn't have 'proxy' attribute")
    elif service_instance.proxy is None:
        issues.append("Service proxy is None")

    if not hasattr(service_instance, 'set_proxy'):
        issues.append("Service doesn't have 'set_proxy' method")

    # Проверяем глобальный менеджер
    global_manager = get_global_service_manager()
    if not global_manager:
        issues.append("No global service manager available")
    elif not global_manager.proxy_client:
        issues.append("Global service manager has no proxy_client")

    return {
        "service_name": getattr(service_instance, 'service_name', 'unknown'),
        "has_proxy": service_instance.proxy is not None if hasattr(service_instance, 'proxy') else False,
        "issues": issues,
        "recommendations": _generate_recommendations(issues)
    }


def _generate_recommendations(issues):
    """Генерация рекомендаций по исправлению проблем"""
    recommendations = []

    if "Service proxy is None" in issues:
        recommendations.append("Call service.set_proxy(proxy_client) after service creation")
        recommendations.append("Ensure ServiceManager.set_proxy_client() is called before service initialization")

    if "No global service manager available" in issues:
        recommendations.append("Ensure ServiceManager is created and set as global")

    if not recommendations:
        recommendations.append("All proxy checks passed")

    return recommendations
