import asyncio
import os
import socket
import sys
import argparse
import logging
from typing import List
from pathlib import Path

import os
from pathlib import Path
from dotenv import load_dotenv
# Добавляем текущую директорию в путь для импортов
sys.path.insert(0, str(Path(__file__).parent))

# Импортируем новый Application Context
from layers.application_context import P2PApplicationContext, P2PConfig, P2PComponent
from layers.transport import P2PTransportLayer, TransportConfig
from layers.network import P2PNetworkLayer
from layers.service import P2PServiceLayer
from layers.cache import P2PMultiLevelCache, CacheConfig
from layers.service_framework import ServiceManager, set_global_service_manager


def setup_logging(verbose: bool = False):
    """Настройка системы логирования"""
    level = logging.DEBUG if verbose else logging.INFO

    # Создаем форматтер
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
        datefmt='%H:%M:%S'
    )

    # Настраиваем root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Очищаем существующие обработчики
    root_logger.handlers.clear()

    # Добавляем консольный обработчик
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Настраиваем логгеры библиотек
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# === Компоненты системы как отдельные классы ===

class TransportComponent(P2PComponent):
    """Компонент транспортного уровня"""

    def __init__(self, context: P2PApplicationContext):
        super().__init__("transport", context)

    async def _do_initialize(self):
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

        # Настройка gossip из конфигурации
        self.network.gossip.gossip_interval = self.context.config.gossip_interval
        self.network.gossip.failure_timeout = self.context.config.failure_timeout

        # Получаем координаторы для подключения из контекста
        join_addresses = self.context.get_shared("join_addresses", [])
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
    """Компонент сервисного уровня"""

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

        # Создаем сервисный слой с инжекцией method_registry из контекста
        self.service_layer = P2PServiceLayer(network)

        # Инициализируем менеджер сервисов
        from layers.service import RPCMethods

        # Используем method_registry из контекста вместо глобального
        self.rpc = RPCMethods(self.context.list_methods())

        # Устанавливаем связки
        self.rpc.method_registry = self.context.list_methods()  # Прямая ссылка

        # Инициализируем административные методы
        await self._setup_admin_methods(cache)

        # Инициализируем локальные сервисы
        await self._initialize_local_services()

        self.context.set_shared("service_layer", self.service_layer)
        self.context.set_shared("rpc", self.rpc)

        self.logger.info("Service layer initialized")

    async def _setup_admin_methods(self, cache):
        """Настройка административных методов"""
        try:
            from methods.system import SystemMethods
            system_methods = SystemMethods(cache)

            # Привязка кеша к методам с декораторами
            self._bind_cache_to_methods(system_methods, cache)

            # Регистрация методов в контексте вместо глобального registry
            await self._register_methods_in_context("system", system_methods)

            self.logger.info("Administrative methods registered: system")

        except Exception as e:
            self.logger.error(f"Error setting up admin methods: {e}")
            raise

    async def _register_methods_in_context(self, path: str, methods_instance):
        """Регистрация методов в контексте приложения"""
        import inspect

        for name, method in inspect.getmembers(methods_instance, predicate=inspect.ismethod):
            if not name.startswith('_'):
                method_path = f"{path}/{name}"
                self.context.register_method(method_path, method)
                self.logger.debug(f"Registered method: {method_path}")

    def _bind_cache_to_methods(self, methods_instance, cache):
        """Привязка кеша к методам с декораторами"""
        for method_name in dir(methods_instance):
            if not method_name.startswith('_'):
                method = getattr(methods_instance, method_name)
                if hasattr(method, '__wrapped__') and hasattr(method, '__name__'):
                    method._cache = cache

    async def _initialize_local_services(self):
        """Инициализация системы локальных сервисов"""
        try:
            from layers.local_service_bridge import create_local_service_bridge
            from layers.service_framework import ServiceManager

            # Создаем только ServiceManager (без Observer)
            service_manager = ServiceManager(self.rpc)
            set_global_service_manager(service_manager)

            local_bridge = create_local_service_bridge(
                self.context.list_methods(),
                service_manager  # Передаем ServiceManager
            )

            await local_bridge.initialize()
            service_manager.set_proxy_client(local_bridge.get_proxy())
            await service_manager.initialize_all_services()

            self.service_manager = service_manager
            self.local_bridge = local_bridge
            self.service_layer.set_local_bridge(local_bridge)

            self.logger.info("Local services system initialized (ServiceManager only)")

        except Exception as e:
            self.logger.error(f"Error initializing local services: {e}")
            raise

    async def _do_shutdown(self):
        if hasattr(self, 'service_manager'):
            await self.service_manager.shutdown_all_services()

        self.logger.info("Service layer shutdown")


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

        self.config = uvicorn.Config(
            app=service_layer.app,
            host=self.context.config.bind_address,
            port=self.context.config.port,
            log_level="warning",
            access_log=False,
            server_header=False,
            date_header=False
        )

        self.server = uvicorn.Server(self.config)

        # Запускаем сервер в фоновой задаче
        self.server_task = asyncio.create_task(self.server.serve())

        self.logger.info(f"Web server started on {self.context.config.bind_address}:{self.context.config.port}")

        # Ждем немного чтобы сервер успел запуститься
        await asyncio.sleep(1)

    async def _do_shutdown(self):
        if hasattr(self, 'server_task'):
            self.server_task.cancel()
            try:
                await self.server_task
            except asyncio.CancelledError:
                pass

        self.logger.info("Web server shutdown")


# === Factory функции для создания приложения ===

async def create_coordinator_application(config: P2PConfig) -> P2PApplicationContext:
    """Создание координатора"""
    context = P2PApplicationContext(config)

    # Регистрируем компоненты
    context.register_component(TransportComponent(context))
    context.register_component(CacheComponent(context))
    context.register_component(NetworkComponent(context))
    context.register_component(ServiceComponent(context))
    context.register_component(WebServerComponent(context))

    # Устанавливаем порядок запуска
    context.set_startup_order([
        "transport",
        "cache",
        "network",
        "service",
        "webserver"
    ])

    return context


async def create_worker_application(config: P2PConfig, coordinator_addresses: List[str]) -> P2PApplicationContext:
    """Создание рабочего узла"""
    context = P2PApplicationContext(config)

    # Сохраняем адреса координаторов в контексте
    context.set_shared("join_addresses", coordinator_addresses)

    # Регистрируем компоненты (те же что и для координатора)
    context.register_component(TransportComponent(context))
    context.register_component(CacheComponent(context))
    context.register_component(NetworkComponent(context))
    context.register_component(ServiceComponent(context))
    context.register_component(WebServerComponent(context))

    # Устанавливаем порядок запуска
    context.set_startup_order([
        "transport",
        "cache",
        "network",
        "service",
        "webserver"
    ])

    return context


# === Основная логика запуска ===

async def run_coordinator(node_id: str, port: int, bind_address: str, redis_url: str):
    """Запуск координатора с использованием Application Context"""
    logger = logging.getLogger("Coordinator")

    config = P2PConfig(
        node_id=node_id,
        port=port,
        bind_address=bind_address,
        coordinator_mode=True,
        redis_url=redis_url
    )

    # Создаем приложение
    app_context = await create_coordinator_application(config)

    try:
        # Инициализируем все компоненты
        await app_context.initialize_all()

        # Проверяем здоровье системы
        health = app_context.health_check()
        if not health["healthy"]:
            raise RuntimeError(f"System is not healthy: {health}")

        logger.info("Coordinator started successfully")
        logger.info(f"Available endpoints:")
        logger.info(f"  Health: http://{bind_address}:{port}/health")
        logger.info(f"  API Docs: http://{bind_address}:{port}/docs")
        logger.info(f"  Cluster Status: http://{bind_address}:{port}/cluster/status")

        # Ждем сигнал shutdown
        await app_context.wait_for_shutdown()

    except Exception as e:
        logger.error(f"Coordinator error: {e}")
        raise
    finally:
        # Graceful shutdown
        await app_context.shutdown_all()


async def run_worker(node_id: str, port: int, bind_address: str,
                     coordinator_addresses: List[str], redis_url: str):
    """Запуск рабочего узла с использованием Application Context"""
    logger = logging.getLogger("Worker")

    config = P2PConfig(
        node_id=node_id,
        port=port,
        bind_address=bind_address,
        coordinator_mode=False,
        redis_url=redis_url
    )

    # Создаем приложение
    app_context = await create_worker_application(config, coordinator_addresses)

    try:
        # Инициализируем все компоненты
        await app_context.initialize_all()

        # Проверяем здоровье системы
        health = app_context.health_check()
        if not health["healthy"]:
            raise RuntimeError(f"System is not healthy: {health}")

        logger.info("Worker started successfully")
        logger.info(f"Available endpoints:")
        logger.info(f"  Health: http://{bind_address}:{port}/health")
        logger.info(f"  API Docs: http://{bind_address}:{port}/docs")

        # Ждем сигнал shutdown
        await app_context.wait_for_shutdown()

    except Exception as e:
        logger.error(f"Worker error: {e}")
        raise
    finally:
        # Graceful shutdown
        await app_context.shutdown_all()


def create_argument_parser():
    """Создание парсера аргументов командной строки"""
    env_config = load_environment()

    parser = argparse.ArgumentParser(
        description="P2P Administrative System - Distributed service computing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  %(prog)s coordinator                     # Координатор с настройками из .env
  %(prog)s worker                          # Рабочий узел с настройками из .env
        """
    )

    parser.add_argument('mode', choices=['coordinator', 'worker'])
    parser.add_argument('--node-id', default=None)
    parser.add_argument('--port', type=int, default=None)
    parser.add_argument('--address', default=None,
                        help=f'Адрес привязки (по умолчанию из .env: {env_config["bind_address"]})')
    parser.add_argument('--coord', '--coordinator', default=None,
                        help=f'Адрес координатора (по умолчанию из .env: {env_config["coordinator_address"]})')
    parser.add_argument('--redis-url', default=None,
                        help=f'URL Redis (по умолчанию из .env: {env_config["redis_url"]})')
    parser.add_argument('--verbose', '-v', action='store_true', help='Подробный вывод')

    return parser


def load_environment():
    """Загрузка переменных окружения из .env файла"""
    env_path = Path('.env')
    if env_path.exists():
        load_dotenv(env_path)

    return {
        'bind_address': os.getenv('BIND_ADDRESS', '0.0.0.0'),
        'coordinator_port': int(os.getenv('DEFAULT_COORDINATOR_PORT', '8001')),
        'worker_port': int(os.getenv('DEFAULT_WORKER_PORT', '8002')),
        'coordinator_address': os.getenv('COORDINATOR_ADDRESS', '192.168.53.53:8001'),
        'redis_url': os.getenv('REDIS_URL', 'redis://localhost:6379'),
        'log_level': os.getenv('LOG_LEVEL', 'INFO'),
        'verbose': os.getenv('VERBOSE_LOGGING', 'false').lower() == 'true'
    }


async def main():
    """Главная функция приложения"""
    env_config = load_environment()

    parser = create_argument_parser()
    args = parser.parse_args()

    # Применяем env конфиг как defaults
    verbose = args.verbose or env_config['verbose']
    setup_logging(verbose)
    logger = logging.getLogger("Main")

    try:
        if args.mode == 'coordinator':
            node_id = args.node_id or f"coordinator-{socket.gethostname()}"
            port = args.port or env_config['coordinator_port']
            bind_address = args.address or env_config['bind_address']
            redis_url = args.redis_url or env_config['redis_url']

            logger.info(f"Starting coordinator: {node_id} on {bind_address}:{port}")
            await run_coordinator(
                node_id=node_id,
                port=port,
                bind_address=bind_address,
                redis_url=redis_url
            )

        elif args.mode == 'worker':
            node_id = args.node_id or f"worker-{socket.gethostname()}"
            port = args.port or env_config['worker_port']
            bind_address = args.address or env_config['bind_address']
            coordinator = args.coord or env_config['coordinator_address']
            redis_url = args.redis_url or env_config['redis_url']

            logger.info(f"Starting worker: {node_id} on {bind_address}:{port}")
            logger.info(f"Connecting to coordinator: {coordinator}")
            await run_worker(
                node_id=node_id,
                port=port,
                bind_address=bind_address,
                coordinator_addresses=[coordinator],
                redis_url=redis_url
            )

    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
    except Exception as e:
        logger.error(f"Critical error: {e}")
        if verbose:
            import traceback
            logger.error(traceback.format_exc())
        return 1

    return 0


def check_python_version():
    """Проверка версии Python"""
    if sys.version_info < (3, 7):
        print("Требуется Python 3.7 или новее")
        print(f"   Текущая версия: {sys.version}")
        return False
    return True


def check_dependencies():
    """Проверка зависимостей"""
    required_packages = [
        'fastapi', 'uvicorn', 'httpx', 'psutil',
        'cachetools', 'pydantic', 'jwt',
    ]

    missing_packages = []
    for package in required_packages:
        try:
            __import__(package)
        except ImportError:
            missing_packages.append(package)

    if missing_packages:
        print("Отсутствуют обязательные зависимости:")
        for package in missing_packages:
            print(f"   - {package}")
        print("\nУстановите зависимости:")
        print("   pip install fastapi uvicorn httpx psutil cachetools pydantic PyJWT")
        return False

    return True


if __name__ == "__main__":
    # Проверки системы
    if not check_python_version():
        sys.exit(1)

    if not check_dependencies():
        sys.exit(1)

    # Запуск главной функции
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nStopped")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)