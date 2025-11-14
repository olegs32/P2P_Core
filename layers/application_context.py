"""
application_context.py - Централизованное управление жизненным циклом P2P системы

Решает критические проблемы:
1. Убирает глобальные переменные
2. Управляет порядком инициализации
3. Обеспечивает graceful shutdown
4. Устраняет циклические зависимости
5. Поддержка защищенного хранилища
"""

import asyncio
import logging
import time
import yaml
from typing import Dict, Any, Optional, List
from enum import Enum
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger("AppContext")


class ComponentState(Enum):
    """Состояния компонентов системы"""
    NOT_INITIALIZED = "not_initialized"
    INITIALIZING = "initializing"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class ComponentMetrics:
    """Метрики компонента"""
    start_time: Optional[float] = None
    stop_time: Optional[float] = None
    restart_count: int = 0
    last_error: Optional[str] = None
    error_count: int = 0


@dataclass
class P2PConfig:
    """Централизованная конфигурация системы"""
    node_id: str
    port: int
    bind_address: str = "0.0.0.0"
    advertise_address: Optional[str] = None  # Explicit advertise address (overrides auto-detection)
    coordinator_mode: bool = False
    version = "2.1.0"

    # Redis конфигурация
    redis_url: str = "redis://localhost:6379"
    redis_enabled: bool = True

    # Транспортная конфигурация
    connect_timeout: float = 15.0
    read_timeout: float = 45.0

    # Gossip конфигурация
    gossip_interval: int = 30
    gossip_interval_min: int = 5  # минимальный интервал (низкая нагрузка)
    gossip_interval_max: int = 30  # максимальный интервал (высокая нагрузка)
    gossip_interval_current: int = 15  # текущий интервал (адаптивный)
    gossip_compression_enabled: bool = True  # LZ4 компрессия для gossip сообщений
    gossip_compression_threshold: int = 1024  # минимальный размер для сжатия (байты)
    failure_timeout: int = 60
    gossip_state_file: str = "gossip_state.json"  # файл для сохранения состояния
    coordinator_addresses: list = None  # адреса координаторов для worker узлов
    message_count: int = 0  # количество сообщений за интервал
    adjust_interval_period: int = 60  # период адаптации (секунды)
    compression_enabled: bool = True  # LZ4 компрессия
    compression_threshold: int = 1024  # байты
    max_gossip_targets: int = 5
    cleanup_interval: int = 60
    capabilities = ['coordinator' if coordinator_mode else 'worker']

    # Сервисы
    services_directory: str = "services"
    scan_interval: int = 60
    service_state_file: str = "service_state.json"  # файл для сохранения состояния сервисов

    # Безопасность - JWT
    jwt_secret: str = "change-this-in-production"
    jwt_expiration_hours: int = 24
    jwt_blacklist_file: str = "jwt_blacklist.json"  # файл для сохранения blacklist

    # Безопасность - HTTPS/SSL
    https_enabled: bool = True
    ssl_cert_file: str = "certs/node_cert.cer"
    ssl_key_file: str = "certs/node_key.key"
    ssl_ca_cert_file: str = "certs/ca_cert.cer"  # CA сертификат для верификации
    ssl_ca_key_file: str = "certs/ca_key.key"  # CA ключ для подписания (только на CA сервере)
    ssl_verify: bool = True  # верификация сертификатов через CA

    # Rate Limiting
    rate_limit_enabled: bool = True
    rate_limit_rpc_requests: int = 100  # запросов в минуту для RPC
    rate_limit_rpc_burst: int = 20  # burst размер для RPC
    rate_limit_health_requests: int = 300  # запросов в минуту для health
    rate_limit_health_burst: int = 50  # burst размер для health
    rate_limit_default_requests: int = 200  # запросов в минуту по умолчанию
    rate_limit_default_burst: int = 30  # burst размер по умолчанию

    # Persistence
    state_directory: str = "data"  # директория для хранения состояния

    @classmethod
    def create_default(cls, node_id: str = None, coordinator_mode: bool = False) -> 'P2PConfig':
        """
        Создать конфигурацию с дефолтными параметрами

        Args:
            node_id: ID узла (если None, генерируется автоматически)
            coordinator_mode: True для координатора, False для воркера

        Returns:
            P2PConfig с дефолтными параметрами
        """
        import socket

        if node_id is None:
            node_id = f"{'coordinator' if coordinator_mode else 'worker'}-{socket.gethostname()}"

        # Дефолтные параметры для координатора и воркера
        if coordinator_mode:
            port = 8001
            ssl_cert_file = "certs/coordinator_cert.cer"
            ssl_key_file = "certs/coordinator_key.key"
            state_directory = "data/coordinator"
        else:
            port = 8002
            ssl_cert_file = "certs/worker_cert.cer"
            ssl_key_file = "certs/worker_key.key"
            coordinator_addresses = ["127.0.0.1:8001"]
            state_directory = "data/worker"

        logger.info(f"Creating default config for {'coordinator' if coordinator_mode else 'worker'}: {node_id}")

        config = cls(
            node_id=node_id,
            port=port,
            coordinator_mode=coordinator_mode,
            ssl_cert_file=ssl_cert_file,
            ssl_key_file=ssl_key_file,
            state_directory=state_directory
        )

        # Для воркера устанавливаем адреса координаторов
        if not coordinator_mode:
            config.coordinator_addresses = coordinator_addresses

        return config

    def save_to_storage(self, config_name: str, storage_manager):
        """
        Сохранить конфигурацию в защищенное хранилище

        Args:
            config_name: имя файла конфигурации (например, "coordinator.yaml")
            storage_manager: менеджер защищенного хранилища (обязательный)
        """
        if not storage_manager:
            raise RuntimeError("Storage manager is required to save configuration")

        # Конвертируем в YAML
        from dataclasses import asdict
        config_dict = asdict(self)
        yaml_content = yaml.safe_dump(config_dict, default_flow_style=False, allow_unicode=True)

        # Сохраняем в storage
        storage_manager.write_config(config_name, yaml_content)
        logger.info(f"Config saved to secure storage: {config_name}")

    @classmethod
    def from_yaml(cls, yaml_path: str, context = None) -> 'P2PConfig':
        """
        Загрузить конфигурацию из защищенного хранилища

        Если конфига нет в хранилище, создает дефолтный и сохраняет его.

        Args:
            yaml_path: путь к YAML файлу (используется для определения имени конфига)
            context: контекст приложения для доступа к storage_manager

        Returns:
            P2PConfig загруженный из хранилища или созданный дефолтный
        """
        from layers.storage_manager import get_storage_manager

        storage_manager = get_storage_manager(context)

        if not storage_manager:
            raise RuntimeError("Storage manager is not available. Cannot load configuration without secure storage.")

        # Извлечение имени файла
        config_name = Path(yaml_path).name

        try:
            logger.info(f"Loading config from secure storage: {config_name}")
            yaml_content = storage_manager.read_config(config_name)

            config_data = yaml.safe_load(yaml_content)

            if not config_data.get('coordinator_mode'):
                config_data['ssl_ca_key_file'] = None

            logger.info(f"Config loaded successfully from secure storage: {config_name}")
            return cls(**config_data)

        except FileNotFoundError:
            logger.warning(f"Config not found in storage: {config_name}")
            logger.info("Creating default configuration...")

            # Определяем режим по имени файла
            coordinator_mode = "coordinator" in config_name.lower()

            # Создаем дефолтный конфиг
            default_config = cls.create_default(coordinator_mode=coordinator_mode)

            # Сохраняем в хранилище
            default_config.save_to_storage(config_name, storage_manager)

            logger.info(f"Default config created and saved: {config_name}")
            return default_config

        except Exception as e:
            logger.error(f"Error loading config from storage: {e}")
            raise RuntimeError(f"Failed to load configuration: {e}")

    def to_yaml(self, yaml_path: str) -> None:
        """Сохранить конфигурацию в YAML файл"""
        path = Path(yaml_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(asdict(self), f, default_flow_style=False, allow_unicode=True)

    def get_state_path(self, filename: str) -> Path:
        """Получить полный путь к файлу состояния"""
        state_dir = Path(self.state_directory)
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir / filename


class P2PComponent:
    """Базовый класс для всех компонентов P2P системы"""

    def __init__(self, name: str, context: 'P2PApplicationContext'):
        self.name = name
        self.context = context
        self.state = ComponentState.NOT_INITIALIZED
        self.metrics = ComponentMetrics()
        self.logger = logging.getLogger(f"{name}")
        self._dependencies: List[str] = []
        self._dependents: List[str] = []

    def add_dependency(self, component_name: str):
        """Добавить зависимость от другого компонента"""
        if component_name not in self._dependencies:
            self._dependencies.append(component_name)

    def add_dependent(self, component_name: str):
        """Добавить компонент, который зависит от этого"""
        if component_name not in self._dependents:
            self._dependents.append(component_name)

    async def initialize(self):
        """Инициализация компонента (переопределяется в наследниках)"""
        self.state = ComponentState.INITIALIZING
        import time
        self.metrics.start_time = time.time()

        try:
            await self._do_initialize()
            self.state = ComponentState.RUNNING
            self.logger.info(f"Component {self.name} initialized successfully")

        except Exception as e:
            self.state = ComponentState.ERROR
            self.metrics.last_error = str(e)
            self.metrics.error_count += 1
            self.logger.error(f"Failed to initialize component {self.name}: {e}")
            raise

    async def _do_initialize(self):
        """Реальная инициализация (переопределяется в наследниках)"""
        pass

    async def shutdown(self):
        """Остановка компонента"""
        if self.state in [ComponentState.STOPPED, ComponentState.NOT_INITIALIZED]:
            return

        self.state = ComponentState.STOPPING

        try:
            await self._do_shutdown()
            self.state = ComponentState.STOPPED
            import time
            self.metrics.stop_time = time.time()
            self.logger.info(f"Component {self.name} shutdown successfully")

        except Exception as e:
            self.state = ComponentState.ERROR
            self.metrics.last_error = str(e)
            self.metrics.error_count += 1
            self.logger.error(f"Failed to shutdown component {self.name}: {e}")
            raise

    async def _do_shutdown(self):
        """Реальная остановка (переопределяется в наследниках)"""
        pass

    def get_status(self) -> Dict[str, Any]:
        """Получить статус компонента"""
        return {
            "name": self.name,
            "state": self.state.value,
            "dependencies": self._dependencies,
            "dependents": self._dependents,
            "metrics": {
                "uptime": (time.time() - self.metrics.start_time) if self.metrics.start_time else 0,
                "restart_count": self.metrics.restart_count,
                "error_count": self.metrics.error_count,
                "last_error": self.metrics.last_error
            }
        }


class P2PApplicationContext:
    """Централизованный контекст приложения с управлением жизненным циклом"""
    _current_context = None

    def __init__(self, config: P2PConfig):
        self.config = config
        self.logger = logging.getLogger("AppContext")

        # Реестр компонентов вместо глобальных переменных
        self._components: Dict[str, P2PComponent] = {}
        self._method_registry: Dict[str, Any] = {}
        self._shared_state: Dict[str, Any] = {}

        # Управление жизненным циклом
        self._startup_order: List[str] = []
        self._shutdown_order: List[str] = []
        self._initialization_lock = asyncio.Lock()

        # Graceful shutdown
        self._shutdown_event = asyncio.Event()
        self._shutdown_handlers: List[callable] = []

        self._setup_signal_handlers()
        self._setup_asyncio_exception_handler()  # Подавление Windows ошибок сокетов
        P2PApplicationContext.set_current_context(self)

    @classmethod
    def get_current_context(cls):
        """Получить текущий активный контекст"""
        return cls._current_context

    @classmethod
    def set_current_context(cls, context):
        """Установить текущий активный контекст"""
        cls._current_context = context

    def _setup_signal_handlers(self):
        """Настройка обработчиков сигналов для asyncio"""
        import signal
        import asyncio

        def handle_shutdown_signal(signum):
            """Обработчик сигналов для graceful shutdown"""
            signal_name = signal.Signals(signum).name
            self.logger.info(f"\n{'='*60}")
            self.logger.info(f"Received signal {signal_name}, initiating graceful shutdown...")
            self.logger.info(f"{'='*60}")

            # Устанавливаем событие shutdown
            # Это разбудит wait_for_shutdown() и позволит выполнить finally блок
            self._shutdown_event.set()

        # Регистрируем обработчики сигналов
        try:
            loop = asyncio.get_event_loop()

            # Используем add_signal_handler для POSIX систем
            loop.add_signal_handler(
                signal.SIGINT,
                lambda: handle_shutdown_signal(signal.SIGINT)
            )
            loop.add_signal_handler(
                signal.SIGTERM,
                lambda: handle_shutdown_signal(signal.SIGTERM)
            )
            self.logger.debug("Signal handlers registered with asyncio event loop")

        except (NotImplementedError, AttributeError):
            # Fallback для Windows или если add_signal_handler не поддерживается
            self.logger.warning("asyncio signal handlers not supported, using standard signal module")

            def signal_handler(signum, frame):
                signal_name = signal.Signals(signum).name
                self.logger.info(f"\n{'='*60}")
                self.logger.info(f"Received signal {signal_name}, initiating graceful shutdown...")
                self.logger.info(f"{'='*60}")
                self._shutdown_event.set()

            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)

    def _setup_asyncio_exception_handler(self):
        """
        Настройка обработчика исключений для asyncio event loop

        Подавляет ConnectionResetError и другие ошибки закрытия сокетов
        на Windows (ProactorEventLoop), которые возникают в колбэках asyncio
        при закрытии соединений.
        """
        import asyncio
        import sys

        def asyncio_exception_handler(loop, context):
            """Обработчик исключений asyncio для подавления Windows ошибок"""
            exception = context.get('exception')

            # Список исключений которые нужно подавить (Windows ProactorEventLoop)
            ignored_exceptions = (
                ConnectionResetError,  # [WinError 10054]
                ConnectionAbortedError,  # [WinError 10053]
                BrokenPipeError,  # [Errno 32]
                OSError,  # Общие ошибки сокетов
            )

            # Проверяем тип исключения
            if exception and isinstance(exception, ignored_exceptions):
                # Логируем на уровне debug вместо error
                message = context.get('message', 'Unhandled exception')
                self.logger.debug(
                    f"Suppressed asyncio exception ({type(exception).__name__}): {message}"
                )
                return  # Подавляем исключение

            # Проверяем по строке (для вложенных исключений)
            exception_str = str(exception) if exception else ""
            if any(err in exception_str for err in ["WinError 10054", "WinError 10053", "ConnectionReset", "ConnectionAborted"]):
                self.logger.debug(f"Suppressed Windows socket exception: {exception_str}")
                return

            # Для всех остальных исключений - стандартная обработка
            message = context.get('message', 'Unhandled exception')
            self.logger.error(f"Asyncio exception: {message}")

            if exception:
                self.logger.error(
                    f"Exception type: {type(exception).__name__}",
                    exc_info=exception
                )

        # Устанавливаем обработчик для текущего event loop
        try:
            loop = asyncio.get_event_loop()
            loop.set_exception_handler(asyncio_exception_handler)
            self.logger.debug("Asyncio exception handler installed (suppresses Windows socket errors)")
        except RuntimeError:
            # Нет event loop - установим позже при первом использовании
            self.logger.debug("No event loop yet, exception handler will be set on first use")

    # === Управление компонентами ===

    def register_component(self, component: P2PComponent) -> None:
        """Регистрация компонента в контексте"""
        if component.name in self._components:
            raise ValueError(f"Component {component.name} already registered")

        self._components[component.name] = component
        self.logger.info(f"Registered component: {component.name}")

    def get_component(self, name: str) -> Optional[P2PComponent]:
        """Получить компонент по имени"""
        return self._components.get(name)

    def require_component(self, name: str) -> P2PComponent:
        """Получить компонент (с исключением если не найден)"""
        component = self.get_component(name)
        if not component:
            raise RuntimeError(f"Required component {name} not found")
        return component

    # === Управление методами (замена глобального method_registry) ===

    def register_method(self, path: str, method: callable) -> None:
        """Регистрация RPC метода"""
        if path in self._method_registry:
            self.logger.warning(f"Method {path} already registered, overwriting")

        self._method_registry[path] = method
        self.logger.debug(f"Registered method: {path}")

    def get_method(self, path: str) -> Optional[callable]:
        """Получить зарегистрированный метод"""
        return self._method_registry.get(path)

    def list_methods(self) -> Dict[str, callable]:
        """Получить все зарегистрированные методы"""
        return self._method_registry.copy()

    def unregister_method(self, path: str) -> bool:
        """Удалить метод из реестра"""
        if path in self._method_registry:
            del self._method_registry[path]
            self.logger.debug(f"Unregistered method: {path}")
            return True
        return False

    # === Shared State (замена глобальных переменных) ===

    def set_shared(self, key: str, value: Any) -> None:
        """Установить значение в общем состоянии"""
        self._shared_state[key] = value

    def get_shared(self, key: str, default: Any = None) -> Any:
        """Получить значение из общего состояния"""
        return self._shared_state.get(key, default)

    # === Управление жизненным циклом ===

    def set_startup_order(self, order: List[str]) -> None:
        """Установить порядок запуска компонентов"""
        # Проверяем что все компоненты зарегистрированы
        for name in order:
            if name not in self._components:
                raise ValueError(f"Component {name} not registered")

        self._startup_order = order
        self._shutdown_order = order[::-1]  # Обратный порядок для shutdown

    async def initialize_all(self) -> None:
        """Инициализация всех компонентов в правильном порядке"""
        async with self._initialization_lock:
            self.logger.info("Starting system initialization...")

            # Переустанавливаем exception handler для текущего event loop
            # (на случай если в __init__ loop еще не существовал)
            try:
                import asyncio
                loop = asyncio.get_running_loop()
                if not hasattr(loop, '_exception_handler') or loop._exception_handler is None:
                    self._setup_asyncio_exception_handler()
            except Exception as e:
                self.logger.debug(f"Could not reinstall exception handler: {e}")

            # Если порядок не задан, используем порядок регистрации
            if not self._startup_order:
                self._startup_order = list(self._components.keys())
                self._shutdown_order = self._startup_order[::-1]

            # Инициализируем компоненты по порядку
            for component_name in self._startup_order:
                component = self._components.get(component_name)
                if not component:
                    continue

                self.logger.info(f"Initializing component: {component_name}")

                # Проверяем что все зависимости уже инициализированы
                for dep_name in component._dependencies:
                    dep_component = self._components.get(dep_name)
                    if not dep_component or dep_component.state != ComponentState.RUNNING:
                        raise RuntimeError(f"Dependency {dep_name} not ready for {component_name}")

                try:
                    await component.initialize()
                except Exception as e:
                    self.logger.error(f"Failed to initialize {component_name}: {e}")
                    # Откатываем уже инициализированные компоненты
                    await self._rollback_initialization(component_name)
                    raise

            self.logger.info("System initialization completed successfully")

    async def _rollback_initialization(self, failed_component: str) -> None:
        """Откат инициализации при ошибке"""
        self.logger.warning(f"Rolling back initialization due to failure in {failed_component}")

        # Находим индекс упавшего компонента
        try:
            failed_index = self._startup_order.index(failed_component)
        except ValueError:
            failed_index = len(self._startup_order)

        # Останавливаем все компоненты до упавшего (в обратном порядке)
        for i in range(failed_index - 1, -1, -1):
            component_name = self._startup_order[i]
            component = self._components.get(component_name)
            if component and component.state == ComponentState.RUNNING:
                try:
                    await component.shutdown()
                except Exception as e:
                    self.logger.error(f"Error during rollback shutdown of {component_name}: {e}")

    async def shutdown_all(self) -> None:
        """Graceful shutdown всех компонентов"""
        self.logger.info("Starting graceful shutdown...")

        # Выполняем shutdown handlers
        self.logger.debug(f"Running {len(self._shutdown_handlers)} shutdown handlers...")
        for i, handler in enumerate(self._shutdown_handlers):
            try:
                self.logger.debug(f"Running shutdown handler {i+1}/{len(self._shutdown_handlers)}...")
                if asyncio.iscoroutinefunction(handler):
                    await handler()
                else:
                    handler()
                self.logger.debug(f"Shutdown handler {i+1} completed")
            except Exception as e:
                # На Windows игнорируем ConnectionResetError
                if "ConnectionResetError" not in str(type(e).__name__):
                    self.logger.error(f"Error in shutdown handler {i+1}: {e}")

        # Останавливаем компоненты в обратном порядке
        self.logger.debug(f"Shutting down {len(self._shutdown_order)} components...")
        for component_name in self._shutdown_order:
            component = self._components.get(component_name)
            if not component or component.state != ComponentState.RUNNING:
                self.logger.debug(f"Skipping component {component_name} (not running)")
                continue

            self.logger.info(f"Shutting down component: {component_name}")

            try:
                await asyncio.wait_for(component.shutdown(), timeout=3.0)
                self.logger.info(f"Component {component_name} shutdown completed")
            except asyncio.TimeoutError:
                self.logger.error(f"Component {component_name} shutdown timed out (3s) - continuing anyway")
            except Exception as e:
                # На Windows могут возникать ConnectionResetError - игнорируем их
                if "ConnectionResetError" not in str(type(e).__name__):
                    self.logger.error(f"Error shutting down {component_name}: {e}")
                # Продолжаем shutdown других компонентов

        # Сохраняем защищенное хранилище перед выходом
        try:
            storage_manager = self.get_shared("storage_manager")
            if storage_manager:
                self.logger.info("Saving secure storage before shutdown...")
                storage_manager.save()
                self.logger.info("Secure storage saved successfully")
        except Exception as e:
            self.logger.error(f"Error saving storage during shutdown: {e}")

        self.logger.info("Graceful shutdown completed")

    def add_shutdown_handler(self, handler: callable) -> None:
        """Добавить обработчик для graceful shutdown"""
        self._shutdown_handlers.append(handler)

    async def wait_for_shutdown(self) -> None:
        """Ожидание сигнала shutdown"""
        await self._shutdown_event.wait()

    # === Мониторинг и диагностика ===

    def get_system_status(self) -> Dict[str, Any]:
        """Получить статус всей системы"""
        return {
            "node_id": self.config.node_id,
            "components": {
                name: component.get_status()
                for name, component in self._components.items()
            },
            "registered_methods_count": len(self._method_registry),
            "shared_state_keys": list(self._shared_state.keys())
        }

    def health_check(self) -> Dict[str, Any]:
        """Проверка здоровья системы"""
        unhealthy_components = []

        for name, component in self._components.items():
            if component.state in [ComponentState.ERROR, ComponentState.NOT_INITIALIZED]:
                unhealthy_components.append({
                    "name": name,
                    "state": component.state.value,
                    "last_error": component.metrics.last_error
                })

        is_healthy = len(unhealthy_components) == 0

        return {
            "healthy": is_healthy,
            "total_components": len(self._components),
            "running_components": len([c for c in self._components.values()
                                       if c.state == ComponentState.RUNNING]),
            "unhealthy_components": unhealthy_components
        }


# === Component Implementations ===

class TransportComponent(P2PComponent):
    """Компонент транспортного уровня"""

    def __init__(self, context: P2PApplicationContext):
        super().__init__("transport", context)

    async def _do_initialize(self):
        from layers.transport import P2PTransportLayer, TransportConfig

        config = TransportConfig()
        config.connect_timeout = self.context.config.connect_timeout
        config.read_timeout = self.context.config.read_timeout

        self.transport = P2PTransportLayer(config)
        self.context.set_shared("transport", self.transport)
        self.logger.info("Transport layer initialized")

    async def _do_shutdown(self):
        if hasattr(self, 'transport'):
            await self.transport.close_all()
            self.logger.info("Transport layer shutdown")


class CacheComponent(P2PComponent):
    """Компонент системы кеширования"""

    def __init__(self, context: P2PApplicationContext):
        super().__init__("cache", context)

    async def _do_initialize(self):
        from layers.cache import P2PMultiLevelCache, CacheConfig

        cache_config = CacheConfig(
            redis_url=self.context.config.redis_url,
            redis_enabled=self.context.config.redis_enabled
        )

        self.cache = P2PMultiLevelCache(cache_config, self.context.config.node_id)
        await self.cache.setup_distributed_cache()
        await self.cache.setup_invalidation_listener()

        cache_type = 'Redis + Memory' if self.cache.redis_available else 'Memory Only'
        self.context.set_shared("cache", self.cache)
        self.logger.info(f"Cache system initialized: {cache_type}")

    async def _do_shutdown(self):
        if hasattr(self, 'cache'):
            await self.cache.close()
            self.logger.info("Cache system shutdown")


class NetworkComponent(P2PComponent):
    """Компонент сетевого уровня"""

    def __init__(self, context: P2PApplicationContext):
        super().__init__("network", context)
        self.add_dependency("transport")  # Зависит от транспорта

    async def _do_initialize(self):
        from layers.network import P2PNetworkLayer

        transport = self.context.get_shared("transport")
        if not transport:
            raise RuntimeError("Transport not available")

        # Get coordinator addresses for smart IP detection
        coordinator_addresses = self.context.config.coordinator_addresses or []

        self.network = P2PNetworkLayer(
            transport,
            self.context.config.node_id,
            self.context.config.bind_address,
            self.context.config.port,
            self.context.config.coordinator_mode,
            ssl_verify=self.context.config.ssl_verify,
            ca_cert_file=self.context.config.ssl_ca_cert_file,
            advertise_address=self.context.config.advertise_address,
            coordinator_addresses=coordinator_addresses,
            context=self.context
        )

        # Настройка gossip из конфигурации
        self.network.gossip.gossip_interval = self.context.config.gossip_interval
        self.network.gossip.failure_timeout = self.context.config.failure_timeout
        self.network.gossip.gossip_interval = self.context.config.gossip_interval_current
        self.network.gossip.gossip_interval_min = self.context.config.gossip_interval_min
        self.network.gossip.gossip_interval_max = self.context.config.gossip_interval_max

        # Adaptive gossip interval
        self.network.gossip.message_count = self.context.config.message_count  # количество сообщений за интервал
        self.network.gossip.adjust_interval_period = self.context.config.adjust_interval_period  # период адаптации (секунды)

        # LZ4 компрессия
        self.network.gossip.compression_enabled = self.context.config.compression_enabled
        self.network.gossip.compression_threshold = self.context.config.compression_threshold  # байты
        self.network.gossip.max_gossip_targets = self.context.config.max_gossip_targets
        self.network.gossip.cleanup_interval = self.context.config.cleanup_interval

        # Получаем координаторы для подключения из контекста
        join_addresses = self.context.get_shared("join_addresses", [])

        def setup_service_gossip_integration():
            self.logger.info("🔧 Setting up service gossip integration...")
            service_manager = self.context.get_shared("service_manager")
            if service_manager:
                self.logger.info(f"   Service manager found: {type(service_manager).__name__}")
                self.network.gossip.set_service_info_provider(
                    service_manager.get_services_info_for_gossip
                )
                self.logger.info("✓ Service info provider connected to gossip")
            else:
                self.logger.warning("⚠️  Service manager NOT found during gossip setup!")

        # Вызвать после инициализации сервисов или через callback
        self.context.set_shared("setup_service_gossip", setup_service_gossip_integration)
        await self.network.start(join_addresses)

        if join_addresses:
            self.logger.info(f"Connected to coordinators: {', '.join(join_addresses)}")

        # Ждем стабилизации
        await asyncio.sleep(3)

        status = self.network.get_cluster_status()
        self.logger.info(f"Cluster status - Total: {status['total_nodes']}, "
                         f"Live: {status['live_nodes']}, "
                         f"Coordinators: {status['coordinators']}, "
                         f"Workers: {status['workers']}")

        self.context.set_shared("network", self.network)

    async def _do_shutdown(self):
        if hasattr(self, 'network'):
            await self.network.stop()
            self.logger.info("Network layer shutdown")


class ServiceComponent(P2PComponent):
    """Компонент сервисного уровня с объединенной архитектурой"""

    def __init__(self, context: P2PApplicationContext):
        super().__init__("service", context)
        self.add_dependency("network")
        self.add_dependency("cache")

    async def _do_initialize(self):
        network = self.context.get_shared("network")
        cache = self.context.get_shared("cache")

        if not network:
            raise RuntimeError("Network not available")
        if not cache:
            raise RuntimeError("Cache not available")

        # Создаем объединенный сервисный обработчик
        from layers.service import P2PServiceHandler, set_global_service_manager

        # P2PServiceHandler уже включает ServiceManager внутри себя
        # Передаем context чтобы использовать единый method_registry
        self.service_handler = P2PServiceHandler(
            network_layer=network,
            context=self.context
        )

        # method_registry уже связан через context в P2PServiceHandler
        # ЕДИНЫЙ ИСТОЧНИК ИСТИНЫ: context._method_registry

        self.service_manager = self.service_handler.service_manager

        # Устанавливаем менеджер в контексте
        set_global_service_manager(self.service_manager)

        # Создаем local bridge
        # Создаем local bridge
        from layers.local_service_bridge import create_local_service_bridge

        local_bridge = create_local_service_bridge(
            self.context._method_registry,  # <- ИЗМЕНИТЬ: прямая ссылка вместо .list_methods()
            self.service_manager,
            self.context
        )
        await local_bridge.initialize()

        # Устанавливаем proxy клиент
        self.service_manager.set_proxy_client(local_bridge.get_proxy())

        # Сохраняем ссылки
        self.local_bridge = local_bridge

        # Устанавливаем в контексте
        self.context.set_shared("service_manager", self.service_manager)
        self.context.set_shared("service_handler", self.service_handler)
        self.context.set_shared("local_bridge", local_bridge)

        # Настройка административных методов
        await self._setup_admin_methods(cache)

        # Инициализация всех сервисов через объединенный handler
        await self.service_handler.initialize_all()

        # Настройка gossip если необходимо
        self.logger.info("🔧 Attempting to setup gossip integration...")
        setup_gossip = self.context.get_shared("setup_service_gossip")
        if setup_gossip:
            self.logger.info("   Found setup_gossip callback, calling it...")
            setup_gossip()
            self.logger.info("✓ Gossip setup finished")
        else:
            self.logger.warning("⚠️  setup_service_gossip callback NOT found in context!")

        # Регистрация в контексте для обратной совместимости
        self.context.set_shared("service_layer", self.service_handler)
        self.context.set_shared("rpc", self.service_handler)

        self.logger.info("Service component initialized with unified architecture")

    async def _setup_admin_methods(self, cache):
        """Настройка административных методов"""
        try:
            # Импортируем из нового объединенного файла
            from methods.system import SystemService

            # Создаем system service
            system_service = SystemService("system", None)

            # Инициализируем сервис
            await system_service.initialize()

            # Привязка кеша
            if hasattr(system_service, 'cache'):
                system_service.cache = cache
            self._bind_cache_to_methods(system_service, cache)

            # Регистрируем методы в context и глобальном реестре
            await self._register_methods_in_context("system", system_service)

            # Регистрируем в ServiceManager через новую архитектуру
            await self.service_manager.initialize_service(system_service)

            self.logger.info("Administrative methods registered: system")

        except Exception as e:
            self.logger.error(f"Error setting up admin methods: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            raise

    async def _register_methods_in_context(self, path: str, methods_instance):
        """Регистрация методов в контексте приложения"""
        import inspect
        from layers.service import get_method_registry

        registry = get_method_registry()
        for name, method in inspect.getmembers(methods_instance, predicate=inspect.ismethod):
            if not name.startswith('_'):
                method_path = f"{path}/{name}"

                # Регистрируем в context
                self.context.register_method(method_path, method)

                # Регистрируем в реестре для RPC
                registry[method_path] = method

                self.logger.debug(f"Registered method: {method_path}")

    def _bind_cache_to_methods(self, methods_instance, cache):
        """Привязка кеша к методам с декораторами"""
        for method_name in dir(methods_instance):
            if not method_name.startswith('_'):
                method = getattr(methods_instance, method_name)
                if hasattr(method, '__wrapped__') and hasattr(method, '__name__'):
                    method._cache = cache

    async def _do_shutdown(self):
        """Graceful shutdown всех сервисов"""
        try:
            # Используем объединенный метод shutdown
            if hasattr(self, 'service_handler'):
                await self.service_handler.shutdown_all()
            elif hasattr(self, 'service_manager'):
                await self.service_manager.shutdown_all_services()

            self.logger.info("Service component shutdown completed")

        except Exception as e:
            self.logger.error(f"Error during service shutdown: {e}")

    # =====================================================
    # ДОПОЛНИТЕЛЬНЫЕ МЕТОДЫ ДЛЯ ИНТЕГРАЦИИ
    # =====================================================

    def get_service_handler(self) -> 'P2PServiceHandler':
        """Получить основной сервисный обработчик"""
        return getattr(self, 'service_handler', None)

    def get_service_manager(self) -> 'ServiceManager':
        """Получить менеджер сервисов"""
        return getattr(self, 'service_manager', None)

    def get_local_bridge(self):
        """Получить локальный мост сервисов"""
        return getattr(self, 'local_bridge', None)

    async def reload_service(self, service_name: str):
        """Перезагрузка конкретного сервиса"""
        if hasattr(self, 'service_manager'):
            await self.service_manager.registry.reload_service(service_name)
        else:
            self.logger.error("Service manager not initialized")

    def get_service_metrics(self, service_name: str = None):
        """Получить метрики сервиса(ов)"""
        if not hasattr(self, 'service_manager'):
            return {}

        if service_name:
            service = self.service_manager.registry.get_service(service_name)
            if service:
                return {
                    "counters": service.metrics.counters,
                    "gauges": service.metrics.gauges,
                    "timers": {k: len(v) for k, v in service.metrics.timers.items()},
                    "last_updated": service.metrics.last_updated
                }
            return {}
        else:
            # Возвращаем метрики всех сервисов
            all_metrics = {}
            for svc_name, service in self.service_manager.services.items():
                all_metrics[svc_name] = {
                    "counters": service.metrics.counters,
                    "gauges": service.metrics.gauges,
                    "timers": {k: len(v) for k, v in service.metrics.timers.items()},
                    "status": service.status.value
                }
            return all_metrics

    def get_health_status(self) -> dict:
        """Получить статус здоровья всех сервисов"""
        if not hasattr(self, 'service_manager'):
            return {"status": "error", "message": "Service manager not initialized"}

        try:
            from layers.service import ServiceStatus

            healthy_services = 0
            total_services = len(self.service_manager.services)
            service_statuses = {}

            for service_name, service in self.service_manager.services.items():
                status = service.status.value
                service_statuses[service_name] = status

                if service.status == ServiceStatus.RUNNING:
                    healthy_services += 1

            return {
                "status": "healthy" if healthy_services == total_services else "degraded",
                "services": {
                    "total": total_services,
                    "healthy": healthy_services,
                    "degraded": total_services - healthy_services
                },
                "service_statuses": service_statuses,
                "timestamp": time.time()
            }

        except Exception as e:
            self.logger.error(f"Error getting health status: {e}")
            return {"status": "error", "message": str(e)}

    # =====================================================
    # BACKWARD COMPATIBILITY МЕТОДЫ
    # =====================================================

    def get_rpc_handler(self):
        """Обратная совместимость: получить RPC обработчик"""
        return self.get_service_handler()

    async def register_external_service(self, service_name: str, service_instance):
        """Регистрация внешнего сервиса"""
        if hasattr(self, 'service_manager'):
            await self.service_manager.initialize_service(service_instance)
            self.logger.info(f"External service registered: {service_name}")
        else:
            self.logger.error("Cannot register external service: ServiceManager not available")

    def list_available_methods(self) -> list:
        """
        Список всех доступных методов
        ЕДИНЫЙ ИСТОЧНИК ИСТИНЫ: context._method_registry
        """
        if self.context:
            return list(self.context._method_registry.keys())
        return []

    async def get_service_info_for_gossip(self) -> dict:
        """Получить информацию о сервисах для gossip протокола"""
        if hasattr(self, 'service_manager'):
            try:
                # Используем метод из ServiceManager если он есть
                if hasattr(self.service_manager, 'get_services_info_for_gossip'):
                    return await self.service_manager.get_services_info_for_gossip()
                else:
                    # Fallback: создаем базовую информацию
                    services_info = {}
                    for service_name, service in self.service_manager.services.items():
                        services_info[service_name] = {
                            "status": service.status.value,
                            "methods": service.info.exposed_methods,
                            "version": service.info.version
                        }
                    return services_info
            except Exception as e:
                self.logger.error(f"Error getting service info for gossip: {e}")

        return {}


class WebServerComponent(P2PComponent):
    """Компонент веб-сервера"""

    def __init__(self, context: P2PApplicationContext):
        super().__init__("webserver", context)
        self.add_dependency("service")

    async def _do_initialize(self):
        service_layer = self.context.get_shared("service_layer")
        if not service_layer:
            raise RuntimeError("Service layer not available")

        import uvicorn

        # Настройка HTTPS если включен
        ssl_config = {}
        protocol = "http"

        if hasattr(self.context.config, 'https_enabled') and self.context.config.https_enabled:
            from layers.ssl_helper import (
                _cert_exists, needs_certificate_renewal,
                get_certificate_fingerprint, get_current_network_info,
                generate_challenge, request_certificate_from_coordinator,
                save_certificate_and_key
            )

            cert_file = self.context.config.ssl_cert_file
            key_file = self.context.config.ssl_key_file
            # Прямой доступ к атрибутам dataclass (вместо getattr)
            ca_cert_file = self.context.config.ssl_ca_cert_file
            ca_key_file = self.context.config.ssl_ca_key_file

            self.logger.debug(f"SSL Configuration from config:")
            self.logger.debug(f"  cert_file: {cert_file}")
            self.logger.debug(f"  key_file: {key_file}")
            self.logger.debug(f"  ca_cert_file: {ca_cert_file}")
            self.logger.debug(f"  ca_key_file: {ca_key_file}")
            self.logger.debug(f"  ssl_verify: {getattr(self.context.config, 'ssl_verify', False)}")

            # Координаторы уже имеют сертификаты (подготовлены после init storage)
            if self.context.config.coordinator_mode:
                self.logger.info("Coordinator mode: certificates should be ready from preparation phase")

                # Проверяем что сертификаты существуют
                if not _cert_exists(cert_file, self.context) or not _cert_exists(key_file, self.context):
                    self.logger.error(f"Coordinator certificates not found after preparation!")
                    self.logger.error(f"  cert_file: {cert_file}")
                    self.logger.error(f"  key_file: {key_file}")
                    raise RuntimeError(
                        "Coordinator certificates missing - should have been prepared after storage init")

                # Сертификаты готовы, создаем SSL контекст из защищенного хранилища
                from layers.ssl_helper import ServerSSLContext

                self.server_ssl_context = ServerSSLContext(context=self.context)
                try:
                    ssl_ctx = self.server_ssl_context.create(
                        cert_file=cert_file,
                        key_file=key_file,
                        verify_mode=self.context.config.ssl_verify,
                        ca_cert_file=ca_cert_file if self.context.config.ssl_verify else None
                    )

                    # uvicorn требует пути к файлам, используем get_cert_path/get_key_path
                    ssl_config = {
                        "ssl_keyfile": self.server_ssl_context.get_key_path(),
                        "ssl_certfile": self.server_ssl_context.get_cert_path()
                    }
                    protocol = "https"

                    if ca_cert_file and self.context.config.ssl_verify:
                        self.logger.info(f"HTTPS enabled with CA verification from secure storage")
                        self.logger.info(f"  Node cert: {cert_file}")
                        self.logger.info(f"  CA cert: {ca_cert_file}")
                    else:
                        self.logger.info(f"HTTPS enabled from secure storage: {cert_file}")
                except Exception as e:
                    self.logger.error(f"Failed to create SSL context: {e}")
                    raise

            # Воркеры проверяют и запрашивают сертификаты у координатора если нужно
            elif not self.context.config.coordinator_mode:
                # Это воркер - проверяем сертификат
                needs_renewal, renewal_reason = needs_certificate_renewal(cert_file, ca_cert_file, self.context)

                if needs_renewal:
                    self.logger.warning(f"Certificate renewal needed: {renewal_reason}")

                    # Получаем адрес координатора
                    coordinator_addresses = self.context.config.coordinator_addresses
                    if not coordinator_addresses or len(coordinator_addresses) == 0:
                        self.logger.error("No coordinator address configured, cannot request certificate")
                    else:
                        # ВАЖНО: Сначала запускаем временный HTTP сервер для валидации challenge
                        # Используем отдельный порт 8802 для временного сервера
                        temp_port = 8802
                        self.logger.info(
                            f"Starting temporary HTTP server on port {temp_port} for certificate validation...")

                        temp_config = uvicorn.Config(
                            app=service_layer.app,
                            host=self.context.config.bind_address,
                            port=temp_port,
                            log_level="warning",
                            access_log=False,
                        )
                        temp_http_server = uvicorn.Server(temp_config)

                        # Запускаем сервер в фоновой задаче
                        temp_server_task = asyncio.create_task(temp_http_server.serve())

                        # Даем серверу время на запуск
                        await asyncio.sleep(2)

                        try:
                            # Берем первый координатор
                            coordinator_addr = coordinator_addresses[0]

                            # Формируем URL координатора (без протокола, будет добавлен HTTPS)
                            if '://' not in coordinator_addr:
                                coordinator_url = coordinator_addr
                            else:
                                coordinator_url = coordinator_addr.replace("http://", "").replace("https://", "")

                            self.logger.info(f"Requesting new certificate from coordinator: {coordinator_url}")

                            # Генерируем challenge
                            challenge = generate_challenge()

                            # Сохраняем challenge в контекст для эндпоинта валидации
                            self.context.set_shared("cert_challenge", challenge)

                            # Получаем текущие IP и hostname
                            current_ips, current_hostname = get_current_network_info()

                            # Получаем fingerprint старого сертификата если есть
                            old_fingerprint = None
                            if Path(cert_file).exists():
                                old_fingerprint = get_certificate_fingerprint(cert_file, self.context)

                            # from layers.ssl_helper import ServerSSLContext, read_cert_bytes
                            # ssl = ServerSSLContext(self.context)
                            # ca_temp_cert = ssl.create_temp_files(read_cert_bytes(ca_cert_file))
                            # print('ca_temp_cert', ca_temp_cert)
                            print(challenge)

                            # Запрашиваем сертификат
                            cert_pem, key_pem = await request_certificate_from_coordinator(
                                node_id=self.context.config.node_id,
                                coordinator_url=coordinator_url,
                                challenge=challenge,
                                ip_addresses=current_ips,
                                dns_names=[current_hostname],
                                old_cert_fingerprint=old_fingerprint,
                                ca_cert_file=ca_cert_file,
                                challenge_port=temp_port  # Передаем порт для валидации
                            )
                            # ssl.cleanup()

                            if cert_pem and key_pem:
                                # Сохраняем сертификат
                                if save_certificate_and_key(cert_pem, key_pem, cert_file, key_file,
                                                            context=self.context):
                                    self.logger.info("Certificate successfully updated from coordinator")
                                else:
                                    self.logger.error("Failed to save certificate")
                            else:
                                self.logger.error("Failed to obtain certificate from coordinator")

                            # Очищаем challenge из контекста
                            self.context.set_shared("cert_challenge", None)

                        finally:
                            # Останавливаем временный HTTP сервер
                            self.logger.info("Stopping temporary HTTP server...")
                            try:
                                # Правильная остановка uvicorn сервера
                                temp_http_server.should_exit = True
                                await temp_http_server.shutdown()
                                # Ждем завершения задачи
                                try:
                                    await temp_server_task
                                except asyncio.CancelledError:
                                    pass
                                self.logger.info("Temporary HTTP server stopped successfully")
                            except Exception as e:
                                self.logger.warning(f"Error stopping temporary server: {e}")
                                # В крайнем случае принудительно отменяем задачу
                                temp_server_task.cancel()
                                try:
                                    await temp_server_task
                                except asyncio.CancelledError:
                                    pass

                            # Даем порту время освободиться
                            await asyncio.sleep(1)
                            self.logger.info("Temporary HTTP server cleanup completed")

                # Проверяем что сертификаты воркера готовы ПОСЛЕ получения
                self.logger.info(f"Checking if worker certificates are ready...")
                self.logger.info(f"  cert_file: {cert_file}")
                self.logger.info(f"  key_file: {key_file}")
                cert_exists_result = _cert_exists(cert_file, self.context)
                key_exists_result = _cert_exists(key_file, self.context)
                self.logger.info(f"  cert_exists: {cert_exists_result}")
                self.logger.info(f"  key_exists: {key_exists_result}")

                if _cert_exists(cert_file, self.context) and _cert_exists(key_file, self.context):
                    # Создаем SSL контекст из защищенного хранилища
                    from layers.ssl_helper import ServerSSLContext

                    self.server_ssl_context = ServerSSLContext(context=self.context)
                    try:
                        ssl_ctx = self.server_ssl_context.create(
                            cert_file=cert_file,
                            key_file=key_file,
                            verify_mode=self.context.config.ssl_verify,
                            ca_cert_file=ca_cert_file if self.context.config.ssl_verify else None
                        )

                        # uvicorn требует пути к файлам, используем get_cert_path/get_key_path
                        ssl_config = {
                            "ssl_keyfile": self.server_ssl_context.get_key_path(),
                            "ssl_certfile": self.server_ssl_context.get_cert_path()
                        }
                        protocol = "https"

                        if ca_cert_file and self.context.config.ssl_verify:
                            self.logger.info(f"Worker HTTPS enabled with CA verification from secure storage")
                            self.logger.info(f"  Node cert: {cert_file}")
                            self.logger.info(f"  CA cert: {ca_cert_file}")
                        else:
                            self.logger.info(f"Worker HTTPS enabled from secure storage: {cert_file}")
                    except Exception as e:
                        self.logger.error(f"Failed to create SSL context: {e}")
                        raise
                else:
                    self.logger.warning("Worker certificates not available, falling back to HTTP")
                    self.logger.warning("This may indicate certificate request failed")

        self.config = uvicorn.Config(
            app=service_layer.app,
            host=self.context.config.bind_address,
            port=self.context.config.port,
            log_level="debug",
            access_log=False,
            server_header=False,
            date_header=False,
            **ssl_config
        )

        self.server = uvicorn.Server(self.config)

        # Отключаем ВСЮ обработку сигналов в uvicorn - используем свою
        # Переопределяем все методы, связанные с сигналами
        self.server.install_signal_handlers = lambda: None
        self.server.handle_exit = lambda sig, frame: None

        # Важно: uvicorn проверяет should_exit в цикле
        # Наш глобальный обработчик сигналов установит флаги через _shutdown_event

        # Запускаем сервер в фоновой задаче
        self.server_task = asyncio.create_task(self.server.serve())

        self.logger.info(
            f"Web server started on {protocol}://{self.context.config.bind_address}:{self.context.config.port}"
        )

        # Ждем немного чтобы сервер успел запуститься
        await asyncio.sleep(1)

    async def _do_shutdown(self):
        """Корректное завершение веб-сервера"""
        # Заглушаем логирование uvicorn перед shutdown, чтобы не показывать
        # "INFO: Shutting down" и "INFO: Waiting for connections to close"
        import logging
        uvicorn_logger = logging.getLogger("uvicorn")
        uvicorn_error_logger = logging.getLogger("uvicorn.error")
        original_level = uvicorn_logger.level
        original_error_level = uvicorn_error_logger.level
        uvicorn_logger.setLevel(logging.CRITICAL)
        uvicorn_error_logger.setLevel(logging.CRITICAL)

        # Останавливаем uvicorn сервер корректно
        if hasattr(self, 'server') and self.server:
            try:
                self.logger.info("Shutting down web server...")
                # Устанавливаем флаги для немедленного завершения
                self.server.should_exit = True
                self.server.force_exit = True  # Форсируем выход без ожидания соединений

                # Даем серверу короткое время для начала завершения
                await asyncio.sleep(0.1)

            except Exception as e:
                self.logger.warning(f"Error during server shutdown signal: {e}")

        # Отменяем задачу сервера
        if hasattr(self, 'server_task') and self.server_task:
            try:
                if not self.server_task.done():
                    self.logger.debug("Cancelling server task...")
                    self.server_task.cancel()
                    # Ждем завершения с очень коротким таймаутом
                    try:
                        await asyncio.wait_for(self.server_task, timeout=0.5)
                        self.logger.debug("Server task cancelled successfully")
                    except asyncio.TimeoutError:
                        self.logger.debug("Server task shutdown timeout - continuing anyway")
                        # Просто игнорируем таймаут и продолжаем
                    except asyncio.CancelledError:
                        self.logger.debug("Server task received CancelledError")
                        pass
                else:
                    self.logger.debug("Server task already done")
            except Exception as e:
                # На Windows могут возникать ConnectionResetError - игнорируем
                if "ConnectionResetError" not in str(type(e).__name__):
                    self.logger.warning(f"Error during server task cancellation: {e}")

        # Очистка SSL контекста (закрытие memfd дескрипторов)
        if hasattr(self, 'server_ssl_context'):
            try:
                self.server_ssl_context.cleanup()
            except Exception as e:
                self.logger.warning(f"Error during SSL context cleanup: {e}")

        # Восстанавливаем уровень логирования uvicorn
        uvicorn_logger.setLevel(original_level)
        uvicorn_error_logger.setLevel(original_error_level)

        self.logger.info("Web server shutdown completed")
