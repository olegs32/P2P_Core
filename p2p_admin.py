#!/usr/bin/env python3
"""
P2P Administrative System - Main Entry Point (FIXED VERSION)
Исправления:
- Клиент не регистрируется в gossip как сервер
- Увеличены таймауты для стабильности
- Улучшена обработка ошибок RPC
"""

import asyncio
import os
import random
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

# Импорты компонентов системы
try:
    from layers.transport import P2PTransportLayer, TransportConfig
    from layers.network import P2PNetworkLayer
    from layers.service import P2PServiceLayer, P2PServiceClient, register_rpc_methods
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

        # Регистрация административных методов
        self._setup_admin_methods()

        # Добавляем систему в глобальный список для graceful shutdown
        active_systems.append(self)

    def _setup_admin_methods(self):
        """Настройка административных методов"""
        try:
            # Создание экземпляров методов
            system_methods = SystemMethods(self.cache)

            # Привязка кеша к методам с декораторами
            self._bind_cache_to_methods(system_methods)

            # Регистрация методов для RPC
            register_rpc_methods("system", system_methods)

            self.logger.info("Зарегистрированы administrative методы: system")

        except Exception as e:
            self.logger.error(f"Ошибка при настройке административных методов: {e}")
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
            self.logger.info(f"Запуск P2P Admin System")
            self.logger.info(f"Node ID: {self.node_id}")
            self.logger.info(f"Address: {self.bind_address}:{self.port}")
            self.logger.info(f"Mode: {'Coordinator' if self.coordinator_mode else 'Worker'}")

            # Инициализация кеша (с fallback)
            self.logger.info("Инициализация системы кеширования...")
            await self.cache.setup_distributed_cache()
            await self.cache.setup_invalidation_listener()

            cache_type = 'Redis + Memory' if self.cache.redis_available else 'Memory Only'
            self.logger.info(f"Cache: {cache_type}")

            # Запуск сетевого уровня
            self.logger.info("Запуск сетевого уровня...")
            await self.network.start(join_addresses)

            if join_addresses:
                self.logger.info(f"Подключение к координаторам: {', '.join(join_addresses)}")

            # Увеличиваем паузу для стабилизации
            await asyncio.sleep(3)

            # Вывод статистики кластера
            status = self.network.get_cluster_status()
            self.logger.info(f"Статус кластера - Всего узлов: {status['total_nodes']}, "
                             f"Активных: {status['live_nodes']}, "
                             f"Координаторов: {status['coordinators']}, "
                             f"Рабочих: {status['workers']}")

            self.started = True
            self.logger.info("P2P Admin System успешно запущен!")

        except Exception as e:
            self.logger.error(f"Ошибка запуска P2P системы: {e}")
            raise

    async def stop(self):
        """Остановка системы"""
        if not self.started:
            return

        self.logger.info("Остановка P2P Admin System...")

        try:
            # Остановка сетевого уровня
            self.logger.debug("Остановка сетевого уровня...")
            await self.network.stop()

            # Закрытие кеша
            self.logger.debug("Закрытие системы кеширования...")
            await self.cache.close()

            self.started = False
            self.logger.info(f"P2P Admin System остановлен: {self.node_id}")

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

        # Конфигурация uvicorn
        config = uvicorn.Config(
            app=self.service_layer.app,
            host=self.bind_address,
            port=self.port,
            log_level="warning",  # Уменьшаем вербозность uvicorn
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
                except asyncio.CancelledError:
                    pass

        except Exception as e:
            self.logger.error(f"Ошибка сервера: {e}")
        finally:
            # Graceful shutdown
            await self.stop()


# Специальный клиент без HTTP сервера
class P2PClient:
    """Облегченный P2P клиент без HTTP сервера"""

    def __init__(self, client_id: str = "p2p-client"):
        self.node_index = 0
        self.client_id = client_id
        self.logger = logging.getLogger(f"P2PClient.{client_id}")

        # Создаем только транспортный уровень
        transport_config = TransportConfig()
        transport_config.connect_timeout = 15.0
        transport_config.read_timeout = 90.0  # Увеличиваем до 90 секунд
        self.transport = P2PTransportLayer(transport_config)

        self.connected_nodes = []
        self.token = None

    async def connect(self, coordinator_addresses: List[str]):
        """Подключение к кластеру через прямые HTTP вызовы"""
        self.logger.info(f"Подключение к кластеру...")

        for coord_addr in coordinator_addresses:
            try:
                # Проверяем доступность координатора
                coord_host, coord_port = coord_addr.split(':')
                health_url = f"http://{coord_host}:{coord_port}/health"

                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(health_url)
                    if response.status_code == 200:
                        self.connected_nodes.append(coord_addr)
                        self.logger.info(f"✅ Подключен к координатору: {coord_addr}")
                        break
            except Exception as e:
                self.logger.warning(f"❌ Не удалось подключиться к {coord_addr}: {e}")

        if not self.connected_nodes:
            raise RuntimeError("Не удалось подключиться ни к одному координатору")

    async def authenticate(self):
        """Получение токена аутентификации"""
        if not self.connected_nodes:
            raise RuntimeError("Не подключен к кластеру")

        coord_addr = self.connected_nodes[0]
        coord_host, coord_port = coord_addr.split(':')
        token_url = f"http://{coord_host}:{coord_port}/auth/token"

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                token_url,
                json={"node_id": self.client_id}
            )

            if response.status_code != 200:
                raise RuntimeError(f"Ошибка аутентификации: {response.status_code}")

            data = response.json()
            self.token = data["access_token"]
            self.logger.info("✅ Токен аутентификации получен")

    async def rpc_call(self, method_path: str, params: dict = None, target_role: str = None, timeout: int = 90) -> dict:
        """Выполнение RPC вызова с детальной отладкой"""
        if not self.token:
            raise RuntimeError("Не аутентифицирован")

        if params is None:
            params = {}

        self.logger.debug(f"Начинаем RPC вызов: {method_path} с параметрами: {params}")

        # Получаем список доступных узлов
        coord_addr = self.connected_nodes[0]
        coord_host, coord_port = coord_addr.split(':')
        nodes_url = f"http://{coord_host}:{coord_port}/cluster/nodes"

        headers = {"Authorization": f"Bearer {self.token}"}

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                # Получаем список узлов
                self.logger.debug(f"Получение списка узлов от {nodes_url}")
                nodes_response = await client.get(nodes_url, headers=headers)
                if nodes_response.status_code != 200:
                    raise RuntimeError(
                        f"Не удалось получить список узлов: {nodes_response.status_code} - {nodes_response.text}")

                nodes_data = nodes_response.json()
                self.logger.debug(f"Получено узлов: {len(nodes_data.get('nodes', []))}")

                available_nodes = [
                    node for node in nodes_data["nodes"]
                    if node["status"] == "alive" and
                       (not target_role or node["role"] == target_role) and
                       node["port"] > 0  # Исключаем клиентов
                ]

                self.logger.debug(f"Доступных узлов для RPC: {len(available_nodes)}")

                if not available_nodes:
                    raise RuntimeError(f"Нет доступных узлов для RPC вызова (роль: {target_role})")

                # Выбираем первый доступный узел
                target_node = random.choice(available_nodes)
                # target_node = available_nodes[self.node_index % len(available_nodes)]
                # self.node_index += 1
                rpc_url = f"http://{target_node['address']}:{target_node['port']}/rpc/{method_path}"

                # Выполняем RPC вызов
                rpc_payload = {
                    "method": method_path.split('/')[-1],
                    "params": params,
                    "id": f"client_req_{datetime.now().timestamp()}"
                }

                self.logger.debug(f"RPC вызов к {target_node['node_id']} ({rpc_url})")
                self.logger.debug(f"Payload: {rpc_payload}")

                # Увеличиваем таймаут для RPC вызова
                rpc_response = await client.post(
                    rpc_url,
                    json=rpc_payload,
                    headers=headers,
                    timeout=httpx.Timeout(timeout)
                )

                self.logger.debug(f"RPC ответ: статус {rpc_response.status_code}")

                if rpc_response.status_code != 200:
                    error_text = rpc_response.text
                    self.logger.error(f"RPC вызов неудачен: {rpc_response.status_code} - {error_text}")
                    raise RuntimeError(f"RPC вызов неудачен: {rpc_response.status_code} - {error_text}")

                result = rpc_response.json()
                self.logger.debug(f"RPC результат: {result}")

                if result.get("error"):
                    raise RuntimeError(f"RPC ошибка: {result['error']}")

                return result.get("result")

        except httpx.TimeoutException as e:
            self.logger.error(f"Таймаут RPC вызова после {timeout}с: {e}")
            raise RuntimeError(f"Таймаут RPC вызова после {timeout}с")
        except Exception as e:
            self.logger.error(f"Ошибка RPC вызова: {e}")
            raise RuntimeError(f"Ошибка RPC вызова: {e}")

    async def broadcast_call(self, method_path: str, params: dict = None, target_role: str = None) -> List[dict]:
        """Широковещательный RPC вызов"""
        if not self.token:
            raise RuntimeError("Не аутентифицирован")

        coord_addr = self.connected_nodes[0]
        coord_host, coord_port = coord_addr.split(':')
        broadcast_url = f"http://{coord_host}:{coord_port}/admin/broadcast"

        headers = {"Authorization": f"Bearer {self.token}"}
        broadcast_payload = {
            "method": method_path,
            "params": params or {},
            "target_role": target_role
        }

        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(broadcast_url, json=broadcast_payload, headers=headers)

            if response.status_code != 200:
                raise RuntimeError(f"Broadcast неудачен: {response.status_code}")

            return response.json().get("results", [])

    async def close(self):
        """Закрытие клиента"""
        await self.transport.close_all()
        self.logger.info("P2P клиент закрыт")

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

        # Показываем информацию о доступных endpoints
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

        # Показываем информацию о доступных endpoints
        logger.info("Доступные endpoints:")
        logger.info(f"  Health: http://{bind_address}:{port}/health")
        logger.info(f"  API Docs: http://{bind_address}:{port}/docs")

        await worker.run_server()

    except Exception as e:
        logger.error(f"Ошибка рабочего узла: {e}")
        raise


async def run_client_demo(coordinator_address: str, verbose: bool = False):
    """Исправленная демонстрация работы клиента"""
    logger = logging.getLogger("ClientDemo")

    logger.info("P2P Client Demo - подключение к кластеру...")

    # Используем новый облегченный клиент
    client = P2PClient("client-demo")

    try:
        # Подключение и аутентификация
        await client.connect([coordinator_address])
        await client.authenticate()

        print("\n" + "=" * 60)
        print("🔬 ДЕМОНСТРАЦИЯ RPC ВЫЗОВОВ P2P СИСТЕМЫ")
        print("=" * 60)

        # 1. Получение информации о системе
        print("\n📊 1. Получение информации о системе...")
        try:
            system_info = await client.rpc_call("system/get_system_info")
            print(f"   ✅ Hostname: {system_info.get('hostname')}")
            print(f"   ✅ Platform: {system_info.get('platform')}")
            print(f"   ✅ CPU Count: {system_info.get('cpu_count')}")
            print(f"   ✅ Memory: {system_info.get('memory_total', 0) // (1024 ** 3)} GB")
            print(f"   ✅ Architecture: {system_info.get('architecture')}")
        except Exception as e:
            print(f"   ❌ Ошибка: {e}")

        # 2. Получение метрик системы
        print("\n📈 2. Получение текущих метрик производительности...")
        try:
            metrics = await client.rpc_call("system/get_system_metrics")
            memory = metrics.get('memory', {})
            load_avg = metrics.get('load_average', [0, 0, 0])
            print(f"   ✅ CPU Usage: {metrics.get('cpu_percent', 0):.1f}%")
            print(f"   ✅ Memory Usage: {memory.get('percent', 0):.1f}%")
            print(f"   ✅ Available Memory: {memory.get('available', 0) // (1024 ** 2)} MB")
            print(f"   ✅ Process Count: {metrics.get('process_count', 0)}")
            print(f"   ✅ Load Average: {load_avg[0]:.2f}, {load_avg[1]:.2f}, {load_avg[2]:.2f}")
        except Exception as e:
            print(f"   ❌ Ошибка: {e}")

        # 3. Выполнение безопасной команды
        print("\n⚙️  3. Выполнение системной команды...")
        try:
            result = await client.rpc_call("system/execute_command", {
                "command": "ping -n 10 8.8.8.8", 'timeout': 15
            })
            if result.get('success'):
                output_lines = result.get('stdout', '').strip().split('\n')
                for line in output_lines:
                    if line.strip():
                        print(f"   ✅ {line}")
                print(f"   ✅ Exit Code: {result.get('return_code')}")
            else:
                print(f"   ❌ Команда не выполнена: {result.get('error')}")
        except Exception as e:
            print(f"   ❌ Ошибка: {e}")

        # 4. Тестирование кеширования
        print("\n💾 4. Тестирование системы кеширования...")
        try:
            # Первый вызов (без кеша)
            start_time = asyncio.get_event_loop().time()
            await client.rpc_call("system/get_system_info")
            first_call_time = asyncio.get_event_loop().time() - start_time

            # Второй вызов (с кешем)
            start_time = asyncio.get_event_loop().time()
            await client.rpc_call("system/get_system_info")
            second_call_time = asyncio.get_event_loop().time() - start_time

            print(f"   ✅ Первый вызов: {first_call_time * 1000:.1f}ms")
            print(f"   ✅ Второй вызов (кеш): {second_call_time * 1000:.1f}ms")
            speedup = first_call_time / second_call_time if second_call_time > 0 else 1
            print(f"   ✅ Ускорение: {speedup:.1f}x")
        except Exception as e:
            print(f"   ❌ Ошибка: {e}")

        # 5. Широковещательный запрос
        print("\n📡 5. Широковещательный запрос ко всем узлам кластера...")
        try:
            broadcast_results = await client.broadcast_call("system/get_system_metrics")
            successful = [r for r in broadcast_results if r.get('success')]
            failed = [r for r in broadcast_results if not r.get('success')]

            print(f"   ✅ Ответили узлов: {len(successful)}/{len(broadcast_results)}")

            if failed:
                print(f"   ⚠️  Неудачных запросов: {len(failed)}")

            for i, result in enumerate(successful[:3]):  # Показываем первые 3
                node_id = result.get('node_id')
                metrics = result.get('result', {})
                cpu_percent = metrics.get('cpu_percent', 'N/A')
                memory_percent = metrics.get('memory', {}).get('percent', 'N/A')
                process_count = metrics.get('process_count', 'N/A')
                print(f"   📊 {node_id}: CPU {cpu_percent}%, Memory {memory_percent}%, Processes {process_count}")

        except Exception as e:
            print(f"   ❌ Ошибка: {e}")

        # 6. Получение статуса кластера
        print("\n🏛️  6. Анализ состояния кластера...")
        try:
            coord_host, coord_port = coordinator_address.split(':')
            status_url = f"http://{coord_host}:{coord_port}/cluster/status"

            async with httpx.AsyncClient(timeout=10.0) as http_client:
                headers = {"Authorization": f"Bearer {client.token}"}
                status_response = await http_client.get(status_url, headers=headers)

                if status_response.status_code == 200:
                    cluster_status = status_response.json()
                    print(f"   ✅ Размер кластера: {cluster_status.get('cluster_size', 0)} узлов")
                    print(f"   ✅ Координаторов: {cluster_status.get('coordinators', 0)}")
                    print(f"   ✅ Рабочих узлов: {cluster_status.get('workers', 0)}")
                    print(f"   ✅ Время работы: {cluster_status.get('uptime', 0):.1f} сек")

                    req_stats = cluster_status.get('request_stats', {})
                    total_req = req_stats.get('total_requests', 0)
                    success_rate = req_stats.get('success_rate', 0)
                    avg_duration = req_stats.get('average_duration_ms', 0)

                    print(f"   ✅ Всего запросов: {total_req}")
                    print(f"   ✅ Успешность: {success_rate:.1%}")
                    print(f"   ✅ Среднее время ответа: {avg_duration:.1f}ms")

                    network_health = cluster_status.get('network_health', {})
                    live_ratio = network_health.get('live_node_ratio', 0)
                    print(f"   ✅ Здоровье сети: {live_ratio:.1%} узлов активны")
                else:
                    print(f"   ❌ Не удалось получить статус: {status_response.status_code}")

        except Exception as e:
            print(f"   ❌ Ошибка: {e}")

        print("\n" + "=" * 60)
        print("✅ ДЕМОНСТРАЦИЯ ЗАВЕРШЕНА УСПЕШНО!")
        print("=" * 60)
        print("\n💡 Советы:")
        print("   - Попробуйте API документацию: http://127.0.0.1:8001/docs")
        print("   - Мониторьте кластер: http://127.0.0.1:8001/cluster/status")
        print("   - Проверьте здоровье узлов: http://127.0.0.1:8001/health")

    except Exception as e:
        logger.error(f"Ошибка демонстрации клиента: {e}")
        raise
    finally:
        await client.close()


async def run_test_cluster():
    """Запуск тестового кластера для демонстрации"""
    logger = logging.getLogger("TestCluster")

    logger.info("Запуск тестового P2P кластера...")

    # Создание узлов
    coordinator = P2PAdminSystem("test-coordinator", 8001, coordinator_mode=True)
    worker1 = P2PAdminSystem("test-worker-1", 8002)
    worker2 = P2PAdminSystem("test-worker-2", 8003)

    try:
        # Запуск координатора
        logger.info("Запуск координатора...")
        await coordinator.start()
        await asyncio.sleep(5)  # Увеличенное время для полного запуска

        # Запуск рабочих узлов
        logger.info("Запуск рабочих узлов...")
        await worker1.start(join_addresses=["127.0.0.1:8001"])
        await asyncio.sleep(3)

        await worker2.start(join_addresses=["127.0.0.1:8001"])
        await asyncio.sleep(3)

        # Безопасное получение статистики кластера
        try:
            status = coordinator.network.get_cluster_status()
        except Exception as e:
            logger.warning(f"Не удалось получить статус кластера: {e}")
            status = {
                'cluster_size': 0,
                'coordinators': 0,
                'workers': 0,
                'uptime': 0,
                'request_stats': {},
                'network_health': {}
            }

        print("\n" + "=" * 50)
        print("🧪 ТЕСТОВЫЙ P2P КЛАСТЕР ЗАПУЩЕН")
        print("=" * 50)
        print(f"📊 Статистика кластера:")
        print(f"   Узлов в кластере: {status.get('cluster_size', 0)}")
        print(f"   Координаторов: {status.get('coordinators', 0)}")
        print(f"   Рабочих узлов: {status.get('workers', 0)}")
        print(f"   Время работы: {status.get('uptime', 0):.1f} сек")

        # Показываем endpoints
        print(f"\n🌐 Доступные endpoints:")
        print(f"   Координатор: http://127.0.0.1:8001")
        print(f"   Рабочий-1:  http://127.0.0.1:8002")
        print(f"   Рабочий-2:  http://127.0.0.1:8003")
        print(f"   API Docs:   http://127.0.0.1:8001/docs")

        # Ждем и мониторим
        print(f"\n⏳ Кластер работает 20 секунд...")
        for i in range(4):
            await asyncio.sleep(5)
            try:
                current_status = coordinator.network.get_cluster_status()
                req_stats = current_status.get('request_stats', {})
                print(f"   ⏱️  {(i + 1) * 5}с: {req_stats.get('total_requests', 0)} запросов, "
                      f"узлов: {current_status.get('live_nodes', 0)}")
            except Exception as e:
                print(f"   ⏱️  {(i + 1) * 5}с: Ошибка получения статуса - {e}")

        # Финальная статистика
        try:
            final_status = coordinator.network.get_cluster_status()
            req_stats = final_status.get('request_stats', {})
            network_health = final_status.get('network_health', {})

            print(f"\n📈 Финальная статистика:")
            print(f"   Внутренних запросов: {req_stats.get('total_requests', 0)}")
            print(f"   Успешность: {req_stats.get('success_rate', 0):.1%}")
            print(f"   Среднее время ответа: {req_stats.get('average_duration_ms', 0):.1f}ms")
            print(f"   Здоровье сети: {network_health.get('live_node_ratio', 0):.1%}")
        except Exception as e:
            print(f"\n📈 Не удалось получить финальную статистику: {e}")

        print("=" * 50)

    except Exception as e:
        logger.error(f"Ошибка тестового кластера: {e}")
        raise
    finally:
        logger.info("Остановка тестового кластера...")
        try:
            await coordinator.stop()
        except Exception as e:
            logger.error(f"Ошибка остановки координатора: {e}")

        try:
            await worker1.stop()
        except Exception as e:
            logger.error(f"Ошибка остановки worker1: {e}")

        try:
            await worker2.stop()
        except Exception as e:
            logger.error(f"Ошибка остановки worker2: {e}")

def create_argument_parser():
    """Создание парсера аргументов командной строки"""
    parser = argparse.ArgumentParser(
        description="P2P Administrative System - Distributed service administration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  %(prog)s coordinator                     # Координатор на порту 8001
  %(prog)s coordinator --port 9001        # Координатор на порту 9001
  %(prog)s worker                          # Рабочий узел на порту 8002
  %(prog)s worker --port 9002 --coord 127.0.0.1:9001
  %(prog)s client                          # Демо клиента
  %(prog)s test                           # Тестовый кластер

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

    # Настройка логирования
    setup_logging(args.verbose)
    logger = logging.getLogger("Main")

    # Настройка обработчиков сигналов
    setup_signal_handlers()

    try:
        if args.mode == 'coordinator':
            node_id = args.node_id or f"coordinator-{os.getpid()}"
            port = args.port or 8001

            logger.info(f"🎯 Запуск координатора: {node_id} на {args.address}:{port}")
            await run_coordinator(
                node_id=node_id,
                port=port,
                bind_address=args.address,
                redis_url=args.redis_url
            )

        elif args.mode == 'worker':
            node_id = args.node_id or f"worker-{os.getpid()}"
            port = args.port or 8002

            logger.info(f"⚙️  Запуск рабочего узла: {node_id} на {args.address}:{port}")
            await run_worker(
                node_id=node_id,
                port=port,
                bind_address=args.address,
                coordinator_addresses=[args.coord],
                redis_url=args.redis_url
            )

        elif args.mode == 'client':
            logger.info("🔗 Запуск демонстрации клиента...")
            await run_client_demo(
                coordinator_address=args.coord,
                verbose=args.verbose
            )

        elif args.mode == 'test':
            logger.info("🧪 Запуск тестового кластера...")
            await run_test_cluster()

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
        'cachetools', 'pydantic', 'jwt'
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
║           🚀 P2P Administrative System v1.0                  ║
║                                                              ║
║     Легковесная асинхронная система администрирования       ║
║              локальных сервисов в P2P сети                  ║
║                                                              ║
║                         ИСПРАВЛЕНО                          ║
║               ✅ Фиксы клиента и стабильности                ║
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
        print("\n👋 Программа завершена")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Фатальная ошибка: {e}")
        sys.exit(1)