"""
application_context.py - Централизованное управление жизненным циклом P2P системы

Решает критические проблемы:
1. Убирает глобальные переменные
2. Управляет порядком инициализации
3. Обеспечивает graceful shutdown
4. Устраняет циклические зависимости
"""

import asyncio
import logging
import time
import yaml
from typing import Dict, Any, Optional, List
from enum import Enum
from dataclasses import dataclass, field, asdict
from pathlib import Path


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
    bind_address: str = "127.0.0.1"
    coordinator_mode: bool = False

    # Redis конфигурация
    redis_url: str = "redis://localhost:6379"
    redis_enabled: bool = True

    # Транспортная конфигурация
    connect_timeout: float = 15.0
    read_timeout: float = 45.0

    # Gossip конфигурация
    gossip_interval_min: int = 5  # минимальный интервал (низкая нагрузка)
    gossip_interval_max: int = 30  # максимальный интервал (высокая нагрузка)
    gossip_interval_current: int = 15  # текущий интервал (адаптивный)
    gossip_compression_enabled: bool = True  # LZ4 компрессия для gossip сообщений
    gossip_compression_threshold: int = 1024  # минимальный размер для сжатия (байты)
    failure_timeout: int = 60
    gossip_state_file: str = "gossip_state.json"  # файл для сохранения состояния

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
    ssl_cert_file: str = "node_cert.pem"
    ssl_key_file: str = "node_key.pem"
    ssl_verify: bool = False  # для самоподписанных сертификатов

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
    def from_yaml(cls, yaml_path: str) -> 'P2PConfig':
        """Загрузить конфигурацию из YAML файла"""
        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {yaml_path}")

        with open(path, 'r', encoding='utf-8') as f:
            config_data = yaml.safe_load(f)

        # Создаем экземпляр с данными из YAML
        return cls(**config_data)

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
        """Настройка обработчиков сигналов"""
        import signal

        def signal_handler(signum, frame):
            signal_name = signal.Signals(signum).name
            self.logger.info(f"Received signal {signal_name}, initiating graceful shutdown...")
            self._shutdown_event.set()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

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
        for handler in self._shutdown_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler()
                else:
                    handler()
            except Exception as e:
                self.logger.error(f"Error in shutdown handler: {e}")

        # Останавливаем компоненты в обратном порядке
        for component_name in self._shutdown_order:
            component = self._components.get(component_name)
            if not component or component.state != ComponentState.RUNNING:
                continue

            self.logger.info(f"Shutting down component: {component_name}")

            try:
                await component.shutdown()
            except Exception as e:
                self.logger.error(f"Error shutting down {component_name}: {e}")
                # Продолжаем shutdown других компонентов

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


# === Пример использования ===

class TransportComponent(P2PComponent):
    """Компонент транспортного уровня"""

    async def _do_initialize(self):
        from layers.transport import P2PTransportLayer, TransportConfig

        config = TransportConfig()
        config.connect_timeout = self.context.config.connect_timeout
        config.read_timeout = self.context.config.read_timeout

        self.transport = P2PTransportLayer(config)
        self.context.set_shared("transport", self.transport)

    async def _do_shutdown(self):
        if hasattr(self, 'transport'):
            await self.transport.close_all()


class NetworkComponent(P2PComponent):
    """Компонент сетевого уровня"""

    def __init__(self, context):
        super().__init__("network", context)
        self.add_dependency("transport")  # Зависит от транспорта

    async def _do_initialize(self):
        from layers.network import P2PNetworkLayer

        transport = self.context.get_shared("transport")
        if not transport:
            raise RuntimeError("Transport not available")

        self.network = P2PNetworkLayer(
            transport,
            self.context.config.node_id,
            self.context.config.bind_address,
            self.context.config.port,
            self.context.config.coordinator_mode
        )

        # Настройка gossip с адаптивными интервалами и компрессией
        self.network.gossip.gossip_interval = self.context.config.gossip_interval_current
        self.network.gossip.gossip_interval_min = self.context.config.gossip_interval_min
        self.network.gossip.gossip_interval_max = self.context.config.gossip_interval_max
        self.network.gossip.failure_timeout = self.context.config.failure_timeout
        self.network.gossip.compression_enabled = self.context.config.gossip_compression_enabled
        self.network.gossip.compression_threshold = self.context.config.gossip_compression_threshold

        self.context.set_shared("network", self.network)

    async def _do_shutdown(self):
        if hasattr(self, 'network'):
            await self.network.stop()


async def create_p2p_application(config: P2PConfig) -> P2PApplicationContext:
    """Factory функция для создания P2P приложения"""

    # Создаем контекст
    context = P2PApplicationContext(config)

    # Регистрируем компоненты
    transport = TransportComponent(context)
    network = NetworkComponent(context)

    context.register_component(transport)
    context.register_component(network)

    # Устанавливаем порядок запуска
    context.set_startup_order(["transport", "network"])

    return context


# === Пример основного цикла приложения ===

async def main():
    """Главная функция с использованием Application Context"""

    config = P2PConfig(
        node_id="coordinator-1",
        port=8001,
        coordinator_mode=True
    )

    # Создаем приложение
    app_context = await create_p2p_application(config)

    try:
        # Инициализируем все компоненты
        await app_context.initialize_all()

        # Проверяем здоровье системы
        health = app_context.health_check()
        if not health["healthy"]:
            raise RuntimeError(f"System is not healthy: {health}")

        print("P2P System started successfully")
        print(f"System status: {app_context.get_system_status()}")

        # Ждем сигнал shutdown
        await app_context.wait_for_shutdown()

    except Exception as e:
        print(f"Fatal error: {e}")

    finally:
        # Graceful shutdown
        await app_context.shutdown_all()


if __name__ == "__main__":
    asyncio.run(main())