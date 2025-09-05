import asyncio
import threading
import importlib
import logging
import time
import functools
import inspect
import traceback
from typing import Dict, Callable, Any, List, Optional, Union, Type, Tuple
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from collections import defaultdict
from enum import Enum
import weakref

# Настройка логирования
logger = logging.getLogger("p2p_interface")


class MethodType(Enum):
    QUERY = "query"
    COMMAND = "command"
    EVENT = "event"


@dataclass
class MethodInfo:
    """Метаинформация о методе"""
    name: str
    handler: Callable
    method_type: MethodType = MethodType.QUERY
    description: str = ""
    timeout: Optional[float] = None
    retries: int = 0
    rate_limit: Optional[int] = None
    requires_auth: bool = False
    async_method: bool = True
    middleware: List[Callable] = field(default_factory=list)


class P2PInterface:
    """Production-ready P2P интерфейс для проектов"""

    def __init__(self, node, project_name: str):
        self.node = node
        self.project_name = project_name
        self.universal = create_universal_client(node.client)

        # Основные коллекции
        self.methods: Dict[str, MethodInfo] = {}
        self.middleware_stack: List[Callable] = []
        self.startup_tasks: List[Callable] = []
        self.shutdown_tasks: List[Callable] = []
        self.event_handlers: Dict[str, List[Callable]] = defaultdict(list)

        # Состояние
        self._running = False
        self._stats = {
            "calls": defaultdict(int),
            "errors": defaultdict(int),
            "total_calls": 0,
            "start_time": time.time()
        }

        # Rate limiting
        self._rate_limits: Dict[str, List[float]] = defaultdict(list)

        # Logger для проекта
        self.logger = logging.getLogger(f"p2p.{project_name}")

    # === Декораторы для регистрации методов ===

    def method(self,
               name: Optional[str] = None,
               method_type: MethodType = MethodType.QUERY,
               description: str = "",
               timeout: Optional[float] = None,
               retries: int = 0,
               rate_limit: Optional[int] = None,
               requires_auth: bool = False):
        """Универсальный декоратор для регистрации методов"""

        def decorator(func):
            method_name = name or func.__name__

            # Автоопределение типа метода по имени
            if method_type == MethodType.QUERY and method_name.startswith(('set_', 'create_', 'update_', 'delete_')):
                inferred_type = MethodType.COMMAND
            else:
                inferred_type = method_type

            self._register_method(
                method_name, func, inferred_type, description,
                timeout, retries, rate_limit, requires_auth
            )
            return func

        return decorator

    def query(self, name: Optional[str] = None, **kwargs):
        """Декоратор для query методов"""
        return self.method(name, MethodType.QUERY, **kwargs)

    def command(self, name: Optional[str] = None, **kwargs):
        """Декоратор для command методов"""
        return self.method(name, MethodType.COMMAND, **kwargs)

    def event_handler(self, event_type: str):
        """Декоратор для обработчиков событий"""

        def decorator(func):
            self.event_handlers[event_type].append(func)
            return func

        return decorator

    def startup(self, func: Callable):
        """Декоратор для startup задач"""
        self.startup_tasks.append(func)
        return func

    def shutdown(self, func: Callable):
        """Декоратор для shutdown задач"""
        self.shutdown_tasks.append(func)
        return func

    def middleware(self, func: Callable):
        """Декоратор для middleware"""
        self.middleware_stack.append(func)
        return func

    # === Регистрация методов ===

    def register(self, name: str, handler: Callable, **kwargs):
        """Прямая регистрация метода"""
        self._register_method(name, handler, **kwargs)

    def register_class(self, cls: Union[Type, object], prefix: str = "", **default_kwargs):
        """Регистрация всех публичных методов класса"""
        if inspect.isclass(cls):
            instance = cls()
        else:
            instance = cls

        # Инъекция p2p интерфейса
        if hasattr(instance, 'set_p2p'):
            instance.set_p2p(self)
        elif hasattr(instance, 'p2p'):
            instance.p2p = self

        for attr_name in dir(instance):
            if not attr_name.startswith('_') and not attr_name in ['set_p2p']:
                attr = getattr(instance, attr_name)
                if callable(attr):
                    method_name = f"{prefix}{attr_name}" if prefix else attr_name

                    # Проверяем декораторы метода
                    method_kwargs = getattr(attr, '_p2p_config', {})
                    combined_kwargs = {**default_kwargs, **method_kwargs}

                    self._register_method(method_name, attr, **combined_kwargs)

    def _register_method(self, name: str, handler: Callable,
                         method_type: MethodType = MethodType.QUERY,
                         description: str = "",
                         timeout: Optional[float] = None,
                         retries: int = 0,
                         rate_limit: Optional[int] = None,
                         requires_auth: bool = False):
        """Внутренняя регистрация метода"""

        # Извлекаем описание из docstring если не задано
        if not description and handler.__doc__:
            description = handler.__doc__.split('\n')[0].strip()

        self.methods[name] = MethodInfo(
            name=name,
            handler=handler,
            method_type=method_type,
            description=description,
            timeout=timeout,
            retries=retries,
            rate_limit=rate_limit,
            requires_auth=requires_auth,
            async_method=asyncio.iscoroutinefunction(handler)
        )

        self.logger.debug(f"Registered method: {name} ({method_type.value})")

    # === P2P вызовы с улучшенной обработкой ===

    async def call(self, service_path: str, timeout: Optional[float] = None,
                   retries: int = 3, **kwargs) -> Any:
        """Универсальный вызов P2P сервисов с retry логикой"""
        for attempt in range(retries + 1):
            try:
                parts = service_path.split('.')
                service = self.universal

                for part in parts[:-1]:
                    service = getattr(service, part)

                method = getattr(service, parts[-1])

                if timeout:
                    result = await asyncio.wait_for(method(**kwargs), timeout=timeout)
                else:
                    result = await method(**kwargs)

                self.logger.debug(f"Called {service_path} successfully")
                return result

            except Exception as e:
                if attempt < retries:
                    self.logger.warning(f"Call to {service_path} failed (attempt {attempt + 1}/{retries + 1}): {e}")
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                else:
                    self.logger.error(f"Call to {service_path} failed after {retries + 1} attempts: {e}")
                    raise

    async def safe_call(self, service_path: str, default=None, **kwargs) -> Any:
        """Безопасный вызов с возвратом default при ошибке"""
        try:
            return await self.call(service_path, **kwargs)
        except Exception as e:
            self.logger.warning(f"Safe call to {service_path} failed: {e}")
            return default

    async def broadcast(self, service_name: str, method_name: str, **kwargs) -> List[Any]:
        """Broadcast вызов с фильтрацией успешных результатов"""
        try:
            service = getattr(self.universal, service_name)
            method = getattr(service, method_name)
            results = await method(**kwargs)

            # Фильтруем успешные результаты
            successful = [r for r in results if r.get('success', False)]
            self.logger.debug(f"Broadcast to {service_name}.{method_name}: {len(successful)}/{len(results)} successful")

            return successful
        except Exception as e:
            self.logger.error(f"Broadcast to {service_name}.{method_name} failed: {e}")
            return []

    async def call_local(self, service_name: str, method_name: str, **kwargs) -> Any:
        """Вызов только локальных сервисов"""
        try:
            service = getattr(self.universal, service_name)
            local_service = getattr(service, 'local_domain')
            method = getattr(local_service, method_name)
            results = await method(**kwargs)

            for result in results:
                if result.get('success'):
                    return result.get('data')

            raise Exception("No successful local calls")
        except Exception as e:
            self.logger.error(f"Local call to {service_name}.{method_name} failed: {e}")
            raise

    # === События ===

    async def emit(self, event_type: str, data: Any, local_only: bool = False):
        """Отправка события"""
        try:
            if local_only:
                # Только локальные обработчики
                await self._handle_local_event(event_type, data)
            else:
                # Отправка в P2P сеть
                await self.safe_call("events.emit", event_type=event_type, data=data)

            self.logger.debug(f"Emitted event: {event_type}")
        except Exception as e:
            self.logger.error(f"Failed to emit event {event_type}: {e}")

    async def _handle_local_event(self, event_type: str, data: Any):
        """Обработка локальных событий"""
        handlers = self.event_handlers.get(event_type, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except Exception as e:
                self.logger.error(f"Event handler for {event_type} failed: {e}")

    # === Middleware система ===

    async def _execute_with_middleware(self, method_info: MethodInfo, kwargs: Dict[str, Any]):
        """Выполнение метода с middleware"""

        # Pre-processing middleware
        for middleware in self.middleware_stack:
            try:
                kwargs = await self._call_middleware(middleware, 'before', method_info.name, kwargs)
            except Exception as e:
                self.logger.error(f"Middleware failed (before): {e}")
                raise

        # Rate limiting
        if method_info.rate_limit:
            await self._check_rate_limit(method_info.name, method_info.rate_limit)

        # Выполнение основного метода
        start_time = time.time()
        try:
            if method_info.async_method:
                if method_info.timeout:
                    result = await asyncio.wait_for(
                        method_info.handler(**kwargs),
                        timeout=method_info.timeout
                    )
                else:
                    result = await method_info.handler(**kwargs)
            else:
                # Синхронный метод в executor
                result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: method_info.handler(**kwargs)
                )

            # Статистика
            execution_time = time.time() - start_time
            self._update_stats(method_info.name, execution_time, success=True)

        except Exception as e:
            execution_time = time.time() - start_time
            self._update_stats(method_info.name, execution_time, success=False)

            self.logger.error(f"Method {method_info.name} failed: {e}")
            raise

        # Post-processing middleware
        for middleware in reversed(self.middleware_stack):
            try:
                result = await self._call_middleware(middleware, 'after', method_info.name, result)
            except Exception as e:
                self.logger.error(f"Middleware failed (after): {e}")

        return result

    async def _call_middleware(self, middleware: Callable, phase: str, method_name: str, data: Any):
        """Вызов middleware"""
        if asyncio.iscoroutinefunction(middleware):
            return await middleware(phase, method_name, data)
        else:
            return middleware(phase, method_name, data)

    async def _check_rate_limit(self, method_name: str, limit: int):
        """Проверка rate limit"""
        now = time.time()
        calls = self._rate_limits[method_name]

        # Удаляем старые вызовы (старше минуты)
        calls[:] = [call_time for call_time in calls if now - call_time < 60]

        if len(calls) >= limit:
            raise Exception(f"Rate limit exceeded for {method_name} ({limit}/min)")

        calls.append(now)

    def _update_stats(self, method_name: str, execution_time: float, success: bool):
        """Обновление статистики"""
        self._stats["total_calls"] += 1
        self._stats["calls"][method_name] += 1

        if not success:
            self._stats["errors"][method_name] += 1

        # Можно добавить метрики времени выполнения
        if not hasattr(self._stats, "execution_times"):
            self._stats["execution_times"] = defaultdict(list)

        self._stats["execution_times"][method_name].append(execution_time)

    # === Жизненный цикл ===

    async def start(self):
        """Запуск интерфейса"""
        self._running = True
        self.logger.info(f"Starting project {self.project_name}")

        for task in self.startup_tasks:
            try:
                if asyncio.iscoroutinefunction(task):
                    await task()
                else:
                    task()
                self.logger.debug(f"Startup task completed: {task.__name__}")
            except Exception as e:
                self.logger.error(f"Startup task {task.__name__} failed: {e}")
                raise

        self.logger.info(f"Project {self.project_name} started successfully")

    async def stop(self):
        """Остановка интерфейса"""
        self._running = False
        self.logger.info(f"Stopping project {self.project_name}")

        for task in self.shutdown_tasks:
            try:
                if asyncio.iscoroutinefunction(task):
                    await task()
                else:
                    task()
                self.logger.debug(f"Shutdown task completed: {task.__name__}")
            except Exception as e:
                self.logger.error(f"Shutdown task {task.__name__} failed: {e}")

        self.logger.info(f"Project {self.project_name} stopped")

    # === Утилиты ===

    def get_stats(self) -> Dict[str, Any]:
        """Получить статистику проекта"""
        uptime = time.time() - self._stats["start_time"]

        # Средние времена выполнения
        avg_times = {}
        if hasattr(self._stats, "execution_times"):
            for method, times in self._stats["execution_times"].items():
                if times:
                    avg_times[method] = sum(times) / len(times)

        return {
            "project": self.project_name,
            "uptime": uptime,
            "running": self._running,
            "total_calls": self._stats["total_calls"],
            "methods": dict(self._stats["calls"]),
            "errors": dict(self._stats["errors"]),
            "average_execution_times": avg_times,
            "registered_methods": list(self.methods.keys()),
            "event_handlers": {k: len(v) for k, v in self.event_handlers.items()}
        }

    def get_method_info(self, method_name: str) -> Optional[MethodInfo]:
        """Получить информацию о методе"""
        return self.methods.get(method_name)

    async def health_check(self) -> Dict[str, Any]:
        """Health check проекта"""
        return {
            "status": "healthy" if self._running else "stopped",
            "project": self.project_name,
            "methods_count": len(self.methods),
            "uptime": time.time() - self._stats["start_time"],
            "total_calls": self._stats["total_calls"]
        }


# === Базовый класс для сервисов ===

class P2PService:
    """Базовый класс для P2P сервисов"""

    def __init__(self):
        self.p2p: Optional[P2PInterface] = None
        self.logger = logging.getLogger(self.__class__.__name__)

    def set_p2p(self, p2p_interface: P2PInterface):
        """Инъекция P2P интерфейса"""
        self.p2p = p2p_interface
        self.logger = p2p_interface.logger.getChild(self.__class__.__name__)

    async def call(self, path: str, **kwargs):
        """Хелпер для вызова сервисов"""
        if not self.p2p:
            raise RuntimeError("P2P interface not injected")
        return await self.p2p.call(path, **kwargs)

    async def safe_call(self, path: str, default=None, **kwargs):
        """Безопасный вызов сервисов"""
        if not self.p2p:
            raise RuntimeError("P2P interface not injected")
        return await self.p2p.safe_call(path, default, **kwargs)

    async def emit(self, event_type: str, data: Any):
        """Отправка события"""
        if not self.p2p:
            raise RuntimeError("P2P interface not injected")
        await self.p2p.emit(event_type, data)


# === Производственный менеджер проектов ===

class ProductionProjectManager:
    """Production-ready менеджер проектов"""

    def __init__(self, node):
        self.node = node
        self.projects: Dict[str, 'ProjectInstance'] = {}
        self.logger = logging.getLogger("project_manager")

    async def load_project(self, project_name: str, module_path: str,
                           config: Optional[Dict[str, Any]] = None,
                           auto_restart: bool = True):
        """Загрузка проекта с production настройками"""

        if project_name in self.projects:
            self.logger.warning(f"Project {project_name} already loaded")
            return

        try:
            self.logger.info(f"Loading project: {project_name} from {module_path}")

            # Импорт модуля
            module = importlib.import_module(f"{module_path}.main")

            # Создание интерфейса
            p2p = P2PInterface(self.node, project_name)

            # Создание отдельного event loop для проекта
            loop = asyncio.new_event_loop()

            # Создание проектного instance
            instance = ProjectInstance(
                name=project_name,
                module=module,
                p2p_interface=p2p,
                loop=loop,
                config=config or {},
                auto_restart=auto_restart
            )

            # Запуск в отдельном потоке
            def run_project():
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(instance.run())
                except Exception as e:
                    instance.last_error = str(e)
                    instance.error_count += 1
                    self.logger.error(f"Project {project_name} crashed: {e}")
                    traceback.print_exc()

                    if auto_restart and instance.error_count < 5:
                        self.logger.info(f"Restarting project {project_name}")
                        asyncio.get_event_loop().call_later(5, run_project)
                finally:
                    instance.running = False

            thread = threading.Thread(target=run_project, daemon=True)
            instance.thread = thread
            thread.start()

            # Ждем инициализации
            await asyncio.sleep(0.2)

            # Регистрация в основной ноде
            self._register_project_service(project_name, p2p)

            self.projects[project_name] = instance

            self.logger.info(f"✅ Project '{project_name}' loaded successfully")

        except Exception as e:
            self.logger.error(f"❌ Failed to load project '{project_name}': {e}")
            traceback.print_exc()
            raise

    def _register_project_service(self, project_name: str, p2p_interface: P2PInterface):
        """Регистрация проектного сервиса с production wrapper"""

        class ProductionProjectService:
            def __init__(self, interface: P2PInterface):
                self.interface = interface
                self.logger = logging.getLogger(f"service.{project_name}")

            async def health_check(self):
                """Стандартный health check"""
                return await self.interface.health_check()

            async def get_stats(self):
                """Получить статистику проекта"""
                return self.interface.get_stats()

            async def get_methods(self):
                """Список доступных методов"""
                return {
                    name: {
                        "type": info.method_type.value,
                        "description": info.description,
                        "timeout": info.timeout,
                        "retries": info.retries,
                        "rate_limit": info.rate_limit
                    }
                    for name, info in self.interface.methods.items()
                }

            def __getattr__(self, method_name: str):
                """Прокси для методов проекта"""
                if method_name in self.interface.methods:
                    method_info = self.interface.methods[method_name]

                    @functools.wraps(method_info.handler)
                    async def wrapper(**kwargs):
                        try:
                            return await self.interface._execute_with_middleware(method_info, kwargs)
                        except Exception as e:
                            self.logger.error(f"Method {method_name} failed: {e}")
                            raise

                    return wrapper
                else:
                    raise AttributeError(f"Method '{method_name}' not found in project '{project_name}'")

        self.node.register_service(project_name, ProductionProjectService(p2p_interface))

    async def stop_project(self, project_name: str):
        """Остановка проекта"""
        if project_name not in self.projects:
            self.logger.warning(f"Project {project_name} not found")
            return

        instance = self.projects[project_name]
        await instance.stop()
        del self.projects[project_name]

        self.logger.info(f"🛑 Project '{project_name}' stopped")

    async def restart_project(self, project_name: str):
        """Перезапуск проекта"""
        if project_name in self.projects:
            config = self.projects[project_name].config
            module_path = self.projects[project_name].module.__name__.replace('.main', '')
            await self.stop_project(project_name)
            await self.load_project(project_name, module_path, config)

    def get_projects_status(self) -> Dict[str, Any]:
        """Статус всех проектов"""
        return {
            name: {
                "running": instance.running,
                "error_count": instance.error_count,
                "last_error": instance.last_error,
                "uptime": time.time() - instance.start_time
            }
            for name, instance in self.projects.items()
        }


@dataclass
class ProjectInstance:
    """Экземпляр проекта"""
    name: str
    module: Any
    p2p_interface: P2PInterface
    loop: asyncio.AbstractEventLoop
    config: Dict[str, Any]
    auto_restart: bool = True
    thread: Optional[threading.Thread] = None
    running: bool = False
    error_count: int = 0
    last_error: Optional[str] = None
    start_time: float = field(default_factory=time.time)

    async def run(self):
        """Запуск проекта"""
        self.running = True
        try:
            await self.p2p_interface.start()
            await self.module.run(self.p2p_interface, self.config)
        finally:
            await self.p2p_interface.stop()

    async def stop(self):
        """Остановка проекта"""
        self.running = False
        if self.p2p_interface._running:
            await self.p2p_interface.stop()


# === Production Node ===

class P2PProductionNode(P2PNode):
    """Production-ready P2P нода"""

    def __init__(self, node_id: str):
        super().__init__(node_id)
        self.project_manager = ProductionProjectManager(self)

        # Настройка логирования
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

    async def load_project(self, name: str, module_path: str,
                           config: Dict[str, Any] = None, **kwargs):
        await self.project_manager.load_project(name, module_path, config, **kwargs)

    async def stop_project(self, name: str):
        await self.project_manager.stop_project(name)

    async def restart_project(self, name: str):
        await self.project_manager.restart_project(name)

    def get_projects_status(self):
        return self.project_manager.get_projects_status()
