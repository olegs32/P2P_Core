# integration.py - Интеграционные компоненты для существующей архитектуры

import asyncio
import logging
import time
from typing import Dict, Any, Optional
from pathlib import Path

# Интеграция с существующими компонентами
from layers.service_framework import ServiceManager, BaseService, service_method
from layers.universal_proxy import create_universal_client


class P2PServiceBridge:
    """Мост между P2P системой и сервисами"""

    def __init__(self, p2p_admin_system):
        self.p2p_system = p2p_admin_system
        self.service_manager = ServiceManager(p2p_admin_system.rpc)
        self.logger = logging.getLogger("P2PServiceBridge")
        self.proxy_client = None

    async def initialize(self):
        """Инициализация моста"""
        # Создаем universal client для сервисов
        from p2p_admin import P2PClient  # Используем существующий клиент

        # Создаем внутреннего клиента для сервисов
        internal_client = P2PClient("internal-service-client")
        time.sleep(5)

        # Подключаемся к собственному узлу
        node_address = f"{self.p2p_system.bind_address}:{self.p2p_system.port}"
        await internal_client.connect([node_address])
        await internal_client.authenticate()

        # Создаем universal proxy
        self.proxy_client = create_universal_client(internal_client)
        self.service_manager.set_proxy_client(self.proxy_client)

        # Инициализируем все сервисы
        await self.service_manager.initialize_all_services()

        self.logger.info("P2P Service Bridge initialized successfully")

    async def shutdown(self):
        """Остановка моста"""
        await self.service_manager.shutdown_all_services()
        if self.proxy_client and hasattr(self.proxy_client, 'base_client'):
            await self.proxy_client.base_client.close()
        self.logger.info("P2P Service Bridge shutdown complete")


# Модификации для p2p_admin.py
class EnhancedP2PAdminSystem:
    """Расширенная версия P2PAdminSystem с поддержкой сервисов"""

    def __init__(self, *args, **kwargs):
        # Инициализируем базовый функционал (из существующего кода)
        # super().__init__(*args, **kwargs)  # В реальной реализации

        self.service_bridge = None
        self.logger = logging.getLogger(f"EnhancedP2PSystem")

    async def start_with_services(self, join_addresses=None):
        """Запуск с поддержкой сервисов"""
        # Запускаем базовую P2P систему
        # await super().start(join_addresses)  # В реальной реализации

        # Инициализируем сервисный мост
        # self.service_bridge = P2PServiceBridge(self)
        # await self.service_bridge.initialize()

        self.logger.info("Enhanced P2P Admin System with services started")

    async def stop_with_services(self):
        """Остановка с корректным завершением сервисов"""
        if self.service_bridge:
            await self.service_bridge.shutdown()

        # Останавливаем базовую систему
        # await super().stop()  # В реальной реализации

        self.logger.info("Enhanced P2P Admin System stopped")


# Расширенный Universal Proxy для сервисов
class ServiceAwareUniversalProxy:
    """Universal Proxy с поддержкой сервисной архитектуры"""

    def __init__(self, base_client, local_service_registry=None):
        self.base_client = base_client
        self.local_registry = local_service_registry
        self.logger = logging.getLogger("ServiceAwareProxy")

    def __getattr__(self, service_name: str):
        """Получение прокси для сервиса"""
        return ServiceProxy(
            service_name=service_name,
            base_client=self.base_client,
            local_registry=self.local_registry
        )


class ServiceProxy:
    """Прокси для конкретного сервиса с умной маршрутизацией"""

    def __init__(self, service_name: str, base_client, local_registry=None):
        self.service_name = service_name
        self.base_client = base_client
        self.local_registry = local_registry
        self.logger = logging.getLogger(f"ServiceProxy.{service_name}")

    def __getattr__(self, method_name: str):
        """Получение прокси для метода сервиса"""
        return ServiceMethodProxy(
            service_name=self.service_name,
            method_name=method_name,
            base_client=self.base_client,
            local_registry=self.local_registry
        )


class ServiceMethodProxy:
    """Прокси для вызова метода сервиса"""

    def __init__(self, service_name: str, method_name: str, base_client, local_registry=None):
        self.service_name = service_name
        self.method_name = method_name
        self.base_client = base_client
        self.local_registry = local_registry
        self.logger = logging.getLogger(f"MethodProxy.{service_name}.{method_name}")

    async def __call__(self, **kwargs):
        """Вызов метода с умной маршрутизацией"""

        # Сначала пробуем локальный вызов
        if self.local_registry:
            local_service = self.local_registry.get_service(self.service_name)
            if local_service and hasattr(local_service, self.method_name):
                method = getattr(local_service, self.method_name)
                if hasattr(method, '_service_method') and method._service_public:
                    self.logger.debug(f"Local call: {self.service_name}.{self.method_name}")
                    return await method(**kwargs)

        # Если локально нет - делаем удаленный вызов
        method_path = f"{self.service_name}/{self.method_name}"

        try:
            # Пробуем обычный RPC вызов
            result = await self.base_client.rpc_call(
                method_path=method_path,
                params=kwargs
            )
            self.logger.debug(f"Remote call: {method_path} -> success")
            return result

        except Exception as e:
            self.logger.error(f"Failed to call {method_path}: {e}")
            raise


# Базовый сервис для системных операций (замена SystemMethods)
class SystemService(BaseService):
    """Системный сервис с базовой функциональностью"""

    SERVICE_NAME = "system"

    def __init__(self, service_name: str, proxy_client=None, cache=None):
        super().__init__(service_name, proxy_client)
        self.cache = cache
        self.info.version = "2.0.0"
        self.info.description = "Core system service for P2P administration"
        self.info.domain = "system"

    async def initialize(self):
        """Инициализация системного сервиса"""
        self.logger.info("System service initializing...")
        # Здесь можно добавить системную инициализацию

    async def cleanup(self):
        """Очистка ресурсов"""
        self.logger.info("System service cleaning up...")

    @service_method(description="Get system information", public=True, cache_ttl=60)
    async def get_system_info(self) -> Dict[str, Any]:
        """Получить информацию о системе"""
        import platform
        import psutil
        import socket

        return {
            "hostname": socket.gethostname(),
            "platform": platform.system(),
            "platform_release": platform.release(),
            "platform_version": platform.version(),
            "architecture": platform.machine(),
            "processor": platform.processor(),
            "cpu_count": psutil.cpu_count(),
            "memory_total": psutil.virtual_memory().total,
            "uptime": psutil.boot_time()
        }

    @service_method(description="Get system metrics", public=True, cache_ttl=10)
    async def get_system_metrics(self) -> Dict[str, Any]:
        """Получить метрики производительности системы"""
        import psutil

        # Получаем информацию о памяти
        memory = psutil.virtual_memory()

        # Получаем среднюю загрузку
        try:
            load_avg = psutil.getloadavg()
        except AttributeError:
            load_avg = [0, 0, 0]  # Windows не поддерживает getloadavg

        return {
            "cpu_percent": psutil.cpu_percent(interval=1),
            "memory": {
                "total": memory.total,
                "available": memory.available,
                "used": memory.used,
                "percent": memory.percent
            },
            "load_average": load_avg,
            "process_count": len(psutil.pids()),
            "timestamp": asyncio.get_event_loop().time()
        }

    @service_method(description="Execute system command", public=True, requires_auth=True)
    async def execute_command(self, command: str, timeout: int = 30) -> Dict[str, Any]:
        """Выполнить системную команду (безопасно)"""
        import subprocess
        import shlex

        # Список разрешенных команд (для безопасности)
        allowed_commands = [
            'ping', 'tracert', 'nslookup', 'ipconfig', 'systeminfo',
            'tasklist', 'dir', 'type', 'echo', 'date', 'time'
        ]

        cmd_parts = shlex.split(command)
        if not cmd_parts or cmd_parts[0] not in allowed_commands:
            return {
                "success": False,
                "error": f"Command '{cmd_parts[0] if cmd_parts else 'empty'}' not allowed",
                "stdout": "",
                "stderr": "",
                "return_code": -1
            }

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )

            return {
                "success": process.returncode == 0,
                "stdout": stdout.decode('utf-8', errors='replace'),
                "stderr": stderr.decode('utf-8', errors='replace'),
                "return_code": process.returncode,
                "command": command
            }

        except asyncio.TimeoutError:
            return {
                "success": False,
                "error": f"Command timed out after {timeout} seconds",
                "stdout": "",
                "stderr": "",
                "return_code": -1
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "stdout": "",
                "stderr": "",
                "return_code": -1
            }


# Пример сервиса пользователя
class ExampleUserService(BaseService):
    """Пример пользовательского сервиса"""

    SERVICE_NAME = "user_example"

    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.info.version = "1.0.0"
        self.info.description = "Example user service showing service interaction"
        self.info.dependencies = ["system"]
        self.info.domain = "user_services"

    async def initialize(self):
        """Инициализация пользовательского сервиса"""
        self.logger.info("User example service initializing...")

        # Можем получить системную информацию через proxy
        if self.proxy:
            try:
                system_info = await self.proxy.system.get_system_info()
                self.logger.info(f"Running on: {system_info.get('hostname', 'unknown')}")
            except Exception as e:
                self.logger.warning(f"Could not get system info: {e}")

    async def cleanup(self):
        """Очистка ресурсов"""
        self.logger.info("User example service cleaning up...")

    @service_method(description="Process user data with system integration", public=True)
    async def process_with_system_check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Обработка данных с проверкой системных метрик"""

        result = {
            "processed_data": data,
            "processed_by": self.service_name,
            "timestamp": asyncio.get_event_loop().time()
        }

        # Получаем системные метрики через proxy
        if self.proxy:
            try:
                metrics = await self.proxy.system.get_system_metrics()
                result["system_metrics"] = {
                    "cpu_percent": metrics.get("cpu_percent"),
                    "memory_percent": metrics.get("memory", {}).get("percent"),
                    "process_count": metrics.get("process_count")
                }

                # Можем вызвать другие сервисы
                # other_result = await self.proxy.other_service.some_method()

            except Exception as e:
                self.logger.warning(f"Could not get system metrics: {e}")
                result["system_metrics"] = {"error": str(e)}

        return result

    @service_method(description="Test inter-service communication", public=True)
    async def test_service_communication(self) -> Dict[str, Any]:
        """Тестирование межсервисного взаимодействия"""

        results = {"tests": []}

        if self.proxy:
            # Тест 1: Вызов системного сервиса
            try:
                system_info = await self.proxy.system.get_system_info()
                results["tests"].append({
                    "test": "system_service_call",
                    "success": True,
                    "response": f"Hostname: {system_info.get('hostname')}"
                })
            except Exception as e:
                results["tests"].append({
                    "test": "system_service_call",
                    "success": False,
                    "error": str(e)
                })

            # Тест 2: Попытка вызова несуществующего сервиса
            try:
                await self.proxy.nonexistent_service.test_method()
                results["tests"].append({
                    "test": "nonexistent_service_call",
                    "success": False,
                    "error": "Should have failed"
                })
            except Exception as e:
                results["tests"].append({
                    "test": "nonexistent_service_call",
                    "success": True,
                    "response": f"Correctly failed: {str(e)[:100]}"
                })
        else:
            results["error"] = "No proxy client available"

        return results


# Вспомогательные функции для интеграции
def create_enhanced_universal_client(base_client, service_registry=None):
    """Создать расширенный universal client с поддержкой сервисов"""
    return ServiceAwareUniversalProxy(base_client, service_registry)


async def setup_default_services(service_manager: ServiceManager, cache=None):
    """Настройка стандартных системных сервисов"""

    # Регистрируем системный сервис
    system_service_class = lambda name, proxy: SystemService(name, proxy, cache)
    system_service_class.SERVICE_NAME = "system"

    await service_manager.registry.register_service_class(
        type("SystemService", (BaseService,), {
            "SERVICE_NAME": "system",
            "__init__": lambda self, name, proxy: SystemService.__init__(self, name, proxy, cache),
            "initialize": SystemService.initialize,
            "cleanup": SystemService.cleanup,
            "get_system_info": SystemService.get_system_info,
            "get_system_metrics": SystemService.get_system_metrics,
            "execute_command": SystemService.execute_command,
        }),
        service_manager.proxy_client
    )