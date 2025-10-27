import argparse
import asyncio
import logging
import os
import socket
import sys
import time
from pathlib import Path
from typing import List
from getpass import getpass

from dotenv import load_dotenv

# Добавляем текущую директорию в путь для импортов
sys.path.insert(0, str(Path(__file__).parent))

# Импортируем новый Application Context и все компоненты
from layers.application_context import (
    P2PApplicationContext, P2PConfig, P2PComponent,
    TransportComponent, CacheComponent, NetworkComponent,
    ServiceComponent, WebServerComponent
)
from layers.transport import P2PTransportLayer, TransportConfig
from layers.network import P2PNetworkLayer
from layers.cache import P2PMultiLevelCache, CacheConfig
from layers.service import (
    P2PServiceHandler, BaseService, ServiceManager,
    service_method, P2PAuthBearer,
    RPCRequest, RPCResponse, P2PServiceHandler )

# Импортируем модули безопасного хранилища
from layers.storage_manager import init_storage


# Настройка кодировки для Windows
if sys.platform == 'win32':
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    import locale
    try:
        locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
    except:
        pass

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


# === Factory функции для создания приложения ===
# Примечание: Все компоненты теперь импортируются из layers.application_context

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
#
# async def run_coordinator(node_id: str, port: int, bind_address: str, redis_url: str):
#     """Запуск координатора с использованием Application Context"""
#     logger = logging.getLogger("Coordinator")
#
#     config = P2PConfig(
#         node_id=node_id,
#         port=port,
#         bind_address=bind_address,
#         coordinator_mode=True,
#         redis_url=redis_url
#     )
#
#     # Создаем приложение
#     app_context = await create_coordinator_application(config)
#
#     try:
#         # Инициализируем все компоненты
#         await app_context.initialize_all()
#
#         # Проверяем здоровье системы
#         health = app_context.health_check()
#         if not health["healthy"]:
#             raise RuntimeError(f"System is not healthy: {health}")
#
#         # Определяем отображаемый адрес для логов
#         display_address = "127.0.0.1" if bind_address == "0.0.0.0" else bind_address
#
#         logger.info("Coordinator started successfully")
#         logger.info(f"Binding to: {bind_address}:{port}")
#         logger.info(f"Available endpoints:")
#
#         # Основные пользовательские эндпоинты
#         logger.info(f"  Health Check: http://{display_address}:{port}/health")
#         logger.info(f"  RPC Interface: http://{display_address}:{port}/rpc")
#
#         # Документация API
#         logger.info(f"  API Documentation: http://{display_address}:{port}/docs")
#         logger.info(f"  ReDoc Documentation: http://{display_address}:{port}/redoc")
#         logger.info(f"  OpenAPI Schema: http://{display_address}:{port}/openapi.json")
#
#         # Управление сервисами
#         logger.info(f"  Services List: http://{display_address}:{port}/services")
#         logger.info(f"  Service Info: http://{display_address}:{port}/services/{{service_name}}")
#         logger.info(f"  Service Restart: http://{display_address}:{port}/services/{{service_name}}/restart")
#
#         # Метрики и мониторинг
#         logger.info(f"  System Metrics: http://{display_address}:{port}/metrics")
#         logger.info(f"  Service Metrics: http://{display_address}:{port}/metrics/{{service_name}}")
#
#         # Кластер и сеть
#         logger.info(f"  Cluster Nodes: http://{display_address}:{port}/cluster/nodes")
#
#         # Аутентификация
#         logger.info(f"  Auth Token: http://{display_address}:{port}/auth/token")
#         logger.info(f"  Auth Revoke: http://{display_address}:{port}/auth/revoke")
#
#         # Внутренние эндпоинты (для отладки)
#         logger.info(f"  Internal Gossip Join: http://{display_address}:{port}/internal/gossip/join")
#         logger.info(f"  Internal Gossip Exchange: http://{display_address}:{port}/internal/gossip/exchange")
#
#         # Ждем сигнал shutdown
#         await app_context.wait_for_shutdown()
#
#     except Exception as e:
#         logger.error(f"Coordinator error: {e}")
#         raise
#     finally:
#         # Graceful shutdown
#         await app_context.shutdown_all()
#
#
# async def run_worker(node_id: str, port: int, bind_address: str,
#                      coordinator_addresses: List[str], redis_url: str):
#     """Запуск рабочего узла с использованием Application Context"""
#     logger = logging.getLogger("Worker")
#
#     config = P2PConfig(
#         node_id=node_id,
#         port=port,
#         bind_address=bind_address,
#         coordinator_mode=False,
#         redis_url=redis_url
#     )
#
#     # Создаем приложение
#     app_context = await create_worker_application(config, coordinator_addresses)
#
#     try:
#         # Инициализируем все компоненты
#         await app_context.initialize_all()
#
#         # Проверяем здоровье системы
#         health = app_context.health_check()
#         if not health["healthy"]:
#             raise RuntimeError(f"System is not healthy: {health}")
#
#         # Определяем отображаемый адрес для логов
#         display_address = "127.0.0.1" if bind_address == "0.0.0.0" else bind_address
#
#         logger.info("Worker started successfully")
#         logger.info(f"Binding to: {bind_address}:{port}")
#         logger.info(f"Connected to coordinators: {', '.join(coordinator_addresses)}")
#         logger.info(f"Available endpoints:")
#
#         # Основные пользовательские эндпоинты
#         logger.info(f"  Health Check: http://{display_address}:{port}/health")
#         logger.info(f"  RPC Interface: http://{display_address}:{port}/rpc")
#
#         # Документация API
#         logger.info(f"  API Documentation: http://{display_address}:{port}/docs")
#         logger.info(f"  ReDoc Documentation: http://{display_address}:{port}/redoc")
#
#         # Управление сервисами
#         logger.info(f"  Services List: http://{display_address}:{port}/services")
#         logger.info(f"  Service Info: http://{display_address}:{port}/services/{{service_name}}")
#
#         # Метрики и мониторинг
#         logger.info(f"  System Metrics: http://{display_address}:{port}/metrics")
#         logger.info(f"  Service Metrics: http://{display_address}:{port}/metrics/{{service_name}}")
#
#         # Кластер и сеть
#         logger.info(f"  Cluster Nodes: http://{display_address}:{port}/cluster/nodes")
#
#         # Ждем сигнал shutdown
#         await app_context.wait_for_shutdown()
#
#     except Exception as e:
#         logger.error(f"Worker error: {e}")
#         raise
#     finally:
#         # Graceful shutdown
#         await app_context.shutdown_all()


async def run_coordinator_from_config(config: P2PConfig):
    """Запуск координатора из готового P2PConfig объекта"""
    logger = logging.getLogger("Coordinator")

    # Создаем приложение
    app_context = await create_coordinator_application(config)

    try:
        # Инициализируем все компоненты
        await app_context.initialize_all()

        # Проверяем здоровье системы
        health = app_context.health_check()
        if not health["healthy"]:
            raise RuntimeError(f"System is not healthy: {health}")

        # Определяем отображаемый адрес для логов
        display_address = "127.0.0.1" if config.bind_address == "0.0.0.0" else config.bind_address
        protocol = "https" if config.https_enabled else "http"

        logger.info("Coordinator started successfully")
        logger.info(f"Binding to: {config.bind_address}:{config.port}")
        logger.info(f"Available endpoints:")

        # Основные пользовательские эндпоинты
        logger.info(f"  Health Check: {protocol}://{display_address}:{config.port}/health")
        logger.info(f"  RPC Interface: {protocol}://{display_address}:{config.port}/rpc")

        # Документация API
        logger.info(f"  API Documentation: {protocol}://{display_address}:{config.port}/docs")
        logger.info(f"  ReDoc Documentation: {protocol}://{display_address}:{config.port}/redoc")

        # Управление сервисами
        logger.info(f"  Services List: {protocol}://{display_address}:{config.port}/services")
        logger.info(f"  Service Info: {protocol}://{display_address}:{config.port}/services/{{service_name}}")

        # Метрики и мониторинг
        logger.info(f"  System Metrics: {protocol}://{display_address}:{config.port}/metrics")
        logger.info(f"  Service Metrics: {protocol}://{display_address}:{config.port}/metrics/{{service_name}}")

        # Кластер и сеть
        logger.info(f"  Cluster Nodes: {protocol}://{display_address}:{config.port}/cluster/nodes")

        # Аутентификация
        logger.info(f"  Auth Token: {protocol}://{display_address}:{config.port}/auth/token")
        logger.info(f"  Auth Revoke: {protocol}://{display_address}:{config.port}/auth/revoke")

        # Конфигурация
        logger.info(f"Configuration:")
        logger.info(f"  Rate Limiting: {'enabled' if config.rate_limit_enabled else 'disabled'}")
        logger.info(f"  HTTPS: {'enabled' if config.https_enabled else 'disabled'}")
        logger.info(f"  Gossip Compression: {'enabled' if config.gossip_compression_enabled else 'disabled'}")
        logger.info(f"  Adaptive Gossip: {config.gossip_interval_min}-{config.gossip_interval_max}s")

        # Ждем сигнал shutdown
        await app_context.wait_for_shutdown()

    except Exception as e:
        logger.error(f"Coordinator error: {e}")
        raise
    finally:
        # Graceful shutdown
        await app_context.shutdown_all()


async def run_worker_from_config(config: P2PConfig):
    """Запуск worker узла из готового P2PConfig объекта"""
    logger = logging.getLogger("Worker")

    # Для worker нужен coordinator_addresses - берем из конфига
    coordinator_addresses = config.coordinator_addresses or []

    # Создаем приложение
    app_context = await create_worker_application(config, coordinator_addresses)

    try:
        # Инициализируем все компоненты
        await app_context.initialize_all()

        # Проверяем здоровье системы
        health = app_context.health_check()
        if not health["healthy"]:
            raise RuntimeError(f"System is not healthy: {health}")

        # Определяем отображаемый адрес для логов
        display_address = "127.0.0.1" if config.bind_address == "0.0.0.0" else config.bind_address
        protocol = "https" if config.https_enabled else "http"

        logger.info("Worker started successfully")
        logger.info(f"Binding to: {config.bind_address}:{config.port}")
        if coordinator_addresses:
            logger.info(f"Connected to coordinators: {', '.join(coordinator_addresses)}")
        logger.info(f"Available endpoints:")

        # Основные пользовательские эндпоинты
        logger.info(f"  Health Check: {protocol}://{display_address}:{config.port}/health")
        logger.info(f"  RPC Interface: {protocol}://{display_address}:{config.port}/rpc")

        # Документация API
        logger.info(f"  API Documentation: {protocol}://{display_address}:{config.port}/docs")
        logger.info(f"  ReDoc Documentation: {protocol}://{display_address}:{config.port}/redoc")

        # Управление сервисами
        logger.info(f"  Services List: {protocol}://{display_address}:{config.port}/services")
        logger.info(f"  Service Info: {protocol}://{display_address}:{config.port}/services/{{service_name}}")

        # Метрики и мониторинг
        logger.info(f"  System Metrics: {protocol}://{display_address}:{config.port}/metrics")
        logger.info(f"  Service Metrics: {protocol}://{display_address}:{config.port}/metrics/{{service_name}}")

        # Кластер и сеть
        logger.info(f"  Cluster Nodes: {protocol}://{display_address}:{config.port}/cluster/nodes")

        # Конфигурация
        logger.info(f"Configuration:")
        logger.info(f"  Rate Limiting: {'enabled' if config.rate_limit_enabled else 'disabled'}")
        logger.info(f"  HTTPS: {'enabled' if config.https_enabled else 'disabled'}")
        logger.info(f"  Gossip Compression: {'enabled' if config.gossip_compression_enabled else 'disabled'}")
        logger.info(f"  Adaptive Gossip: {config.gossip_interval_min}-{config.gossip_interval_max}s")

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
  %(prog)s --config config/coordinator.yaml    # Загрузка из YAML конфигурации
  %(prog)s coordinator                          # Координатор с настройками из .env (legacy)
  %(prog)s worker                               # Рабочий узел с настройками из .env (legacy)
        """
    )

    # Новый метод: загрузка из YAML конфигурации
    parser.add_argument('--config', type=str, default=None,
                        help='Путь к YAML файлу конфигурации (например, config/coordinator.yaml)')

    # Безопасное хранилище
    parser.add_argument('--password', type=str, default=None,
                        help='Пароль для расшифровки защищенного хранилища (будет запрошен, если не указан)')
    parser.add_argument('--storage', type=str, default='data/p2p_secure.bin',
                        help='Путь к файлу защищенного хранилища (по умолчанию: data/p2p_secure.bin)')
    parser.add_argument('--no-storage', action='store_true',
                        help='Отключить использование защищенного хранилища')

    # Legacy метод: старые аргументы для обратной совместимости
    parser.add_argument('mode', nargs='?', choices=['coordinator', 'worker'], default=None,
                        help='Режим работы (используется если не указан --config)')
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
    if getattr(sys, 'frozen', False):
        work_dir = Path(sys.executable).parent
    else:
        work_dir = Path(__file__).parent
    if str(__file__).endswith(".py"):
        env_path = work_dir / "dist" / ".env"
    else:
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
        # Новый метод: загрузка из YAML конфигурации
        if args.config:
            # Проверяем, нужно ли использовать защищенное хранилище
            use_storage = not args.no_storage
            storage_path = args.storage
            password = args.password

            # Проверка наличия файла хранилища
            storage_exists = Path(storage_path).exists()

            # Если хранилище существует или пароль указан, запрашиваем пароль
            if use_storage and (storage_exists or password):
                if not password:
                    logger.info("Secure storage detected. Please enter password.")
                    password = getpass("Storage password: ")

                    if len(password) < 8:
                        logger.error("Password must be at least 8 characters")
                        return 1

                    if len(password) > 100:
                        logger.error("Password must not exceed 100 characters")
                        return 1

                logger.info(f"Initializing secure storage: {storage_path}")

                # Инициализируем защищенное хранилище через контекстный менеджер
                try:
                    with init_storage(password, storage_path):
                        logger.info("Secure storage initialized successfully")

                        # Загружаем конфигурацию (будет использовать хранилище через get_storage_manager)
                        logger.info(f"Loading configuration from: {args.config}")
                        config = P2PConfig.from_yaml(args.config)

                        logger.info(f"Starting {config.node_id} ({'coordinator' if config.coordinator_mode else 'worker'})")
                        logger.info(f"Binding to: {config.bind_address}:{config.port}")

                        if config.coordinator_mode:
                            await run_coordinator_from_config(config)
                        else:
                            await run_worker_from_config(config)

                except ValueError as e:
                    logger.error(f"Storage error: {e}")
                    logger.error("Invalid password or corrupted storage")
                    return 1
                except Exception as e:
                    logger.error(f"Failed to initialize secure storage: {e}")
                    if verbose:
                        import traceback
                        logger.error(traceback.format_exc())
                    return 1

            else:
                # Без защищенного хранилища (стандартный режим)
                if use_storage and not storage_exists:
                    logger.info("Secure storage not found, using filesystem for configuration")

                logger.info(f"Loading configuration from: {args.config}")
                config = P2PConfig.from_yaml(args.config)

                logger.info(f"Starting {config.node_id} ({'coordinator' if config.coordinator_mode else 'worker'})")
                logger.info(f"Binding to: {config.bind_address}:{config.port}")

                if config.coordinator_mode:
                    await run_coordinator_from_config(config)
                else:
                    await run_worker_from_config(config)

        # # Legacy метод: старые аргументы для обратной совместимости
        # elif args.mode == 'coordinator':
        #     node_id = args.node_id or f"coordinator-{socket.gethostname()}"
        #     port = args.port or env_config['coordinator_port']
        #     bind_address = args.address or env_config['bind_address']
        #     redis_url = args.redis_url or env_config['redis_url']
        #
        #     logger.info(f"Starting coordinator: {node_id} on {bind_address}:{port}")
        #     await run_coordinator(
        #         node_id=node_id,
        #         port=port,
        #         bind_address=bind_address,
        #         redis_url=redis_url
        #     )
        #
        # elif args.mode == 'worker':
        #     node_id = args.node_id or f"worker-{socket.gethostname()}"
        #     port = args.port or env_config['worker_port']
        #     bind_address = args.address or env_config['bind_address']
        #     coordinator = args.coord or env_config['coordinator_address']
        #     redis_url = args.redis_url or env_config['redis_url']
        #
        #     logger.info(f"Starting worker: {node_id} on {bind_address}:{port}")
        #     logger.info(f"Connecting to coordinator: {coordinator}")
        #     await run_worker(
        #         node_id=node_id,
        #         port=port,
        #         bind_address=bind_address,
        #         coordinator_addresses=[coordinator],
        #         redis_url=redis_url
        #     )

        else:
            parser.print_help()
            logger.error("Необходимо указать --config")
            sys.exit(1)

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
        import traceback
        traceback.print_exc()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nStopped")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)