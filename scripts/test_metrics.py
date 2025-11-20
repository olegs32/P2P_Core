#!/usr/bin/env python3
# test_metrics.py - Скрипт для тестирования системы метрик P2P

import asyncio
import aiohttp
import json
import time
from typing import Dict, Any, List


class P2PMetricsTestClient:
    """Клиент для тестирования P2P метрик"""

    def __init__(self, base_url: str = "http://127.0.0.1:8001", node_id: str = "test-client"):
        self.base_url = base_url.rstrip('/')
        self.node_id = node_id
        self.token = None
        self.session = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        await self.authenticate()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def authenticate(self):
        """Аутентификация и получение токена"""
        try:
            async with self.session.post(
                    f"{self.base_url}/auth/token",
                    json={"node_id": self.node_id}
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    self.token = data["access_token"]
                    print(f"✅ Authenticated successfully as {self.node_id}")
                else:
                    print(f"❌ Authentication failed: {response.status}")
        except Exception as e:
            print(f"❌ Authentication error: {e}")

    @property
    def headers(self):
        """Заголовки с токеном авторизации"""
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    async def get_health(self) -> Dict[str, Any]:
        """Получить health информацию узла"""
        async with self.session.get(
                f"{self.base_url}/health",
                headers=self.headers
        ) as response:
            return await response.json()

    async def get_services_health(self) -> Dict[str, Any]:
        """Получить health статус всех сервисов"""
        async with self.session.get(
                f"{self.base_url}/metrics/health",
                headers=self.headers
        ) as response:
            return await response.json()

    async def get_node_metrics(self) -> Dict[str, Any]:
        """Получить метрики узла"""
        async with self.session.get(
                f"{self.base_url}/metrics/node",
                headers=self.headers
        ) as response:
            return await response.json()

    async def get_service_metrics(self, service_name: str) -> Dict[str, Any]:
        """Получить метрики конкретного сервиса"""
        async with self.session.get(
                f"{self.base_url}/local/services/{service_name}/metrics",
                headers=self.headers
        ) as response:
            return await response.json()

    async def get_cluster_metrics(self) -> Dict[str, Any]:
        """Получить метрики кластера (если это координатор)"""
        async with self.session.get(
                f"{self.base_url}/cluster/metrics",
                headers=self.headers
        ) as response:
            return await response.json()

    async def call_service_method(self, service: str, method: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Вызов метода сервиса"""
        payload = {
            "method": method,
            "params": params or {},
            "id": f"test_{int(time.time())}"
        }

        async with self.session.post(
                f"{self.base_url}/rpc/{service}/{method}",
                headers=self.headers,
                json=payload
        ) as response:
            return await response.json()

    async def debug_metrics_system(self) -> Dict[str, Any]:
        """Отладочная информация о системе метрик"""
        async with self.session.get(
                f"{self.base_url}/debug/metrics",
                headers=self.headers
        ) as response:
            return await response.json()


async def test_basic_metrics():
    """Базовое тестирование системы метрик"""
    print("\n🧪 Testing P2P Metrics System")
    print("=" * 50)

    async with P2PMetricsTestClient() as client:
        # 1. Проверка health
        print("\n1. Health Check")
        health = await client.get_health()
        print(f"   Node: {health.get('node_id')}")
        print(f"   Status: {health.get('status')}")
        print(f"   Role: {health.get('role')}")
        print(f"   Uptime: {health.get('uptime_seconds', 0):.1f}s")

        # 2. Проверка сервисов
        print("\n2. Services Health")
        services_health = await client.get_services_health()
        print(f"   Total services: {services_health.get('summary', {}).get('total_services', 0)}")
        print(f"   Alive services: {services_health.get('summary', {}).get('alive_services', 0)}")

        # 3. Отладочная информация
        print("\n3. Debug Metrics System")
        debug_info = await client.debug_metrics_system()
        print(f"   Metrics collector active: {debug_info.get('metrics_collector_active', False)}")
        print(f"   Services with metrics: {len(debug_info.get('services_with_metrics', []))}")
        print(f"   Total metrics count: {debug_info.get('total_metrics_count', 0)}")

        # 4. Метрики системного сервиса
        print("\n4. System Service Metrics")
        try:
            system_metrics = await client.get_service_metrics("system")
            if system_metrics:
                metrics = system_metrics.get('metrics', {})
                print(f"   System service metrics: {len(metrics)}")

                # Показываем несколько интересных метрик
                interesting_metrics = [
                    "cpu_usage_percent", "memory_usage_percent",
                    "get_system_info_calls", "execute_command_calls"
                ]

                for metric_name in interesting_metrics:
                    if metric_name in metrics:
                        metric_data = metrics[metric_name]
                        print(f"   {metric_name}: {metric_data.get('value')} ({metric_data.get('type')})")
            else:
                print("   ❌ No system service metrics available")
        except Exception as e:
            print(f"   ❌ Error getting system metrics: {e}")

        # 5. Вызов системных методов для генерации метрик
        print("\n5. Generating Metrics via RPC Calls")

        # Вызов get_system_info

        system_info_result = await client.call_service_method("system", "get_system_info")
        if system_info_result.get('result'):
            print(f"   ✅ get_system_info call successful")
        else:
            print(f"   ❌ get_system_info failed: {system_info_result.get('error')}")

        # Вызов simple test
        test_result = await client.call_service_method("system", "execute_simple_test")
        print(type(test_result), test_result)
        if test_result.get('result', {}).get('success'):
            print(f"   ✅ execute_simple_test successful")
        else:
            print(f"   ❌ execute_simple_test failed")

        # 6. Проверка обновленных метрик
        print("\n6. Updated Metrics After RPC Calls")
        try:
            system_metrics_updated = await client.get_service_metrics("system")
            if system_metrics_updated:
                metrics = system_metrics_updated.get('metrics', {})

                call_metrics = [
                    ("get_system_info_calls", "System info calls"),
                    ("execute_simple_test_calls", "Simple test calls"),
                    ("get_system_info_success", "System info success"),
                    ("execute_simple_test_success", "Simple test success")
                ]

                for metric_name, description in call_metrics:
                    if metric_name in metrics:
                        value = metrics[metric_name].get('value', 0)
                        print(f"   {description}: {value}")

        except Exception as e:
            print(f"   ❌ Error getting updated metrics: {e}")


async def test_example_service():
    """Тестирование example сервиса (если доступен)"""
    print("\n🧪 Testing Example Service Metrics")
    print("=" * 50)

    async with P2PMetricsTestClient() as client:
        try:
            # Проверяем, есть ли example сервис
            example_metrics = await client.get_service_metrics("example")

            if "error" in str(example_metrics).lower():
                print("   ℹ️  Example service not available, skipping...")
                return

            print("\n1. Example Service Initial Metrics")
            if example_metrics.get('metrics'):
                metrics = example_metrics['metrics']
                print(f"   Total metrics: {len(metrics)}")

                # Показываем интересные метрики
                interesting = ["database_size_mb", "active_connections", "processing_queue_size"]
                for metric in interesting:
                    if metric in metrics:
                        value = metrics[metric].get('value')
                        print(f"   {metric}: {value}")

            # 2. Вызываем методы example сервиса
            print("\n2. Calling Example Service Methods")

            # Process request
            process_result = await client.call_service_method(
                "example", "process_request",
                {"request_id": "test_123", "data": {"test": "data"}}
            )

            if process_result.get('result'):
                print("   ✅ process_request successful")
                result = process_result['result']
                print(f"      Processing time: {result.get('processing_time_ms', 0):.2f}ms")

            # Get statistics
            stats_result = await client.call_service_method("example", "get_statistics")
            if stats_result.get('result'):
                print("   ✅ get_statistics successful")
                stats = stats_result['result']
                print(f"      Database size: {stats.get('database_size_mb', 0):.2f} MB")
                print(f"      Active connections: {stats.get('active_connections', 0)}")

            # Heavy task
            print("\n   Running heavy task (this will take a few seconds)...")
            heavy_task_result = await client.call_service_method(
                "example", "heavy_task",
                {"duration": 2.0, "complexity": "medium"}
            )

            if heavy_task_result.get('result'):
                print("   ✅ heavy_task completed")
                result = heavy_task_result['result']
                print(f"      Total result: {result.get('total_result', 0)}")

            # 3. Проверяем обновленные метрики
            print("\n3. Updated Example Service Metrics")
            updated_metrics = await client.get_service_metrics("example")
            if updated_metrics.get('metrics'):
                metrics = updated_metrics['metrics']

                # Показываем метрики методов
                method_metrics = [
                    ("method_process_request_calls", "Process request calls"),
                    ("method_get_statistics_calls", "Get statistics calls"),
                    ("method_heavy_task_calls", "Heavy task calls"),
                    ("method_process_request_duration_ms", "Avg process request time"),
                    ("heavy_task_total_duration_ms", "Heavy task total time")
                ]

                for metric_name, description in method_metrics:
                    if metric_name in metrics:
                        value = metrics[metric_name].get('value', 0)
                        unit = "ms" if "duration" in metric_name else ""
                        print(f"   {description}: {value} {unit}")

        except Exception as e:
            print(f"   ❌ Error testing example service: {e}")


async def test_node_metrics():
    """Тестирование метрик узла"""
    print("\n🧪 Testing Node-Level Metrics")
    print("=" * 50)

    async with P2PMetricsTestClient() as client:
        try:
            node_metrics = await client.get_node_metrics()

            print(f"   Node ID: {node_metrics.get('node_id')}")
            print(
                f"   Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(node_metrics.get('timestamp', 0)))}")

            metrics_data = node_metrics.get('metrics', {})
            print(f"   Total services: {metrics_data.get('total_services', 0)}")
            print(f"   Alive services: {metrics_data.get('alive_services', 0)}")
            print(f"   Total metrics updates: {metrics_data.get('total_metrics_updates', 0)}")

            # Показываем сервисы с метриками
            services = metrics_data.get('services', {})
            if services:
                print(f"\n   Active Services:")
                for service_name, service_data in services.items():
                    print(f"      {service_name}: {service_data.get('metrics_count', 0)} metrics, "
                          f"{service_data.get('total_updates', 0)} updates")

        except Exception as e:
            print(f"   ❌ Error getting node metrics: {e}")


async def performance_test():
    """Тест производительности системы метрик"""
    print("\n⚡ Performance Test")
    print("=" * 50)

    async with P2PMetricsTestClient() as client:
        print("   Running multiple RPC calls to test metrics performance...")

        start_time = time.time()
        tasks = []

        # Запускаем 20 параллельных вызовов
        for i in range(20):
            if i % 2 == 0:
                task = client.call_service_method("system", "get_system_info")
            else:
                task = client.call_service_method("system", "execute_simple_test")
            tasks.append(task)

        # Ждем выполнения всех задач
        results = await asyncio.gather(*tasks, return_exceptions=True)

        end_time = time.time()
        duration = end_time - start_time

        # Анализируем результаты
        successful = len([r for r in results if not isinstance(r, Exception) and r.get('result')])
        failed = len(results) - successful

        print(f"   Total calls: {len(results)}")
        print(f"   Successful: {successful}")
        print(f"   Failed: {failed}")
        print(f"   Duration: {duration:.2f}s")
        print(f"   Calls/second: {len(results) / duration:.1f}")

        # Проверяем обновленные метрики
        await asyncio.sleep(2)  # Даем время для обновления метрик

        try:
            system_metrics = await client.get_service_metrics("system")
            if system_metrics and system_metrics.get('metrics'):
                metrics = system_metrics['metrics']
                total_calls = metrics.get("get_system_info_calls", {}).get("value", 0) + \
                              metrics.get("execute_simple_test_calls", {}).get("value", 0)
                print(f"   Total system method calls recorded in metrics: {total_calls}")
        except Exception as e:
            print(f"   ❌ Error checking metrics after performance test: {e}")


async def main():
    """Основная функция тестирования"""
    print("🚀 P2P Metrics System Test Suite")
    print("Ensure your P2P node is running on http://127.0.0.1:8001")
    print("(Use: python p2p.py coordinator)")

    try:
        await test_basic_metrics()
        await test_example_service()
        await test_node_metrics()
        await performance_test()

        print("\n✅ All tests completed!")
        print("\nNext steps:")
        print("1. Check the P2P node logs for metrics updates")
        print("2. Create more services in the 'services/' directory")
        print("3. Monitor metrics using the web dashboard")
        print("4. Set up metrics thresholds for monitoring")

    except KeyboardInterrupt:
        print("\n❌ Tests interrupted by user")
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        print(f"\n❌ Test suite error: {e}")


if __name__ == "__main__":
    asyncio.run(main())