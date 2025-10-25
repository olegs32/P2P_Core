import argparse
import asyncio
import logging
import os
import socket
import sys
import time
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# Добавляем текущую директорию в путь для импортов
sys.path.insert(0, str(Path(__file__).parent))

# Импортируем новый Application Context
from layers.application_context import P2PApplicationContext, P2PConfig, P2PComponent
from layers.transport import P2PTransportLayer, TransportConfig
from layers.network import P2PNetworkLayer
from layers.cache import P2PMultiLevelCache, CacheConfig
from layers.service import (
    P2PServiceHandler, BaseService, ServiceManager,
    service_method, P2PAuthBearer,
    RPCRequest, RPCResponse, P2PServiceHandler )


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

        def setup_service_gossip_integration():
            service_manager = self.context.get_shared("service_manager")
            if service_manager:
                self.network.gossip.set_service_info_provider(
                    service_manager.get_services_info_for_gossip
                )
                self.logger.info("Service info provider connected to gossip")

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
            self.service_manager
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
        setup_gossip = self.context.get_shared("setup_service_gossip")
        if setup_gossip:
            setup_gossip()
            self.logger.info("Gossip setup finished")

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

    def get_service_info_for_gossip(self) -> dict:
        """Получить информацию о сервисах для gossip протокола"""
        if hasattr(self, 'service_manager'):
            try:
                # Используем метод из ServiceManager если он есть
                if hasattr(self.service_manager, 'get_services_info_for_gossip'):
                    return asyncio.create_task(
                        self.service_manager.get_services_info_for_gossip()
                    )
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
            from layers.ssl_helper import ensure_certificates_exist

            cert_file = self.context.config.ssl_cert_file
            key_file = self.context.config.ssl_key_file

            # Убедимся что сертификаты существуют
            if ensure_certificates_exist(cert_file, key_file, self.context.config.node_id):
                ssl_config = {
                    "ssl_keyfile": key_file,
                    "ssl_certfile": cert_file
                }
                protocol = "https"
                self.logger.info(f"HTTPS enabled with certificates: {cert_file}, {key_file}")
            else:
                self.logger.warning("Failed to setup HTTPS, falling back to HTTP")

        self.config = uvicorn.Config(
            app=service_layer.app,
            host=self.context.config.bind_address,
            port=self.context.config.port,
            log_level="warning",
            access_log=False,
            server_header=False,
            date_header=False,
            **ssl_config
        )

        self.server = uvicorn.Server(self.config)

        # Запускаем сервер в фоновой задаче
        self.server_task = asyncio.create_task(self.server.serve())

        self.logger.info(
            f"Web server started on {protocol}://{self.context.config.bind_address}:{self.context.config.port}"
        )

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

        # Определяем отображаемый адрес для логов
        display_address = "127.0.0.1" if bind_address == "0.0.0.0" else bind_address

        logger.info("Coordinator started successfully")
        logger.info(f"Binding to: {bind_address}:{port}")
        logger.info(f"Available endpoints:")

        # Основные пользовательские эндпоинты
        logger.info(f"  Health Check: http://{display_address}:{port}/health")
        logger.info(f"  RPC Interface: http://{display_address}:{port}/rpc")

        # Документация API
        logger.info(f"  API Documentation: http://{display_address}:{port}/docs")
        logger.info(f"  ReDoc Documentation: http://{display_address}:{port}/redoc")
        logger.info(f"  OpenAPI Schema: http://{display_address}:{port}/openapi.json")

        # Управление сервисами
        logger.info(f"  Services List: http://{display_address}:{port}/services")
        logger.info(f"  Service Info: http://{display_address}:{port}/services/{{service_name}}")
        logger.info(f"  Service Restart: http://{display_address}:{port}/services/{{service_name}}/restart")

        # Метрики и мониторинг
        logger.info(f"  System Metrics: http://{display_address}:{port}/metrics")
        logger.info(f"  Service Metrics: http://{display_address}:{port}/metrics/{{service_name}}")

        # Кластер и сеть
        logger.info(f"  Cluster Nodes: http://{display_address}:{port}/cluster/nodes")

        # Аутентификация
        logger.info(f"  Auth Token: http://{display_address}:{port}/auth/token")
        logger.info(f"  Auth Revoke: http://{display_address}:{port}/auth/revoke")

        # Внутренние эндпоинты (для отладки)
        logger.info(f"  Internal Gossip Join: http://{display_address}:{port}/internal/gossip/join")
        logger.info(f"  Internal Gossip Exchange: http://{display_address}:{port}/internal/gossip/exchange")

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

        # Определяем отображаемый адрес для логов
        display_address = "127.0.0.1" if bind_address == "0.0.0.0" else bind_address

        logger.info("Worker started successfully")
        logger.info(f"Binding to: {bind_address}:{port}")
        logger.info(f"Connected to coordinators: {', '.join(coordinator_addresses)}")
        logger.info(f"Available endpoints:")

        # Основные пользовательские эндпоинты
        logger.info(f"  Health Check: http://{display_address}:{port}/health")
        logger.info(f"  RPC Interface: http://{display_address}:{port}/rpc")

        # Документация API
        logger.info(f"  API Documentation: http://{display_address}:{port}/docs")
        logger.info(f"  ReDoc Documentation: http://{display_address}:{port}/redoc")

        # Управление сервисами
        logger.info(f"  Services List: http://{display_address}:{port}/services")
        logger.info(f"  Service Info: http://{display_address}:{port}/services/{{service_name}}")

        # Метрики и мониторинг
        logger.info(f"  System Metrics: http://{display_address}:{port}/metrics")
        logger.info(f"  Service Metrics: http://{display_address}:{port}/metrics/{{service_name}}")

        # Кластер и сеть
        logger.info(f"  Cluster Nodes: http://{display_address}:{port}/cluster/nodes")

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