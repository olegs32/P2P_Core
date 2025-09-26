# local_service_bridge.py - Локальный мост сервисов с поддержкой таргетинга узлов
import asyncio
import logging
from typing import Dict, Any, Optional


class LocalServiceBridge:
    """Локальный мост для вызова сервисов без сетевых запросов"""

    def __init__(self, method_registry: Dict[str, Any], service_manager):
        self.local_proxy = None
        self.method_registry = method_registry
        self.service_manager = service_manager
        self.registry = self.service_manager.registry
        self.logger = logging.getLogger("ServiceBridge")

    async def initialize(self):
        """Инициализация локального моста с диагностикой"""
        self.logger.info("Initializing service bridge...")

        # ✅ DEBUG: Проверяем что registry не пустой
        available_methods = list(self.method_registry.keys())
        self.logger.info(f"Service bridge initialized with {len(available_methods)} methods")

        if not available_methods:
            self.logger.warning("⚠️  Method registry is EMPTY! This will cause 'method not found' errors.")
            self.logger.warning("This usually means LocalServiceBridge was created before services were registered.")

        self.local_proxy = SimpleLocalProxy(self.method_registry)
        self.logger.info("Service bridge initialized")

    def get_proxy(self, remote_client=None):
        """Получить локальный прокси"""
        return self.local_proxy

    async def call_method_direct(self, service_name: str, method_name: str, **kwargs):
        """Прямой вызов метода через реестр с retry логикой против race condition"""
        method_path = f"{service_name}/{method_name}"

        # ✅ ИСПРАВЛЕНИЕ: Retry логика для race condition
        max_retries = 5
        base_delay = 0.5

        for attempt in range(max_retries):
            if method_path in self.method_registry:
                method = self.method_registry[method_path]
                return await method(**kwargs)

            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                self.logger.warning(
                    f"Method {method_path} not found in registry, retrying in {delay:.1f}s (attempt {attempt + 1})")
                await asyncio.sleep(delay)
            else:
                available_methods = list(self.method_registry.keys())
                raise ValueError(
                    f"Method {method_path} not found in local registry after {max_retries} attempts. Available: {available_methods[:10]}")

    def is_system_ready(self) -> bool:
        """Проверка готовности системы для межсервисных вызовов"""
        return getattr(self, '_system_ready', False)




class SimpleLocalProxy:
    """Простой локальный прокси для вызова методов"""

    def __init__(self, method_registry: Dict[str, Any]):
        self.method_registry = method_registry
        self.logger = logging.getLogger("SimpleLocalProxy")

    def __getattr__(self, service_name: str):
        """Получить прокси для сервиса"""
        return ServiceMethodProxy(service_name, self.method_registry)


class ServiceMethodProxy:
    """Прокси для методов сервиса с поддержкой таргетинга узлов"""

    def __init__(self, service_name: str, method_registry: Dict[str, Any], target_node: str = None):
        self.service_name = service_name
        self.method_registry = method_registry
        self.target_node = target_node
        self.logger = logging.getLogger(f"Proxy.{service_name}")

    def __getattr__(self, attr_name: str):
        """Получить callable для метода или прокси для узла"""

        # Список известных узлов/ролей для таргетинга
        known_targets = [
            'coordinator', 'worker', 'worker1', 'worker2', 'worker3',
            'node1', 'node2', 'host1', 'host2', 'pc1', 'pc2',
            'localhost', 'local', 'remote'
        ]

        # Если это похоже на имя узла - создаем таргетированный прокси
        if attr_name in known_targets or attr_name.startswith(('node_', 'host_', 'pc_', 'worker_')):
            self.logger.debug(f"Creating targeted proxy for {self.service_name}.{attr_name}")
            return ServiceMethodProxy(
                service_name=self.service_name,
                method_registry=self.method_registry,
                target_node=attr_name
            )

        # Иначе это имя метода
        return MethodCaller(
            service_name=self.service_name,
            method_name=attr_name,
            method_registry=self.method_registry,
            target_node=self.target_node
        )


class MethodCaller:
    """Вызов метода через реестр с поддержкой таргетинга"""

    def __init__(self, service_name: str, method_name: str, method_registry: Dict[str, Any], target_node: str = None):
        self.service_name = service_name
        self.method_name = method_name
        self.method_registry = method_registry
        self.target_node = target_node
        self.logger = logging.getLogger(f"Method.{service_name}.{method_name}")

    async def __call__(self, **kwargs):
        """Выполнение вызова с устойчивостью к race condition"""
        method_path = f"{self.service_name}/{self.method_name}"

        # ✅ ИСПРАВЛЕНИЕ: Retry логика вместо немедленного падения
        if method_path not in self.method_registry:
            if self.target_node:
                raise RuntimeError(
                    f"Method {method_path} not found in local registry. "
                    f"Remote calls to '{self.target_node}' not implemented in local bridge."
                )
            else:
                # Пытаемся дождаться появления метода в registry
                max_retries = 8
                base_delay = 0.3

                for attempt in range(max_retries):
                    # Проверяем снова - может метод уже зарегистрировался
                    if method_path in self.method_registry:
                        self.logger.info(f"Method {method_path} found after {attempt} retries")
                        break

                    if attempt < max_retries - 1:
                        delay = base_delay * (1.5 ** attempt)  # Более мягкий exponential backoff
                        self.logger.warning(
                            f"Method {method_path} not found, waiting {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                        await asyncio.sleep(delay)
                    else:
                        available_methods = list(self.method_registry.keys())
                        raise RuntimeError(
                            f"Method {method_path} not found in local registry after {max_retries} attempts. "
                            f"Available methods: {available_methods[:10]}"
                        )

        # Логируем и выполняем вызов
        if self.target_node:
            self.logger.debug(f"Targeted call to {self.target_node}: {method_path}")
        else:
            self.logger.debug(f"Local call: {method_path}")

        method = self.method_registry[method_path]
        return await method(**kwargs)


def create_local_service_bridge(method_registry: Dict[str, Any], service_manager):
    """Создание локального моста сервисов"""
    return LocalServiceBridge(method_registry, service_manager)