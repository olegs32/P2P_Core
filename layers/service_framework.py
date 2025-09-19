# service_framework.py - Фреймворк для сервисов P2P системы с интегрированными метриками

import asyncio
import inspect
import logging
import time
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Callable, Type, Union
from pathlib import Path
import importlib.util
import sys
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict, deque
import threading


class ServiceStatus(Enum):
    """Статусы сервиса"""
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class MetricType(Enum):
    """Типы метрик"""
    GAUGE = "gauge"  # Текущее значение
    COUNTER = "counter"  # Накопительный счетчик
    TIMER = "timer"  # Timing метрики
    HISTOGRAM = "histogram"  # Распределение значений


@dataclass
class MetricEntry:
    """Запись метрики"""
    name: str
    value: Any
    metric_type: MetricType
    timestamp: float
    service_id: str


@dataclass
class ServiceInfo:
    """Информация о сервисе"""
    name: str
    version: str = "1.0.0"
    description: str = ""
    dependencies: List[str] = field(default_factory=list)
    exposed_methods: List[str] = field(default_factory=list)
    status: ServiceStatus = ServiceStatus.STOPPED
    node_id: str = ""
    domain: str = "default"
    metadata: Dict[str, Any] = field(default_factory=dict)


class MetricsState:
    """Реактивная система метрик для сервиса"""

    def __init__(self, service_name: str, manager_callback: Callable = None):
        self.service_name = service_name
        self.data: Dict[str, Dict] = {}  # name -> {value, type, timestamp, history}
        self.manager_callback = manager_callback
        self.last_update = time.time()
        self.throttle_interval = 1.0  # Максимум 1 push в секунду на метрику
        self.last_push = {}  # name -> timestamp последнего push
        self.history_size = 100  # Размер истории для каждой метрики
        self._lock = threading.Lock()

        # Автоматические метрики
        self._setup_automatic_metrics()

    def _setup_automatic_metrics(self):
        """Настройка автоматических метрик"""
        # Сначала создаем счетчик обновлений БЕЗ вызова set()
        current_time = time.time()
        self.data["metrics_updates_total"] = {
            'value': 0,
            'type': MetricType.COUNTER,
            'timestamp': current_time,
            'history': deque(maxlen=self.history_size),
            'update_count': 0
        }

        # Теперь можно использовать обычный set()
        self.set("service_startup_time", current_time, MetricType.GAUGE)

    def set(self, name: str, value: Any, metric_type: MetricType = MetricType.GAUGE, force_push: bool = False):
        """Установка значения метрики с автоматическим push при изменении"""
        current_time = time.time()

        with self._lock:
            # Проверяем изменение значения
            old_data = self.data.get(name, {})
            old_value = old_data.get('value')

            # Инициализируем структуру метрики если нужно
            if name not in self.data:
                self.data[name] = {
                    'value': None,
                    'type': metric_type,
                    'timestamp': current_time,
                    'history': deque(maxlen=self.history_size),
                    'update_count': 0
                }

            metric_data = self.data[name]

            # Обновляем значение и историю
            if metric_type == MetricType.COUNTER and old_value is not None:
                # Для счетчиков - инкрементальное обновление
                if isinstance(value, (int, float)) and value > 0:
                    new_value = old_value + value
                else:
                    new_value = value
            else:
                new_value = value

            # Проверяем необходимость push (изменилось ли значение)
            value_changed = old_value != new_value
            should_push = force_push or value_changed

            # Throttling check
            last_push_time = self.last_push.get(name, 0)
            throttle_passed = (current_time - last_push_time) >= self.throttle_interval

            if should_push and throttle_passed:
                # Обновляем данные
                metric_data['value'] = new_value
                metric_data['timestamp'] = current_time
                metric_data['update_count'] += 1
                metric_data['history'].append({
                    'value': new_value,
                    'timestamp': current_time
                })

                self.last_update = current_time
                self.last_push[name] = current_time

                # Обновляем счетчик метрик
                if name != "metrics_updates_total" and "metrics_updates_total" in self.data:
                    self.data["metrics_updates_total"]["value"] += 1

                # Push в ServiceManager
                if self.manager_callback and value_changed:
                    try:
                        self.manager_callback(name, new_value, current_time, metric_type)
                    except Exception as e:
                        logging.getLogger(f"Metrics.{self.service_name}").error(
                            f"Failed to push metric {name}: {e}"
                        )

            elif should_push and not throttle_passed:
                # Обновляем локально, но без push (throttling)
                metric_data['value'] = new_value
                metric_data['timestamp'] = current_time
                self.last_update = current_time

    def gauge(self, name: str, value: Union[int, float]):
        """Установка gauge метрики"""
        self.set(name, value, MetricType.GAUGE)

    def counter(self, name: str, value: Union[int, float] = 1, increment: bool = True):
        """Обновление counter метрики"""
        if increment:
            self.set(name, value, MetricType.COUNTER)  # Будет добавлено к существующему
        else:
            # Установка абсолютного значения
            current_time = time.time()
            with self._lock:
                if name not in self.data:
                    self.data[name] = {
                        'value': 0,
                        'type': MetricType.COUNTER,
                        'timestamp': current_time,
                        'history': deque(maxlen=self.history_size),
                        'update_count': 0
                    }
                self.data[name]['value'] = value
                self.data[name]['timestamp'] = current_time

    def timer(self, name: str, duration_ms: float):
        """Записать timing метрику"""
        self.set(name, duration_ms, MetricType.TIMER)

    def histogram(self, name: str, value: Union[int, float]):
        """Записать histogram значение"""
        self.set(name, value, MetricType.HISTOGRAM)

    def increment(self, name: str, value: Union[int, float] = 1):
        """Инкремент counter"""
        self.counter(name, value, increment=True)

    def timing_context(self, name: str):
        """Context manager для измерения времени"""
        return TimingContext(self, name)

    def get_metric(self, name: str) -> Optional[Dict]:
        """Получить метрику по имени"""
        return self.data.get(name)

    def get_all_metrics(self) -> Dict[str, Any]:
        """Получить все метрики"""
        with self._lock:
            return {
                name: {
                    'value': data['value'],
                    'type': data['type'].value,
                    'timestamp': data['timestamp'],
                    'update_count': data['update_count']
                }
                for name, data in self.data.items()
            }

    def get_metrics_summary(self) -> Dict[str, Any]:
        """Получить краткую сводку метрик"""
        with self._lock:
            return {
                'total_metrics': len(self.data),
                'last_update': self.last_update,
                'update_count': self.data.get('metrics_updates_total', {}).get('value', 0)
            }


class TimingContext:
    """Context manager для измерения времени выполнения"""

    def __init__(self, metrics_state: MetricsState, metric_name: str):
        self.metrics = metrics_state
        self.metric_name = metric_name
        self.start_time = None

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time:
            duration_ms = (time.time() - self.start_time) * 1000
            self.metrics.timer(self.metric_name, duration_ms)


def service_method(
        description: str = "",
        public: bool = True,
        cache_ttl: int = 0,
        requires_auth: bool = True,
        track_metrics: bool = True
):
    """Декоратор для методов сервиса с автоматическими метриками"""

    def decorator(func):
        func._service_method = True
        func._service_description = description
        func._service_public = public
        func._service_cache_ttl = cache_ttl
        func._service_requires_auth = requires_auth
        func._service_track_metrics = track_metrics

        if track_metrics:
            # Wrapper для автоматических метрик
            original_func = func

            async def metrics_wrapper(*args, **kwargs):
                service_instance = args[0] if args else None
                if hasattr(service_instance, 'metrics'):
                    method_name = func.__name__

                    # Метрики вызовов
                    service_instance.metrics.increment(f"method_{method_name}_calls")

                    # Timing метрика
                    start_time = time.time()
                    try:
                        result = await original_func(*args, **kwargs)
                        service_instance.metrics.increment(f"method_{method_name}_success")
                        return result
                    except Exception as e:
                        service_instance.metrics.increment(f"method_{method_name}_errors")
                        raise
                    finally:
                        duration_ms = (time.time() - start_time) * 1000
                        service_instance.metrics.timer(f"method_{method_name}_duration_ms", duration_ms)
                else:
                    return await original_func(*args, **kwargs)

            metrics_wrapper.__name__ = func.__name__
            metrics_wrapper.__doc__ = func.__doc__
            metrics_wrapper._service_method = True
            metrics_wrapper._service_description = description
            metrics_wrapper._service_public = public
            metrics_wrapper._service_cache_ttl = cache_ttl
            metrics_wrapper._service_requires_auth = requires_auth
            metrics_wrapper._service_track_metrics = track_metrics

            return metrics_wrapper

        return func

    return decorator


class BaseService(ABC):
    """Базовый класс для всех сервисов с интегрированными метриками"""

    def __init__(self, service_name: str, proxy_client=None):
        self._proxy_set_callback = None
        self.service_name = service_name
        self.proxy = proxy_client
        self.logger = logging.getLogger(f"Service.{service_name}")
        self.status = ServiceStatus.STOPPED
        self.info = ServiceInfo(name=service_name)

        # Инициализация системы метрик
        self.metrics = MetricsState(
            service_name=service_name,
            manager_callback=self._push_metrics_to_manager
        )

        self._extract_service_info()

        # Регистрируем базовые метрики
        self._setup_base_metrics()

    def _setup_base_metrics(self):
        """Настройка базовых метрик сервиса"""
        import psutil
        import os

        # Базовая информация о сервисе
        self.metrics.gauge("service_pid", os.getpid())
        self.metrics.gauge("service_status", 0)  # 0=stopped, 1=starting, 2=running, 3=stopping, 4=error

        # Запускаем background задачу для системных метрик
        asyncio.create_task(self._update_system_metrics())

    async def _update_system_metrics(self):
        """Background задача для обновления системных метрик"""
        while self.status in [ServiceStatus.STARTING, ServiceStatus.RUNNING]:
            try:
                import psutil
                process = psutil.Process()

                # Память
                memory_info = process.memory_info()
                self.metrics.gauge("memory_usage_bytes", memory_info.rss)
                self.metrics.gauge("memory_usage_mb", memory_info.rss / 1024 / 1024)

                # CPU (требует интервала)
                cpu_percent = process.cpu_percent()
                if cpu_percent > 0:  # Избегаем 0 при первом вызове
                    self.metrics.gauge("cpu_usage_percent", cpu_percent)

                # Файловые дескрипторы
                try:
                    self.metrics.gauge("open_files", process.num_fds())
                except (AttributeError, psutil.AccessDenied):
                    pass  # Не доступно на некоторых системах

                # Потоки
                self.metrics.gauge("thread_count", process.num_threads())

            except Exception as e:
                self.logger.warning(f"Error updating system metrics: {e}")

            await asyncio.sleep(30)  # Обновление каждые 30 секунд

    def _push_metrics_to_manager(self, metric_name: str, value: Any, timestamp: float, metric_type: MetricType):
        """Callback для отправки метрик в ServiceManager"""
        # Будет подключен ServiceManager при регистрации
        manager = get_global_service_manager()
        if manager and hasattr(manager, 'on_metrics_push'):
            try:
                manager.on_metrics_push(self.service_name, metric_name, value, timestamp, metric_type)
            except Exception as e:
                self.logger.error(f"Failed to push metric to manager: {e}")

    def _extract_service_info(self):
        """Извлекает информацию о сервисе из методов класса"""
        methods = []
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if hasattr(method, '_service_method') and method._service_public:
                methods.append(name)

        self.info.exposed_methods = methods
        self.info.description = self.__class__.__doc__ or ""

    def metric(self, name: str, value: Any, metric_type: str = "gauge"):
        """Удобный сеттер для метрик"""
        if isinstance(metric_type, str):
            try:
                metric_type_enum = MetricType(metric_type.lower())
            except ValueError:
                metric_type_enum = MetricType.GAUGE
        else:
            metric_type_enum = metric_type

        self.metrics.set(name, value, metric_type_enum)

    @abstractmethod
    async def initialize(self):
        """Инициализация сервиса (переопределяется наследниками)"""
        pass

    @abstractmethod
    async def cleanup(self):
        """Очистка ресурсов при остановке"""
        pass

    async def start(self):
        """Запуск сервиса"""
        try:
            self.status = ServiceStatus.STARTING
            self.metrics.gauge("service_status", 1)
            self.logger.info(f"Starting service {self.service_name}")

            await self.initialize()

            self.status = ServiceStatus.RUNNING
            self.metrics.gauge("service_status", 2)
            self.metrics.gauge("service_uptime_start", time.time())
            self.logger.info(f"Service {self.service_name} started successfully")

        except Exception as e:
            self.status = ServiceStatus.ERROR
            self.metrics.gauge("service_status", 4)
            self.metrics.increment("service_start_errors")
            self.logger.error(f"Failed to start service {self.service_name}: {e}")
            raise

    async def stop(self):
        """Остановка сервиса"""
        try:
            self.status = ServiceStatus.STOPPING
            self.metrics.gauge("service_status", 3)
            self.logger.info(f"Stopping service {self.service_name}")

            await self.cleanup()

            self.status = ServiceStatus.STOPPED
            self.metrics.gauge("service_status", 0)

            # Финальные метрики
            uptime_start = self.metrics.get_metric("service_uptime_start")
            if uptime_start and uptime_start['value']:
                total_uptime = time.time() - uptime_start['value']
                self.metrics.gauge("service_total_uptime_seconds", total_uptime)

            self.logger.info(f"Service {self.service_name} stopped")

        except Exception as e:
            self.status = ServiceStatus.ERROR
            self.metrics.gauge("service_status", 4)
            self.logger.error(f"Error stopping service {self.service_name}: {e}")
            raise

    @service_method(description="Get service information", public=True, track_metrics=False)
    async def get_service_info(self) -> Dict[str, Any]:
        """Получить информацию о сервисе"""
        uptime = 0
        uptime_start = self.metrics.get_metric("service_uptime_start")
        if uptime_start and uptime_start['value'] and self.status == ServiceStatus.RUNNING:
            uptime = time.time() - uptime_start['value']

        return {
            "name": self.info.name,
            "version": self.info.version,
            "description": self.info.description,
            "status": self.status.value,
            "uptime_seconds": uptime,
            "exposed_methods": self.info.exposed_methods,
            "dependencies": self.info.dependencies,
            "domain": self.info.domain,
            "metadata": self.info.metadata,
            "metrics_summary": self.metrics.get_metrics_summary()
        }

    @service_method(description="Health check", public=True, track_metrics=False)
    async def health_check(self) -> Dict[str, Any]:
        """Проверка состояния сервиса"""
        return {
            "status": "healthy" if self.status == ServiceStatus.RUNNING else "unhealthy",
            "service": self.service_name,
            "uptime": self._get_uptime(),
            "last_metrics_update": self.metrics.last_update,
            "total_metrics": len(self.metrics.data),
            "last_check": time.time()
        }

    @service_method(description="Get service metrics", public=True, track_metrics=False)
    async def get_metrics(self) -> Dict[str, Any]:
        """Получить все метрики сервиса"""
        return {
            "service_name": self.service_name,
            "timestamp": time.time(),
            "metrics": self.metrics.get_all_metrics()
        }

    def _get_uptime(self) -> float:
        """Получить uptime сервиса"""
        uptime_start = self.metrics.get_metric("service_uptime_start")
        if uptime_start and uptime_start['value'] and self.status == ServiceStatus.RUNNING:
            return time.time() - uptime_start['value']
        return 0

    def set_proxy(self, proxy_client):
        """
        ИСПРАВЛЕНИЕ: Улучшенный метод установки proxy
        """
        old_proxy = self.proxy
        self.proxy = proxy_client

        if old_proxy is None and proxy_client is not None:
            self.logger.info(f"Proxy successfully set for service: {self.service_name}")

            # Вызываем callback если он был установлен
            if self._proxy_set_callback:
                try:
                    if asyncio.iscoroutinefunction(self._proxy_set_callback):
                        asyncio.create_task(self._proxy_set_callback())
                    else:
                        self._proxy_set_callback()
                except Exception as e:
                    self.logger.error(f"Error in proxy set callback: {e}")

        return proxy_client is not None

    def on_proxy_set(self, callback):
        """Установить callback который вызывается когда proxy установлен"""
        self._proxy_set_callback = callback


class ReactiveMetricsCollector:
    """Реактивный сборщик метрик для ServiceManager"""

    def __init__(self):
        self.services: Dict[str, Dict] = {}  # service_id -> service_metrics_state
        self.aggregated_metrics: Dict[str, Any] = {}
        self.health_threshold = 90  # секунд для определения dead сервиса
        self.logger = logging.getLogger("MetricsCollector")
        self._lock = threading.Lock()

        # Запуск background задач
        asyncio.create_task(self._health_monitor_loop())
        asyncio.create_task(self._aggregation_loop())

    def register_service(self, service_id: str, service_instance):
        """Регистрация сервиса в системе метрик"""
        with self._lock:
            self.services[service_id] = {
                'service_instance': service_instance,
                'last_seen': time.time(),
                'status': 'alive',
                'metrics': {},
                'total_updates': 0
            }

        self.logger.info(f"Service {service_id} registered in metrics system")

    def on_metrics_push(self, service_id: str, metric_name: str, value: Any, timestamp: float, metric_type: MetricType):
        """Обработка push обновлений от сервисов"""
        current_time = time.time()

        with self._lock:
            if service_id not in self.services:
                # Автоматическая регистрация при первом push
                self.services[service_id] = {
                    'service_instance': None,
                    'last_seen': current_time,
                    'status': 'alive',
                    'metrics': {},
                    'total_updates': 0
                }
                self.logger.info(f"Auto-registered service {service_id} from metrics push")

            service_data = self.services[service_id]
            service_data['last_seen'] = current_time
            service_data['status'] = 'alive'
            service_data['total_updates'] += 1

            # Сохраняем метрику
            service_data['metrics'][metric_name] = {
                'value': value,
                'timestamp': timestamp,
                'type': metric_type.value if isinstance(metric_type, MetricType) else metric_type
            }

        self.logger.debug(f"Received metric from {service_id}: {metric_name} = {value}")

    async def _health_monitor_loop(self):
        """Background проверка здоровья сервисов"""
        while True:
            try:
                current_time = time.time()
                dead_services = []

                with self._lock:
                    for service_id, service_data in self.services.items():
                        last_seen = service_data['last_seen']
                        time_since_seen = current_time - last_seen

                        if time_since_seen > self.health_threshold:
                            if service_data['status'] != 'dead':
                                service_data['status'] = 'dead'
                                dead_services.append(service_id)
                        else:
                            if service_data['status'] == 'dead':
                                service_data['status'] = 'alive'
                                self.logger.info(f"Service {service_id} recovered")

                # Логируем мертвые сервисы
                for service_id in dead_services:
                    self.logger.warning(
                        f"Service {service_id} marked as dead (no activity for {self.health_threshold}s)")

            except Exception as e:
                self.logger.error(f"Error in health monitor loop: {e}")

            await asyncio.sleep(30)  # Проверка каждые 30 секунд

    async def _aggregation_loop(self):
        """Background агрегация метрик"""
        while True:
            try:
                self._update_aggregated_metrics()
            except Exception as e:
                self.logger.error(f"Error in aggregation loop: {e}")

            await asyncio.sleep(60)  # Агрегация каждую минуту

    def _update_aggregated_metrics(self):
        """Обновление агрегированных метрик узла"""
        current_time = time.time()

        with self._lock:
            alive_services = [s for s in self.services.values() if s['status'] == 'alive']

            # Базовые метрики узла
            self.aggregated_metrics = {
                'node_metrics_timestamp': current_time,
                'total_services': len(self.services),
                'alive_services': len(alive_services),
                'dead_services': len(self.services) - len(alive_services),
                'total_metrics_updates': sum(s['total_updates'] for s in self.services.values()),
                'services': {}
            }

            # Агрегированные метрики по сервисам
            for service_id, service_data in self.services.items():
                if service_data['status'] == 'alive':
                    self.aggregated_metrics['services'][service_id] = {
                        'status': service_data['status'],
                        'last_seen': service_data['last_seen'],
                        'metrics_count': len(service_data['metrics']),
                        'total_updates': service_data['total_updates']
                    }

    def get_service_metrics(self, service_id: str) -> Optional[Dict]:
        """Получить метрики конкретного сервиса"""
        with self._lock:
            service_data = self.services.get(service_id)
            if service_data:
                return {
                    'service_id': service_id,
                    'status': service_data['status'],
                    'last_seen': service_data['last_seen'],
                    'metrics': service_data['metrics'].copy(),
                    'total_updates': service_data['total_updates']
                }
        return None

    def get_aggregated_metrics(self) -> Dict[str, Any]:
        """Получить агрегированные метрики узла"""
        with self._lock:
            return self.aggregated_metrics.copy()

    def get_all_services_health(self) -> Dict[str, Any]:
        """Получить health status всех сервисов"""
        with self._lock:
            return {
                service_id: {
                    'status': data['status'],
                    'last_seen': data['last_seen'],
                    'uptime': time.time() - data['last_seen'] if data['status'] == 'alive' else 0
                }
                for service_id, data in self.services.items()
            }


class ServiceRegistry:
    """Реестр сервисов для автоматической регистрации и управления"""

    def __init__(self, rpc_methods_instance):
        self.services: Dict[str, BaseService] = {}
        self.service_classes: Dict[str, Type[BaseService]] = {}
        self.rpc_methods = rpc_methods_instance
        self.logger = logging.getLogger("ServiceRegistry")

    async def register_service_class(self, service_class: Type[BaseService], proxy_client=None):
        """Регистрация класса сервиса"""
        service_name = getattr(service_class, 'SERVICE_NAME', service_class.__name__.lower())
        self.service_classes[service_name] = service_class
        self.logger.info(f"Registered service class: {service_name}")

        # Создаем экземпляр и запускаем
        service_instance = service_class(service_name, proxy_client)
        await self.start_service(service_name, service_instance)

    async def start_service(self, service_name: str, service_instance: BaseService):
        """Запуск сервиса и регистрация его методов в RPC"""
        try:
            await service_instance.start()
            self.services[service_name] = service_instance

            # Регистрируем все публичные методы сервиса в RPC
            await self.rpc_methods.register_rpc_methods(service_name, service_instance)

            self.logger.info(f"Service {service_name} started and registered")

        except Exception as e:
            self.logger.error(f"Failed to start service {service_name}: {e}")
            raise

    async def stop_service(self, service_name: str):
        """Остановка сервиса"""
        if service_name in self.services:
            service = self.services[service_name]
            await service.stop()
            del self.services[service_name]
            self.logger.info(f"Service {service_name} stopped and unregistered")

    async def reload_service(self, service_name: str):
        """Перезагрузка сервиса"""
        if service_name in self.services:
            await self.stop_service(service_name)

        if service_name in self.service_classes:
            service_class = self.service_classes[service_name]
            await self.register_service_class(service_class)

    def get_service(self, service_name: str) -> Optional[BaseService]:
        """Получить экземпляр сервиса"""
        return self.services.get(service_name)

    def list_services(self) -> Dict[str, Dict[str, Any]]:
        """Список всех сервисов"""
        return {
            name: {
                "status": service.status.value,
                "info": service.info.__dict__,
                "methods": service.info.exposed_methods
            }
            for name, service in self.services.items()
        }


class ServiceLoader:
    """Загрузчик сервисов из файловой системы"""

    def __init__(self, services_directory: Path, registry: ServiceRegistry):
        self.services_dir = services_directory
        self.registry = registry
        self.logger = logging.getLogger("ServiceLoader")

    async def discover_and_load_services(self, proxy_client=None):
        """Обнаружение и загрузка всех сервисов"""
        if not self.services_dir.exists():
            self.logger.warning(f"Services directory not found: {self.services_dir}")
            return

        for service_dir in self.services_dir.iterdir():
            if not service_dir.is_dir():
                continue

            await self.load_service_from_directory(service_dir, proxy_client)

    async def load_service_from_directory(self, service_dir: Path, proxy_client=None):
        """Загрузка сервиса из директории"""
        service_name = service_dir.name
        main_file = service_dir / "main.py"

        if not main_file.exists():
            self.logger.warning(f"No main.py found in service directory: {service_dir}")
            return

        try:
            # Загружаем модуль
            spec = importlib.util.spec_from_file_location(f"service_{service_name}", main_file)
            module = importlib.util.module_from_spec(spec)
            sys.modules[f"service_{service_name}"] = module
            spec.loader.exec_module(module)

            # Ищем класс Run
            if hasattr(module, 'Run'):
                run_class = module.Run

                # Проверяем, наследуется ли от BaseService
                if not issubclass(run_class, BaseService):
                    self.logger.error(f"Service {service_name}: Run class must inherit from BaseService")
                    return

                # Регистрируем сервис
                await self.registry.register_service_class(run_class, proxy_client)
                self.logger.info(f"Successfully loaded service: {service_name}")

            else:
                self.logger.error(f"No Run class found in {main_file}")

        except Exception as e:
            self.logger.error(f"Failed to load service {service_name}: {e}")


class ServiceManager:
    """Менеджер сервисов с улучшенной инжекцией proxy и интегрированными метриками"""

    def __init__(self, rpc_handler):
        self.rpc = rpc_handler
        self.services = {}
        self.registry = ServiceRegistry(rpc_handler)
        self.proxy_client = None
        self.logger = logging.getLogger("ServiceManager")

        # Интегрированная система метрик
        self.metrics_collector = ReactiveMetricsCollector()

        # Устанавливаем как глобальный менеджер
        set_global_service_manager(self)

    async def get_services_info_for_gossip(self):
        """Получить информацию о сервисах для gossip протокола"""
        services_info = {}
        for service_name, service_instance in self.services.items():
            try:
                info = await service_instance.get_service_info()
                services_info[service_name] = {
                    "version": info.get("version", "1.0.0"),
                    "status": info.get("status", "unknown"),
                    "methods": info.get("exposed_methods", []),
                    "description": info.get("description", ""),
                    "metadata": info.get("metadata", {}),
                    "metrics_summary": info.get("metrics_summary", {})
                }
            except Exception as e:
                self.logger.error(f"Error getting info for service {service_name}: {e}")

        return services_info

    def on_metrics_push(self, service_id: str, metric_name: str, value: Any, timestamp: float, metric_type: MetricType):
        """Обработка push метрик от сервисов"""
        self.metrics_collector.on_metrics_push(service_id, metric_name, value, timestamp, metric_type)

    def set_proxy_client(self, proxy_client):
        """Улучшенная установка proxy клиента"""
        self.proxy_client = proxy_client
        self.logger.info("Setting proxy client for all services...")

        # ИСПРАВИТЬ: Инжектируем proxy во все уже созданные сервисы
        for service_name, service_instance in self.services.items():
            try:
                if hasattr(service_instance, 'set_proxy'):
                    service_instance.set_proxy(proxy_client)
                    self.logger.info(f"Proxy injected into service: {service_name}")
                elif hasattr(service_instance, 'proxy'):
                    service_instance.proxy = proxy_client
                    self.logger.info(f"Proxy set directly for service: {service_name}")
            except Exception as e:
                self.logger.error(f"Failed to inject proxy into {service_name}: {e}")

    async def load_service(self, service_path: Path) -> Optional[BaseService]:
        service_name = service_path.name
        main_file = service_path / "main.py"

        self.logger.info(f"Loading service {service_name} from {main_file}")

        try:
            # Создаем уникальное имя модуля
            module_name = f"service_{service_name}_{hash(str(main_file))}"
            self.logger.debug(f"Module name: {module_name}")

            # Динамическая загрузка модуля
            spec = importlib.util.spec_from_file_location(module_name, main_file)
            if not spec:
                self.logger.error(f"Failed to create spec for {main_file}")
                return None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.logger.debug(f"Module {module_name} loaded")

            # Ищем класс Run
            if not hasattr(module, 'Run'):
                self.logger.error(f"Service {service_name}: Class 'Run' not found in module")
                return None

            self.logger.debug(f"Found Run class in {service_name}")

            # Создаем экземпляр сервиса
            RunClass = module.Run
            service_instance = RunClass(service_name, None)

            self.logger.info(f"Service instance created for {service_name}")
            return service_instance

        except Exception as e:
            self.logger.error(f"Failed to load service {service_name}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None

    async def initialize_service(self, service_instance: BaseService):
        """
        ИСПРАВЛЕНИЕ: Инициализация сервиса с проверкой proxy
        """
        try:
            # Еще одна проверка proxy перед инициализацией
            if self.proxy_client and not service_instance.proxy:
                if hasattr(service_instance, 'set_proxy'):
                    service_instance.set_proxy(self.proxy_client)
                else:
                    service_instance.proxy = self.proxy_client
                self.logger.info(f"Last chance proxy injection for: {service_instance.service_name}")

            # Вызываем инициализацию
            if hasattr(service_instance, 'initialize'):
                await service_instance.initialize()

            # Регистрируем в системе метрик
            self.metrics_collector.register_service(service_instance.service_name, service_instance)

            # Регистрируем публичные методы
            await self._register_service_methods(service_instance)

            # Добавляем в локальный реестр
            self.services[service_instance.service_name] = service_instance

            self.logger.info(f"Service initialized: {service_instance.service_name}")

        except Exception as e:
            self.logger.error(f"Failed to initialize service {service_instance.service_name}: {e}")
            import traceback
            traceback.print_exc()

    async def _register_service_methods(self, service_instance: BaseService):
        """Регистрация методов сервиса в RPC"""
        service_name = service_instance.service_name

        for method_name in dir(service_instance):
            method = getattr(service_instance, method_name)

            if (hasattr(method, '_service_method') and
                    getattr(method, '_service_public', False)):

                rpc_path = f"{service_name}/{method_name}"

                if hasattr(self.rpc, 'register_method'):
                    await self.rpc.register_method(rpc_path, method)
                elif hasattr(self.rpc, 'method_registry'):
                    self.rpc.method_registry[rpc_path] = method

                self.logger.info(f"Registered method: {rpc_path}")

    async def initialize_all_services(self):
        """Инициализация всех найденных сервисов"""
        services_dir = Path("services")

        """Получить директорию exe файла"""
        if getattr(sys, 'frozen', False):
            exe_dir = Path(sys.executable).parent
        else:
            exe_dir = Path(__file__).parent

        services_dir = exe_dir / "services"
        log = logging.getLogger('Path')
        log.info(services_dir)
        if not services_dir.exists():
            services_dir.mkdir(exist_ok=True)

        if not services_dir.exists():
            services_path = Path.cwd() / "services"
            log.info(services_path)
            services_dir.mkdir(exist_ok=True)

        if not services_dir.exists():
            self.logger.info("Services directory not found, skipping service initialization")
            return

        # ДОБАВИТЬ ОТЛАДКУ:
        self.logger.info(f"Scanning services directory: {services_dir.absolute()}")

        for service_path in services_dir.iterdir():
            self.logger.info(f"Found item: {service_path.name} (is_dir: {service_path.is_dir()})")

            if service_path.is_dir():
                main_file = service_path / "main.py"
                self.logger.info(f"Checking main.py in {service_path.name}: exists={main_file.exists()}")

                if main_file.exists():
                    self.logger.info(f"Attempting to load service: {service_path.name}")
                    service_instance = await self.load_service(service_path)

                    if service_instance:
                        self.logger.info(f"Service {service_path.name} loaded successfully")
                        await self.initialize_service(service_instance)
                    else:
                        self.logger.error(f"Failed to load service: {service_path.name}")

        self.logger.info(f"Initialized {len(self.services)} services")

    async def shutdown_all_services(self):
        """Остановка всех сервисов"""
        for service_name, service_instance in self.services.items():
            try:
                if hasattr(service_instance, 'cleanup'):
                    await service_instance.cleanup()
                self.logger.info(f"Service {service_name} shutdown completed")
            except Exception as e:
                self.logger.error(f"Error shutting down service {service_name}: {e}")

        self.services.clear()
        self.logger.info("All services shutdown completed")

    # === Новые методы для работы с метриками ===

    def get_service_metrics(self, service_name: str) -> Optional[Dict]:
        """Получить метрики конкретного сервиса"""
        return self.metrics_collector.get_service_metrics(service_name)

    def get_all_services_metrics(self) -> Dict[str, Any]:
        """Получить метрики всех сервисов"""
        return self.metrics_collector.get_aggregated_metrics()

    def get_services_health(self) -> Dict[str, Any]:
        """Получить health статус всех сервисов"""
        return self.metrics_collector.get_all_services_health()


_global_service_manager = None


def set_global_service_manager(manager):
    """Установить глобальный менеджер сервисов"""
    global _global_service_manager
    _global_service_manager = manager


def get_global_service_manager():
    """Получить глобальный менеджер сервисов"""
    return _global_service_manager


def diagnose_proxy_issues(service_instance):
    """Диагностика проблем с proxy для отладки"""
    issues = []

    if not hasattr(service_instance, 'proxy'):
        issues.append("Service doesn't have 'proxy' attribute")
    elif service_instance.proxy is None:
        issues.append("Service proxy is None")

    if not hasattr(service_instance, 'set_proxy'):
        issues.append("Service doesn't have 'set_proxy' method")

    # Проверяем глобальный менеджер
    global_manager = get_global_service_manager()
    if not global_manager:
        issues.append("No global service manager available")
    elif not global_manager.proxy_client:
        issues.append("Global service manager has no proxy_client")

    return {
        "service_name": getattr(service_instance, 'service_name', 'unknown'),
        "has_proxy": service_instance.proxy is not None if hasattr(service_instance, 'proxy') else False,
        "issues": issues,
        "recommendations": _generate_recommendations(issues)
    }


def _generate_recommendations(issues):
    """Генерация рекомендаций по исправлению проблем"""
    recommendations = []

    if "Service proxy is None" in issues:
        recommendations.append("Call service.set_proxy(proxy_client) after service creation")
        recommendations.append("Ensure ServiceManager.set_proxy_client() is called before service initialization")

    if "No global service manager available" in issues:
        recommendations.append("Ensure ServiceManager is created and set as global")

    if not recommendations:
        recommendations.append("All proxy checks passed")

    return recommendations