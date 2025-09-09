import asyncio
import os
import sys
import signal
import argparse
import logging
from typing import List
from datetime import datetime
import uvicorn
from pathlib import Path

# Добавляем текущую директорию в путь для импортов
sys.path.insert(0, str(Path(__file__).parent))
from layers.local_service_bridge import create_local_service_bridge
from layers.service_framework import ServiceManager

try:
    from layers.transport import P2PTransportLayer, TransportConfig
    from layers.network import P2PNetworkLayer
    from layers.service import P2PServiceLayer, RPCMethods, method_registry
    from layers.cache import P2PMultiLevelCache, CacheConfig
    from methods.system import SystemMethods
except ImportError as e:
    print(f"Ошибка импорта модулей: {e}")
    print("Убедитесь, что все файлы находятся в правильных директориях:")
    print("  layers/transport.py, layers/network.py, layers/service.py, layers/cache.py")
    print("  methods/system.py")
    sys.exit(1)

# Глобальные переменные для graceful shutdown
shutdown_event = asyncio.Event()
active_systems = []


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


def setup_signal_handlers():
    """Настройка обработчиков сигналов для graceful shutdown"""

    def signal_handler(signum, frame):
        signal_name = signal.Signals(signum).name
        print(f"\nПолучен сигнал {signal_name}, начинаем graceful shutdown...")
        shutdown_event.set()

    # Обработка SIGINT (Ctrl+C) и SIGTERM
    if hasattr(signal, 'SIGINT'):
        signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)


class P2PAdminSystem:
    """Полная система P2P администрирования"""

    def __init__(self, node_id: str, port: int,
                 bind_address: str = "127.0.0.1",
                 coordinator_mode: bool = False,
                 redis_url: str = "redis://localhost:6379"):
        self.node_id = node_id
        self.port = port
        self.bind_address = bind_address
        self.coordinator_mode = coordinator_mode
        self.started = False

        self.logger = logging.getLogger(f"P2PSystem.{node_id}")

        # Инициализация компонентов
        transport_config = TransportConfig()
        # Увеличиваем таймауты для стабильности
        transport_config.connect_timeout = 15.0
        transport_config.read_timeout = 45.0
        self.transport = P2PTransportLayer(transport_config)

        self.network = P2PNetworkLayer(
            self.transport,
            node_id,
            bind_address,
            port,
            coordinator_mode
        )

        # Увеличиваем интервалы gossip для стабильности
        self.network.gossip.gossip_interval = 15  # было 10
        self.network.gossip.failure_timeout = 60  # было 30

        # Кеш с возможностью fallback
        cache_config = CacheConfig(redis_url=redis_url, redis_enabled=True)
        self.cache = P2PMultiLevelCache(cache_config, node_id)

        self.service_layer = P2PServiceLayer(self.network)
        self.rpc = RPCMethods(method_registry)

        # Добавляем систему в глобальный список для graceful shutdown
        active_systems.append(self)

    async def _setup_admin_methods(self):
        try:
            # Создаем экземпляры методов
            system_methods = SystemMethods(self.cache)

            # Привязка кеша к методам с декораторами
            self._bind_cache_to_methods(system_methods)

            # Регистрация методов для RPC
            await self.rpc.register_rpc_methods("system", system_methods)

            self.logger.info("Зарегистрированы administrative методы: system")

        except Exception as e:
            self.logger.error(f"Ошибка при настройке административных методов: {e}")
            raise

    async def _initialize_local_services(self):
        """Инициализация локальных сервисов БЕЗ сетевых подключений"""
        try:
            self.logger.info("Инициализация системы сервисов...")
            service_manager = ServiceManager(self.rpc)
            local_bridge = create_local_service_bridge(
                self.rpc.method_registry,
                service_manager
            )
            await local_bridge.initialize()
            service_manager.set_proxy_client(local_bridge.get_proxy())
            await service_manager.initialize_all_services()
            self.service_manager = service_manager
            self.local_bridge = local_bridge
            self.logger.info("Система сервисов инициализирована")

        except Exception as e:
            self.logger.error(f"Ошибка инициализации сервисов: {e}")
            raise

    def _bind_cache_to_methods(self, methods_instance):
        """Привязка кеша к методам с декораторами"""
        for method_name in dir(methods_instance):
            if not method_name.startswith('_'):
                method = getattr(methods_instance, method_name)
                # Проверяем, является ли метод декорированной функцией кеширования
                if hasattr(method, '__wrapped__') and hasattr(method, '__name__'):
                    # Привязываем кеш к декорированному методу
                    method._cache = self.cache

    async def start(self, join_addresses: List[str] = None):
        """Запуск P2P системы"""
        if self.started:
            self.logger.warning("Система уже запущена")
            return

        try:
            await self._setup_admin_methods()
            self.logger.info(f"Запуск P2P Core")
            self.logger.info(f"Node ID: {self.node_id}")
            self.logger.info(f"Address: {self.bind_address}:{self.port}")
            self.logger.info(f"Mode: {'Coordinator' if self.coordinator_mode else 'Worker'}")

            self.logger.info("Инициализация системы кеширования...")
            await self.cache.setup_distributed_cache()
            await self.cache.setup_invalidation_listener()
            cache_type = 'Redis + Memory' if self.cache.redis_available else 'Memory Only'
            self.logger.info(f"Cache: {cache_type}")

            self.logger.info("Запуск сетевого уровня...")
            await self.network.start(join_addresses)

            if join_addresses:
                self.logger.info(f"Подключение к координаторам: {', '.join(join_addresses)}")

            # Увеличиваем паузу для стабилизации
            await asyncio.sleep(3)

            status = self.network.get_cluster_status()
            self.logger.info(f"Статус кластера - Всего узлов: {status['total_nodes']}, "
                             f"Активных: {status['live_nodes']}, "
                             f"Координаторов: {status['coordinators']}, "
                             f"Рабочих: {status['workers']}")

            await self._initialize_local_services()

            self.started = True
            self.logger.info("P2P Core успешно запущен!")

        except Exception as e:
            self.logger.error(f"Ошибка запуска P2P системы: {e}")
            raise

    async def stop(self):
        """Остановка системы"""
        if not self.started:
            return

        self.logger.info("Остановка P2P Core...")

        try:
            if hasattr(self, 'service_manager'):
                self.logger.debug("Остановка системы локальных сервисов...")
                await self.service_manager.shutdown_all_services()

            self.logger.debug("Остановка сетевого уровня...")
            await self.network.stop()

            self.logger.debug("Закрытие системы кеширования...")
            await self.cache.close()

            self.started = False
            self.logger.info(f"P2P Core остановлен: {self.node_id}")

            # Удаляем из глобального списка
            if self in active_systems:
                active_systems.remove(self)

        except Exception as e:
            self.logger.error(f"Ошибка при остановке системы: {e}")

    async def run_server(self):
        """Запуск FastAPI сервера"""
        if not self.started:
            raise RuntimeError("Система не запущена. Вызовите start() сначала.")

        self.logger.info(f"Запуск HTTP сервера на {self.bind_address}:{self.port}")

        config = uvicorn.Config(
            app=self.service_layer.app,
            host=self.bind_address,
            port=self.port,
            log_level="warning",
            access_log=False,
            server_header=False,
            date_header=False
        )

        server = uvicorn.Server(config)

        # Запуск сервера с обработкой shutdown
        server_task = asyncio.create_task(server.serve())
        shutdown_task = asyncio.create_task(shutdown_event.wait())

        try:
            # Ждем либо завершения сервера, либо сигнала shutdown
            done, pending = await asyncio.wait(
                [server_task, shutdown_task],
                return_when=asyncio.FIRST_COMPLETED
            )

            # Отменяем оставшиеся задачи
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError as e:
                    print(f'Error due stop task: {e}')

        except Exception as e:
            self.logger.error(f"Ошибка сервера: {e}")
        finally:
            # Graceful shutdown
            await self.stop()


async def run_coordinator(node_id: str, port: int, bind_address: str, redis_url: str):
    """Запуск координатора"""
    logger = logging.getLogger("Coordinator")

    coordinator = P2PAdminSystem(
        node_id=node_id,
        port=port,
        bind_address=bind_address,
        coordinator_mode=True,
        redis_url=redis_url
    )

    try:
        await coordinator.start()

        logger.info("Доступные endpoints:")
        logger.info(f"  Health: http://{bind_address}:{port}/health")
        logger.info(f"  API Docs: http://{bind_address}:{port}/docs")
        logger.info(f"  Cluster Status: http://{bind_address}:{port}/cluster/status")

        await coordinator.run_server()

    except Exception as e:
        logger.error(f"Ошибка координатора: {e}")
        raise


async def run_worker(node_id: str, port: int, bind_address: str,
                     coordinator_addresses: List[str], redis_url: str):
    """Запуск рабочего узла"""
    logger = logging.getLogger("Worker")

    worker = P2PAdminSystem(
        node_id=node_id,
        port=port,
        bind_address=bind_address,
        coordinator_mode=False,
        redis_url=redis_url
    )

    try:
        await worker.start(join_addresses=coordinator_addresses)

        logger.info("Доступные endpoints:")
        logger.info(f"  Health: http://{bind_address}:{port}/health")
        logger.info(f"  API Docs: http://{bind_address}:{port}/docs")

        await worker.run_server()

    except Exception as e:
        logger.error(f"Ошибка рабочего узла: {e}")
        raise

def create_argument_parser():
    """Создание парсера аргументов командной строки"""
    parser = argparse.ArgumentParser(
        description="P2P Administrative System - Distributed service computing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  %(prog)s coordinator                     # Координатор на порту 8001
  %(prog)s coordinator --port 9001        # Координатор на порту 9001
  %(prog)s worker                          # Рабочий узел на порту 8002
  %(prog)s worker --port 9002 --coord 127.0.0.1:9001


Документация API:
  После запуска координатора откройте http://127.0.0.1:8001/docs
        """
    )

    parser.add_argument(
        'mode',
        choices=['coordinator', 'worker'],
        help='Режим запуска системы'
    )

    parser.add_argument(
        '--node-id',
        default=None,
        help='Идентификатор узла (автогенерация по умолчанию)'
    )

    parser.add_argument(
        '--port',
        type=int,
        default=None,
        help='Порт HTTP сервера (coordinator: 8001, worker: 8002+)'
    )

    parser.add_argument(
        '--address',
        default='127.0.0.1',
        help='Адрес привязки сервера (по умолчанию: 127.0.0.1)'
    )

    parser.add_argument(
        '--coord', '--coordinator',
        default='127.0.0.1:8001',
        help='Адрес координатора для подключения'
    )

    parser.add_argument(
        '--redis-url',
        default='redis://localhost:6379',
        help='URL Redis для кеширования'
    )

    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Подробный вывод и отладочные логи'
    )

    return parser


async def graceful_shutdown():
    """Graceful shutdown всех активных систем"""
    logger = logging.getLogger("Shutdown")

    if active_systems:
        logger.info(f"Graceful shutdown {len(active_systems)} активных систем...")

        # Останавливаем все системы параллельно
        shutdown_tasks = [system.stop() for system in active_systems.copy()]
        await asyncio.gather(*shutdown_tasks, return_exceptions=True)

        logger.info("Все системы остановлены")


async def main():
    """Главная функция приложения"""
    parser = create_argument_parser()
    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger("Main")

    # Настройка обработчиков сигналов
    setup_signal_handlers()

    try:
        if args.mode == 'coordinator':
            node_id = args.node_id or f"coordinator-{os.getpid()}"
            port = args.port or 8001

            logger.info(f"Запуск координатора: {node_id} на {args.address}:{port}")
            await run_coordinator(
                node_id=node_id,
                port=port,
                bind_address=args.address,
                redis_url=args.redis_url
            )

        elif args.mode == 'worker':
            node_id = args.node_id or f"worker-{os.getpid()}"
            port = args.port or 8002

            logger.info(f"Запуск рабочего узла: {node_id} на {args.address}:{port}")
            await run_worker(
                node_id=node_id,
                port=port,
                bind_address=args.address,
                coordinator_addresses=[args.coord],
                redis_url=args.redis_url
            )

    except KeyboardInterrupt:
        logger.info("Программа прервана пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        if args.verbose:
            import traceback
            logger.error(traceback.format_exc())
        return 1
    finally:
        # Graceful shutdown
        await graceful_shutdown()

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


def print_banner():
    """Вывод баннера приложения"""
    banner = """
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║              P2P Administrative System v1.0                  ║
║                                                              ║
║              Distributed service computing                   ║
║              Booting...                                      ║
║                                                              ║
║                                                              ║
║                                                              ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""
    print(banner)


if __name__ == "__main__":
    # Проверки системы
    if not check_python_version():
        sys.exit(1)

    if not check_dependencies():
        sys.exit(1)

    # Показать баннер только если не передан аргумент --help
    if len(sys.argv) == 1 or (len(sys.argv) == 2 and sys.argv[1] not in ['-h', '--help']):
        print_banner()

    # Запуск главной функции
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nStopped")
        sys.exit(0)
    except Exception as e:
        print(f"Фатальная ошибка: {e}")
        sys.exit(1)