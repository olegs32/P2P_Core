import asyncio
import importlib
import logging
import os.path
import sys
import time
from pathlib import Path
from typing import Any, Dict, Union, List, Optional

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import jwt
import inspect
from datetime import datetime, timedelta
from starlette.responses import HTMLResponse

from layers.network import P2PNetworkLayer
from layers.local_service_bridge import create_local_service_bridge

# JWT конфигурация
JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'fallback-dev-key-only')
JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'HS256')
JWT_EXPIRATION_HOURS = int(os.getenv('JWT_EXPIRATION_HOURS', '24'))

method_registry: Dict[str, Any] = {}


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


def get_exe_dir():
    """Получить директорию exe файла"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    else:
        return Path(__file__).parent


def get_services_path():
    """Получить путь к папке services"""
    exe_dir = get_exe_dir()
    services_path = exe_dir / "services"
    if 'PycharmProjects' in str(services_path):
        services_path = exe_dir / ".." / "dist" / "services"

    log = logging.getLogger('Path')
    log.info(services_path)
    if not services_path.exists():
        services_path.mkdir(exist_ok=True)

    if not services_path.exists():
        services_path = Path.cwd() / "services"
        log.info(services_path)

    if not services_path.exists():
        services_path.mkdir(exist_ok=True)

    return services_path


class P2PAuthBearer(HTTPBearer):
    """P2P аутентификация через Bearer токен"""

    async def __call__(self, request: Request) -> str:
        credentials: HTTPAuthorizationCredentials = await super().__call__(request)

        if not credentials or credentials.scheme != "Bearer":
            raise HTTPException(
                status_code=401,
                detail="Invalid authentication scheme"
            )

        token = credentials.credentials
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
        methods = []
        for method_path in self.method_registry.keys():
            if method_path.startswith(f"{service_name}/"):
                method_name = method_path.split('/', 1)[1]
                methods.append(method_name)

        if methods:
            return {
                "name": service_name,
                "methods": methods,
                "status": "running"
            }
        return None


class RPCMethods:
    def __init__(self, method_registry):
        self.method_registry = method_registry
        self.services_path = Path("services")
        self.registered_services = set()

        # Локальный слой сервисов
        self.local_service_layer = SimpleLocalServiceLayer(method_registry)
        self.local_bridge = None

        self.services_path = get_services_path()

        # asyncio.create_task(self.observer())

    async def register_rpc_methods(self, path: str, methods_instance):
        """Регистрация RPC методов с локальным прокси"""
        # Стандартная регистрация
        for name, method in inspect.getmembers(methods_instance, predicate=inspect.ismethod):
            if not name.startswith('_'):
                method_path = f"{path}/{name}"
                self.method_registry[method_path] = method
                logging.info(f"Зарегистрирован RPC метод: {method_path}")

        if self.local_bridge and hasattr(methods_instance, 'proxy'):
            # Создаем локальный прокси
            local_proxy = self.local_bridge.get_proxy()
            methods_instance.proxy = local_proxy
            logging.info(f"Установлен прокси для сервиса: {path}")

    def load_core_service(self, service_dir: Path):
        """Загрузка core_service.py или main.py из директории сервиса"""
        try:
            # Проверяем сначала main.py (новый стандарт)
            main_service_path = service_dir / "main.py"
            core_service_path = service_dir / "core_service.py"

            service_path = None
            if main_service_path.exists():
                service_path = main_service_path
                class_name = "Run"  # Для main.py ищем класс Run
            elif core_service_path.exists():
                service_path = core_service_path
                class_name = "CoreMethods"  # Для core_service.py ищем CoreMethods
            else:
                return None

            # Создаем уникальное имя модуля
            module_name = f"service_{service_dir.name}_{int(time.time())}"

            # Загружаем модуль
            spec = importlib.util.spec_from_file_location(module_name, service_path)
            module = importlib.util.module_from_spec(spec)

            # Добавляем в sys.modules чтобы избежать повторных загрузок
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Ищем нужный класс
            if hasattr(module, class_name):
                return getattr(module, class_name)
            else:
                logging.warning(f"Класс {class_name} не найден в {service_path}")
                return None

        except Exception as e:
            logging.error(f"Ошибка загрузки {service_dir}/main.py или core_service.py: {e}")
            return None

    # В service.py добавить отладку в scan_services():
    async def scan_services(self):
        """Сканирование папок сервисов и регистрация новых методов"""
        try:
            if not self.services_path.exists():
                logging.warning(f"Директория сервисов не найдена: {self.services_path}")
                return

            logging.info(f"Scanning services directory: {self.services_path}")

            # Получаем все поддиректории в services
            for service_dir in self.services_path.iterdir():
                if not service_dir.is_dir():
                    continue

                service_name = service_dir.name
                logging.info(f"Found service directory: {service_name}")

                # Проверяем, не зарегистрирован ли уже этот сервис
                if service_name in self.registered_services:
                    logging.info(f"Service {service_name} already registered, skipping")
                    continue

                # Проверяем, что методы сервиса еще не зарегистрированы
                service_methods_exist = any(
                    key.startswith(service_name + "/")
                    for key in self.method_registry.keys()
                )

                if service_methods_exist:
                    logging.info(f"Methods for {service_name} already exist, marking as registered")
                    self.registered_services.add(service_name)
                    continue

                # Загружаем класс сервиса
                service_class = self.load_core_service(service_dir)
                if service_class is None:
                    logging.warning(f"No service class found in {service_dir}")
                    continue

                logging.info(f"Loaded service class for {service_name}: {service_class}")

                # Создаем экземпляр класса и регистрируем методы
                try:
                    # Создаем локальный прокси для сервиса
                    local_proxy = None
                    if self.local_bridge:
                        local_proxy = self.local_bridge.get_proxy()
                        logging.info(f"Created local proxy for {service_name}")
                    else:
                        logging.warning(f"No local bridge available for {service_name}")

                    # Создаем экземпляр с локальным прокси
                    if hasattr(service_class, '__init__'):
                        # Для BaseService
                        methods_instance = service_class(service_name, local_proxy)
                        logging.info(f"Created BaseService instance for {service_name}")

                        # ДОБАВЛЕНО: Принудительная инициализация
                        if hasattr(methods_instance, 'initialize') and asyncio.iscoroutinefunction(
                                methods_instance.initialize):
                            logging.info(f"Manually initializing {service_name}...")
                            await methods_instance.initialize()
                            logging.info(f"Service {service_name} initialized successfully")
                    else:
                        # Для старых CoreMethods
                        methods_instance = service_class()
                        if hasattr(methods_instance, 'proxy'):
                            methods_instance.proxy = local_proxy
                        logging.info(f"Created legacy CoreMethods instance for {service_name}")

                    await self.register_rpc_methods(service_name, methods_instance)
                    self.registered_services.add(service_name)
                    logging.info(f"Service {service_name} успешно зарегистрирован и инициализирован")

                except Exception as e:
                    logging.error(f"Ошибка создания экземпляра сервиса {service_name}: {e}")
                    import traceback
                    logging.error(traceback.format_exc())

        except Exception as e:
            logging.error(f"Ошибка сканирования сервисов: {e}")
            import traceback
            logging.error(traceback.format_exc())

    def set_service_manager(self, service_manager):
        """Установка менеджера сервисов для локального моста"""
        try:
            from layers.local_service_bridge import create_local_service_bridge

            self.local_bridge = create_local_service_bridge(
                self.method_registry,
                service_manager
            )
            logging.info("Local service bridge установлен в RPCMethods")

        except ImportError as e:
            logging.warning(f"Не удалось импортировать local_service_bridge: {e}")
            self.local_bridge = None


class P2PServiceLayer:
    """Уровень сервисов с FastAPI и локальным взаимодействием + метрики"""

    def __init__(self, network_layer: P2PNetworkLayer):
        self.network = network_layer
        self.app = FastAPI(
            title="P2P Administrative Service",
            description="Distributed P2P system for local service administration with metrics",
            version="1.0.0"
        )
        self.security = P2PAuthBearer()

        # Локальный слой для сервисов (инициализируется позже через set_local_bridge)
        self.local_service_layer = None
        self.local_bridge = None

        self.setup_endpoints()

    def set_local_bridge(self, local_bridge):
        """Установка локального моста сервисов"""
        self.local_bridge = local_bridge
        # Создаем простой слой для работы с method_registry
        self.local_service_layer = SimpleLocalServiceLayer(method_registry)

    def setup_endpoints(self):
        """Настройка FastAPI endpoints"""

        @self.app.post("/rpc/{path:path}")
        async def rpc_endpoint(
                path: str,
                rpc_request: RPCRequest,
                node_id: str = Depends(self.security)
        ):
            """Динамический RPC endpoint с локальной оптимизацией"""

            import logging
            logger = logging.getLogger("RPC")
            logger.debug(f"RPC call: {path} with params: {rpc_request.params}")

            # ИСПРАВЛЕНИЕ: Используем context method_registry вместо глобального
            # from layers.application_context import get_current_context  # Нужно добавить эту функцию
            # ИЛИ получаем через network layer:
            context_methods = {}
            if hasattr(self.network, 'gossip') and hasattr(self.network.gossip, '_context'):
                context_methods = self.network.gossip._context.list_methods()

            # Объединяем глобальный и контекстный реестры
            all_methods = {**method_registry, **context_methods}

            if path not in all_methods:
                available_methods = list(all_methods.keys())
                logger.error(f"RPC method not found: {path}")
                logger.error(f"Global registry: {list(method_registry.keys())}")
                logger.error(f"Context registry: {list(context_methods.keys())}")
                raise HTTPException(
                    status_code=404,
                    detail=f"RPC method not found: {path}. Available: {available_methods[:5]}"
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
                import traceback
                logger.error(traceback.format_exc())
                return RPCResponse(error=str(e), id=rpc_request.id)

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

        # === Внутренние Gossip endpoints ===

        @self.app.post("/internal/gossip/join")
        async def gossip_join(join_request: Dict[str, Any]):
            """Обработка запроса на присоединение к кластеру"""
            return await self.network.gossip.handle_join_request(join_request)

        @self.app.post("/internal/gossip/exchange")
        async def gossip_exchange(gossip_data: Dict[str, Any]):
            """Обработка gossip обмена информацией"""
            return await self.network.gossip.handle_gossip_exchange(gossip_data)

        # === Публичные administrative endpoints ===

        @self.app.get("/cluster/nodes")
        async def get_cluster_nodes(node_id: str = Depends(self.security)):
            """Получение списка узлов кластера"""
            return {
                "nodes": [node.to_dict() for node in self.network.gossip.get_live_nodes()],
                "total": len(self.network.gossip.get_live_nodes()),
                "coordinators": len(self.network.gossip.get_coordinators()),
                "workers": len(self.network.gossip.get_workers())
            }

        @self.app.get("/cluster/status")
        async def get_cluster_status(node_id: str = Depends(self.security)):
            """Получение детального статуса кластера"""
            return self.network.get_cluster_status()

        @self.app.get("/health")
        async def health_check():
            """Проверка состояния узла"""
            return {
                "status": "healthy",
                "node_id": self.network.gossip.node_id,
                "role": self.network.gossip.self_info.role,
                "timestamp": datetime.now().isoformat(),
                "active_nodes": len(self.network.gossip.get_live_nodes()),
                "uptime_seconds": (datetime.now() -
                                   datetime.fromisoformat(
                                       self.network.gossip.self_info.metadata['started_at'])).total_seconds()
            }

        @self.app.get("/")
        async def main_web_page():
            """simple web"""
            try:
                with open('docs/p2p_admin_dashboard.html', 'r', encoding='utf-8') as f:
                    return HTMLResponse(content=f.read())
            except FileNotFoundError:
                return {"message": "P2P Admin System", "status": "running"}

        # === Локальные административные endpoints ===

        @self.app.get("/local/services")
        async def get_local_services(node_id: str = Depends(self.security)):
            """Получение списка локальных сервисов"""
            if self.local_service_layer:
                return {
                    "services": self.local_service_layer.list_all_services(),
                    "registry_methods": self.local_service_layer.list_registry_methods()
                }
            return {"services": {}, "registry_methods": []}

        @self.app.get("/local/services/{service_name}")
        async def get_service_info(service_name: str, node_id: str = Depends(self.security)):
            """Получение информации о конкретном сервисе"""
            if self.local_service_layer:
                info = self.local_service_layer.get_service_info(service_name)
                if info:
                    return info
            raise HTTPException(status_code=404, detail=f"Service {service_name} not found")

        @self.app.post("/local/call/{service_name}/{method_name}")
        async def call_local_service_method(
                service_name: str,
                method_name: str,
                params: Dict[str, Any] = {},
                node_id: str = Depends(self.security)
        ):
            """Прямой вызов локального метода сервиса"""
            try:
                if self.local_bridge:
                    result = await self.local_bridge.call_method_direct(service_name, method_name, **params)
                    return {"result": result, "success": True}
                else:
                    raise HTTPException(status_code=503, detail="Local bridge not available")
            except Exception as e:
                return {"error": str(e), "success": False}

        # === НОВЫЕ ENDPOINTS ДЛЯ МЕТРИК ===

        @self.app.get("/local/services/{service_name}/metrics")
        async def get_service_metrics(service_name: str, node_id: str = Depends(self.security)):
            """Получение метрик конкретного сервиса"""
            from layers.service_framework import get_global_service_manager

            manager = get_global_service_manager()
            if not manager:
                raise HTTPException(status_code=503, detail="Service manager not available")

            metrics = manager.get_service_metrics(service_name)
            if not metrics:
                raise HTTPException(status_code=404, detail=f"Service {service_name} not found or no metrics available")

            return metrics

        @self.app.get("/metrics/node")
        async def get_node_metrics(node_id: str = Depends(self.security)):
            """Получение агрегированных метрик узла"""
            from layers.service_framework import get_global_service_manager

            manager = get_global_service_manager()
            if not manager:
                raise HTTPException(status_code=503, detail="Service manager not available")

            return {
                "node_id": self.network.gossip.node_id,
                "timestamp": time.time(),
                "metrics": manager.get_all_services_metrics()
            }

        @self.app.get("/metrics/health")
        async def get_services_health(node_id: str = Depends(self.security)):
            """Health статус всех сервисов с метриками"""
            from layers.service_framework import get_global_service_manager

            manager = get_global_service_manager()
            if not manager:
                raise HTTPException(status_code=503, detail="Service manager not available")

            health_data = manager.get_services_health()

            return {
                "node_id": self.network.gossip.node_id,
                "timestamp": time.time(),
                "services": health_data,
                "summary": {
                    "total_services": len(health_data),
                    "alive_services": len([s for s in health_data.values() if s['status'] == 'alive']),
                    "dead_services": len([s for s in health_data.values() if s['status'] == 'dead'])
                }
            }

        @self.app.get("/cluster/metrics")
        async def get_cluster_metrics(node_id: str = Depends(self.security)):
            """Получение метрик всего кластера"""
            # Получаем метрики с других узлов через broadcast
            try:
                headers = {"Authorization": f"Bearer {self._generate_internal_token(node_id)}"}

                # Broadcast запрос метрик ко всем узлам
                results = await self.network.broadcast_request(
                    endpoint="/metrics/node",
                    data={},
                    headers=headers
                )

                cluster_metrics = {
                    "coordinator_node": self.network.gossip.node_id,
                    "timestamp": time.time(),
                    "total_nodes": len(results),
                    "nodes": {}
                }

                for result in results:
                    if result.get('success') and 'result' in result:
                        node_data = result['result']
                        if 'node_id' in node_data:
                            cluster_metrics["nodes"][node_data['node_id']] = node_data

                return cluster_metrics

            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error collecting cluster metrics: {e}")

        @self.app.post("/admin/metrics/threshold")
        async def set_metrics_threshold(
                threshold_request: MetricsThresholdRequest,
                node_id: str = Depends(self.security)
        ):
            """Установка порогов для метрик (placeholder для будущего функционала)"""
            # Placeholder - можно расширить для автоматических алертов
            return {
                "status": "accepted",
                "service_name": threshold_request.service_name,
                "metric_name": threshold_request.metric_name,
                "warning_threshold": threshold_request.warning_threshold,
                "critical_threshold": threshold_request.critical_threshold,
                "enabled": threshold_request.enabled,
                "message": "Threshold configuration saved (feature in development)"
            }

        @self.app.post("/admin/metrics/reset/{service_name}")
        async def reset_service_metrics(
                service_name: str,
                node_id: str = Depends(self.security)
        ):
            """Сброс метрик сервиса (экспериментальная функция)"""
            from layers.service_framework import get_global_service_manager

            manager = get_global_service_manager()
            if not manager:
                raise HTTPException(status_code=503, detail="Service manager not available")

            # Попытка получить сервис и сбросить его метрики
            service = manager.services.get(service_name)
            if not service:
                raise HTTPException(status_code=404, detail=f"Service {service_name} not found")

            # Сбрасываем counters (осторожно с этой функцией!)
            try:
                if hasattr(service, 'metrics'):
                    # Сохраняем только основные метрики, сбрасываем счетчики
                    for metric_name, metric_data in service.metrics.data.items():
                        if metric_data['type'].value == 'counter':
                            service.metrics.data[metric_name]['value'] = 0

                    return {
                        "status": "success",
                        "service_name": service_name,
                        "message": "Counter metrics reset",
                        "timestamp": time.time()
                    }
                else:
                    raise HTTPException(status_code=400, detail="Service doesn't have metrics system")

            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error resetting metrics: {e}")

        # Модифицированный broadcast endpoint с поддержкой метрик

        @self.app.post("/admin/broadcast")
        async def admin_broadcast(
                broadcast_request: Dict[str, Any],
                node_id: str = Depends(self.security)
        ):
            """Административный широковещательный запрос с поддержкой доменов"""

            method_path = broadcast_request.get('method')
            params = broadcast_request.get('params', {})
            target_role = broadcast_request.get('target_role')

            if not method_path:
                raise HTTPException(status_code=400, detail="method is required")

            # Извлекаем домен из параметров
            target_domain = params.get('_target_domain')

            # Убираем служебные параметры перед отправкой методу
            clean_params = {k: v for k, v in params.items() if not k.startswith('_target_')}

            # Создание RPC запроса с ЧИСТЫМИ параметрами
            rpc_request = RPCRequest(
                method=method_path.split('/')[-1],
                params=clean_params,
                id=f"broadcast_{int(datetime.now().timestamp())}"
            )

            headers = {"Authorization": f"Bearer {self._generate_internal_token(node_id)}"}

            # Используем существующий broadcast через сеть
            results = await self.network.broadcast_request(
                endpoint=f"/rpc/{method_path}",
                data=rpc_request.dict(),
                headers=headers,
                target_role=target_role
            )

            success_count = len([r for r in results if r.get('success')])

            return {
                "broadcast_id": rpc_request.id,
                "results": results,
                "success_count": success_count,
                "total_count": len(results),
                "target_domain": target_domain
            }

        @self.app.get("/debug/registry")
        async def debug_method_registry(node_id: str = Depends(self.security)):
            """Отладочная информация о зарегистрированных методах"""
            return {
                "registered_methods": list(method_registry.keys()),
                "total_methods": len(method_registry)
            }

        @self.app.get("/debug/metrics")
        async def debug_metrics_system(node_id: str = Depends(self.security)):
            """Отладочная информация о системе метрик"""
            from layers.service_framework import get_global_service_manager

            manager = get_global_service_manager()
            if not manager:
                return {"error": "Service manager not available"}

            debug_info = {
                "metrics_collector_active": hasattr(manager, 'metrics_collector'),
                "registered_services": list(manager.services.keys()),
                "services_with_metrics": [],
                "total_metrics_count": 0
            }

            for service_name, service in manager.services.items():
                if hasattr(service, 'metrics'):
                    metrics_count = len(service.metrics.data)
                    debug_info["services_with_metrics"].append({
                        "name": service_name,
                        "metrics_count": metrics_count,
                        "last_update": service.metrics.last_update
                    })
                    debug_info["total_metrics_count"] += metrics_count

            if hasattr(manager, 'metrics_collector'):
                debug_info["collector_services"] = list(manager.metrics_collector.services.keys())
                debug_info["collector_aggregated"] = manager.metrics_collector.aggregated_metrics

            return debug_info

        @self.app.get("/debug/rpc-status")
        async def debug_rpc_status():
            """Отладочная информация о RPC системе"""
            from layers.service_framework import get_global_service_manager
            manager = get_global_service_manager()

            context_methods = {}
            if self.app_context and hasattr(self.app_context, 'list_methods'):
                context_methods = self.app_context.list_methods()

            return {
                "global_method_registry_count": len(method_registry),
                "context_method_registry_count": len(context_methods),
                "global_methods": list(method_registry.keys()),
                "context_methods": list(context_methods.keys()),
                "system_in_global": any(k.startswith('system/') for k in method_registry.keys()),
                "system_in_context": any(k.startswith('system/') for k in context_methods.keys()),
                "manager_available": manager is not None,
                "manager_services": list(manager.services.keys()) if manager else []
            }

    def _generate_internal_token(self, node_id: str) -> str:
        """Генерация внутреннего токена для межузлового общения"""
        expires = datetime.now() + timedelta(hours=1)
        payload = {
            'sub': node_id,
            'exp': expires.timestamp(),
            'iat': datetime.now().timestamp(),
            'internal': True
        }
        return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)