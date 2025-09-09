# layers/p2p_interface.py
# Прозрачный layer interface для взаимодействия с P2P системой через universal proxy

import asyncio
import logging
from typing import Any, Optional, Dict, List
from dataclasses import dataclass


# === ИСПРАВЛЕНИЕ JWT ИМПОРТА ===
# Замените в layers/service.py строку:
# import jwt
# на:
# import jwt as PyJWT
#
# Или установите правильную библиотеку:
# pip uninstall jwt
# pip install PyJWT
#
# И в коде используйте:
# token = PyJWT.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

@dataclass
class P2PConfig:
    """Конфигурация P2P интерфейса"""
    node_id: str
    project_name: str
    connect_timeout: float = 30.0
    retry_attempts: int = 3
    auto_reconnect: bool = True


class P2PInterface:
    """Прозрачный интерфейс для взаимодействия с P2P системой через universal proxy"""

    def __init__(self, admin_system, project_name: str, universal_proxy):
        self.admin_system = admin_system
        self.project_name = project_name
        self.universal = universal_proxy
        self.logger = logging.getLogger(f"P2PInterface.{project_name}")
        self._initialized = False

    async def start(self):
        """Инициализация интерфейса"""
        if self._initialized:
            return

        self.logger.info(f"Инициализация P2P интерфейса для проекта: {self.project_name}")
        self._initialized = True

    async def stop(self):
        """Остановка интерфейса"""
        if not self._initialized:
            return

        self.logger.info(f"Остановка P2P интерфейса для проекта: {self.project_name}")
        self._initialized = False

    # === Прямой доступ к universal proxy ===

    @property
    def network(self):
        """Прямой доступ к universal proxy для сетевых вызовов

        Использование:
            await p2p.network.system.coordinator.get_info()
            await p2p.network.certs.local_domain.install(cert_path)
            await p2p.network.docker.production.deploy(image)
        """
        return self.universal

    # === Удобные сокращения ===

    @property
    def system(self):
        """Системные вызовы: await p2p.system.coordinator.get_info()"""
        return self.universal.system

    @property
    def certs(self):
        """Управление сертификатами: await p2p.certs.local_domain.install(cert_path)"""
        return self.universal.certs

    @property
    def docker(self):
        """Docker операции: await p2p.docker.production.deploy(image)"""
        return self.universal.docker

    @property
    def services(self):
        """Управление сервисами: await p2p.services.worker.restart('nginx')"""
        return self.universal.services

    # === Информационные методы ===

    async def get_cluster_info(self) -> Dict[str, Any]:
        """Получение информации о кластере"""
        return await self.universal.system.coordinator.get_cluster_info()

    async def get_node_status(self, node_name: Optional[str] = None) -> Dict[str, Any]:
        """Получение статуса узла"""
        if node_name:
            return await getattr(self.universal.system, node_name).get_status()
        else:
            return await self.universal.system.get_status()

    async def list_available_services(self) -> List[str]:
        """Список доступных сервисов в кластере"""
        cluster_info = await self.get_cluster_info()
        return cluster_info.get('available_services', [])

    # === Вспомогательные методы ===

    def is_connected(self) -> bool:
        """Проверка соединения с кластером"""
        return self._initialized and hasattr(self.universal, '_client')

    async def ping_cluster(self) -> bool:
        """Проверка доступности кластера"""
        try:
            await self.universal.system.coordinator.ping()
            return True
        except Exception as e:
            self.logger.warning(f"Ping кластера не удался: {e}")
            return False


class P2PInterfaceFactory:
    """Фабрика для создания P2P интерфейсов"""

    @staticmethod
    async def create_interface(admin_system, project_name: str) -> P2PInterface:
        """Создание P2P интерфейса с автоматической настройкой"""

        # Создаем P2PClient для проекта
        from main import P2PClient, create_universal_client

        project_client = P2PClient(f"{admin_system.node_id}-{project_name}")

        # Определяем адрес подключения
        if admin_system.coordinator_mode:
            connect_address = f"{admin_system.bind_address}:{admin_system.port}"
        else:
            # Находим координатор через gossip
            coordinators = []
            if hasattr(admin_system, 'network') and hasattr(admin_system.network, 'gossip'):
                nodes = admin_system.network.gossip.nodes
                coordinators = [f"{node.host}:{node.port}" for node in nodes.values()
                                if node.role == "coordinator"]
            connect_address = coordinators[0] if coordinators else "127.0.0.1:8001"

        # Подключение и аутентификация
        await project_client.connect([connect_address])
        await project_client.authenticate()

        # Создание universal proxy
        universal_proxy = create_universal_client(project_client)

        # Создание и инициализация интерфейса
        interface = P2PInterface(admin_system, project_name, universal_proxy)
        await interface.start()

        return interface


# === Интеграция с P2PAdminSystem ===

class P2PAdminSystemExtended:
    """Расширение P2PAdminSystem с поддержкой проектных интерфейсов"""

    def __init__(self, admin_system):
        self.admin_system = admin_system
        self.project_interfaces: Dict[str, P2PInterface] = {}
        self.logger = logging.getLogger("P2PAdminSystemExtended")

    async def load_project_interface(self, project_name: str) -> P2PInterface:
        """Загрузка P2P интерфейса для проекта"""
        if project_name in self.project_interfaces:
            return self.project_interfaces[project_name]

        self.logger.info(f"Создание P2P интерфейса для проекта: {project_name}")

        interface = await P2PInterfaceFactory.create_interface(
            self.admin_system, project_name
        )

        self.project_interfaces[project_name] = interface
        return interface

    async def unload_project_interface(self, project_name: str):
        """Выгрузка P2P интерфейса проекта"""
        if project_name in self.project_interfaces:
            await self.project_interfaces[project_name].stop()
            del self.project_interfaces[project_name]
            self.logger.info(f"P2P интерфейс проекта {project_name} выгружен")

    def get_project_interface(self, project_name: str) -> Optional[P2PInterface]:
        """Получение P2P интерфейса проекта"""
        return self.project_interfaces.get(project_name)

    async def shutdown_all_interfaces(self):
        """Остановка всех проектных интерфейсов"""
        for project_name in list(self.project_interfaces.keys()):
            await self.unload_project_interface(project_name)


# === Диагностика доступных методов ===

async def diagnose_available_methods(p2p_client):
    """Диагностика доступных RPC методов в системе"""
    try:
        # Получаем информацию о зарегистрированных методах
        debug_info = await p2p_client.rpc_call("debug/registry", {})
        print("🔍 Доступные методы:", debug_info.get('registered_methods', []))
        return debug_info.get('registered_methods', [])
    except Exception as e:
        print(f"❌ Не удалось получить список методов: {e}")
        return []


# === Пример использования ===

async def example_usage():
    """Пример использования P2P Layer Interface с диагностикой"""

    # Предполагается, что admin_system уже создан и запущен
    from p2p_admin import P2PClient
    from universal_proxy import create_universal_client

    # Создаем тестовый клиент для диагностики
    client = P2PClient("diagnostic-client")
    await client.connect(["127.0.0.1:8001"])
    await client.authenticate()

    # Диагностируем доступные методы
    print("=== ДИАГНОСТИКА ДОСТУПНЫХ МЕТОДОВ ===")
    available_methods = await diagnose_available_methods(client)

    if not available_methods:
        print("⚠️  Попробуем стандартные методы...")
        # Попробуем стандартные методы
        test_methods = [
            "system/get_system_info",
            "system/status",
            "system/ping",
            "cluster/info",
            "node/status"
        ]

        for method in test_methods:
            try:
                result = await client.rpc_call(method, {})
                print(f"✅ {method} - работает")
                print(f"   Результат: {result}")
            except Exception as e:
                print(f"❌ {method} - не работает: {e}")

    # Создание universal proxy только если методы найдены
    if available_methods or True:  # Продолжаем тестирование
        universal = create_universal_client(client)

        print("\n=== ТЕСТИРОВАНИЕ UNIVERSAL PROXY ===")

        # Тестируем разные варианты вызовов
        test_calls = [
            ("universal.system.coordinator.get_system_info", lambda: universal.system.coordinator.get_system_info()),
            ("universal.system.get_system_info", lambda: universal.system.get_system_info()),
            ("universal.cluster.info", lambda: universal.cluster.info()),
            ("universal.node.status", lambda: universal.node.status()),
        ]

        for description, call_func in test_calls:
            try:
                print(f"\n🧪 Тестирую: {description}")
                result = await call_func()
                print(f"✅ Успех: {result}")
            except Exception as e:
                print(f"❌ Ошибка: {e}")

    await client.close()


# === Создание P2P интерфейса для реальной работы ===

async def create_working_interface():
    """Создание рабочего P2P интерфейса после диагностики"""

    try:
        from main import P2PAdminSystem
    except ImportError:
        from p2p_admin import P2PAdminSystem

    admin_system = P2PAdminSystem("worker", 8002, coordinator_mode=False)
    await admin_system.start(["127.0.0.1:8001"])

    # Создание расширенной системы
    extended_system = P2PAdminSystemExtended(admin_system)

    # Загрузка интерфейса для проекта
    p2p = await extended_system.load_project_interface("test_project")

    return p2p, extended_system

if __name__ == "__main__":
    asyncio.run(example_usage())