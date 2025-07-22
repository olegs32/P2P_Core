# layers/universal_proxy_simple.py - ПРОСТАЯ версия без путаницы

import asyncio
from typing import Dict, Any, List, Optional, Union


class SimpleServiceProxy:
    """ПРОСТАЯ прокси для сервисов - без сложной логики"""

    def __init__(self, client, service_name: str = "", node_name: str = "", domain_name: str = ""):
        self.client = client
        self.service_name = service_name
        self.node_name = node_name
        self.domain_name = domain_name

        print(f"🔧 Created proxy: service='{service_name}', node='{node_name}', domain='{domain_name}'")

    def __getattr__(self, name: str):
        """ПРОСТАЯ логика: проверяем по очереди что это может быть"""

        print(
            f"🔍 Accessing '{name}' on service='{self.service_name}', node='{self.node_name}', domain='{self.domain_name}'")

        # ШАГ 1: Если нет сервиса - это точно сервис
        if not self.service_name:
            print(f"   → Setting service: {name}")
            return SimpleServiceProxy(
                self.client,
                service_name=name,
                node_name="",
                domain_name=""
            )

        # ШАГ 2: Есть сервис, но нет узла/домена - проверяем что это
        if self.service_name and not self.node_name and not self.domain_name:

            # Проверяем известные узлы
            known_nodes = ['coordinator', 'worker', 'pc1', 'server1', 'host1']
            if name in known_nodes or name.startswith(('pc', 'server', 'node', 'host')):
                print(f"   → Setting node: {name}")
                return SimpleServiceProxy(
                    self.client,
                    service_name=self.service_name,
                    node_name=name,
                    domain_name=""
                )

            # Проверяем известные домены
            known_domains = ['local_domain', 'production', 'staging', 'dev']
            if name in known_domains:
                print(f"   → Setting domain: {name}")
                return SimpleServiceProxy(
                    self.client,
                    service_name=self.service_name,
                    node_name="",
                    domain_name=name
                )

            # Иначе это метод
            print(f"   → Creating method: {name}")
            return SimpleMethodProxy(
                self.client,
                service_name=self.service_name,
                method_name=name,
                node_name="",
                domain_name=""
            )

        # ШАГ 3: Есть сервис + (узел ИЛИ домен) - это метод
        print(f"   → Creating method with context: {name}")
        return SimpleMethodProxy(
            self.client,
            service_name=self.service_name,
            method_name=name,
            node_name=self.node_name,
            domain_name=self.domain_name
        )


class SimpleMethodProxy:
    """ПРОСТАЯ прокси для методов"""

    def __init__(self, client, service_name: str, method_name: str, node_name: str = "", domain_name: str = ""):
        self.client = client
        self.service_name = service_name
        self.method_name = method_name
        self.node_name = node_name
        self.domain_name = domain_name

        print(f"🚀 Method proxy: {service_name}.{method_name} (node={node_name}, domain={domain_name})")

    async def __call__(self, *args, **kwargs):
        """Выполнение метода"""

        print(f"🎯 EXECUTING: {self.service_name}.{self.method_name}")
        print(f"   Node: {self.node_name}")
        print(f"   Domain: {self.domain_name}")
        print(f"   Args: {args}")
        print(f"   Kwargs: {kwargs}")

        # Подготовка параметров
        params = dict(kwargs)
        if args:
            params['args'] = list(args)

        # Извлекаем служебные параметры для роутинга (НЕ передаем в метод!)
        routing_params = {}
        if self.node_name:
            routing_params['_target_node'] = self.node_name
        if self.domain_name:
            routing_params['_target_domain'] = self.domain_name

        # Формируем правильный путь метода
        method_path = f"{self.service_name}/{self.method_name}"

        print(f"   Method path: {method_path}")
        print(f"   User params: {params}")
        print(f"   Routing params: {routing_params}")

        # Выбираем тип вызова
        if self.node_name:
            print(f"   → Using RPC call to specific node")
            # Для RPC добавляем routing параметры к user параметрам
            all_params = {**params, **routing_params}
            result = await self.client.rpc_call(
                method_path=method_path,
                params=all_params,
                target_role=None  # Не ограничиваем по роли, ищем по имени
            )
            return result
        else:
            print(f"   → Using broadcast call")
            # Для broadcast добавляем домен в params для координатора
            broadcast_params = dict(params)
            if self.domain_name:
                broadcast_params['_target_domain'] = self.domain_name
                print(f"   → Adding domain filter: {self.domain_name}")

            results = await self.client.broadcast_call(
                method_path=method_path,
                params=broadcast_params,  # Включаем домен для координатора
                target_role="worker"
            )
            return results


class SimpleUniversalClient:
    """ПРОСТОЙ универсальный клиент"""

    def __init__(self, base_client):
        self.base_client = base_client
        print(f"🎯 Created SimpleUniversalClient")

    def __getattr__(self, name: str):
        """Простое делегирование"""

        # Проверяем базовый клиент
        if hasattr(self.base_client, name):
            return getattr(self.base_client, name)

        # Создаем прокси сервиса с ПРАВИЛЬНЫМ именем
        print(f"🔍 Creating service proxy for: {name}")
        return SimpleServiceProxy(self.base_client, service_name=name)


def create_simple_universal_client(base_client):
    """Создание простого универсального клиента"""
    return SimpleUniversalClient(base_client)


# Простой тест
async def simple_logic():
    """Тест простой логики"""

    try:
        from p2p_admin import P2PClient
    except ImportError:
        try:
            from main import P2PClient
        except ImportError:
            print("❌ Не удалось импортировать P2PClient")
            return

    client = P2PClient("simple-test")

    try:
        await client.connect(["127.0.0.1:8001"])
        await client.authenticate()

        simple = create_simple_universal_client(client)

        print("🧪 Simple Logic Test")
        print("=" * 50)

        print("\n🔍 Step by step:")
        print("1. simple.system")
        system = simple.system

        print("\n2. simple.system.coordinator")
        coordinator = system.coordinator

        print("\n3. simple.system.coordinator.get_system_info")
        method = coordinator.get_system_info

        print("\n4. Executing...")
        result = await method()

        print(f"\n✅ Result: {result}")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.close()


async def domain_calls():
    """Тест доменных вызовов"""

    try:
        from p2p_admin import P2PClient
    except ImportError:
        from main import P2PClient

    client = P2PClient("domain-test")

    try:
        await client.connect(["127.0.0.1:8001"])
        await client.authenticate()

        simple = create_simple_universal_client(client)

        print("🌐 Domain Calls Test")
        print("=" * 50)

        print("\n🔍 Testing: simple.system.local_domain.get_system_metrics()")

        print("Step 1: simple.system")
        system = simple.system

        print("Step 2: simple.system.local_domain")
        domain = system.local_domain

        print("Step 3: simple.system.local_domain.get_system_metrics")
        method = domain.get_system_metrics

        print("Step 4: Execute...")
        result = await method()
        print(result)

        print(await simple.system.coordinator.get_system_info())

        print(f"✅ Domain result: {len(result)} responses")
        for r in result:
            if r.get('success'):
                node_id = r.get('node_id', 'unknown')
                print(f"   📍 {node_id}: OK")
            else:
                print(f"   ❌ {r.get('node_id', 'unknown')}: {r.get('error', 'Unknown error')}")

    except Exception as e:
        print(f"❌ Domain test failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(domain_calls())