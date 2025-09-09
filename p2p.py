import asyncio
import os
import random
import socket
import sys
import signal
import argparse
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
import uvicorn
import httpx
from pathlib import Path

# Добавляем текущую директорию в путь для импортов
sys.path.insert(0, str(Path(__file__).parent))
# from layers.local_service_layer import P2PServiceBridge, create_enhanced_universal_client
from layers.local_service_bridge import create_local_service_bridge
from layers.service_framework import ServiceManager

try:
    from layers.transport import P2PTransportLayer, TransportConfig
    from layers.network import P2PNetworkLayer
    from layers.service import P2PServiceLayer, P2PServiceClient, RPCMethods, method_registry
    from layers.cache import P2PMultiLevelCache, CacheConfig
    from methods.system import SystemMethods
except ImportError as e:
    print(f"❌ Ошибка импорта модулей: {e}")
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
        print(f"\n🛑 Получен сигнал {signal_name}, начинаем graceful shutdown...")
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
        self.service_bridge = None

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

        # Регистрация административных методов
        # self._setup_admin_methods()

        # Добавляем систему в глобальный список для graceful shutdown
        active_systems.append(self)
        # TODO: add register methods

    async def _setup_admin_methods(self):
        try:
            # Создаем экземпляры методов
            from methods.system import SystemMethods
            system_methods = SystemMethods(self.cache)

            # Привязка кэша к методам с декораторами
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
                # Проверяем, является ли метод декорированным функцией кеширования
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


# Устаревший клиент
# class P2PClient:
#     """Облегченный P2P клиент с ИСПРАВЛЕННЫМ таргетингом узлов"""
#
#     def __init__(self, client_id: str = "p2p-client"):
#         self.client_id = client_id
#         self.logger = logging.getLogger(f"P2PClient.{client_id}")
#
#         # Создаем только транспортный уровень
#         transport_config = TransportConfig()
#         transport_config.connect_timeout = 15.0
#         transport_config.read_timeout = 90.0
#         self.transport = P2PTransportLayer(transport_config)
#
#         self.connected_nodes = []
#         self.token = None
#
#     async def connect(self, coordinator_addresses: List[str]):
#         """Подключение к кластеру через прямые HTTP вызовы"""
#         self.logger.info(f"Подключение к кластеру...")
#
#         for coord_addr in coordinator_addresses:
#             try:
#                 coord_host, coord_port = coord_addr.split(':')
#                 health_url = f"http://{coord_host}:{coord_port}/health"
#
#                 async with httpx.AsyncClient(timeout=10.0) as client:
#                     response = await client.get(health_url)
#                     if response.status_code == 200:
#                         self.connected_nodes.append(coord_addr)
#                         self.logger.info(f"✅ Подключен к координатору: {coord_addr}")
#                         break
#             except Exception as e:
#                 self.logger.warning(f"❌ Не удалось подключиться к {coord_addr}: {e}")
#
#         if not self.connected_nodes:
#             raise RuntimeError("Не удалось подключиться ни к одному координатору")
#
#     async def authenticate(self):
#         """Получение токена аутентификации"""
#         if not self.connected_nodes:
#             raise RuntimeError("Не подключен к кластеру")
#
#         coord_addr = self.connected_nodes[0]
#         coord_host, coord_port = coord_addr.split(':')
#         token_url = f"http://{coord_host}:{coord_port}/auth/token"
#
#         async with httpx.AsyncClient(timeout=10.0) as client:
#             response = await client.post(
#                 token_url,
#                 json={"node_id": self.client_id}
#             )
#
#             if response.status_code != 200:
#                 raise RuntimeError(f"Ошибка аутентификации: {response.status_code}")
#
#             data = response.json()
#             self.token = data["access_token"]
#             self.logger.info("✅ Токен аутентификации получен")
#
#     async def _get_available_nodes(self) -> List[Dict[str, Any]]:
#         """Получение списка доступных узлов"""
#         coord_addr = self.connected_nodes[0]
#         coord_host, coord_port = coord_addr.split(':')
#         nodes_url = f"http://{coord_host}:{coord_port}/cluster/nodes"
#
#         headers = {"Authorization": f"Bearer {self.token}"}
#
#         async with httpx.AsyncClient(timeout=30.0) as client:
#             nodes_response = await client.get(nodes_url, headers=headers)
#             if nodes_response.status_code != 200:
#                 raise RuntimeError(f"Не удалось получить список узлов: {nodes_response.status_code}")
#
#             nodes_data = nodes_response.json()
#             return [
#                 node for node in nodes_data["nodes"]
#                 if node["status"] == "alive" and node["port"] > 0
#             ]
#
#     async def _select_target_node(self, target_node_name: str = None, target_role: str = None) -> Dict[str, Any]:
#         """ИСПРАВЛЕННЫЙ выбор целевого узла"""
#         available_nodes = await self._get_available_nodes()
#
#         print(f"🎯 Node selection:")
#         print(f"   Available nodes: {[n['node_id'] for n in available_nodes]}")
#         print(f"   Target node name: {target_node_name}")
#         print(f"   Target role: {target_role}")
#
#         # Если указано конкретное имя узла
#         if target_node_name:
#             # Ищем точное совпадение
#             exact_match = [node for node in available_nodes if node["node_id"] == target_node_name]
#             if exact_match:
#                 print(f"   → Found exact match: {exact_match[0]['node_id']}")
#                 return exact_match[0]
#
#             # Ищем частичное совпадение (например "coordinator" найдет "coordinator-12345")
#             partial_matches = [
#                 node for node in available_nodes
#                 if target_node_name.lower() in node["node_id"].lower()
#             ]
#             if partial_matches:
#                 print(f"   → Found partial match: {partial_matches[0]['node_id']}")
#                 return partial_matches[0]
#
#             # Ищем по роли, если имя похоже на роль
#             if target_node_name.lower() in ['coordinator', 'worker']:
#                 role_matches = [
#                     node for node in available_nodes
#                     if node["role"] == target_node_name.lower()
#                 ]
#                 if role_matches:
#                     print(f"   → Found by role: {role_matches[0]['node_id']}")
#                     return role_matches[0]
#
#             raise RuntimeError(f"Node '{target_node_name}' not found")
#
#         # Если указана роль
#         if target_role:
#             role_nodes = [node for node in available_nodes if node["role"] == target_role]
#             if role_nodes:
#                 print(f"   → Selected by role: {role_nodes[0]['node_id']}")
#                 return role_nodes[0]
#
#         # По умолчанию - первый доступный
#         if available_nodes:
#             print(f"   → Default selection: {available_nodes[0]['node_id']}")
#             return available_nodes[0]
#
#         raise RuntimeError("No available nodes found")
#
#     async def rpc_call(self, method_path: str, params: dict = None, target_role: str = None, timeout: int = 90) -> dict:
#         """RPC вызов с отладкой"""
#
#         print(f"🔍 DEBUG RPC_CALL START:")
#         print(f"   method_path: {method_path}")
#         print(f"   params: {params}")
#         print(f"   target_role: {target_role}")
#
#         # Извлекаем target_node из параметров
#         target_node_name = params.pop('_target_node', None) if params else None
#         print(f"   extracted target_node_name: {target_node_name}")
#
#         # ВОТ ЗДЕСЬ ДОБАВЬТЕ ПРОВЕРКУ:
#         if target_node_name:
#             print(f"🎯 ATTEMPTING TO SELECT SPECIFIC NODE: {target_node_name}")
#         if not self.token:
#             raise RuntimeError("Не аутентифицирован")
#
#         if params is None:
#             params = {}
#
#         # Извлекаем target_node из параметров
#         target_node_name = params.pop('_target_node', None)
#         target_domain = params.pop('_target_domain', None)  # Пока не используем, но извлекаем
#
#         self.logger.debug(f"RPC Call: {method_path}")
#         self.logger.debug(f"Target node: {target_node_name}")
#         self.logger.debug(f"Target role: {target_role}")
#         self.logger.debug(f"Params: {params}")
#
#         try:
#             # Выбираем целевой узел
#             target_node = await self._select_target_node(target_node_name, target_role)
#
#             # Формируем URL
#             rpc_url = f"http://{target_node['address']}:{target_node['port']}/rpc/{method_path}"
#
#             print(f"📍 FINAL TARGET NODE: {target_node.get('node_id', 'UNKNOWN')}")
#             print(f"🌐 FORMING URL: http://{target_node['address']}:{target_node['port']}/rpc/{method_path}")
#
#             # Подготавливаем RPC payload
#             rpc_payload = {
#                 "method": method_path.split('/')[-1],  # Только имя метода!
#                 "params": params,
#                 "id": f"client_req_{datetime.now().timestamp()}"
#             }
#
#             headers = {"Authorization": f"Bearer {self.token}"}
#
#             print(f"🚀 RPC Call Details:")
#             print(f"   Target: {target_node['node_id']} ({target_node['role']})")
#             print(f"   URL: {rpc_url}")
#             print(f"   Method: {rpc_payload['method']}")
#
#             async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
#                 rpc_response = await client.post(rpc_url, json=rpc_payload, headers=headers)
#
#                 if rpc_response.status_code != 200:
#                     error_text = rpc_response.text
#                     self.logger.error(f"RPC failed: {rpc_response.status_code} - {error_text}")
#                     raise RuntimeError(f"RPC failed: {rpc_response.status_code} - {error_text}")
#
#                 result = rpc_response.json()
#
#                 if result.get("error"):
#                     raise RuntimeError(f"RPC error: {result['error']}")
#
#                 return result.get("result")
#
#         except Exception as e:
#             self.logger.error(f"RPC call failed: {e}")
#             raise RuntimeError(f"RPC call failed: {e}")
#
#     async def broadcast_call(self, method_path: str, params: dict = None, target_role: str = None) -> List[dict]:
#         """Широковещательный RPC вызов"""
#         if not self.token:
#             raise RuntimeError("Не аутентифицирован")
#
#         coord_addr = self.connected_nodes[0]
#         coord_host, coord_port = coord_addr.split(':')
#         broadcast_url = f"http://{coord_host}:{coord_port}/admin/broadcast"
#
#         headers = {"Authorization": f"Bearer {self.token}"}
#         broadcast_payload = {
#             "method": method_path,
#             "params": params or {},
#             "target_role": target_role
#         }
#
#         async with httpx.AsyncClient(timeout=90.0) as client:
#             response = await client.post(broadcast_url, json=broadcast_payload, headers=headers)
#
#             if response.status_code != 200:
#                 raise RuntimeError(f"Broadcast неудачен: {response.status_code}")
#
#             return response.json().get("results", [])
#
#     async def close(self):
#         """Закрытие клиента"""
#         await self.transport.close_all()
#         self.logger.info("P2P клиент закрыт")

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
        choices=['coordinator', 'worker', 'client', 'test'],
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
            node_id = args.node_id or f"worker-{socket.gethostname()}"
            # node_id = args.node_id or f"worker-{os.getpid()}"
            port = args.port or 8002

            logger.info(f"⚙️  Запуск рабочего узла: {node_id} на {args.address}:{port}")
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
        print("❌ Требуется Python 3.7 или новее")
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
        print("❌ Отсутствуют обязательные зависимости:")
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
        print(f"❌ Фатальная ошибка: {e}")
        sys.exit(1)