# services/example/main.py - Пример сервиса с использованием реактивной системы метрик

import asyncio
import time
import random
from typing import Dict, Any, List
from layers.service import BaseService, service_method


class Run(BaseService):
    """Пример сервиса с демонстрацией различных типов метрик"""

    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.request_count = 0
        self.active_connections = 0
        self.database_size = 1024  # MB
        self.error_rate = 0.0
        self.processing_queue = []

    async def initialize(self):
        """Инициализация сервиса с настройкой метрик"""
        self.logger.info("Initializing example service with metrics")

        # Базовые метрики при запуске
        self.metric("service_version", "1.0.0")
        self.metric("service_type", "example")
        self.metric("database_size_mb", self.database_size)
        self.metric("max_connections", 100)

        # Запускаем фоновые задачи для обновления внутренних метрик
        asyncio.create_task(self._update_database_metrics())
        asyncio.create_task(self._simulate_background_activity())
        asyncio.create_task(self._monitor_queue_size())

        self.logger.info("Example service initialized successfully")

    async def cleanup(self):
        """Очистка ресурсов"""
        self.logger.info("Cleaning up example service")
        self.metric("service_status", "shutting_down")

    async def _update_database_metrics(self):
        """Фоновая задача для обновления метрик БД"""
        while self.status.value == "running":
            try:
                # Симуляция изменения размера БД
                old_size = self.database_size

                # Случайные изменения размера (рост или небольшое уменьшение)
                change = random.uniform(-10, 50)  # MB
                self.database_size = max(100, self.database_size + change)

                # Обновляем метрику только при значительном изменении (>1MB)
                if abs(self.database_size - old_size) > 1:
                    self.metric("database_size_mb", round(self.database_size, 2))
                    self.logger.debug(f"Database size updated: {self.database_size:.2f} MB")

                # Дополнительные метрики БД
                table_count = random.randint(50, 100)
                self.metric("database_table_count", table_count)

                # Симуляция индексов
                index_size = self.database_size * 0.2  # 20% от основных данных
                self.metric("database_index_size_mb", round(index_size, 2))

                # Cache hit ratio
                cache_hit_ratio = random.uniform(0.7, 0.95)
                self.metric("database_cache_hit_ratio", round(cache_hit_ratio, 3))

            except Exception as e:
                self.logger.error(f"Error updating database metrics: {e}")
                self.metric("database_metric_errors", 1, "counter")

            # Обновление каждые 30 секунд
            await asyncio.sleep(30)

    async def _simulate_background_activity(self):
        """Симуляция фоновой активности для демонстрации метрик"""
        while self.status.value == "running":
            try:
                # Симуляция обработки задач
                tasks_processed = random.randint(0, 10)
                if tasks_processed > 0:
                    self.metric("background_tasks_processed", tasks_processed, "counter")

                # Симуляция ошибок (редко)
                if random.random() < 0.05:  # 5% вероятность ошибки
                    self.metric("background_errors", 1, "counter")
                    self.logger.warning("Simulated background error occurred")

                # CPU usage simulation
                cpu_usage = random.uniform(10, 80)
                self.metric("service_cpu_usage_percent", round(cpu_usage, 1))

                # Memory usage simulation
                memory_usage = random.uniform(50, 200)  # MB
                self.metric("service_memory_usage_mb", round(memory_usage, 1))

            except Exception as e:
                self.logger.error(f"Error in background activity: {e}")

            await asyncio.sleep(15)

    async def _monitor_queue_size(self):
        """Мониторинг размера очереди обработки"""
        while self.status.value == "running":
            try:
                # Симуляция изменения размера очереди
                if random.random() < 0.3:  # 30% шанс добавить элементы
                    new_items = random.randint(1, 5)
                    self.processing_queue.extend([f"task_{i}" for i in range(new_items)])

                if random.random() < 0.5 and self.processing_queue:  # 50% шанс обработать элементы
                    processed = min(random.randint(1, 3), len(self.processing_queue))
                    for _ in range(processed):
                        self.processing_queue.pop(0)
                    self.metric("queue_items_processed", processed, "counter")

                # Обновляем метрику размера очереди
                queue_size = len(self.processing_queue)
                self.metric("processing_queue_size", queue_size)

                # Alert при большой очереди
                if queue_size > 20:
                    self.metric("queue_size_alerts", 1, "counter")
                    self.logger.warning(f"Processing queue is large: {queue_size} items")

            except Exception as e:
                self.logger.error(f"Error monitoring queue: {e}")

            await asyncio.sleep(10)

    @service_method(description="Process a request with automatic metrics", public=True)
    async def process_request(self, request_id: str, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Обработка запроса с автоматическими метриками через декоратор"""

        # Увеличиваем счетчик активных соединений
        self.active_connections += 1
        self.metric("active_connections", self.active_connections)

        try:
            # Симуляция обработки
            processing_time = random.uniform(0.1, 2.0)  # 0.1-2 секунды
            await asyncio.sleep(processing_time)

            # Симуляция различных результатов
            if random.random() < 0.1:  # 10% вероятность ошибки
                self.error_rate += 0.01
                self.metric("service_error_rate", round(self.error_rate, 3))
                raise Exception("Simulated processing error")

            # Успешная обработка
            result = {
                "request_id": request_id,
                "status": "completed",
                "processing_time_ms": round(processing_time * 1000, 2),
                "timestamp": time.time()
            }

            # Дополнительные метрики
            self.metric("last_request_size_bytes", len(str(data)) if data else 0)

            return result

        finally:
            # Уменьшаем счетчик активных соединений
            self.active_connections = max(0, self.active_connections - 1)
            self.metric("active_connections", self.active_connections)

    @service_method(description="Get current service statistics", public=True)
    async def get_statistics(self) -> Dict[str, Any]:
        """Получение текущей статистики сервиса"""

        # Эти метрики автоматически добавятся через декоратор
        return {
            "database_size_mb": self.database_size,
            "active_connections": self.active_connections,
            "queue_size": len(self.processing_queue),
            "error_rate": self.error_rate,
            "uptime_seconds": self._get_uptime(),
            "service_metrics_count": len(self.metrics.data)
        }

    @service_method(description="Simulate heavy processing task", public=True)
    async def heavy_task(self, duration: float = 5.0, complexity: str = "medium") -> Dict[str, Any]:
        """Симуляция тяжелой задачи с детальными метриками"""

        # Используем timing context для измерения времени
        with self.metrics.timing_context("heavy_task_total_duration"):
            # Метрики по типу сложности
            complexity_multiplier = {"low": 0.5, "medium": 1.0, "high": 2.0}.get(complexity, 1.0)
            actual_duration = duration * complexity_multiplier

            self.metric(f"heavy_task_{complexity}_started", 1, "counter")
            self.metric("heavy_task_complexity_multiplier", complexity_multiplier)

            # Симуляция этапов обработки
            stages = ["initialization", "processing", "validation", "cleanup"]
            results = {}

            for i, stage in enumerate(stages):
                stage_duration = actual_duration / len(stages)

                with self.metrics.timing_context(f"heavy_task_{stage}_duration"):
                    await asyncio.sleep(stage_duration)

                    # Метрики по этапам
                    self.metric(f"heavy_task_{stage}_completed", 1, "counter")

                    # Симуляция промежуточных результатов
                    stage_result = random.randint(100, 1000)
                    results[stage] = stage_result

                    # Обновляем прогресс
                    progress = ((i + 1) / len(stages)) * 100
                    self.metric("heavy_task_progress_percent", round(progress, 1))

            # Финальные метрики
            total_result = sum(results.values())
            self.metric("heavy_task_total_result", total_result)
            self.metric("heavy_task_completed", 1, "counter")

            return {
                "status": "completed",
                "complexity": complexity,
                "actual_duration": actual_duration,
                "stages": results,
                "total_result": total_result,
                "timestamp": time.time()
            }

    @service_method(description="Update internal configuration with metrics", public=True)
    async def update_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Обновление конфигурации с отслеживанием изменений в метриках"""

        changes_made = 0

        # Обновляем max_connections
        if "max_connections" in config:
            old_max = self.metrics.get_metric("max_connections")
            old_value = old_max["value"] if old_max else 100
            new_value = config["max_connections"]

            if old_value != new_value:
                self.metric("max_connections", new_value)
                self.metric("config_changes", 1, "counter")
                changes_made += 1
                self.logger.info(f"Max connections updated: {old_value} -> {new_value}")

        # Обновляем database_size (например, после очистки)
        if "reset_database_size" in config and config["reset_database_size"]:
            old_size = self.database_size
            self.database_size = random.uniform(500, 1000)
            self.metric("database_size_mb", round(self.database_size, 2))
            self.metric("database_resets", 1, "counter")
            changes_made += 1
            self.logger.info(f"Database size reset: {old_size:.2f} -> {self.database_size:.2f} MB")

        # Обновляем error_rate
        if "reset_error_rate" in config and config["reset_error_rate"]:
            old_rate = self.error_rate
            self.error_rate = 0.0
            self.metric("service_error_rate", self.error_rate)
            self.metric("error_rate_resets", 1, "counter")
            changes_made += 1
            self.logger.info(f"Error rate reset: {old_rate:.3f} -> {self.error_rate:.3f}")

        # Общая метрика конфигураций
        self.metric("total_config_updates", 1, "counter")

        return {
            "status": "success",
            "changes_made": changes_made,
            "timestamp": time.time(),
            "applied_config": {k: v for k, v in config.items() if not k.startswith("_")}
        }

    @service_method(description="Get detailed metrics information", public=True)
    async def get_detailed_metrics(self) -> Dict[str, Any]:
        """Получение детальной информации о метриках сервиса"""

        all_metrics = self.metrics.get_all_metrics()

        # Группировка метрик по типам
        metrics_by_type = {"gauge": {}, "counter": {}, "timer": {}, "histogram": {}}

        for name, data in all_metrics.items():
            metric_type = data.get("type", "gauge")
            metrics_by_type[metric_type][name] = data

        return {
            "service_name": self.service_name,
            "total_metrics": len(all_metrics),
            "metrics_by_type": {
                k: {"count": len(v), "metrics": v}
                for k, v in metrics_by_type.items()
            },
            "metrics_summary": self.metrics.get_metrics_summary(),
            "last_update": self.metrics.last_update
        }

    @service_method(description="Trigger manual metrics update", public=True, track_metrics=False)
    async def force_metrics_update(self) -> Dict[str, Any]:
        """Принудительное обновление всех внутренних метрик"""

        # Обновляем все внутренние значения с force_push=True
        self.metric("database_size_mb", self.database_size, force_push=True)
        self.metric("active_connections", self.active_connections, force_push=True)
        self.metric("processing_queue_size", len(self.processing_queue), force_push=True)
        self.metric("service_error_rate", self.error_rate, force_push=True)

        # Добавляем timestamp принудительного обновления
        self.metric("last_forced_update", time.time())

        return {
            "status": "metrics_updated",
            "timestamp": time.time(),
            "total_metrics": len(self.metrics.data),
            "message": "All internal metrics have been force-pushed to ServiceManager"
        }