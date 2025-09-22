# service.py - Unified Service Management System
"""
Объединенная система управления сервисами:
- FastAPI HTTP интерфейс
- RPC система
- JWT аутентификация
- Архитектура сервисов с метриками
- Динамическая загрузка сервисов
- Управление жизненным циклом
"""

import asyncio
import hashlib
import importlib
import importlib.util
import logging
import os
import sys
import time
from abc import ABC
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Union, List, Optional, Set, Type

import jwt
import psutil
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

try:
    HAS_ORJSON = True
except ImportError:
    HAS_ORJSON = False
    orjson = None

# Try to import network layer
try:
    from layers.network import P2PNetworkLayer
    from layers.local_service_bridge import create_local_service_bridge
except ImportError:
    P2PNetworkLayer = None
    create_local_service_bridge = None

# =====================================================
# CONFIGURATION AND CONSTANTS
# =====================================================

# JWT Configuration
JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'fallback-dev-key-only')
JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'HS256')
JWT_EXPIRATION_HOURS = int(os.getenv('JWT_EXPIRATION_HOURS', '24'))

# Global registries
method_registry: Dict[str, Any] = {}
_global_service_manager: Optional['ServiceManager'] = None


# =====================================================
# SECURITY & AUTHENTICATION
# =====================================================

class JWTBlacklist:
    """JWT Token blacklist для отзыва токенов"""

    def __init__(self):
        self.blacklisted_tokens: Set[str] = set()
        self.token_exp_times: Dict[str, float] = {}

    def blacklist_token(self, token: str, exp_time: float):
        """Добавить токен в blacklist"""
        self.blacklisted_tokens.add(token)
        self.token_exp_times[token] = exp_time

        # Очистка просроченных токенов
        current_time = time.time()
        expired_tokens = [
            t for t, exp in self.token_exp_times.items()
            if exp < current_time
        ]
        for token in expired_tokens:
            self.blacklisted_tokens.discard(token)
            self.token_exp_times.pop(token, None)

    def is_blacklisted(self, token: str) -> bool:
        """Проверить, находится ли токен в blacklist"""
        return token in self.blacklisted_tokens


# Global blacklist instance
jwt_blacklist = JWTBlacklist()


class P2PAuthBearer(HTTPBearer):
    """P2P аутентификация через Bearer токен с поддержкой blacklist"""

    async def __call__(self, request: Request) -> str:
        credentials: HTTPAuthorizationCredentials = await super().__call__(request)

        if not credentials or credentials.scheme != "Bearer":
            raise HTTPException(
                status_code=401,
                detail="Invalid authentication scheme"
            )

        token = credentials.credentials

        # Проверка blacklist
        if jwt_blacklist.is_blacklisted(token):
            raise HTTPException(status_code=401, detail="Token has been revoked")

        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])

            if datetime.fromtimestamp(payload.get('exp', 0)) < datetime.now():
                raise HTTPException(
                    status_code=401,
                    detail="Token expired"
                )

            return payload.get('sub')  # node_id

        except jwt.JWTError:
            raise HTTPException(
                status_code=401,
                detail="Invalid token"
            )


# =====================================================
# DATA MODELS
# =====================================================

class RPCRequest(BaseModel):
    method: str
    params: Union[Dict[str, Any], List[Any]]
    id: str


class RPCResponse(BaseModel):
    result: Any = None
    error: str = None
    id: str


class GossipJoinRequest(BaseModel):
    node_id: str
    address: str
    port: int
    role: str
    capabilities: List[str]
    metadata: Dict[str, Any]


class MetricsThresholdRequest(BaseModel):
    """Запрос на установку порогов для метрик"""
    service_name: str
    metric_name: str
    warning_threshold: Optional[float] = None
    critical_threshold: Optional[float] = None
    enabled: bool = True


# =====================================================
# METRICS SYSTEM
# =====================================================

class MetricType(Enum):
    """Типы метрик"""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    TIMER = "timer"


@dataclass
class MetricsState:
    """Состояние метрик сервиса"""
    counters: Dict[str, int] = field(default_factory=dict)
    gauges: Dict[str, float] = field(default_factory=dict)
    histograms: Dict[str, List[float]] = field(default_factory=lambda: defaultdict(list))
    timers: Dict[str, List[float]] = field(default_factory=lambda: defaultdict(list))
    last_updated: Dict[str, float] = field(default_factory=dict)

    # ДОБАВЛЯЕМ ОБРАТНУЮ СОВМЕСТИМОСТЬ
    @property
    def data(self) -> Dict[str, Any]:
        """Обратная совместимость: объединенные данные метрик"""
        all_data = {}
        all_data.update(self.counters)
        all_data.update(self.gauges)
        # Добавляем агрегированные данные для timers
        for name, values in self.timers.items():
            if values:
                all_data[f"{name}_count"] = len(values)
                all_data[f"{name}_avg"] = sum(values) / len(values)
        return all_data

    def increment(self, name: str, value: int = 1):
        self.counters[name] = self.counters.get(name, 0) + value
        self.last_updated[name] = time.time()

    def gauge(self, name: str, value: float):
        self.gauges[name] = value
        self.last_updated[name] = time.time()

    def timer(self, name: str, duration_ms: float):
        self.timers[name].append(duration_ms)
        self.last_updated[name] = time.time()

    def set_data(self, key: str, value: Any):
        """Обратная совместимость: установка данных"""
        if isinstance(value, (int, float)) and key.endswith(('_count', '_total', '_sum')):
            self.counters[key] = int(value)
        else:
            self.gauges[key] = float(value) if isinstance(value, (int, float)) else value


class ReactiveMetricsCollector:
    """Реактивная система сбора метрик для ServiceManager"""

    def __init__(self):
        self.services: Dict[str, Dict] = {}
        self.aggregated_metrics: Dict[str, Any] = {}
        self.thresholds: Dict[str, Dict] = {}
        self.health_threshold = 90  # секунд для определения dead сервиса
        self._lock = Lock()
        self.logger = logging.getLogger("MetricsCollector")

        # Запуск background задач
        asyncio.create_task(self._health_monitor_loop())
        asyncio.create_task(self._aggregation_loop())

    def register_service(self, service_id: str, service_instance):
        """Регистрация сервиса в системе метрик"""
        with self._lock:
            self.services[service_id] = {
                'service_instance': service_instance,
                'status': 'alive',
                'last_seen': time.time(),
                'metrics': {},
                'total_metrics_received': 0
            }

        self.logger.info(f"Service registered in metrics: {service_id}")

    def on_metrics_push(self, service_id: str, metric_name: str, value: Any, timestamp: float, metric_type: MetricType):
        """Обработка push метрик от сервисов"""
        with self._lock:
            if service_id not in self.services:
                self.services[service_id] = {
                    'status': 'alive',
                    'last_seen': timestamp,
                    'metrics': {},
                    'total_metrics_received': 0
                }

            service_data = self.services[service_id]
            service_data['last_seen'] = timestamp
            service_data['total_metrics_received'] += 1

            # Сохранение метрики
            service_data['metrics'][metric_name] = {
                'value': value,
                'timestamp': timestamp,
                'type': metric_type.value
            }

            # Агрегация в общие метрики
            if service_id not in self.aggregated_metrics:
                self.aggregated_metrics[service_id] = {}

            self.aggregated_metrics[service_id][metric_name] = {
                'value': value,
                'timestamp': timestamp,
                'type': metric_type.value
            }

    async def _health_monitor_loop(self):
        """Фоновый мониторинг здоровья сервисов"""
        while True:
            try:
                current_time = time.time()
                with self._lock:
                    for service_id, service_data in self.services.items():
                        last_seen = service_data['last_seen']

                        # Проверяем не "умер" ли сервис
                        if current_time - last_seen > self.health_threshold:
                            if service_data['status'] != 'dead':
                                service_data['status'] = 'dead'
                                self.logger.warning(
                                    f"Service {service_id} marked as dead (last seen: {current_time - last_seen:.1f}s ago)")
                        else:
                            service_data['status'] = 'alive'

                await asyncio.sleep(30)  # Проверка каждые 30 секунд

            except Exception as e:
                self.logger.error(f"Error in health monitor loop: {e}")
                await asyncio.sleep(30)

    async def _aggregation_loop(self):
        """Фоновая агрегация и анализ метрик"""
        while True:
            try:
                # Можно добавить логику агрегации метрик
                # Например, вычисление средних значений, трендов и т.д.
                await asyncio.sleep(60)  # Агрегация каждую минуту

            except Exception as e:
                self.logger.error(f"Error in aggregation loop: {e}")
                await asyncio.sleep(60)

    def get_aggregated_metrics(self) -> Dict[str, Any]:
        """Получить агрегированные метрики узла без deadlock"""
        # Копируем данные под lock'ом
        with self._lock:
            services_copy = {
                service_id: {
                    'status': data['status'],
                    'last_seen': data['last_seen'],
                    'total_metrics_received': data.get('total_metrics_received', 0)
                }
                for service_id, data in self.services.items()
            }
            aggregated_copy = dict(self.aggregated_metrics)

        # Вычисляем статистику без lock'а
        total_services = len(services_copy)
        alive_services = sum(1 for s in services_copy.values() if s['status'] == 'alive')
        dead_services = sum(1 for s in services_copy.values() if s['status'] == 'dead')

        # Создаём health summary без lock'а
        current_time = time.time()
        health_summary = {}
        for service_id, data in services_copy.items():
            health_summary[service_id] = {
                'status': data['status'],
                'last_seen': data['last_seen'],
                'uptime': current_time - data['last_seen'] if data['status'] == 'alive' else 0,
                'total_metrics': data['total_metrics_received']
            }

        return {
            'system': {
                'total_services': total_services,
                'alive_services': alive_services,
                'dead_services': dead_services,
                'timestamp': current_time
            },
            'services': aggregated_copy,
            'health_summary': health_summary
        }

    def get_all_services_health(self) -> Dict[str, Any]:
        """Получить health status всех сервисов без deadlock"""
        with self._lock:
            current_time = time.time()
            return {
                service_id: {
                    'status': data['status'],
                    'last_seen': data['last_seen'],
                    'uptime': current_time - data['last_seen'] if data['status'] == 'alive' else 0,
                    'total_metrics': data.get('total_metrics_received', 0)
                }
                for service_id, data in self.services.items()
            }

    def get_service_metrics(self, service_name: str) -> Optional[Dict]:
        """Получить метрики конкретного сервиса"""
        with self._lock:
            if service_name in self.services:
                return self.services[service_name]['metrics']
            return None


# =====================================================
# SERVICE FRAMEWORK
# =====================================================

class ServiceStatus(Enum):
    """Статусы сервиса"""
    NOT_INITIALIZED = "not_initialized"
    INITIALIZING = "initializing"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class ServiceInfo:
    """Информация о сервисе"""
    name: str
    version: str = "1.0.0"
    description: str = ""
    exposed_methods: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


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
        self.status = ServiceStatus.NOT_INITIALIZED
        self.info = ServiceInfo(name=service_name)
        self.metrics = MetricsState()
        self._start_time = time.time()  # Для uptime
        self._setup_service_methods()

    def _setup_service_methods(self):
        """Автоматическое обнаружение и настройка методов сервиса"""
        self._extract_service_info()

    def _extract_service_info(self):
        """Извлекает информацию о сервисе из методов класса"""
        import inspect
        methods = []
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if hasattr(method, '_service_method') and getattr(method, '_service_public', False):
                methods.append(name)

        self.info.exposed_methods = methods
        self.info.description = self.__class__.__doc__ or ""

    async def start(self):
        """Запуск сервиса с улучшенным отслеживанием метрик"""
        self.status = ServiceStatus.INITIALIZING
        try:
            self.metrics.gauge("service_status", 1)  # 1=starting
            self.logger.info(f"Starting service {self.service_name}")

            # Настройка базовых метрик
            self._setup_base_metrics()

            await self.initialize()

            self.status = ServiceStatus.RUNNING
            self.metrics.gauge("service_status", 2)  # 2=running
            self.metrics.gauge("service_uptime_start", time.time())
            self._start_time = time.time()

            # Запуск фонового мониторинга
            asyncio.create_task(self._update_system_metrics())

            self.logger.info(f"Service {self.service_name} started successfully")

        except Exception as e:
            self.status = ServiceStatus.ERROR
            self.metrics.gauge("service_status", 4)  # 4=error
            self.metrics.increment("service_start_errors")
            self.logger.error(f"Failed to start service {self.service_name}: {e}")
            raise

    async def stop(self):
        """Остановка сервиса с метриками"""
        self.status = ServiceStatus.STOPPING
        try:
            self.metrics.gauge("service_status", 3)  # 3=stopping
            await self.cleanup()
            self.status = ServiceStatus.STOPPED
            self.metrics.gauge("service_status", 0)  # 0=stopped
            self.logger.info(f"Service {self.service_name} stopped")
        except Exception as e:
            self.status = ServiceStatus.ERROR
            self.metrics.gauge("service_status", 4)  # 4=error
            self.logger.error(f"Error stopping service {self.service_name}: {e}")

    def _setup_base_metrics(self):
        """Настройка базовых метрик сервиса"""
        try:
            # Базовая информация о сервисе
            self.metrics.gauge("service_pid", os.getpid())
            self.metrics.gauge("service_status", 0)  # 0=stopped, 1=starting, 2=running, 3=stopping, 4=error
        except ImportError:
            self.logger.debug("psutil not available - limited metrics")

    async def _update_system_metrics(self):
        """Background задача для обновления системных метрик"""
        while self.status in [ServiceStatus.INITIALIZING, ServiceStatus.RUNNING]:
            try:

                process = psutil.Process()

                # Память
                memory_info = process.memory_info()
                self.metrics.gauge("memory_usage_bytes", memory_info.rss)
                self.metrics.gauge("memory_usage_mb", memory_info.rss / 1024 / 1024)

                # CPU (требует интервала)
                cpu_percent = process.cpu_percent()
                if cpu_percent > 0:  # Избегаем 0 при первом вызове
                    self.metrics.gauge("cpu_usage_percent", cpu_percent)

                # Потоки
                self.metrics.gauge("thread_count", process.num_threads())

                # Push метрики в ServiceManager
                self._push_metrics_to_manager()

            except Exception as e:
                self.logger.debug(f"Error updating system metrics: {e}")

            await asyncio.sleep(30)  # Обновление каждые 30 секунд

    def _push_metrics_to_manager(self):
        """Callback для отправки метрик в ServiceManager"""
        # Будет подключен ServiceManager при регистрации
        manager = get_global_service_manager()
        if manager and hasattr(manager, 'on_metrics_push'):
            try:
                # Отправляем все счетчики
                for metric_name, value in self.metrics.counters.items():
                    manager.on_metrics_push(self.service_name, metric_name, value, time.time(), MetricType.COUNTER)

                # Отправляем все gauge
                for metric_name, value in self.metrics.gauges.items():
                    manager.on_metrics_push(self.service_name, metric_name, value, time.time(), MetricType.GAUGE)

            except Exception as e:
                self.logger.error(f"Failed to push metric to manager: {e}")

    def set_proxy(self, proxy_client):
        """Улучшенная установка proxy клиента"""
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

    def get_health_report(self):
        """Получить детальный отчет о здоровье сервиса"""
        uptime = self._get_uptime()

        return {
            "service_name": self.service_name,
            "status": self.status.value,
            "uptime_seconds": uptime,
            "pid": self.metrics.gauges.get("service_pid", 0),
            "memory_mb": self.metrics.gauges.get("memory_usage_mb", 0),
            "cpu_percent": self.metrics.gauges.get("cpu_usage_percent", 0),
            "thread_count": self.metrics.gauges.get("thread_count", 0),
            "error_count": self.metrics.counters.get("service_start_errors", 0),
            "timestamp": time.time(),
            "metrics_summary": {
                "counters": len(self.metrics.counters),
                "gauges": len(self.metrics.gauges),
                "timers": len(self.metrics.timers)
            }
        }

    def _get_uptime(self) -> float:
        """Получить uptime сервиса"""
        if self.status == ServiceStatus.RUNNING:
            return time.time() - self._start_time
        return 0

    async def get_service_info(self) -> Dict[str, Any]:
        """Получение информации о сервисе с расширенными данными"""
        return {
            "name": self.service_name,
            "status": self.status.value,
            "version": self.info.version,
            "description": self.info.description,
            "exposed_methods": self.info.exposed_methods,
            "metadata": self.info.metadata,
            "uptime": self._get_uptime(),
            "health_report": self.get_health_report(),
            "metrics_summary": {
                "counters": len(self.metrics.counters),
                "gauges": len(self.metrics.gauges),
                "timers": len(self.metrics.timers)
            }
        }


# =====================================================
# SERVICE MANAGEMENT
# =====================================================

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
            # Оптимизированная загрузка модуля
            module_name = f"service_{service_name}_{hashlib.md5(str(main_file).encode()).hexdigest()[:8]}"

            spec = importlib.util.spec_from_file_location(module_name, main_file)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
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
            # Cleanup из sys.modules при ошибке
            if module_name in sys.modules:
                del sys.modules[module_name]


# =====================================================
# MAIN SERVICE MANAGER
# =====================================================

class ServiceManager:
    """Главный менеджер сервисов с интегрированными метриками"""

    def __init__(self, rpc_handler):
        self.rpc = rpc_handler
        self.services = {}
        self.registry = ServiceRegistry(rpc_handler)
        self.proxy_client = None
        self.logger = logging.getLogger("ServiceManager")

        # Интегрированная система метрик
        self.metrics_collector = ReactiveMetricsCollector()

        # Устанавливаем как глобальный менеджер
        global _global_service_manager
        _global_service_manager = self

    def set_proxy_client(self, proxy_client):
        """Установка proxy клиента для всех сервисов"""
        self.proxy_client = proxy_client
        self.logger.info("Setting proxy client for all services...")

        # Инжектируем proxy во все уже созданные сервисы
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
        """Загрузка сервиса из пути"""
        service_name = service_path.name
        main_file = service_path / "main.py"

        self.logger.info(f"Loading service {service_name} from {main_file}")

        try:
            # Создание уникального имени модуля
            module_name = f"service_{service_name}_{hashlib.md5(str(main_file).encode()).hexdigest()[:8]}"

            # Динамическая загрузка модуля
            spec = importlib.util.spec_from_file_location(module_name, main_file)
            if not spec:
                self.logger.error(f"Failed to create spec for {main_file}")
                return None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Ищем класс Run
            if not hasattr(module, 'Run'):
                self.logger.error(f"Service {service_name}: Class 'Run' not found in module")
                return None

            # Создаем экземпляр сервиса
            RunClass = module.Run
            service_instance = RunClass(service_name, self.proxy_client)

            self.logger.info(f"Service instance created for {service_name}")
            return service_instance

        except Exception as e:
            self.logger.error(f"Failed to load service {service_name}: {e}")
            return None

    async def initialize_service(self, service_instance: BaseService):
        """Инициализация сервиса"""
        try:
            # Проверка proxy перед инициализацией
            if self.proxy_client and not service_instance.proxy:
                if hasattr(service_instance, 'set_proxy'):
                    service_instance.set_proxy(self.proxy_client)
                else:
                    service_instance.proxy = self.proxy_client

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
        services_dir = get_services_path()

        if not services_dir.exists():
            self.logger.info("Services directory not found, skipping service initialization")
            return

        self.logger.info(f"Scanning services directory: {services_dir.absolute()}")

        for service_path in services_dir.iterdir():
            if service_path.is_dir():
                main_file = service_path / "main.py"
                if main_file.exists():
                    self.logger.info(f"Attempting to load service: {service_path.name}")
                    service_instance = await self.load_service(service_path)

                    if service_instance:
                        self.logger.info(f"Service {service_path.name} loaded successfully")
                        await self.initialize_service(service_instance)

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

        # Cleanup modules
        self.cleanup_service_modules()
        self.logger.info("All services shutdown completed")

    def cleanup_service_modules(self):
        """Очистка модулей сервисов из sys.modules"""
        modules_to_remove = []

        for module_name in sys.modules:
            if module_name.startswith('service_') and len(module_name.split('_')) >= 3:
                modules_to_remove.append(module_name)

        for module_name in modules_to_remove:
            try:
                del sys.modules[module_name]
            except KeyError:
                pass

    def on_metrics_push(self, service_id: str, metric_name: str, value: Any, timestamp: float, metric_type: MetricType):
        """Обработка push метрик от сервисов"""
        self.metrics_collector.on_metrics_push(service_id, metric_name, value, timestamp, metric_type)

    def get_service_metrics(self, service_name: str) -> Optional[Dict]:
        """Получить метрики конкретного сервиса"""
        return self.metrics_collector.get_service_metrics(service_name)

    def get_all_services_metrics(self) -> Dict[str, Any]:
        """Получить метрики всех сервисов"""
        return self.metrics_collector.get_aggregated_metrics()

    def get_services_health(self) -> Dict[str, Any]:
        """Получить health статус всех сервисов"""
        return self.metrics_collector.get_all_services_health()

    def get_aggregated_metrics(self):
        """Получить агрегированные метрики всех сервисов"""
        try:
            # Используем ReactiveMetricsCollector если доступен
            if hasattr(self, 'metrics_collector'):
                return self.metrics_collector.get_aggregated_metrics()

            # Fallback: собираем метрики напрямую из сервисов
            aggregated = {
                "system": {
                    "total_services": len(self.services),
                    "active_services": sum(1 for s in self.services.values()
                                           if hasattr(s, 'status') and s.status.value == "running"),
                    "timestamp": time.time()
                },
                "services": {}
            }

            for service_name, service_instance in self.services.items():
                try:
                    if hasattr(service_instance, 'metrics'):
                        metrics = service_instance.metrics
                        aggregated["services"][service_name] = {
                            "counters": dict(metrics.counters),
                            "gauges": dict(metrics.gauges),
                            "timer_counts": {k: len(v) for k, v in metrics.timers.items()},
                            "last_updated": dict(metrics.last_updated)
                        }
                    else:
                        aggregated["services"][service_name] = {"error": "No metrics available"}
                except Exception as e:
                    aggregated["services"][service_name] = {"error": str(e)}

            return aggregated
        except Exception as e:
            self.logger.error(f"Error getting aggregated metrics: {e}")
            return {
                "system": {"error": str(e), "timestamp": time.time()},
                "services": {}
            }

    async def reload_service(self, service_name: str):
        """Перезагрузка сервиса"""
        if service_name in self.services:
            await self.registry.stop_service(service_name)

        # Поиск и перезагрузка сервиса из файловой системы
        services_dir = get_services_path()
        service_path = services_dir / service_name

        if service_path.exists():
            loader = ServiceLoader(services_dir, self.registry)
            await loader.load_service_from_directory(service_path, self.proxy_client)
            self.logger.info(f"Service {service_name} reloaded")
        else:
            self.logger.error(f"Service directory not found: {service_path}")

    def diagnose_proxy_issues(self, service_name: str = None) -> Dict[str, Any]:
        """Диагностика проблем с proxy для отладки"""
        if service_name:
            # Диагностика конкретного сервиса
            service = self.services.get(service_name)
            if not service:
                return {"error": f"Service {service_name} not found"}

            return self._diagnose_single_service(service)
        else:
            # Диагностика всех сервисов
            results = {}
            for svc_name, service_instance in self.services.items():
                results[svc_name] = self._diagnose_single_service(service_instance)
            return results

    def _diagnose_single_service(self, service_instance) -> Dict[str, Any]:
        """Диагностика одного сервиса"""
        issues = []

        if not hasattr(service_instance, 'proxy'):
            issues.append("Service doesn't have 'proxy' attribute")
        elif service_instance.proxy is None:
            issues.append("Service proxy is None")

        if not hasattr(service_instance, 'set_proxy'):
            issues.append("Service doesn't have 'set_proxy' method")

        # Проверяем proxy_client менеджера
        if not self.proxy_client:
            issues.append("ServiceManager has no proxy_client")

        return {
            "service_name": getattr(service_instance, 'service_name', 'unknown'),
            "has_proxy": service_instance.proxy is not None if hasattr(service_instance, 'proxy') else False,
            "issues": issues,
            "recommendations": self._generate_recommendations(issues)
        }

    def _generate_recommendations(self, issues: List[str]) -> List[str]:
        """Генерация рекомендаций по исправлению проблем"""
        recommendations = []

        if "Service proxy is None" in issues:
            recommendations.append("Call service.set_proxy(proxy_client) after service creation")
            recommendations.append("Ensure ServiceManager.set_proxy_client() is called before service initialization")

        if "ServiceManager has no proxy_client" in issues:
            recommendations.append("Call ServiceManager.set_proxy_client() with valid proxy client")

        if not recommendations:
            recommendations.append("All proxy checks passed")

        return recommendations

    def list_available_methods(self) -> Dict[str, List[str]]:
        """Список всех доступных методов по сервисам"""
        methods = {}
        for service_name, service_instance in self.services.items():
            if hasattr(service_instance, 'info'):
                methods[service_name] = service_instance.info.exposed_methods
            else:
                methods[service_name] = []
        return methods

    def get_service_details(self, service_name: str) -> Optional[Dict[str, Any]]:
        """Получить детальную информацию о сервисе"""
        service = self.services.get(service_name)
        if not service:
            return None

        try:
            health_report = service.get_health_report() if hasattr(service, 'get_health_report') else {}

            return {
                "name": service_name,
                "status": service.status.value if hasattr(service, 'status') else 'unknown',
                "info": service.info.__dict__ if hasattr(service, 'info') else {},
                "health_report": health_report,
                "proxy_status": "connected" if service.proxy else "not_connected",
                "uptime": service._get_uptime() if hasattr(service, '_get_uptime') else 0,
                "metrics_count": {
                    "counters": len(service.metrics.counters) if hasattr(service, 'metrics') else 0,
                    "gauges": len(service.metrics.gauges) if hasattr(service, 'metrics') else 0,
                    "timers": len(service.metrics.timers) if hasattr(service, 'metrics') else 0
                }
            }
        except Exception as e:
            return {"error": f"Failed to get service details: {e}"}

    async def get_services_info_for_gossip(self):
        """Получить информацию о сервисах для gossip протокола"""
        services_info = {}

        for service_name, service_instance in self.services.items():
            try:
                # Получаем базовую информацию о сервисе
                info = await service_instance.get_service_info()
                services_info[service_name] = {
                    "version": info.get("version", "1.0.0"),
                    "status": info.get("status", "unknown"),
                    "methods": info.get("exposed_methods", []),
                    "description": info.get("description", ""),
                    "metadata": info.get("metadata", {}),
                    "metrics_summary": info.get("metrics_summary", {}),
                    # Дополнительная информация для gossip
                    "uptime": time.time() - getattr(service_instance, '_start_time', time.time()),
                    "last_seen": time.time(),
                    "node_id": getattr(self, 'node_id', 'unknown')
                }
            except Exception as e:
                self.logger.error(f"Error getting info for service {service_name}: {e}")
                # Fallback информация при ошибке
                services_info[service_name] = {
                    "version": "unknown",
                    "status": "error",
                    "methods": [],
                    "description": f"Error: {str(e)}",
                    "metadata": {},
                    "metrics_summary": {}
                }

        return services_info

    def get_service_health_status(self):
        """Получить статус здоровья всех сервисов"""
        healthy_services = 0
        total_services = len(self.services)
        service_statuses = {}

        for service_name, service_instance in self.services.items():
            try:
                from layers.service import ServiceStatus
                status = service_instance.status.value if hasattr(service_instance, 'status') else "unknown"
                service_statuses[service_name] = status

                if hasattr(service_instance, 'status') and service_instance.status == ServiceStatus.RUNNING:
                    healthy_services += 1
            except Exception as e:
                service_statuses[service_name] = f"error: {e}"

        return {
            "status": "healthy" if healthy_services == total_services else "degraded",
            "services": {
                "total": total_services,
                "healthy": healthy_services,
                "degraded": total_services - healthy_services
            },
            "service_statuses": service_statuses,
            "timestamp": time.time()
        }

    def get_aggregated_metrics(self):
        """Получить агрегированные метрики всех сервисов"""
        aggregated = {
            "system": {
                "total_services": len(self.services),
                "active_services": sum(1 for s in self.services.values()
                                       if hasattr(s, 'status') and s.status.value == "running"),
                "timestamp": time.time()
            },
            "services": {}
        }

        for service_name, service_instance in self.services.items():
            try:
                if hasattr(service_instance, 'metrics'):
                    metrics = service_instance.metrics
                    aggregated["services"][service_name] = {
                        "counters": dict(metrics.counters),
                        "gauges": dict(metrics.gauges),
                        "timer_counts": {k: len(v) for k, v in metrics.timers.items()},
                        "last_updated": dict(metrics.last_updated)
                    }
                else:
                    aggregated["services"][service_name] = {"error": "No metrics available"}
            except Exception as e:
                aggregated["services"][service_name] = {"error": str(e)}

        return aggregated


# =====================================================
# UTILITY FUNCTIONS
# =====================================================

def get_exe_dir():
    """Получить директорию exe файла"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    else:
        return Path(__file__).parent


def get_services_path():
    """Получить путь к папке services с улучшенной логикой"""
    exe_dir = get_exe_dir()

    # Попробуем несколько локаций
    possible_paths = [
        exe_dir / ".." / "dist" / "services" if 'PycharmProjects' in str(exe_dir) else None,
        exe_dir / "services",
        Path.cwd() / "services"
    ]

    for services_path in filter(None, possible_paths):
        if services_path.exists():
            return services_path

    # Создаем в первой возможной локации
    services_path = possible_paths[0]
    services_path.mkdir(exist_ok=True)
    return services_path


# =====================================================
# SIMPLE LOCAL SERVICE LAYER (Backward compatibility)
# =====================================================

class SimpleLocalServiceLayer:
    """Простой слой для работы с локальными сервисами"""

    def __init__(self, method_registry: Dict[str, Any]):
        self.method_registry = method_registry
        self.logger = logging.getLogger("SimpleLocalServiceLayer")

    def list_all_services(self) -> Dict[str, Dict[str, Any]]:
        """Список всех сервисов из method_registry"""
        services = {}
        for method_path in self.method_registry.keys():
            if '/' in method_path:
                service_name = method_path.split('/')[0]
                if service_name not in services:
                    services[service_name] = {
                        "methods": [],
                        "status": "running"
                    }
                method_name = method_path.split('/', 1)[1]
                services[service_name]["methods"].append(method_name)
        return services

    def list_registry_methods(self) -> List[str]:
        """Список всех методов в реестре"""
        return list(self.method_registry.keys())

    def get_service_info(self, service_name: str) -> Optional[Dict[str, Any]]:
        """Получить информацию о сервисе"""
        services = self.list_all_services()
        return services.get(service_name)


# =====================================================
# OPTIMIZED RPC HANDLING
# =====================================================

# Try to import orjson for faster JSON processing


# =====================================================
# GLOBAL ACCESS FUNCTIONS
# =====================================================

def get_global_service_manager() -> Optional['ServiceManager']:
    """Получить глобальный менеджер сервисов"""
    return _global_service_manager


def set_global_service_manager(manager: 'ServiceManager'):
    """Установить глобальный менеджер сервисов"""
    global _global_service_manager
    _global_service_manager = manager


# =====================================================
# P2P SERVICE HANDLER (Main class for FastAPI integration)
# =====================================================

class P2PServiceHandler:
    """
    Главный обработчик P2P сервисов с FastAPI интеграцией
    Объединяет всю функциональность из обоих файлов
    """

    def __init__(self, network_layer=None, service_manager=None):
        self.app = FastAPI(title="P2P Service Manager", version="2.0.0")
        self.network = network_layer
        self.service_manager = service_manager or ServiceManager(self)
        self.security = P2PAuthBearer()
        self.logger = logging.getLogger("P2PServiceHandler")

        # Local service layer для обратной совместимости
        self.local_service_layer = SimpleLocalServiceLayer(method_registry)

        self._setup_endpoints()

    def _setup_endpoints(self):
        """Настройка всех FastAPI endpoints"""

        # =====================================================
        # CORE RPC ENDPOINT
        # =====================================================

        @self.app.post("/rpc")
        async def rpc_handler(rpc_request: RPCRequest):
            """Главный RPC обработчик с оптимизированной обработкой"""
            logger = logging.getLogger("RPC")
            path = rpc_request.method

            logger.debug(f"RPC call: {path} with params: {rpc_request.params}")

            # Получаем все доступные методы
            all_methods = {}

            # Из service manager
            if hasattr(self.service_manager, 'services'):
                for service_name, service_instance in self.service_manager.services.items():
                    for method_name in service_instance.info.exposed_methods:
                        method_path = f"{service_name}/{method_name}"
                        all_methods[method_path] = getattr(service_instance, method_name)

            # Из method_registry (для обратной совместимости)
            all_methods.update(method_registry)

            if path not in all_methods:
                available_methods = list(all_methods.keys())
                logger.error(f"Method {path} not found. Available: {available_methods[:5]}")
                return RPCResponse(
                    error=f"Method {path} not found. Available: {available_methods[:5]}",
                    id=rpc_request.id
                )

            try:
                method = all_methods[path]
                logger.debug(f"Found method: {method}")

                if isinstance(rpc_request.params, dict):
                    result = await method(**rpc_request.params)
                else:
                    result = await method(*rpc_request.params)

                logger.debug(f"Method {path} returned: {type(result)} - {str(result)[:100]}")
                return RPCResponse(result=result, id=rpc_request.id)

            except Exception as e:
                logger.error(f"RPC error for {path}: {e}")
                return RPCResponse(error=str(e), id=rpc_request.id)

        # =====================================================
        # AUTHENTICATION ENDPOINTS
        # =====================================================

        @self.app.post("/auth/token")
        async def create_token(request: Dict[str, str]):
            """Создание JWT токена для аутентификации"""
            node_id = request.get('node_id')
            if not node_id:
                raise HTTPException(status_code=400, detail="node_id required")

            expires = datetime.now() + timedelta(hours=JWT_EXPIRATION_HOURS)
            payload = {
                'sub': node_id,
                'exp': expires.timestamp(),
                'iat': datetime.now().timestamp()
            }

            token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
            return {"access_token": token, "token_type": "bearer"}

        @self.app.post("/auth/revoke")
        async def revoke_token(request: Request, node_id: str = Depends(self.security)):
            """Отзыв JWT токена"""
            auth_header = request.headers.get('Authorization')
            if auth_header and auth_header.startswith('Bearer '):
                token = auth_header[7:]  # Убрать "Bearer "

                try:
                    payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
                    exp_time = payload.get('exp', 0)
                    jwt_blacklist.blacklist_token(token, exp_time)

                    return {"message": "Token revoked successfully"}
                except jwt.JWTError:
                    raise HTTPException(status_code=400, detail="Invalid token")

            raise HTTPException(status_code=400, detail="No token provided")

        # =====================================================
        # GOSSIP PROTOCOL ENDPOINTS
        # =====================================================

        @self.app.post("/internal/gossip/join")
        async def gossip_join(join_request: Dict[str, Any]):
            """Обработка запроса на присоединение к кластеру"""
            if self.network and hasattr(self.network, 'gossip'):
                return await self.network.gossip.handle_join_request(join_request)
            return {"error": "Gossip protocol not available"}

        @self.app.post("/internal/gossip/exchange")
        async def gossip_exchange(gossip_data: Dict[str, Any]):
            """Обработка gossip обмена информацией"""
            if self.network and hasattr(self.network, 'gossip'):
                return await self.network.gossip.handle_gossip_exchange(gossip_data)
            return {"error": "Gossip protocol not available"}

        # =====================================================
        # SERVICE MANAGEMENT ENDPOINTS
        # =====================================================

        @self.app.get("/services")
        async def list_services():
            """Список всех зарегистрированных сервисов"""
            return self.service_manager.registry.list_services()

        @self.app.get("/services/{service_name}")
        async def get_service_info(service_name: str):
            """Получить информацию о конкретном сервисе"""
            service = self.service_manager.registry.get_service(service_name)
            if not service:
                raise HTTPException(status_code=404, detail=f"Service {service_name} not found")
            return await service.get_service_info()

        @self.app.post("/services/{service_name}/restart")
        async def restart_service(service_name: str, node_id: str = Depends(self.security)):
            """Перезапуск сервиса"""
            try:
                await self.service_manager.registry.stop_service(service_name)
                # Здесь можно добавить логику перезагрузки
                return {"message": f"Service {service_name} restarted"}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        # =====================================================
        # METRICS ENDPOINTS
        # =====================================================

        @self.app.get("/metrics")
        async def get_all_metrics():
            """Получить все метрики системы"""
            return self.service_manager.metrics_collector.get_aggregated_metrics()

        @self.app.get("/metrics/{service_name}")
        async def get_service_metrics(service_name: str):
            """Получить метрики конкретного сервиса"""
            service = self.service_manager.registry.get_service(service_name)
            if not service:
                raise HTTPException(status_code=404, detail=f"Service {service_name} not found")

            return {
                "counters": service.metrics.counters,
                "gauges": service.metrics.gauges,
                "timers": {k: len(v) for k, v in service.metrics.timers.items()},
                "last_updated": service.metrics.last_updated
            }

        # =====================================================
        # HEALTH CHECK & STATUS
        # =====================================================

        @self.app.get("/health")
        async def health_check():
            """Health check endpoint"""
            healthy_services = 0
            total_services = len(self.service_manager.services)

            for service in self.service_manager.services.values():
                if service.status == ServiceStatus.RUNNING:
                    healthy_services += 1

            return {
                "status": "healthy" if healthy_services == total_services else "degraded",
                "services": {
                    "total": total_services,
                    "healthy": healthy_services,
                    "degraded": total_services - healthy_services
                },
                "timestamp": datetime.now().isoformat()
            }

        @self.app.get("/cluster/nodes")
        async def get_cluster_nodes(node_id: str = Depends(self.security)):
            """Получить список узлов в кластере"""
            if self.network and hasattr(self.network, 'gossip'):
                return await self.network.gossip.get_known_nodes()
            return {"nodes": [], "error": "Network layer not available"}

    # =====================================================
    # SERVICE MANAGER INTEGRATION
    # =====================================================

    async def register_rpc_methods(self, service_name: str, service_instance: BaseService):
        """Регистрация RPC методов сервиса"""
        for method_name in service_instance.info.exposed_methods:
            method = getattr(service_instance, method_name)
            method_path = f"{service_name}/{method_name}"
            method_registry[method_path] = method
            self.logger.info(f"Registered RPC method: {method_path}")

    async def initialize_all(self):
        """Инициализация всех компонентов системы"""
        await self.service_manager.initialize_all_services()

    async def shutdown_all(self):
        """Остановка всех компонентов системы"""
        await self.service_manager.shutdown_all_services()


# =====================================================
# EXPORTS & BACKWARDS COMPATIBILITY
# =====================================================

# Utility functions
def diagnose_proxy_issues(service_instance):
    """Диагностика проблем с proxy для отладки (standalone функция)"""
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


def create_service_handler(network_layer=None, service_manager=None):
    """Factory function для создания service handler (обратная совместимость)"""
    return P2PServiceHandler(network_layer=network_layer)


def create_service_manager(rpc_handler=None):
    """Factory function для создания service manager (обратная совместимость)"""
    if rpc_handler is None:
        # Создаем простой RPC handler если не передан
        class SimpleRPCHandler:
            def __init__(self):
                self.method_registry = method_registry

        rpc_handler = SimpleRPCHandler()

    return ServiceManager(rpc_handler)


# Main classes for external use
__all__ = [
    'P2PServiceHandler',
    'ServiceManager',
    'BaseService',
    'service_method',
    'ServiceRegistry',
    'ServiceLoader',
    'RPCRequest',
    'RPCResponse',
    'P2PAuthBearer',
    'get_services_path',
    'method_registry',
    'get_global_service_manager',
    'set_global_service_manager',
    'ReactiveMetricsCollector',
    'MetricsState',
    'MetricType',
    'ServiceStatus',
    'ServiceInfo',
    'JWTBlacklist',
    'jwt_blacklist',
    'SimpleLocalServiceLayer',
    'diagnose_proxy_issues',
    'create_service_handler',
    'create_service_manager'
]