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
JWT_SECRET_KEY = "your-super-secret-key-change-this-in-production"
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24

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

        if os.path.exists("services"):
            self.services_path = Path("services")
        else:
            self.services_path = Path("../services")

        # Запуск observer в фоновом режиме
        asyncio.create_task(self.observer())

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

    async def scan_services(self):
        """Сканирование папок сервисов и регистрация новых методов"""
        try:
            if not self.services_path.exists():
                logging.warning(f"Директория сервисов не найдена: {self.services_path}")
                return

            # Получаем все поддиректории в services
            for service_dir in self.services_path.iterdir():
                if not service_dir.is_dir():
                    continue

                service_name = service_dir.name

                # Проверяем, не зарегистрирован ли уже этот сервис
                if service_name in self.registered_services:
                    continue

                # Проверяем, что методы сервиса еще не зарегистрированы
                service_methods_exist = any(
                    key.startswith(service_name + "/")
                    for key in self.method_registry.keys()
                )

                if service_methods_exist:
                    self.registered_services.add(service_name)
                    continue

                # Загружаем класс сервиса
                service_class = self.load_core_service(service_dir)
                if service_class is None:
                    continue

                # Создаем экземпляр класса и регистрируем методы
                try:
                    # Создаем локальный прокси для сервиса
                    local_proxy = None
                    if self.local_bridge:
                        local_proxy = self.local_bridge.get_proxy()

                    # Создаем экземпляр с локальным прокси
                    if hasattr(service_class, '__init__'):
                        # Для BaseService
                        methods_instance = service_class(service_name, local_proxy)
                    else:
                        # Для старых CoreMethods
                        methods_instance = service_class()
                        if hasattr(methods_instance, 'proxy'):
                            methods_instance.proxy = local_proxy

                    await self.register_rpc_methods(service_name, methods_instance)
                    self.registered_services.add(service_name)
                    logging.info(f"Сервис {service_name} успешно зарегистрирован")

                except Exception as e:
                    logging.error(f"Ошибка создания экземпляра сервиса {service_name}: {e}")

        except Exception as e:
            logging.error(f"Ошибка сканирования сервисов: {e}")

    async def observer(self):
        """Основной цикл наблюдателя"""
        logging.info("Запуск RPC Methods Observer...")

        while True:
            try:
                await self.scan_services()
                await asyncio.sleep(60)  # Проверка каждую минуту

            except Exception as ex:
                logging.exception(f"Ошибка в observer: {ex}")
                await asyncio.sleep(60)  # Продолжаем работу даже при ошибках

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
    """Уровень сервисов с FastAPI и локальным взаимодействием"""

    def __init__(self, network_layer: P2PNetworkLayer):
        self.network = network_layer
        self.app = FastAPI(
            title="P2P Administrative Service",
            description="Distributed P2P system for local service administration",
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

            if path not in method_registry:
                raise HTTPException(
                    status_code=404,
                    detail=f"RPC method not found: {path}"
                )

            try:
                # Прямой вызов через локальный слой
                if self.local_bridge:
                    if isinstance(rpc_request.params, dict):
                        result = await self.local_bridge.call_method_direct(
                            *path.split('/', 1), **rpc_request.params
                        )
                    else:
                        # Для позиционных параметров используем старый способ
                        method = method_registry[path]
                        result = await method(*rpc_request.params)
                else:
                    # Fallback на старый способ
                    method = method_registry[path]
                    if isinstance(rpc_request.params, dict):
                        result = await method(**rpc_request.params)
                    else:
                        result = await method(*rpc_request.params)

                return RPCResponse(result=result, id=rpc_request.id)

            except Exception as e:
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

        # Локальные административные endpoints

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

        # Модифицированный broadcast endpoint

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