# layers/universal_proxy.py - Чистая версия без debug

import asyncio
from typing import Dict, Any, List, Optional, Union


class SimpleServiceProxy:
    """Универсальная прокси для сервисов"""

    def __init__(self, client, service_name: str = "", node_name: str = "", domain_name: str = ""):
        self.client = client
        self.service_name = service_name
        self.node_name = node_name
        self.domain_name = domain_name

    def __getattr__(self, name: str):
        """Динамическое создание методов и под-прокси"""

        # ШАГ 1: Если нет сервиса - это сервис
        if not self.service_name:
            return SimpleServiceProxy(
                self.client,
                service_name=name,
                node_name="",
                domain_name=""
            )

        # ШАГ 2: Есть сервис, но нет узла/домена - определяем что это
        if self.service_name and not self.node_name and not self.domain_name:

            # Проверяем известные узлы
            known_nodes = ['coordinator', 'worker', 'pc1', 'server1', 'host1']
            if name in known_nodes or name.startswith(('pc', 'server', 'node', 'host')):
                return SimpleServiceProxy(
                    self.client,
                    service_name=self.service_name,
                    node_name=name,
                    domain_name=""
                )

            # Проверяем известные домены
            known_domains = ['local_domain', 'production', 'staging', 'dev', 'office', 'test']
            if name in known_domains:
                return SimpleServiceProxy(
                    self.client,
                    service_name=self.service_name,
                    node_name="",
                    domain_name=name
                )

            # Иначе это метод
            return SimpleMethodProxy(
                self.client,
                service_name=self.service_name,
                method_name=name,
                node_name="",
                domain_name=""
            )

        # ШАГ 3: Есть сервис + (узел ИЛИ домен) - это метод
        return SimpleMethodProxy(
            self.client,
            service_name=self.service_name,
            method_name=name,
            node_name=self.node_name,
            domain_name=self.domain_name
        )


class SimpleMethodProxy:
    """Прокси для выполнения методов"""

    def __init__(self, client, service_name: str, method_name: str, node_name: str = "", domain_name: str = ""):
        self.client = client
        self.service_name = service_name
        self.method_name = method_name
        self.node_name = node_name
        self.domain_name = domain_name

    async def __call__(self, *args, **kwargs):
        """Выполнение метода"""

        # Подготовка параметров
        params = dict(kwargs)
        if args:
            params['args'] = list(args)

        # Формируем путь метода
        method_path = f"{self.service_name}/{self.method_name}"

        # Выбираем тип вызова
        if self.node_name:
            # RPC к конкретному узлу
            params['_target_node'] = self.node_name
            result = await self.client.rpc_call(
                method_path=method_path,
                params=params,
                target_role=None
            )
            return result
        else:
            # Broadcast call
            if self.domain_name:
                params['_target_domain'] = self.domain_name

            results = await self.client.broadcast_call(
                method_path=method_path,
                params=params,
                target_role="worker"
            )
            return results


class SimpleUniversalClient:
    """Простой универсальный клиент с поддержкой локальных сервисов"""

    def __init__(self, base_client, local_service_registry=None):
        self.base_client = base_client
        self.local_registry = local_service_registry

    def __getattr__(self, name: str):
        """Делегирование к базовому клиенту или создание прокси"""

        # Проверяем базовый клиент
        if hasattr(self.base_client, name):
            return getattr(self.base_client, name)

        # Создаем прокси сервиса
        return SimpleServiceProxy(
            self.base_client,
            service_name=name,
            local_registry=self.local_registry
        )

def create_universal_client(base_client, local_registry=None):
    """Создание универсального клиента с поддержкой локальных сервисов"""
    return SimpleUniversalClient(base_client, local_registry)


# Простой пример использования
async def example_usage():
    """Пример использования Universal API"""

    try:
        from p2p_admin import P2PClient
    except ImportError:
        from main import P2PClient

    client = P2PClient("example-client")

    try:
        await client.connect(["127.0.0.1:8001"])
        await client.authenticate()

        # Создаем универсальный клиент
        universal = create_universal_client(client)

        # Примеры использования:

        # Получение информации с координатора
        coordinator_info = await universal.system.coordinator.get_system_info()

        # Получение метрик со всех узлов домена
        domain_metrics = await universal.system.local_domain.get_system_metrics()

        # Управление сертификатами
        # cert_result = await universal.certs.local_domain.install("/path/to/cert.pem")
        # cert_list = await universal.certs.coordinator.list()

        return {
            "coordinator_info": coordinator_info,
            "domain_metrics": domain_metrics
        }

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(example_usage())