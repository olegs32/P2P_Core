import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from layers.service_framework import BaseService, service_method


class Run(BaseService):
    """Тестовый сервис с мониторингом координатора"""

    SERVICE_NAME = "test_monitoring"

    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.info.description = "Test service with coordinator monitoring loop"
        self.info.dependencies = ["system"]
        self.info.domain = "monitoring"

        # Данные мониторинга
        self.monitoring_active = False
        self.monitoring_task = None
        self.metrics_history = []
        self.stats = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "last_success": None,
            "last_error": None,
            "uptime_start": None
        }

        # ИСПРАВЛЕНИЕ: Добавляем флаг отложенной инициализации
        self._proxy_retry_count = 0
        self._max_proxy_retries = 30  # 5 минут ожидания (30 * 10 сек)

    async def initialize(self):
        """Инициализация сервиса через фреймворк BaseService"""
        self.logger.info("Test monitoring service initializing...")
        self.stats["uptime_start"] = datetime.now().isoformat()

        # ИСПРАВЛЕНИЕ: Дожидаемся инжекции proxy
        await self._wait_for_proxy()

        # Ждем немного, чтобы все компоненты инициализировались
        await asyncio.sleep(2)

        # Тестируем первый вызов
        if self.proxy:
            try:
                # Пробуем локальный вызов
                local_info = await self.proxy.system.get_system_info()
                self.logger.info(f"Local system call successful: {local_info.get('hostname', 'unknown')}")

                # Пробуем удаленный вызов к координатору только если это worker
                try:
                    coordinator_info = await self.proxy.system.coordinator.get_system_info()
                    self.logger.info(f"Coordinator call successful: {coordinator_info.get('hostname', 'unknown')}")
                except Exception as e:
                    self.logger.info(f"Coordinator call skipped (probably running on coordinator): {e}")

            except Exception as e:
                self.logger.warning(f"Initial system calls failed: {e}")
        else:
            self.logger.warning("No proxy available during initialization - will retry in monitoring loop")

        # Запускаем мониторинг
        await self.start_monitoring()

    async def _wait_for_proxy(self):
        """
        ИСПРАВЛЕНИЕ: Ожидание инжекции proxy с повторными попытками
        Решает проблему с PyInstaller где proxy может инжектироваться позже
        """
        while self.proxy is None and self._proxy_retry_count < self._max_proxy_retries:
            self.logger.debug(
                f"Waiting for proxy injection... Attempt {self._proxy_retry_count + 1}/{self._max_proxy_retries}")
            await asyncio.sleep(2)  # Ждем 2 секунды
            self._proxy_retry_count += 1

            # Проверяем, не был ли proxy установлен через set_proxy
            # (это может произойти если ServiceManager инжектирует позже)
            if hasattr(self, '_pending_proxy'):
                self.proxy = self._pending_proxy
                delattr(self, '_pending_proxy')
                break

        if self.proxy is None:
            self.logger.warning(
                f"Proxy not available after {self._max_proxy_retries} attempts. Service will work in degraded mode.")
        else:
            self.logger.info("Proxy successfully initialized!")

    def set_proxy(self, proxy_client):
        """
        ИСПРАВЛЕНИЕ: Альтернативный способ установки proxy
        Может быть вызван ServiceManager'ом после создания сервиса
        """
        if self.proxy is None:
            self.proxy = proxy_client
            self.logger.info("Proxy set via set_proxy method")
        else:
            self._pending_proxy = proxy_client

    async def cleanup(self):
        """Очистка ресурсов при остановке"""
        self.logger.info("Test monitoring service cleaning up...")
        await self.stop_monitoring()

    async def start_monitoring(self):
        """Запуск цикла мониторинга координатора"""
        if self.monitoring_active:
            self.logger.warning("Monitoring already active")
            return

        self.monitoring_active = True
        self.monitoring_task = asyncio.create_task(self._monitoring_loop())
        self.logger.info("Coordinator monitoring started (every 10 seconds)")

    async def stop_monitoring(self):
        """Остановка цикла мониторинга"""
        self.monitoring_active = False

        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
            self.monitoring_task = None

        self.logger.info("Coordinator monitoring stopped")

    async def _monitoring_loop(self):
        """Основной цикл мониторинга"""
        self.logger.info("Starting monitoring loop for coordinator metrics")

        while self.monitoring_active:
            try:
                await self._fetch_coordinator_metrics()
                await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                self.logger.info("Monitoring loop cancelled")
                break
            except Exception as e:
                self.logger.error(f"Unexpected error in monitoring loop: {e}")
                await asyncio.sleep(10)

    async def _fetch_coordinator_metrics(self):
        """
        ИСПРАВЛЕНИЕ: Получение метрик с координатора с дополнительными проверками
        """
        # Проверяем proxy в каждом цикле и пытаемся получить его если он отсутствует
        if not self.proxy:
            self.logger.debug("No proxy available - attempting to get proxy from service manager...")

            # Попытка получить proxy через глобальный реестр сервисов
            try:
                from layers.service_framework import get_global_service_manager
                service_manager = get_global_service_manager()
                if service_manager and service_manager.proxy_client:
                    self.set_proxy(service_manager.proxy_client)
                    self.logger.info("Successfully obtained proxy from global service manager")
            except Exception as e:
                self.logger.debug(f"Could not get proxy from service manager: {e}")

            # Если proxy все еще нет - пропускаем этот цикл
            if not self.proxy:
                self.logger.debug("No proxy available - skipping metrics collection")
                return

        request_time = datetime.now()
        self.stats["total_requests"] += 1

        network_start = datetime.now()

        try:
            # Время до вызова
            call_start = datetime.now()
            metrics = await self.proxy.system.coordinator.get_system_metrics()
            call_end = datetime.now()

            # Детализация времени
            network_time = (call_start - network_start).total_seconds() * 1000
            actual_call_time = (call_end - call_start).total_seconds() * 1000

            self.logger.info(
                f"Timing breakdown: Network setup: {network_time:.1f}ms, "
                f"Actual call: {actual_call_time:.1f}ms"
            )
        # try:
        #     metrics = await self.proxy.system.coordinator.get_system_metrics()
            source = "remote_coordinator"
            # Сначала пробуем определить тип узла
            # try:
            #     # Получаем информацию о текущем узле
            #     local_info = await self.proxy.system.get_system_info()
            #     is_coordinator = local_info.get('service_mode') == 'coordinator'
            #
            #     if is_coordinator:
            #         # Если мы на координаторе - берем локальные метрики
            #         metrics = await self.proxy.system.get_system_metrics()
            #         source = "local_coordinator"
            #     else:
            #         # Если мы worker - пытаемся получить метрики координатора
            #         try:
            #             metrics = await self.proxy.system.coordinator.get_system_metrics()
            #             source = "remote_coordinator"
            #         except Exception as remote_err:
            #             # Fallback на локальные метрики
            #             self.logger.warning(f"Remote coordinator call failed, using local: {remote_err}")
            #             metrics = await self.proxy.system.get_system_metrics()
            #             source = "local_fallback"
            #
            # except Exception as info_err:
            #     self.logger.warning(f"Could not determine node type: {info_err}, trying local metrics")
            #     metrics = await self.proxy.system.get_system_metrics()
            #     source = "unknown_type"

            # Обновляем статистику
            self.stats["successful_requests"] += 1
            self.stats["last_success"] = request_time.isoformat()

            # Сохраняем метрики с временной меткой
            metrics_entry = {
                "timestamp": request_time.isoformat(),
                "metrics": metrics,
                "source": source,
                "response_time_ms": (datetime.now() - request_time).total_seconds() * 1000
            }

            # Ограничиваем историю (последние 100 записей)
            self.metrics_history.append(metrics_entry)
            if len(self.metrics_history) > 100:
                self.metrics_history.pop(0)

            # Логируем каждые 5 запросов
            if self.stats["total_requests"] % 1 == 0:
                cpu_percent = metrics.get("cpu_percent", "N/A")
                memory_percent = metrics.get("memory", {}).get("percent", "N/A")
                self.logger.info(
                    f"Metrics #{self.stats['total_requests']} [{source}]: "
                    f"CPU {cpu_percent}%, Memory {memory_percent}%, "
                    f"Response: {metrics_entry['response_time_ms']:.1f}ms"
                )

        except Exception as e:
            # Обновляем статистику ошибок
            self.stats["failed_requests"] += 1
            self.stats["last_error"] = {
                "timestamp": request_time.isoformat(),
                "error": str(e),
                "error_type": type(e).__name__
            }

            self.logger.error(f"Failed to fetch coordinator metrics: {e}")

    @service_method(description="Get monitoring statistics", public=True)
    async def get_monitoring_stats(self) -> Dict[str, Any]:
        """Получение статистики мониторинга"""

        # Вычисляем uptime
        uptime_seconds = 0
        if self.stats["uptime_start"]:
            start_time = datetime.fromisoformat(self.stats["uptime_start"])
            uptime_seconds = (datetime.now() - start_time).total_seconds()

        # Вычисляем success rate
        success_rate = 0
        if self.stats["total_requests"] > 0:
            success_rate = (self.stats["successful_requests"] / self.stats["total_requests"]) * 100

        return {
            "service": self.service_name,
            "monitoring_active": self.monitoring_active,
            "proxy_available": self.proxy is not None,
            "proxy_retry_count": self._proxy_retry_count,  # ДОБАВЛЕНО для отладки
            "uptime_seconds": uptime_seconds,
            "statistics": {
                "total_requests": self.stats["total_requests"],
                "successful_requests": self.stats["successful_requests"],
                "failed_requests": self.stats["failed_requests"],
                "success_rate_percent": round(success_rate, 2)
            },
            "last_success": self.stats["last_success"],
            "last_error": self.stats["last_error"],
            "metrics_history_count": len(self.metrics_history)
        }

    # Остальные методы остаются без изменений
    @service_method(description="Get recent coordinator metrics", public=True)
    async def get_recent_metrics(self, limit: int = 10) -> Dict[str, Any]:
        """Получение последних метрик координатора"""

        recent_metrics = self.metrics_history[-limit:] if self.metrics_history else []

        # Вычисляем средние значения за период
        if recent_metrics:
            avg_cpu = sum(
                m["metrics"].get("cpu_percent", 0)
                for m in recent_metrics if isinstance(m["metrics"].get("cpu_percent"), (int, float))
            ) / len(recent_metrics)

            avg_memory = sum(
                m["metrics"].get("memory", {}).get("percent", 0)
                for m in recent_metrics if isinstance(m["metrics"].get("memory", {}).get("percent"), (int, float))
            ) / len(recent_metrics)

            avg_response_time = sum(
                m.get("response_time_ms", 0)
                for m in recent_metrics
            ) / len(recent_metrics)
        else:
            avg_cpu = avg_memory = avg_response_time = 0

        return {
            "service": self.service_name,
            "recent_metrics": recent_metrics,
            "averages": {
                "cpu_percent": round(avg_cpu, 2),
                "memory_percent": round(avg_memory, 2),
                "response_time_ms": round(avg_response_time, 2)
            },
            "period_start": recent_metrics[0]["timestamp"] if recent_metrics else None,
            "period_end": recent_metrics[-1]["timestamp"] if recent_metrics else None
        }

    @service_method(description="Control monitoring state", public=True)
    async def control_monitoring(self, action: str) -> Dict[str, Any]:
        """Управление состоянием мониторинга"""

        if action == "start":
            if not self.monitoring_active:
                await self.start_monitoring()
                return {"action": "start", "success": True, "message": "Monitoring started"}
            else:
                return {"action": "start", "success": False, "message": "Monitoring already active"}

        elif action == "stop":
            if self.monitoring_active:
                await self.stop_monitoring()
                return {"action": "stop", "success": True, "message": "Monitoring stopped"}
            else:
                return {"action": "stop", "success": False, "message": "Monitoring already inactive"}

        elif action == "restart":
            await self.stop_monitoring()
            await asyncio.sleep(1)
            await self.start_monitoring()
            return {"action": "restart", "success": True, "message": "Monitoring restarted"}

        elif action == "status":
            return {
                "action": "status",
                "monitoring_active": self.monitoring_active,
                "task_running": self.monitoring_task is not None and not self.monitoring_task.done(),
                "proxy_available": self.proxy is not None
            }

        else:
            return {"action": action, "success": False, "message": "Unknown action. Use: start, stop, restart, status"}

    @service_method(description="Clear metrics history", public=True)
    async def clear_history(self) -> Dict[str, Any]:
        """Очистка истории метрик"""
        old_count = len(self.metrics_history)
        self.metrics_history.clear()

        return {
            "action": "clear_history",
            "success": True,
            "cleared_records": old_count,
            "message": f"Cleared {old_count} metrics records"
        }

    @service_method(description="Test different proxy call types", public=True)
    async def test_proxy_calls(self) -> Dict[str, Any]:
        """Тестирование различных типов вызовов через прокси"""

        results = {
            "service": self.service_name,
            "test_timestamp": datetime.now().isoformat(),
            "proxy_available": self.proxy is not None,
            "tests": []
        }

        if not self.proxy:
            results["error"] = "No proxy available"
            return results

        # Тест 1: Локальный вызов
        try:
            start_time = datetime.now()
            local_metrics = await self.proxy.system.get_system_metrics()
            response_time = (datetime.now() - start_time).total_seconds() * 1

            results["tests"].append({
                "test": "local_system_call",
                "success": True,
                "response_time_ms": round(response_time, 2),
                "cpu_percent": local_metrics.get("cpu_percent", "N/A")
            })
        except Exception as e:
            results["tests"].append({
                "test": "local_system_call",
                "success": False,
                "error": str(e)
            })

        # Тест 2: Вызов к координатору (может не сработать если мы сами координатор)
        try:
            start_time = datetime.now()
            coordinator_metrics = await self.proxy.system.coordinator.get_system_metrics()
            response_time = (datetime.now() - start_time).total_seconds() * 1000

            results["tests"].append({
                "test": "coordinator_system_call",
                "success": True,
                "response_time_ms": round(response_time, 2),
                "cpu_percent": coordinator_metrics.get("cpu_percent", "N/A")
            })
        except Exception as e:
            results["tests"].append({
                "test": "coordinator_system_call",
                "success": False,
                "error": str(e),
                "note": "This may fail if running on coordinator itself"
            })

        # Тест 3: Информация о системе
        try:
            system_info = await self.proxy.system.get_system_info()
            results["tests"].append({
                "test": "system_info_call",
                "success": True,
                "hostname": system_info.get("hostname", "N/A"),
                "service_mode": system_info.get("service_mode", "N/A")
            })
        except Exception as e:
            results["tests"].append({
                "test": "system_info_call",
                "success": False,
                "error": str(e)
            })

        # Подсчет статистики
        successful_tests = len([t for t in results["tests"] if t.get("success", False)])
        results["summary"] = {
            "total_tests": len(results["tests"]),
            "successful": successful_tests,
            "failed": len(results["tests"]) - successful_tests,
            "success_rate": f"{(successful_tests / len(results['tests']) * 100):.1f}%" if results["tests"] else "0%"
        }

        return results