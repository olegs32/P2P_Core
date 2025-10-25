import asyncio
import logging
from typing import Set, Dict, List, Optional, Any
import json
import random
import time
from datetime import datetime, timedelta
import httpx
from dataclasses import dataclass, asdict, field

# LZ4 компрессия для gossip сообщений
try:
    import lz4.frame
    LZ4_AVAILABLE = True
except ImportError:
    LZ4_AVAILABLE = False
    logging.getLogger("Gossip").warning("LZ4 not available, compression disabled")


@dataclass
class NodeInfo:
    """Информация об узле P2P сети"""
    node_id: str
    address: str
    port: int
    role: str
    capabilities: List[str]
    last_seen: datetime
    metadata: Dict[str, Any]
    status: str = "alive"  # alive, suspected, dead
    services: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['last_seen'] = self.last_seen.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'NodeInfo':
        data['last_seen'] = datetime.fromisoformat(data['last_seen'])
        return cls(**data)

    def get_url(self) -> str:
        """Получение URL узла"""
        return f"http://{self.address}:{self.port}"

    def is_alive(self, timeout_seconds: int = 60) -> bool:
        """Проверка, жив ли узел"""
        return (datetime.now() - self.last_seen).total_seconds() < timeout_seconds


class ConnectionManager:
    """Менеджер подключений с пулингом"""

    def __init__(self, max_connections: int = 100, max_keepalive: int = 20,
                 ssl_verify: bool = True, ca_cert_file: str = None):
        self.clients: Dict[str, httpx.AsyncClient] = {}
        self.limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive
        )
        self._lock = asyncio.Lock()
        self.ssl_verify = ssl_verify
        self.ca_cert_file = ca_cert_file

    async def get_client(self, base_url: str) -> httpx.AsyncClient:
        """Получить клиент для базового URL с переиспользованием соединений"""
        async with self._lock:
            if base_url not in self.clients:
                # Настройка SSL верификации
                if self.ssl_verify and self.ca_cert_file:
                    from pathlib import Path
                    if Path(self.ca_cert_file).exists():
                        verify = self.ca_cert_file
                    else:
                        verify = True
                elif self.ssl_verify:
                    verify = True
                else:
                    verify = False

                self.clients[base_url] = httpx.AsyncClient(
                    base_url=base_url,
                    limits=self.limits,
                    timeout=httpx.Timeout(30.0),
                    verify=verify
                )
            return self.clients[base_url]

    async def close_all(self):
        """Закрыть все соединения"""
        for client in self.clients.values():
            await client.aclose()
        self.clients.clear()


# ConnectionManager теперь создается как атрибут P2PNetworkLayer с SSL параметрами
# connection_manager = ConnectionManager()  # deprecated - не используется


class SimpleGossipProtocol:
    """Упрощенная реализация gossip протокола без внешних зависимостей"""

    def __init__(self, node_id: str, bind_address: str, bind_port: int, coordinator_mode: bool = False):
        self.node_id = node_id
        self.bind_address = bind_address
        self.bind_port = bind_port
        self.coordinator_mode = coordinator_mode

        # Состояние узла
        self.node_registry: Dict[str, NodeInfo] = {}
        self.listeners = []
        self.running = False
        self.service_info_callback = None

        # Информация о собственном узле
        self.self_info = NodeInfo(
            node_id=node_id,
            address=bind_address,
            port=bind_port,
            role='coordinator' if coordinator_mode else 'worker',
            capabilities=['admin', 'rpc'],
            last_seen=datetime.now(),
            metadata={
                'started_at': datetime.now().isoformat(),
                'version': '1.0.0'
            }
        )

        # HTTP клиент для общения с другими узлами
        self.http_client = None

        # Известные координаторы
        self.bootstrap_nodes: List[str] = []

        # Параметры gossip протокола
        self.gossip_interval = 10  # секунд (будет переопределен из config)
        self.gossip_interval_min = 5  # минимальный интервал при низкой нагрузке
        self.gossip_interval_max = 30  # максимальный интервал при высокой нагрузке
        self.failure_timeout = 30  # секунд
        self.cleanup_interval = 60  # секунд
        self.max_gossip_targets = 5  # максимум узлов для gossip за раз

        # Adaptive gossip interval
        self.message_count = 0  # количество сообщений за интервал
        self.last_interval_adjust = time.time()
        self.adjust_interval_period = 60  # период адаптации (секунды)

        # LZ4 компрессия
        self.compression_enabled = True
        self.compression_threshold = 1024  # байты

        self.log = logging.getLogger('Gossip')

    async def start(self, join_addresses: List[str] = None, ssl_verify: bool = True, ca_cert_file: str = None):
        """
        Запуск gossip узла

        Args:
            join_addresses: адреса bootstrap узлов
            ssl_verify: верифицировать ли SSL сертификаты
            ca_cert_file: путь к CA сертификату для верификации
        """
        self.running = True

        # Инициализация HTTP клиента с SSL поддержкой
        timeout = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

        # Настройка SSL верификации
        if ssl_verify and ca_cert_file:
            # Верификация через CA
            from pathlib import Path
            if Path(ca_cert_file).exists():
                # httpx принимает путь к CA файлу в параметре verify
                self.http_client = httpx.AsyncClient(timeout=timeout, verify=ca_cert_file)
                self.log.info(f"HTTPS client configured with CA verification: {ca_cert_file}")
            else:
                self.log.warning(f"CA cert file not found: {ca_cert_file}, using default verification")
                self.http_client = httpx.AsyncClient(timeout=timeout, verify=True)
        elif ssl_verify:
            # Стандартная верификация (системные CA)
            self.http_client = httpx.AsyncClient(timeout=timeout, verify=True)
            self.log.info("HTTPS client configured with system CA verification")
        else:
            # Отключаем верификацию (не рекомендуется)
            self.http_client = httpx.AsyncClient(timeout=timeout, verify=False)
            self.log.warning("HTTPS client configured WITHOUT verification (insecure)")

        # Добавление себя в реестр
        self.node_registry[self.node_id] = self.self_info

        # Подключение к bootstrap узлам
        if join_addresses:
            self.bootstrap_nodes.extend(join_addresses)
            await self._join_cluster()

        # Запуск фоновых задач
        asyncio.create_task(self._gossip_loop())
        asyncio.create_task(self._failure_detection_loop())
        asyncio.create_task(self._cleanup_loop())

        self.log.info(f"Gossip node started: {self.node_id} on {self.bind_address}:{self.bind_port}")
        self.log.info(f"Role: {'Coordinator' if self.coordinator_mode else 'Worker'}")

    async def _join_cluster(self):
        """Присоединение к существующему кластеру"""
        for bootstrap_addr in self.bootstrap_nodes:
            try:
                # Парсинг адреса
                if ':' in bootstrap_addr:
                    host, port = bootstrap_addr.split(':')
                    port = int(port)
                else:
                    host, port = bootstrap_addr, 8000

                # Отправка запроса на присоединение (используем https если включена верификация)
                protocol = "https" if hasattr(self, 'http_client') and self.http_client.verify else "http"
                url = f"{protocol}://{host}:{port}/internal/gossip/join"
                join_data = {
                    'node_info': self.self_info.to_dict(),
                    'timestamp': datetime.now().isoformat()
                }

                response = await self.http_client.post(url, json=join_data)

                if response.status_code == 200:
                    # Получение списка известных узлов
                    cluster_info = response.json()
                    for node_data in cluster_info.get('nodes', []):
                        node_info = NodeInfo.from_dict(node_data)
                        if node_info.node_id != self.node_id:
                            self.node_registry[node_info.node_id] = node_info
                            await self._notify_listeners(node_info.node_id, 'alive', node_info)

                    self.log.info(f"✅ Successfully joined cluster via {bootstrap_addr}")
                    self.log.info(f"   Discovered {len(cluster_info.get('nodes', []))} nodes")
                    break

            except Exception as e:
                self.log.info(f"❌ Failed to join via {bootstrap_addr}: {e}")
                continue
        else:
            if self.bootstrap_nodes:
                self.log.info("⚠️  Could not join any bootstrap nodes, running in isolated mode")

    async def _gossip_loop(self):
        """Основной цикл gossip обмена"""
        while self.running:
            try:
                # Адаптивная регулировка интервала
                self._adjust_gossip_interval()

                await asyncio.sleep(self.gossip_interval)

                # Обновление времени собственного узла
                self.self_info.last_seen = datetime.now()
                await self._update_self_services_info()
                self.node_registry[self.node_id] = self.self_info

                # Выбор случайных узлов для gossip
                alive_nodes = [
                    node for node in self.node_registry.values()
                    if node.node_id != self.node_id and node.status == 'alive'
                ]

                if alive_nodes:
                    # Выбираем случайные узлы для обмена
                    gossip_targets = random.sample(
                        alive_nodes,
                        min(self.max_gossip_targets, len(alive_nodes))
                    )

                    # Отправляем gossip сообщения параллельно
                    tasks = [self._send_gossip_message(target) for target in gossip_targets]
                    await asyncio.gather(*tasks, return_exceptions=True)

                    # Увеличиваем счетчик сообщений для адаптации
                    self.message_count += len(gossip_targets)

            except Exception as e:
                self.log.info(f"❌ Error in gossip loop: {e}")

    def _compress_data(self, data: dict) -> tuple[bytes, bool]:
        """
        Сжать данные используя LZ4
        Returns: (compressed_data, is_compressed)
        """
        json_data = json.dumps(data).encode('utf-8')

        # Проверяем нужна ли компрессия
        if not self.compression_enabled or not LZ4_AVAILABLE:
            return json_data, False

        # Сжимаем только если данные больше порога
        if len(json_data) < self.compression_threshold:
            return json_data, False

        try:
            compressed = lz4.frame.compress(json_data)
            # Проверяем что сжатие дало выигрыш
            if len(compressed) < len(json_data):
                compression_ratio = len(compressed) / len(json_data)
                self.log.debug(
                    f"Compressed gossip: {len(json_data)} -> {len(compressed)} bytes "
                    f"(ratio: {compression_ratio:.2f})"
                )
                return compressed, True
            else:
                return json_data, False
        except Exception as e:
            self.log.warning(f"Compression failed: {e}")
            return json_data, False

    def _decompress_data(self, data: bytes, is_compressed: bool) -> dict:
        """
        Декомпрессировать данные
        """
        try:
            if is_compressed and LZ4_AVAILABLE:
                decompressed = lz4.frame.decompress(data)
                return json.loads(decompressed.decode('utf-8'))
            else:
                return json.loads(data.decode('utf-8'))
        except Exception as e:
            self.log.error(f"Decompression failed: {e}")
            raise

    def _adjust_gossip_interval(self):
        """
        Адаптивная регулировка интервала gossip на основе нагрузки
        Высокая нагрузка = больший интервал
        Низкая нагрузка = меньший интервал
        """
        now = time.time()
        elapsed = now - self.last_interval_adjust

        # Регулируем интервал раз в минуту
        if elapsed < self.adjust_interval_period:
            return

        # Вычисляем нагрузку (сообщений в секунду)
        messages_per_second = self.message_count / elapsed

        # Определяем новый интервал на основе нагрузки
        # Низкая нагрузка (<1 msg/s) -> минимальный интервал
        # Средняя нагрузка (1-5 msg/s) -> средний интервал
        # Высокая нагрузка (>5 msg/s) -> максимальный интервал

        if messages_per_second < 1:
            target_interval = self.gossip_interval_min
        elif messages_per_second < 5:
            # Линейная интерполяция между min и max
            ratio = (messages_per_second - 1) / 4  # 0 to 1
            target_interval = self.gossip_interval_min + \
                (self.gossip_interval_max - self.gossip_interval_min) * ratio
        else:
            target_interval = self.gossip_interval_max

        # Плавная адаптация (изменяем не более чем на 20% за раз)
        if target_interval > self.gossip_interval:
            self.gossip_interval = min(
                target_interval,
                self.gossip_interval * 1.2
            )
        elif target_interval < self.gossip_interval:
            self.gossip_interval = max(
                target_interval,
                self.gossip_interval * 0.8
            )

        self.log.info(
            f"Adaptive gossip: {messages_per_second:.2f} msg/s -> "
            f"interval={self.gossip_interval:.1f}s "
            f"(range: {self.gossip_interval_min}-{self.gossip_interval_max}s)"
        )

        # Сброс счетчиков
        self.message_count = 0
        self.last_interval_adjust = now

    async def _send_gossip_message(self, target_node: NodeInfo):
        """Отправка gossip сообщения узлу"""
        try:
            # Подготовка данных для отправки
            gossip_data = {
                'sender_id': self.node_id,
                'nodes': [node.to_dict() for node in self.node_registry.values()],
                'timestamp': datetime.now().isoformat(),
                'message_type': 'gossip'
            }

            url = f"{target_node.get_url()}/internal/gossip/exchange"
            response = await self.http_client.post(url, json=gossip_data, timeout=5.0)

            if response.status_code == 200:
                # Обработка ответа
                response_data = response.json()
                await self._process_gossip_response(response_data)

                # Обновление времени последнего контакта
                target_node.last_seen = datetime.now()
                if target_node.status != 'alive':
                    target_node.status = 'alive'
                    await self._notify_listeners(target_node.node_id, 'alive', target_node)
            else:
                self.log.info(f"⚠️  Gossip failed to {target_node.node_id}: HTTP {response.status_code}")
                target_node.status = 'suspected'

        except Exception as e:
            self.log.info(f"❌ Failed to send gossip to {target_node.node_id}: {e}")
            # Пометка узла как подозрительного
            if target_node.status == 'alive':
                target_node.status = 'suspected'

    async def _process_gossip_response(self, gossip_data: Dict):
        """Обработка ответа на gossip сообщение"""
        try:
            # Обновление информации об узлах
            for node_data in gossip_data.get('nodes', []):
                node_info = NodeInfo.from_dict(node_data)

                if node_info.node_id == self.node_id:
                    continue

                existing_node = self.node_registry.get(node_info.node_id)

                if not existing_node:
                    # Новый узел
                    self.node_registry[node_info.node_id] = node_info
                    await self._notify_listeners(node_info.node_id, 'alive', node_info)
                    self.log.info(f"🆕 Discovered new node: {node_info.node_id} ({node_info.role})")

                elif existing_node.last_seen < node_info.last_seen:
                    # Обновление информации об узле
                    old_status = existing_node.status
                    self.node_registry[node_info.node_id] = node_info

                    if old_status != node_info.status:
                        await self._notify_listeners(node_info.node_id, node_info.status, node_info)

        except Exception as e:
            self.log.info(f"❌ Error processing gossip response: {e}")

    async def _failure_detection_loop(self):
        """Цикл обнаружения отказов узлов"""
        while self.running:
            try:
                await asyncio.sleep(self.failure_timeout // 3)  # Проверяем чаще чем таймаут

                now = datetime.now()

                for node_id, node_info in list(self.node_registry.items()):
                    if node_id == self.node_id:
                        continue

                    time_since_seen = (now - node_info.last_seen).total_seconds()

                    if time_since_seen > self.failure_timeout:
                        if node_info.status != 'dead':
                            old_status = node_info.status
                            node_info.status = 'dead'
                            await self._notify_listeners(node_id, 'dead', node_info)
                            self.log.info(f"💀 Node marked as dead: {node_id} (last seen {time_since_seen:.1f}s ago)")

                    elif time_since_seen > self.failure_timeout // 2:
                        if node_info.status == 'alive':
                            node_info.status = 'suspected'
                            self.log.info(f"🤔 Node suspected: {node_id}")

            except Exception as e:
                self.log.info(f"❌ Error in failure detection: {e}")

    async def _cleanup_loop(self):
        """Цикл очистки мертвых узлов"""
        while self.running:
            try:
                await asyncio.sleep(self.cleanup_interval)

                now = datetime.now()
                nodes_to_remove = []

                for node_id, node_info in self.node_registry.items():
                    if node_id == self.node_id:
                        continue

                    if (node_info.status == 'dead' and
                            (now - node_info.last_seen).total_seconds() > self.cleanup_interval * 2):
                        nodes_to_remove.append(node_id)

                # Удаление мертвых узлов
                for node_id in nodes_to_remove:
                    del self.node_registry[node_id]
                    self.log.info(f"🗑️  Removed dead node from registry: {node_id}")

            except Exception as e:
                self.log.info(f"❌ Error in cleanup loop: {e}")

    async def _notify_listeners(self, node_id: str, status: str, node_info: NodeInfo):
        """Уведомление слушателей о событиях узлов"""
        for listener in self.listeners:
            try:
                await listener(node_id, status, node_info)
            except Exception as e:
                self.log.info(f"❌ Error notifying listener: {e}")

    def add_discovery_listener(self, listener):
        """Добавление слушателя событий обнаружения"""
        self.listeners.append(listener)

    def get_live_nodes(self) -> List[NodeInfo]:
        """Получение списка активных узлов"""
        return [
            node for node in self.node_registry.values()
            if node.status == 'alive'
        ]

    def get_coordinators(self) -> List[NodeInfo]:
        """Получение списка координаторов"""
        return [
            node for node in self.get_live_nodes()
            if node.role == 'coordinator'
        ]

    def get_workers(self) -> List[NodeInfo]:
        """Получение списка рабочих узлов"""
        return [
            node for node in self.get_live_nodes()
            if node.role == 'worker'
        ]

    def get_suspected_nodes(self) -> List[NodeInfo]:
        """Получение списка подозрительных узлов"""
        return [
            node for node in self.node_registry.values()
            if node.status == 'suspected'
        ]

    def get_dead_nodes(self) -> List[NodeInfo]:
        """Получение списка мертвых узлов"""
        return [
            node for node in self.node_registry.values()
            if node.status == 'dead'
        ]

    def find_nodes_with_service(self, service_name: str) -> List[NodeInfo]:
        """Найти узлы с определенным сервисом"""
        nodes = []
        for node in self.get_live_nodes():
            if service_name in node.services:
                nodes.append(node)
        return nodes

    def get_all_services_in_cluster(self) -> Dict[str, List[str]]:
        """Получить все сервисы в кластере с узлами где они запущены"""
        services = {}
        for node in self.get_live_nodes():
            for service_name in node.services:
                if service_name not in services:
                    services[service_name] = []
                services[service_name].append(node.node_id)
        return services

    async def handle_join_request(self, join_data: Dict) -> Dict:
        """Обработка запроса на присоединение нового узла"""
        try:
            node_info = NodeInfo.from_dict(join_data['node_info'])

            # Добавление нового узла в реестр
            self.node_registry[node_info.node_id] = node_info
            await self._notify_listeners(node_info.node_id, 'alive', node_info)

            self.log.info(f"🤝 Node joined: {node_info.node_id} from {node_info.address}:{node_info.port}")

            # Возврат текущего состояния кластера
            return {
                'status': 'success',
                'nodes': [node.to_dict() for node in self.node_registry.values()],
                'cluster_size': len(self.get_live_nodes()),
                'coordinators': len(self.get_coordinators()),
                'workers': len(self.get_workers())
            }

        except Exception as e:
            self.log.info(f"❌ Error handling join request: {e}")
            return {'status': 'error', 'message': str(e)}

    async def handle_gossip_exchange(self, gossip_data: Dict) -> Dict:
        """Обработка gossip обмена"""
        try:
            await self._process_gossip_response(gossip_data)

            # Возврат собственной информации об узлах
            return {
                'status': 'success',
                'nodes': [node.to_dict() for node in self.node_registry.values()],
                'timestamp': datetime.now().isoformat(),
                'sender': self.node_id
            }

        except Exception as e:
            self.log.info(f"❌ Error handling gossip exchange: {e}")
            return {'status': 'error', 'message': str(e)}

    def get_cluster_stats(self) -> Dict[str, Any]:
        """Получение статистики кластера"""
        live_nodes = self.get_live_nodes()
        suspected_nodes = self.get_suspected_nodes()
        dead_nodes = self.get_dead_nodes()

        return {
            'total_nodes': len(self.node_registry),
            'live_nodes': len(live_nodes),
            'suspected_nodes': len(suspected_nodes),
            'dead_nodes': len(dead_nodes),
            'coordinators': len(self.get_coordinators()),
            'workers': len(self.get_workers()),
            'self_role': self.self_info.role,
            'uptime': (datetime.now() -
                       datetime.fromisoformat(self.self_info.metadata['started_at'])).total_seconds()
        }

    async def stop(self):
        """Остановка gossip узла"""
        self.running = False
        if self.http_client:
            await self.http_client.aclose()
            self.log.info(f"🛑 Gossip node stopped: {self.node_id}")

    def set_service_info_provider(self, callback):
        """Установить callback для получения информации о сервисах"""
        self.service_info_callback = callback

    async def _update_self_services_info(self):
        """Обновить информацию о сервисах на текущем узле"""
        if self.service_info_callback:
            try:
                services_info = await self.service_info_callback()
                self.self_info.services = services_info
            except Exception as e:
                print(f"Error updating services info: {e}")


class P2PNetworkLayer:
    """Сетевой уровень с gossip протоколом и балансировкой нагрузки"""

    def __init__(self, transport_layer,
                 node_id: str, bind_address: str = "127.0.0.1", bind_port: int = 8000,
                 coordinator_mode: bool = False, ssl_verify: bool = True, ca_cert_file: str = None):
        self.log = logging.getLogger('Network')
        if bind_address == '0.0.0.0':
            self.advertise_address = self._get_local_ip()
        else:
            self.advertise_address = bind_address
        self.transport = transport_layer
        self.gossip = SimpleGossipProtocol(node_id, self.advertise_address, bind_port, coordinator_mode)
        self.load_balancer_index = 0

        # SSL параметры для HTTPS клиента
        self.ssl_verify = ssl_verify
        self.ca_cert_file = ca_cert_file

        # Connection manager с SSL поддержкой
        self.connection_manager = ConnectionManager(
            max_connections=100,
            max_keepalive=20,
            ssl_verify=ssl_verify,
            ca_cert_file=ca_cert_file
        )

        # Статистика запросов
        self.request_stats = {}
        self.request_history = []
        self.max_history_size = 1000

    def _get_local_ip(self):
        """Получить локальный IP для рекламы при bind 0.0.0.0"""
        import socket
        try:
            # Создаем временное соединение чтобы определить локальный IP
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))  # Не отправляет данные, просто определяет маршрут
                local_ip = s.getsockname()[0]
            return local_ip
        except Exception:
            # Fallback на получение IP через hostname
            try:
                return socket.gethostbyname(socket.gethostname())
            except Exception:
                return '127.0.0.1'  # Последний fallback

    async def start(self, join_addresses: List[str] = None):
        """Запуск сетевого уровня"""
        await self.gossip.start(join_addresses, ssl_verify=self.ssl_verify, ca_cert_file=self.ca_cert_file)

        # Добавление слушателя событий обнаружения
        self.gossip.add_discovery_listener(self._on_node_discovered)

    async def _on_node_discovered(self, node_id: str, status: str, node_info: NodeInfo):
        """Обработчик обнаружения новых узлов"""
        if status == 'alive':
            self.log.info(f"✅ Node discovered: {node_id} at {node_info.address}:{node_info.port} ({node_info.role})")
        elif status == 'dead':
            self.log.info(f"❌ Node lost: {node_id}")
            # Очистка статистики для потерянного узла
            self.request_stats.pop(node_id, None)
        elif status == 'suspected':
            self.log.info(f"🤔 Node suspected: {node_id}")

    def select_target_node(self, exclude_nodes: Set[str] = None,
                           prefer_role: str = None) -> Optional[NodeInfo]:
        """Выбор целевого узла для запроса с интеллектуальной балансировкой"""
        live_nodes = [
            node for node in self.gossip.get_live_nodes()
            if node.node_id != self.gossip.node_id and
               (not exclude_nodes or node.node_id not in exclude_nodes) and
               (not prefer_role or node.role == prefer_role)
        ]

        if not live_nodes:
            return None

        # Интеллектуальная балансировка на основе статистики
        if self.request_stats and len(live_nodes) > 1:
            # Выбор узла с наименьшим количеством активных запросов
            node_scores = []
            for node in live_nodes:
                request_count = self.request_stats.get(node.node_id, 0)
                node_scores.append((node, request_count))

            # Сортировка по количеству запросов (меньше = лучше)
            node_scores.sort(key=lambda x: x[1])
            selected_node = node_scores[0][0]
        else:
            # Round-robin балансировка для начального случая
            selected_node = live_nodes[self.load_balancer_index % len(live_nodes)]
            self.load_balancer_index += 1

        # Обновление статистики
        self.request_stats[selected_node.node_id] = \
            self.request_stats.get(selected_node.node_id, 0) + 1

        return selected_node

    async def execute_request(self, endpoint: str, data: Dict[str, Any],
                              headers: Dict[str, str],
                              max_retries: int = 3,
                              prefer_role: str = None) -> Any:
        """Выполнение запроса с автоматическими повторами и failover"""

        attempted_nodes = set()
        last_error = None
        request_id = f"req_{len(self.request_history)}"
        start_time = datetime.now()

        for attempt in range(max_retries):
            target_node = self.select_target_node(attempted_nodes, prefer_role)

            if not target_node:
                error_msg = f"No available nodes for request after {len(attempted_nodes)} attempts"
                if last_error:
                    error_msg += f". Last error: {last_error}"
                raise RuntimeError(error_msg)

            attempted_nodes.add(target_node.node_id)
            node_url = target_node.get_url()

            try:
                result = await self.transport.send_request(
                    node_url, endpoint, data, headers
                )

                # Успешный запрос - сброс счетчика ошибок для узла
                if target_node.node_id in self.request_stats:
                    self.request_stats[target_node.node_id] = max(0,
                                                                  self.request_stats[target_node.node_id] - 1)

                # Запись успешного запроса в историю
                self._record_request(request_id, target_node.node_id, endpoint,
                                     start_time, datetime.now(), True, None)

                return result

            except Exception as e:
                last_error = e
                self.log.info(f"❌ Request failed to {target_node.node_id}: {e}")

                # Увеличение штрафа для проблемного узла
                self.request_stats[target_node.node_id] = \
                    self.request_stats.get(target_node.node_id, 0) + 5

                if attempt == max_retries - 1:
                    # Запись неудачного запроса в историю
                    self._record_request(request_id, target_node.node_id, endpoint,
                                         start_time, datetime.now(), False, str(last_error))

                    raise RuntimeError(f"All {len(attempted_nodes)} nodes failed. Last error: {last_error}")

                continue

    def _record_request(self, request_id: str, target_node: str, endpoint: str,
                        start_time: datetime, end_time: datetime, success: bool, error: str):
        """Запись информации о запросе в историю"""
        request_record = {
            'id': request_id,
            'target_node': target_node,
            'endpoint': endpoint,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'duration_ms': (end_time - start_time).total_seconds() * 1000,
            'success': success,
            'error': error
        }

        self.request_history.append(request_record)

        # Ограничиваем размер истории
        if len(self.request_history) > self.max_history_size:
            self.request_history = self.request_history[-self.max_history_size:]

    async def broadcast_request(self, endpoint: str, data: Dict[str, Any],
                                headers: Dict[str, str],
                                target_role: str = None) -> List[Dict[str, Any]]:
        """Широковещательный запрос ко всем доступным узлам"""
        target_nodes = self.gossip.get_live_nodes()

        if target_role:
            target_nodes = [node for node in target_nodes if node.role == target_role]

        # Исключение собственного узла
        target_nodes = [node for node in target_nodes if node.node_id != self.gossip.node_id]

        if not target_nodes:
            return []

        self.log.info(f"📡 Broadcasting to {len(target_nodes)} nodes" +
                      (f" (role: {target_role})" if target_role else ""))

        results = []
        tasks = []

        for node in target_nodes:
            node_url = node.get_url()
            task = asyncio.create_task(
                self._safe_broadcast_request(node, node_url, endpoint, data, headers)
            )
            tasks.append(task)

        # Ждем завершения всех запросов с таймаутом
        try:
            completed_results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            self.log.info("⚠️  Broadcast timeout after 30 seconds")
            completed_results = [asyncio.TimeoutError("Broadcast timeout") for _ in tasks]

        for i, result in enumerate(completed_results):
            if not isinstance(result, Exception):
                results.append({
                    'node_id': target_nodes[i].node_id,
                    'result': result,
                    'success': True
                })
            else:
                results.append({
                    'node_id': target_nodes[i].node_id,
                    'error': str(result),
                    'success': False
                })

        success_count = len([r for r in results if r.get('success')])
        self.log.info(f"📊 Broadcast completed: {success_count}/{len(results)} successful")

        return results

    async def _safe_broadcast_request(self, node: NodeInfo, node_url: str,
                                      endpoint: str, data: Dict[str, Any],
                                      headers: Dict[str, str]):
        """Безопасный запрос для broadcast с переиспользованием соединений"""
        try:
            # Используем HTTPS если включен SSL
            protocol = "https" if self.ssl_verify else "http"
            client = await self.connection_manager.get_client(f"{protocol}://{node.address}:{node.port}")
            response = await client.post(endpoint, json=data, headers=headers)
            return response.json()
        except Exception as e:
            self.log.info(f"❌ Broadcast request failed to {node.node_id}: {e}")
            raise

    def get_cluster_status(self) -> Dict[str, Any]:
        """Получение статуса кластера"""
        gossip_stats = self.gossip.get_cluster_stats()

        # Статистика запросов
        total_requests = len(self.request_history)
        successful_requests = len([r for r in self.request_history if r['success']])

        avg_duration = 0
        if self.request_history:
            avg_duration = sum(r['duration_ms'] for r in self.request_history) / len(self.request_history)

        return {
            **gossip_stats,
            'request_stats': {
                'total_requests': total_requests,
                'successful_requests': successful_requests,
                'success_rate': successful_requests / total_requests if total_requests > 0 else 0,
                'average_duration_ms': avg_duration,
                'active_request_counts': dict(self.request_stats)
            },
            'network_health': {
                'live_node_ratio': gossip_stats['live_nodes'] / gossip_stats['total_nodes'] if gossip_stats[
                                                                                                   'total_nodes'] > 0 else 0,
                'coordinator_available': gossip_stats['coordinators'] > 0,
                'min_workers': gossip_stats['workers']
            }
        }

    def get_request_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Получение истории запросов"""
        return self.request_history[-limit:] if limit > 0 else self.request_history

    async def stop(self):
        """Остановка сетевого уровня"""
        await self.gossip.stop()
        await self.transport.close_all()
        await self.connection_manager.close_all()
        self.log.info(f"🛑 Network layer stopped")
