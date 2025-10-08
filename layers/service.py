"""
service.py - Унифицированный сервисный слой без глобальных переменных
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
from typing import Any, Dict, Union, List, Optional, Set, Type, Callable

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

# =====================================================
# MODELS & ENUMS
# =====================================================

class ServiceStatus(Enum):
    NOT_INITIALIZED = "not_initialized"
    INITIALIZING = "initializing"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class MetricType(Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    TIMER = "timer"


@dataclass
class ServiceInfo:
    name: str
    version: str = "1.0.0"
    description: str = ""
    exposed_methods: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MetricsState:
    counters: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    gauges: Dict[str, float] = field(default_factory=dict)
    timers: Dict[str, List[float]] = field(default_factory=lambda: defaultdict(list))
    last_updated: float = field(default_factory=time.time)


class RPCRequest(BaseModel):
    method: str
    params: Dict[str, Any] = {}


class RPCResponse(BaseModel):
    result: Any = None
    error: Optional[str] = None


# =====================================================
# JWT & SECURITY
# =====================================================

class JWTBlacklist:
    def __init__(self):
        self.blacklist: Set[str] = set()

    def add(self, token: str):
        self.blacklist.add(token)

    def is_blacklisted(self, token: str) -> bool:
        return token in self.blacklist


jwt_blacklist = JWTBlacklist()


class P2PAuthBearer(HTTPBearer):
    async def __call__(self, request) -> str:
        credentials: HTTPAuthorizationCredentials = await super().__call__(request)
        token = credentials.credentials

        if jwt_blacklist.is_blacklisted(token):
            raise HTTPException(status_code=401, detail="Token has been revoked")

        try:
            payload = jwt.decode(token, "change-this-secret", algorithms=["HS256"])
            return payload.get("node_id")
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token has expired")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token")


# =====================================================
# SERVICE DECORATORS
# =====================================================

def service_method(
    description: str = "",
    public: bool = False,
    requires_auth: bool = True,
    rate_limit: Optional[int] = None
):
    def decorator(func: Callable) -> Callable:
        func._service_method = True
        func._service_public = public
        func._service_auth = requires_auth
        func._service_description = description
        func._service_rate_limit = rate_limit
        return func
    return decorator


# =====================================================
# BASE SERVICE
# =====================================================

class BaseService(ABC):
    SERVICE_NAME = "base_service"

    def __init__(self, service_name: str, proxy_client=None):
        self.service_name = service_name
        self.status = ServiceStatus.NOT_INITIALIZED
        self.proxy = proxy_client
        self.logger = logging.getLogger(f"Service.{service_name}")
        self.metrics = MetricsState()
        self.info = ServiceInfo(name=service_name)
        self.start_time = time.time()
        self._collect_exposed_methods()

    def _collect_exposed_methods(self):
        for method_name in dir(self):
            if method_name.startswith('_'):
                continue
            method = getattr(self, method_name)
            if hasattr(method, '_service_method') and getattr(method, '_service_public', False):
                self.info.exposed_methods.append(method_name)

    def set_proxy(self, proxy_client):
        self.proxy = proxy_client
        self.logger.info(f"Proxy client set for service: {self.service_name}")

    async def start(self):
        self.status = ServiceStatus.RUNNING
        self.logger.info(f"Service {self.service_name} started")

    async def stop(self):
        self.status = ServiceStatus.STOPPED
        self.logger.info(f"Service {self.service_name} stopped")

    async def initialize(self):
        self.status = ServiceStatus.RUNNING
        self.logger.info(f"Service {self.service_name} initialized")

    def get_health_report(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "uptime": self._get_uptime(),
            "metrics_count": len(self.metrics.counters) + len(self.metrics.gauges)
        }

    def _get_uptime(self) -> float:
        return time.time() - self.start_time

    @service_method(description="Get service info", public=True, requires_auth=False)
    async def get_info(self) -> Dict[str, Any]:
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

    @service_method(description="Health check ping", public=True, requires_auth=False)
    async def ping(self) -> Dict[str, Any]:
        self.metrics.counters["ping_requests"] += 1
        self.metrics.gauges["service_heartbeat"] = time.time()
        return {
            "status": "alive",
            "service": self.service_name,
            "uptime": self._get_uptime(),
            "timestamp": time.time(),
            "heartbeat": True
        }


# =====================================================
# SERVICE REGISTRY
# =====================================================

class ServiceRegistry:
    """Реестр сервисов для автоматической регистрации и управления"""

    def __init__(self, rpc_methods_instance, service_manager=None):
        self.services: Dict[str, BaseService] = {}
        self.service_classes: Dict[str, Type[BaseService]] = {}
        self.rpc_methods = rpc_methods_instance
        self.service_manager = service_manager
        self.logger = logging.getLogger("ServiceRegistry")

    async def register_service_class(self, service_class: Type[BaseService], proxy_client=None):
        service_name = getattr(service_class, 'SERVICE_NAME', service_class.__name__.lower())
        self.service_classes[service_name] = service_class
        self.logger.info(f"Registered service class: {service_name}")

        service_instance = service_class(service_name, proxy_client)
        await self.start_service(service_name, service_instance)

    async def start_service(self, service_name: str, service_instance: BaseService):
        try:
            if hasattr(service_instance, 'status') and service_instance.status != ServiceStatus.RUNNING:
                await service_instance.start()

            self.services[service_name] = service_instance

            # Регистрация метрик через ServiceManager
            if self.service_manager and hasattr(self.service_manager, 'metrics_collector'):
                self.service_manager.metrics_collector.register_service(service_name, service_instance)
                service_instance._metrics_manager = self.service_manager.metrics_collector
                self.logger.debug(f"Metrics manager linked to service: {service_name}")

            # Регистрируем методы в method_registry через ServiceManager
            await self._register_service_rpc_methods(service_name, service_instance)

            self.logger.info(f"Service {service_name} started and registered")

        except Exception as e:
            self.logger.error(f"Failed to start service {service_name}: {e}")
            raise

    async def _register_service_rpc_methods(self, service_name: str, service_instance: BaseService):
        """Регистрация RPC методов сервиса"""
        if not self.service_manager:
            return

        for method_name in dir(service_instance):
            method = getattr(service_instance, method_name)

            if (hasattr(method, '_service_method') and
                    getattr(method, '_service_public', False)):

                rpc_path = f"{service_name}/{method_name}"

                # Регистрируем в method_registry ServiceManager
                self.service_manager.method_registry[rpc_path] = method

                # Также регистрируем в RPC handler если есть
                if hasattr(self.rpc_methods, 'register_method'):
                    await self.rpc_methods.register_method(rpc_path, method)

                self.logger.info(f"Registered RPC method: {rpc_path}")

    async def stop_service(self, service_name: str):
        if service_name in self.services:
            service = self.services[service_name]
            await service.stop()
            del self.services[service_name]
            self.logger.info(f"Service {service_name} stopped and unregistered")

    def get_service(self, service_name: str) -> Optional[BaseService]:
        return self.services.get(service_name)

    def list_services(self) -> Dict[str, Dict[str, Any]]:
        return {
            name: {
                "status": service.status.value if hasattr(service, 'status') else 'unknown',
                "info": service.info.__dict__ if hasattr(service, 'info') else {},
                "methods": service.info.exposed_methods if hasattr(service, 'info') else []
            }
            for name, service in self.services.items()
        }


# =====================================================
# SERVICE LOADER
# =====================================================

class ServiceLoader:
    """Загрузчик сервисов из файловой системы"""

    def __init__(self, services_directory: Path, registry: ServiceRegistry):
        self.services_dir = services_directory
        self.registry = registry
        self.logger = logging.getLogger("ServiceLoader")

    async def discover_and_load_services(self, proxy_client=None):
        if not self.services_dir.exists():
            self.logger.warning(f"Services directory not found: {self.services_dir}")
            return

        for service_dir in self.services_dir.iterdir():
            if not service_dir.is_dir():
                continue
            await self.load_service_from_directory(service_dir, proxy_client)

    async def load_service_from_directory(self, service_dir: Path, proxy_client=None):
        service_name = service_dir.name
        main_file = service_dir / "main.py"

        if not main_file.exists():
            self.logger.warning(f"No main.py found in service directory: {service_dir}")
            return

        try:
            module_name = f"service_{service_name}_{hashlib.md5(str(main_file).encode()).hexdigest()[:8]}"

            spec = importlib.util.spec_from_file_location(module_name, main_file)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            if hasattr(module, 'Run'):
                run_class = module.Run

                if not issubclass(run_class, BaseService):
                    self.logger.error(f"Service {service_name}: Run class must inherit from BaseService")
                    return

                await self.registry.register_service_class(run_class, proxy_client)
                self.logger.info(f"Successfully loaded service: {service_name}")

            else:
                self.logger.error(f"No Run class found in {main_file}")

        except Exception as e:
            self.logger.error(f"Failed to load service {service_name}: {e}")
            if module_name in sys.modules:
                del sys.modules[module_name]


# =====================================================
# METRICS COLLECTOR
# =====================================================

class ReactiveMetricsCollector:
    def __init__(self):
        self.services: Dict[str, BaseService] = {}
        self.logger = logging.getLogger("MetricsCollector")

    def register_service(self, service_name: str, service_instance: BaseService):
        self.services[service_name] = service_instance
        self.logger.info(f"Registered service for metrics: {service_name}")

    def get_aggregated_metrics(self):
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
                        "last_updated": metrics.last_updated
                    }
            except Exception as e:
                self.logger.error(f"Error collecting metrics from {service_name}: {e}")

        return aggregated


# =====================================================
# SERVICE MANAGER
# =====================================================

class ServiceManager:
    """Главный менеджер сервисов с интегрированными метриками"""

    def __init__(self, rpc_handler):
        self.rpc = rpc_handler
        self.method_registry: Dict[str, Callable] = {}  # ВМЕСТО ГЛОБАЛЬНОЙ ПЕРЕМЕННОЙ
        self.registry = ServiceRegistry(rpc_handler, service_manager=self)
        self.proxy_client = None
        self.logger = logging.getLogger("ServiceManager")
        self.metrics_collector = ReactiveMetricsCollector()

    @property
    def services(self):
        """Прокси к registry.services для обратной совместимости"""
        return self.registry.services

    def set_proxy_client(self, proxy_client):
        self.proxy_client = proxy_client
        self.logger.info("Setting proxy client for all services...")

        for service_name, service_instance in self.registry.services.items():
            try:
                if hasattr(service_instance, 'set_proxy'):
                    service_instance.set_proxy(proxy_client)
                    self.logger.info(f"Proxy injected into service: {service_name}")
                elif hasattr(service_instance, 'proxy'):
                    service_instance.proxy = proxy_client
                    self.logger.info(f"Proxy set directly for service: {service_name}")
            except Exception as e:
                self.logger.error(f"Failed to set proxy for service {service_name}: {e}")

    async def initialize_service(self, service_instance: BaseService):
        try:
            service_instance.status = ServiceStatus.INITIALIZING

            if self.proxy_client and hasattr(service_instance, 'set_proxy'):
                service_instance.set_proxy(self.proxy_client)

            await service_instance.initialize()
            service_instance.status = ServiceStatus.RUNNING

            await self.registry.start_service(service_instance.service_name, service_instance)

            self.logger.info(f"Service initialized: {service_instance.service_name}")

        except Exception as e:
            self.logger.error(f"Failed to initialize service {service_instance.service_name}: {e}")

    async def initialize_all_services(self):
        services_dir = get_services_path()

        if not services_dir.exists():
            self.logger.info("Services directory not found, skipping service initialization")
            return

        self.logger.info(f"Scanning services directory: {services_dir.absolute()}")

        service_loader = ServiceLoader(services_dir, self.registry)
        await service_loader.discover_and_load_services(self.proxy_client)

        self.logger.info(f"Initialized {len(self.registry.services)} services")

    async def shutdown_all_services(self):
        for service_name in list(self.registry.services.keys()):
            try:
                await self.registry.stop_service(service_name)
            except Exception as e:
                self.logger.error(f"Error stopping service {service_name}: {e}")

    def list_available_methods(self) -> Dict[str, List[str]]:
        methods = {}
        for service_name, service_instance in self.registry.services.items():
            if hasattr(service_instance, 'info'):
                methods[service_name] = service_instance.info.exposed_methods
            else:
                methods[service_name] = []
        return methods

    def get_service_details(self, service_name: str) -> Optional[Dict[str, Any]]:
        service = self.registry.get_service(service_name)
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
        services_info = {}

        for service_name, service_instance in self.registry.services.items():
            try:
                services_info[service_name] = {
                    "status": service_instance.status.value,
                    "methods": service_instance.info.exposed_methods,
                    "version": service_instance.info.version
                }
            except Exception as e:
                self.logger.error(f"Error getting service info for gossip: {e}")
                services_info[service_name] = {"error": str(e)}

        return services_info


# =====================================================
# P2P SERVICE HANDLER
# =====================================================

class P2PServiceHandler:
    """Главный обработчик P2P сервисов с FastAPI интеграцией"""

    def __init__(self, network_layer=None, service_manager=None):
        self.app = FastAPI(title="P2P Service Manager", version="2.0.0")
        self.network = network_layer
        self.service_manager = service_manager or ServiceManager(self)
        self.security = P2PAuthBearer()
        self.logger = logging.getLogger("P2PServiceHandler")

        self._setup_endpoints()

    def _setup_endpoints(self):
        """Настройка всех FastAPI endpoints"""

        @self.app.post("/rpc")
        async def rpc_handler(rpc_request: RPCRequest):
            logger = logging.getLogger("RPC")
            path = rpc_request.method

            logger.debug(f"RPC call: {path} with params: {rpc_request.params}")

            # Получаем все доступные методы из method_registry ServiceManager
            all_methods = dict(self.service_manager.method_registry)

            # Также собираем методы из services
            if hasattr(self.service_manager, 'registry'):
                for service_name, service_instance in self.service_manager.registry.services.items():
                    if hasattr(service_instance, 'info'):
                        for method_name in service_instance.info.exposed_methods:
                            method_path = f"{service_name}/{method_name}"
                            all_methods[method_path] = getattr(service_instance, method_name)

            if path not in all_methods:
                available_methods = list(all_methods.keys())
                logger.error(f"Method {path} not found. Available: {available_methods[:5]}")
                return RPCResponse(
                    error=f"Method {path} not found. Available methods: {available_methods[:10]}"
                )

            try:
                method = all_methods[path]

                if asyncio.iscoroutinefunction(method):
                    result = await method(**rpc_request.params)
                else:
                    result = method(**rpc_request.params)

                return RPCResponse(result=result)

            except Exception as e:
                logger.error(f"RPC error for {path}: {e}")
                return RPCResponse(error=str(e))

        @self.app.get("/services")
        async def list_services(node_id: str = Depends(self.security)):
            return self.service_manager.registry.list_services()

        @self.app.get("/services/{service_name}")
        async def get_service_info(service_name: str, node_id: str = Depends(self.security)):
            details = self.service_manager.get_service_details(service_name)
            if details:
                return details
            raise HTTPException(status_code=404, detail=f"Service {service_name} not found")

        @self.app.get("/cluster/nodes")
        async def get_cluster_nodes(node_id: str = Depends(self.security)):
            if self.network and hasattr(self.network, 'gossip'):
                return await self.network.gossip.get_known_nodes()
            return {"nodes": [], "error": "Network layer not available"}

    async def register_rpc_methods(self, service_name: str, service_instance: BaseService):
        """Регистрация RPC методов сервиса"""
        for method_name in service_instance.info.exposed_methods:
            method = getattr(service_instance, method_name)
            method_path = f"{service_name}/{method_name}"
            self.service_manager.method_registry[method_path] = method
            self.logger.info(f"Registered RPC method: {method_path}")

    async def initialize_all(self):
        await self.service_manager.initialize_all_services()

    async def shutdown_all(self):
        await self.service_manager.shutdown_all_services()


# =====================================================
# UTILITY FUNCTIONS
# =====================================================

def get_exe_dir():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    else:
        return Path(__file__).parent


def get_services_path():
    exe_dir = get_exe_dir()

    possible_paths = [
        exe_dir / ".." / "dist" / "services" if 'PycharmProjects' in str(exe_dir) else None,
        exe_dir / "services",
        Path.cwd() / "services"
    ]

    for services_path in filter(None, possible_paths):
        if services_path.exists():
            return services_path

    services_path = possible_paths[0]
    services_path.mkdir(exist_ok=True)
    return services_path


class SimpleLocalServiceLayer:
    """Простой слой для работы с локальными сервисами"""

    def __init__(self, service_manager: ServiceManager):
        self.service_manager = service_manager
        self.logger = logging.getLogger("SimpleLocalServiceLayer")

    def list_all_services(self) -> Dict[str, Dict[str, Any]]:
        services = {}
        for method_path in self.service_manager.method_registry.keys():
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
        return list(self.service_manager.method_registry.keys())

    def get_service_info(self, service_name: str) -> Optional[Dict[str, Any]]:
        services = self.list_all_services()
        return services.get(service_name)


def diagnose_proxy_issues(service_instance, service_manager=None):
    """Диагностика проблем с proxy"""
    issues = []

    if not hasattr(service_instance, 'proxy'):
        issues.append("Service doesn't have 'proxy' attribute")
    elif service_instance.proxy is None:
        issues.append("Service proxy is None")

    if not hasattr(service_instance, 'set_proxy'):
        issues.append("Service doesn't have 'set_proxy' method")

    if service_manager and not service_manager.proxy_client:
        issues.append("ServiceManager has no proxy_client")

    recommendations = []
    if "Service proxy is None" in issues:
        recommendations.append("Call service.set_proxy(proxy_client) after service creation")
    if not recommendations:
        recommendations.append("All proxy checks passed")

    return {
        "service_name": getattr(service_instance, 'service_name', 'unknown'),
        "has_proxy": service_instance.proxy is not None if hasattr(service_instance, 'proxy') else False,
        "issues": issues,
        "recommendations": recommendations
    }


def create_service_handler(network_layer=None, service_manager=None):
    return P2PServiceHandler(network_layer=network_layer)


def create_service_manager(rpc_handler=None):
    if rpc_handler is None:
        class SimpleRPCHandler:
            pass
        rpc_handler = SimpleRPCHandler()

    return ServiceManager(rpc_handler)


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