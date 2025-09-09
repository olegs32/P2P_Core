
import asyncio
import threading
import importlib
import logging
import time
import functools
import inspect
from typing import Dict, Callable, Any, List, Optional, Union
from dataclasses import dataclass, field
from collections import defaultdict
from enum import Enum
import weakref

from layers.interface import P2PService, P2PInterface

# === Пример использования ===

# projects/test_project/main.py
"""
Пример проекта для интеграции с P2PAdminSystem
"""


class TestService(P2PService):
    """Тестовый сервис"""

    def __init__(self):
        super().__init__()
        self.test_data = {}

    async def do_test(self, mode: str = "default", **kwargs):
        """Основной тестовый метод"""
        self.logger.info(f"Executing test in mode: {mode}")

        # Локальная логика
        result = {
            "mode": mode,
            "data": kwargs,
            "timestamp": time.time(),
            "status": "completed"
        }

        # P2P вызовы
        try:
            # Вызов системных методов
            system_info = await self.safe_call("system.get_system_metrics", default={})
            result["system_info"] = system_info

            # Сохранение результата
            self.test_data[f"test_{time.time()}"] = result

        except Exception as e:
            result["error"] = str(e)

        return result

    def get_test_data(self):
        """Получить все тестовые данные"""
        return self.test_data

    async def clear_test_data(self):
        """Очистить тестовые данные"""
        self.test_data.clear()
        return {"status": "cleared", "count": 0}


async def run(p2p: P2PInterface, config: Dict[str, Any]):
    """Entry point проекта"""

    p2p.logger.info(f"🚀 Starting test project with config: {config}")

    # Создание и регистрация сервиса
    service = TestService()
    p2p.register_class(service)

    # Дополнительные методы через декораторы
    @p2p.query("status")
    async def get_status():
        return {
            "project": "test_project",
            "status": "running",
            "config": config,
            "timestamp": time.time()
        }

    # Startup задачи
    @p2p.startup
    async def on_startup():
        p2p.logger.info("📋 Test project initialized!")

    # Shutdown задачи
    @p2p.shutdown
    async def on_shutdown():
        p2p.logger.info("🛑 Test project shutting down")

    # Основной цикл
    try:
        while True:
            await asyncio.sleep(60)
            # Периодические задачи
            stats = p2p.get_stats()
            p2p.logger.debug(f"Hourly stats: {stats['total_calls']} calls")

    except KeyboardInterrupt:
        p2p.logger.info("Received shutdown signal")


# Пример использования
async def example_usage():
    """Пример использования системы"""

    # Предполагаем, что admin_system и universal_proxy уже созданы
    # admin_system = P2PAdminSystem(...)
    # universal_proxy = create_universal_client(...)

    # Создание расширенной системы
    # extended_system = P2PAdminSystemWithProjects(admin_system, universal_proxy)

    # Загрузка проекта
    # await extended_system.load_project("test_project", "projects.test_project", {
    #     "debug": True,
    #     "timeout": 30
    # })

    # Вызов методов проекта через RPC
    # client = P2PClient("test-client")
    # await client.connect(["127.0.0.1:8001"])
    # await client.authenticate()

    # result = await client.rpc_call("test_project/do_test", {
    #     "mode": "production",
    #     "data": {"test": True}
    # })

    # print(f"Test result: {result}")

    pass


if __name__ == "__main__":
    asyncio.run(example_usage())

