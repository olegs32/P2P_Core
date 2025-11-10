import argparse
import asyncio
import logging
import os
import signal
import socket
import sys
import time
from pathlib import Path
from typing import List
from getpass import getpass
from contextlib import asynccontextmanager

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


# Глобальные флаги для обработки сигналов
_shutdown_requested = False
_force_shutdown = False
_shutdown_in_progress = False


def signal_handler(signum, frame):
    """
    Глобальный обработчик сигналов для немедленного завершения.
    Первый Ctrl+C: graceful shutdown
    Второй Ctrl+C: принудительный выход
    """
    global _shutdown_requested, _force_shutdown, _shutdown_in_progress

    logger = logging.getLogger("SignalHandler")

    if _force_shutdown or (_shutdown_requested and _shutdown_in_progress):
        # Второй Ctrl+C или shutdown уже идет - немедленный выход
        logger.warning("Force shutdown requested! Exiting immediately...")
        sys.exit(1)

    if _shutdown_requested:
        # Shutdown уже запрошен, но еще не начался - форсируем
        logger.warning("Shutdown already requested. Press Ctrl+C again to force exit.")
        _force_shutdown = True
        return

    # Первый Ctrl+C - устанавливаем флаг graceful shutdown
    _shutdown_requested = True
    logger.info(f"Shutdown signal received (signal {signum}). Starting graceful shutdown...")
    logger.info("Press Ctrl+C again to force immediate exit.")


async def prepare_certificates_after_storage(config: 'P2PConfig', context):
    """
    Подготовка сертификатов после инициализации хранилища

    Выполняется после init_storage, но до инициализации network компонентов.
    Для координаторов - проверяет и генерирует CA и сертификаты.
    Для воркеров - получает CA с координатора если нужно, сертификаты запросят позже.

    Args:
        config: конфигурация P2P узла
        context: P2PApplicationContext для доступа к storage_manager
    """
    logger = logging.getLogger("CertPrep")

    # Проверяем включен ли HTTPS
    if not hasattr(config, 'https_enabled') or not config.https_enabled:
        logger.debug("HTTPS not enabled, skipping certificate preparation")
        return

    # Проверяем доступность storage manager из контекста
    from layers.storage_manager import get_storage_manager
    storage = get_storage_manager(context)
    if storage:
        logger.info("Storage manager is available - certificates will be saved to secure storage")
    else:
        logger.warning("Storage manager NOT available - certificates will be saved to filesystem only")

    from layers.ssl_helper import (
        ensure_ca_exists, ensure_certificates_exist
    )

    cert_file = config.ssl_cert_file
    key_file = config.ssl_key_file
    ca_cert_file = config.ssl_ca_cert_file
    ca_key_file = config.ssl_ca_key_file

    logger.info("Preparing certificates after storage initialization...")
    logger.debug(f"  cert_file: {cert_file}")
    logger.debug(f"  key_file: {key_file}")
    logger.debug(f"  ca_cert_file: {ca_cert_file}")
    logger.debug(f"  ca_key_file: {ca_key_file}")

    if config.coordinator_mode:
        # Координатор: убеждаемся что CA существует
        logger.info("Coordinator mode: ensuring CA exists...")

        if not ca_cert_file or not ca_key_file:
            logger.error("CA certificate paths not configured for coordinator")
            raise RuntimeError("Coordinator requires CA certificate configuration")

        # Создаем CA если не существует
        if not ensure_ca_exists(ca_cert_file, ca_key_file, context=context):
            logger.error("Failed to ensure CA exists")
            raise RuntimeError("Could not create or verify CA certificate")

        logger.info("CA certificate verified")

        # Генерируем сертификат координатора если не существует
        logger.info("Ensuring coordinator certificate exists...")
        if not ensure_certificates_exist(
            cert_file, key_file,
            config.node_id,
            ca_cert_file=ca_cert_file,
            ca_key_file=ca_key_file,
            context=context
        ):
            logger.error("Failed to ensure coordinator certificate exists")
            raise RuntimeError("Could not create coordinator certificate")

        logger.info("Coordinator certificate ready")

    else:
        # Воркер: проверяем CA сертификат и получаем его с координатора если нужно
        logger.info("Worker mode: verifying CA certificate availability...")

        if ca_cert_file:
            from layers.ssl_helper import _cert_exists, _write_cert_bytes, request_ca_cert_from_coordinator

            # Проверяем что CA доступен
            if not _cert_exists(ca_cert_file, context):
                logger.warning(f"CA certificate not found: {ca_cert_file}")
                logger.info("Attempting to fetch CA certificate from coordinator (ACME-like)...")

                # Получаем адреса координаторов из конфигурации
                coordinator_addresses = getattr(config, 'coordinator_addresses', None)

                if not coordinator_addresses or len(coordinator_addresses) == 0:
                    logger.error("No coordinator addresses configured, cannot fetch CA certificate")
                    raise RuntimeError("Worker requires CA certificate but no coordinators configured")

                # Пробуем получить CA сертификат с каждого координатора
                ca_cert_pem = None
                for coordinator_addr in coordinator_addresses:
                    logger.info(f"Trying to fetch CA certificate from {coordinator_addr}...")

                    try:
                        # Используем await для вызова async функции
                        ca_cert_pem = await request_ca_cert_from_coordinator(
                            coordinator_url=coordinator_addr,
                            context=context
                        )

                        if ca_cert_pem:
                            logger.info(f"Successfully fetched CA certificate from {coordinator_addr}")
                            break
                        else:
                            logger.warning(f"Failed to fetch CA certificate from {coordinator_addr}")
                    except Exception as e:
                        logger.error(f"Error fetching CA certificate from {coordinator_addr}: {e}")
                        continue

                if not ca_cert_pem:
                    logger.error("Failed to fetch CA certificate from any coordinator")
                    raise RuntimeError("Could not obtain CA certificate from coordinators")

                # Сохраняем CA сертификат в защищенное хранилище
                logger.info(f"Saving CA certificate to secure storage: {ca_cert_file}")
                success, msg = _write_cert_bytes(ca_cert_file, ca_cert_pem.encode('utf-8'), context)

                if not success:
                    logger.error(f"Failed to save CA certificate: {msg}")
                    raise RuntimeError(f"Could not save CA certificate: {msg}")

                logger.info("CA certificate successfully saved to secure storage")
            else:
                logger.info("CA certificate found in secure storage")
        else:
            logger.warning("CA certificate path not configured")

        # Сертификаты воркера будут запрошены позже в WebServerComponent
        logger.debug("Worker certificates will be requested during network initialization if needed")


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

async def create_coordinator_application(config: P2PConfig, storage_manager):
    """
    Создание координатора с использованием уже инициализированного хранилища

    Args:
        config: конфигурация P2P узла
        storage_manager: уже инициализированный менеджер хранилища

    Returns:
        P2PApplicationContext: полностью инициализированный контекст приложения
    """
    logger = logging.getLogger("AppFactory")

    # 1. Создаем контекст приложения
    context = P2PApplicationContext(config)
    logger.info("Application context created")

    # 2. Копируем storage_manager в context
    context.set_shared("storage_manager", storage_manager)
    logger.info("Storage manager registered in context")

    # 3. Настраиваем SSL сертификаты
    logger.info("Setting up SSL certificates")
    await prepare_certificates_after_storage(config, context)
    logger.info("SSL certificates ready")

    # 4. Регистрируем компоненты
    logger.info("Registering application components")
    context.register_component(TransportComponent(context))
    context.register_component(CacheComponent(context))
    context.register_component(NetworkComponent(context))
    context.register_component(ServiceComponent(context))
    context.register_component(WebServerComponent(context))

    # 5. Устанавливаем порядок запуска
    context.set_startup_order([
        "transport",
        "cache",
        "network",
        "service",
        "webserver"
    ])
    logger.info("All components registered successfully")

    return context


async def create_worker_application(config: P2PConfig, coordinator_addresses: List[str],
                                   storage_manager):
    """
    Создание рабочего узла с использованием уже инициализированного хранилища

    Args:
        config: конфигурация P2P узла
        coordinator_addresses: список адресов координаторов
        storage_manager: уже инициализированный менеджер хранилища

    Returns:
        P2PApplicationContext: полностью инициализированный контекст приложения
    """
    logger = logging.getLogger("AppFactory")

    # 1. Создаем контекст приложения
    context = P2PApplicationContext(config)
    logger.info("Application context created")

    # Сохраняем адреса координаторов в контексте
    context.set_shared("join_addresses", coordinator_addresses)

    # 2. Копируем storage_manager в context
    context.set_shared("storage_manager", storage_manager)
    logger.info("Storage manager registered in context")

    # 3. Настраиваем SSL сертификаты
    logger.info("Setting up SSL certificates")
    await prepare_certificates_after_storage(config, context)
    logger.info("SSL certificates ready")

    # 4. Регистрируем компоненты (те же что и для координатора)
    logger.info("Registering application components")
    context.register_component(TransportComponent(context))
    context.register_component(CacheComponent(context))
    context.register_component(NetworkComponent(context))
    context.register_component(ServiceComponent(context))
    context.register_component(WebServerComponent(context))

    # 5. Устанавливаем порядок запуска
    context.set_startup_order([
        "transport",
        "cache",
        "network",
        "service",
        "webserver"
    ])
    logger.info("All components registered successfully")

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


async def run_coordinator_from_context(app_context: P2PApplicationContext):
    """Запуск координатора из готового контекста приложения"""
    global _shutdown_requested, _shutdown_in_progress

    logger = logging.getLogger("Coordinator")
    config = app_context.config

    # Фоновая задача для мониторинга флага shutdown
    async def monitor_shutdown_flag():
        """Проверяет глобальный флаг shutdown и инициирует завершение"""
        while not _shutdown_requested:
            await asyncio.sleep(0.1)
        logger.info("Shutdown flag detected, initiating graceful shutdown...")
        app_context._shutdown_event.set()

    # Запускаем монитор флага shutdown
    shutdown_monitor_task = asyncio.create_task(monitor_shutdown_flag())

    try:
        # Инициализируем все компоненты
        await app_context.initialize_all()

        # Запускаем автосохранение защищенного хранилища
        storage_manager = app_context.get_shared("storage_manager")
        if storage_manager:
            logger.info("Starting storage autosave (60s interval)...")
            storage_manager.start_autosave(interval=60)

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
        # Отменяем задачу мониторинга
        shutdown_monitor_task.cancel()
        try:
            await shutdown_monitor_task
        except asyncio.CancelledError:
            pass

        # Устанавливаем флаг что shutdown начался
        _shutdown_in_progress = True

        # Graceful shutdown with timeout and forced exit
        try:
            logger.info("Initiating graceful shutdown...")
            await asyncio.wait_for(app_context.shutdown_all(), timeout=5.0)
            logger.info("Graceful shutdown completed successfully")
        except asyncio.TimeoutError:
            logger.error("Shutdown timeout exceeded (5s), forcing exit...")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            logger.error("Forcing exit due to shutdown error...")
            sys.exit(1)


async def run_worker_from_context(app_context: P2PApplicationContext):
    """Запуск worker узла из готового контекста приложения"""
    global _shutdown_requested, _shutdown_in_progress

    logger = logging.getLogger("Worker")
    config = app_context.config

    # Для worker нужен coordinator_addresses - берем из конфига
    coordinator_addresses = config.coordinator_addresses or []

    # Фоновая задача для мониторинга флага shutdown
    async def monitor_shutdown_flag():
        """Проверяет глобальный флаг shutdown и инициирует завершение"""
        while not _shutdown_requested:
            await asyncio.sleep(0.1)
        logger.info("Shutdown flag detected, initiating graceful shutdown...")
        app_context._shutdown_event.set()

    # Запускаем монитор флага shutdown
    shutdown_monitor_task = asyncio.create_task(monitor_shutdown_flag())

    try:
        # Инициализируем все компоненты
        await app_context.initialize_all()

        # Запускаем автосохранение защищенного хранилища
        storage_manager = app_context.get_shared("storage_manager")
        if storage_manager:
            logger.info("Starting storage autosave (60s interval)...")
            storage_manager.start_autosave(interval=60)

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
        # Отменяем задачу мониторинга
        shutdown_monitor_task.cancel()
        try:
            await shutdown_monitor_task
        except asyncio.CancelledError:
            pass

        # Устанавливаем флаг что shutdown начался
        _shutdown_in_progress = True

        # Graceful shutdown with timeout and forced exit
        try:
            logger.info("Initiating graceful shutdown...")
            await asyncio.wait_for(app_context.shutdown_all(), timeout=5.0)
            logger.info("Graceful shutdown completed successfully")
        except asyncio.TimeoutError:
            logger.error("Shutdown timeout exceeded (5s), forcing exit...")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            logger.error("Forcing exit due to shutdown error...")
            sys.exit(1)


def create_argument_parser():
    """Создание парсера аргументов командной строки"""
    env_config = load_environment()

    parser = argparse.ArgumentParser(
        description="P2P Administrative System - Distributed service computing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  %(prog)s --config config/coordinator.yaml    # Загрузка из YAML конфигурации
        """
    )

    # Новый метод: загрузка из YAML конфигурации
    parser.add_argument('--config', type=str, default=None,
                        help='Путь к YAML файлу конфигурации (например, config/coordinator.yaml)')

    # Безопасное хранилище
    parser.add_argument('--password', type=str, default=None,
                        help='Пароль для расшифровки защищенного хранилища (будет запрошен, если не указан)')
    parser.add_argument('--storage', type=str, default='',
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

                try:
                    # Создаем минимальный контекст для инициализации storage
                    class MinimalContext:
                        def __init__(self):
                            self._shared_state = {}
                        def set_shared(self, key, value):
                            self._shared_state[key] = value
                        def get_shared(self, key, default=None):
                            return self._shared_state.get(key, default)

                    temp_context = MinimalContext()

                    # Инициализируем storage
                    run_type = True if 'coordinator' in args.config else False
                    logger.info(f"Initializing secure storage: {storage_path}")
                    with init_storage(password, storage_path, temp_context, run_type):
                        logger.info("Secure storage initialized successfully")

                        # Загружаем конфигурацию из storage
                        logger.info(f"Loading configuration from: {args.config}")
                        config = P2PConfig.from_yaml(args.config, context=temp_context)

                        logger.info(f"Starting {config.node_id} ({'coordinator' if config.coordinator_mode else 'worker'})")
                        logger.info(f"Binding to: {config.bind_address}:{config.port}")

                        # Получаем storage_manager из temp_context
                        storage_manager = temp_context.get_shared("storage_manager")

                        # Создаем приложение с полной инициализацией (SSL + компоненты)
                        if config.coordinator_mode:
                            app_context = await create_coordinator_application(config, storage_manager)
                            await run_coordinator_from_context(app_context)
                        else:
                            coordinator_addresses = config.coordinator_addresses or []
                            app_context = await create_worker_application(config, coordinator_addresses,
                                                                storage_manager)
                            await run_worker_from_context(app_context)

                except ValueError as e:
                    logger.error(f"Storage error: {e}")
                    logger.error("Invalid password or corrupted storage")
                    return 1
                except Exception as e:
                    import traceback
                    logger.error(f"Failed to initialize application: {e}")
                    logger.error(traceback.format_exc())
                    return 1

            else:
                # Без защищенного хранилища не поддерживается
                logger.error("Application requires secure storage. Use --storage to specify storage path.")
                logger.error("To create new storage, just specify a password and it will be created automatically.")
                return 1

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

    # Устанавливаем глобальный обработчик сигналов ДО запуска asyncio
    # Это позволяет перехватывать Ctrl+C раньше uvicorn
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Запуск главной функции
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code if exit_code else 0)
    except KeyboardInterrupt:
        # Этот блок не должен выполняться, так как сигналы обрабатываются в signal_handler
        print("\nStopped by KeyboardInterrupt")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)