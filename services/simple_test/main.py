import asyncio
import time
from typing import Dict, Any

from layers.interface import P2PService, P2PInterface, P2PProductionNode
from layers.universal_proxy import create_universal_client


class TestService(P2PService):
    """Production тестовый сервис"""

    def __init__(self):
        super().__init__()
        self.test_instance = TestClass()
        self.cache = {}

    async def do_test(self, mode: str = "default", use_cache: bool = True, **kwargs):
        """Выполнить тестирование с кешированием"""
        cache_key = f"{mode}_{hash(str(kwargs))}"

        if use_cache and cache_key in self.cache:
            self.logger.info(f"Cache hit for {cache_key}")
            return self.cache[cache_key]

        # Локальная логика
        result = self.test_instance.do_test(mode=mode, **kwargs)

        # P2P вызовы с retry
        try:
            system_info = await self.call("system.get_system_info", timeout=5.0, retries=2)
            metrics = await self.safe_call("monitoring.get_metrics", default={})

            final_result = {
                "result": result,
                "system_info": system_info,
                "metrics": metrics,
                "timestamp": time.time()
            }

            # Кеширование
            if use_cache:
                self.cache[cache_key] = final_result

            # Событие о завершении
            await self.emit("test_completed", {
                "mode": mode,
                "success": True,
                "cache_hit": False
            })

            return final_result

        except Exception as e:
            self.logger.error(f"Test failed: {e}")
            await self.emit("test_failed", {"mode": mode, "error": str(e)})
            raise

    def get_cache_stats(self):
        """Статистика кеша"""
        return {
            "size": len(self.cache),
            "keys": list(self.cache.keys())
        }

    async def clear_cache(self):
        """Очистка кеша"""
        self.cache.clear()
        self.logger.info("Cache cleared")
        return {"status": "cache_cleared"}


async def run(p2p: P2PInterface, config: Dict[str, Any]):
    """Production entry point"""

    # Настройка middleware
    @p2p.middleware
    async def logging_middleware(phase: str, method_name: str, data: Any):
        if phase == 'before':
            p2p.logger.info(f"Calling {method_name} with args: {len(str(data))} chars")
        else:
            p2p.logger.info(f"Method {method_name} completed")
        return data

    @p2p.middleware
    async def metrics_middleware(phase: str, method_name: str, data: Any):
        if phase == 'before':
            # Можно отправить метрики в мониторинг
            await p2p.safe_call("monitoring.record_call", method=method_name)
        return data

    # Регистрация сервиса
    service = TestService()
    p2p.register_class(service, **config.get('method_defaults', {}))

    # Дополнительные методы через декораторы
    @p2p.query("health", description="Health check endpoint")
    async def health():
        return await p2p.health_check()

    @p2p.command("reload_config", description="Reload configuration")
    async def reload_config(**new_config):
        config.update(new_config)
        p2p.logger.info(f"Config reloaded: {config}")
        return {"status": "reloaded", "config": config}

    # Обработчики событий
    @p2p.event_handler("system_shutdown")
    async def on_system_shutdown(data):
        p2p.logger.warning("System shutdown signal received")
        await service.clear_cache()

    # Startup задачи
    @p2p.startup
    async def on_startup():
        p2p.logger.info(f"Production test project starting with config: {config}")

        # Уведомляем систему о старте
        await p2p.safe_call("events.emit", event_type="project_started", data={
            "project": "production_test",
            "config": config
        })

    # Shutdown задачи
    @p2p.shutdown
    async def on_shutdown():
        p2p.logger.info("Production test project shutting down")
        await service.clear_cache()

        await p2p.safe_call("events.emit", event_type="project_stopped", data={
            "project": "production_test"
        })

    # Основной цикл
    try:
        while True:
            await asyncio.sleep(60)

            # Периодические задачи
            stats = p2p.get_stats()
            p2p.logger.info(f"Hourly stats: {stats['total_calls']} calls")

            # Очистка старого кеша
            if len(service.cache) > 100:
                service.cache.clear()
                p2p.logger.info("Cache auto-cleared due to size limit")

    except KeyboardInterrupt:
        p2p.logger.info("Received shutdown signal")
    except Exception as e:
        p2p.logger.error(f"Unexpected error in main loop: {e}")
        raise


# Использование
async def main():
    # Создание production ноды
    node = P2PProductionNode("production-main")
    await node.start(["127.0.0.1:8001"])

    # Загрузка проектов
    await node.load_project("production_test", "projects.production_test", {
        "debug": False,
        "cache_enabled": True,
        "method_defaults": {
            "timeout": 30.0,
            "retries": 3,
            "rate_limit": 60
        }
    })

    # Тестирование через universal client
    universal = create_universal_client(node.client)

    try:
        # Health check
        health = await universal.production_test.health()
        print(f"Health: {health}")

        # Основной тест
        result = await universal.production_test.do_test(mode="production", data={"test": True})
        print(f"Test result: {result}")

        # Статистика
        stats = await universal.production_test.get_stats()
        print(f"Stats: {stats}")

        # Список методов
        methods = await universal.production_test.get_methods()
        print(f"Available methods: {list(methods.keys())}")

    except Exception as e:
        print(f"Error during testing: {e}")

    # Мониторинг проектов
    while True:
        await asyncio.sleep(30)
        status = node.get_projects_status()
        print(f"📊 Projects status: {status}")


if __name__ == "__main__":
    asyncio.run(main())