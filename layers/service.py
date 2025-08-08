import asyncio
import importlib
import logging
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Any, Dict, Union, List
import jwt
import inspect
from datetime import datetime, timedelta
import uuid

from starlette.responses import HTMLResponse

from layers.network import P2PNetworkLayer

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


class AsyncRPCProxy:
    """Динамический прокси для асинхронных RPC вызовов"""

    def __init__(self, client, base_url: str = "", path: str = "", auth_token: str = None):
        self._client = client
        self._base_url = base_url
        self._path = path
        self._auth_token = auth_token

    def __getattr__(self, name: str) -> 'AsyncRPCProxy':
        """Создание цепочки прокси: service.node.domain -> /service/node/domain"""
        new_path = f"{self._path}/{name}" if self._path else name
        return AsyncRPCProxy(
            client=self._client,
            base_url=self._base_url,
            path=new_path,
            auth_token=self._auth_token
        )

    async def __call__(self, *args, **kwargs) -> Any:
        """Выполнение RPC вызова"""

        payload = RPCRequest(
            method=self._path.split('/')[-1],
            params=kwargs if kwargs else list(args),
            id=f"req_{uuid.uuid4()}"
        )

        headers = {"Content-Type": "application/json"}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"

        try:
            result = await self._client.execute_request(
                endpoint=f"/rpc/{self._path}",
                data=payload.dict(),
                headers=headers
            )

            if result.get("error"):
                raise HTTPException(status_code=400, detail=result["error"])

            return result.get("result")

        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail=f"RPC call failed: {str(e)}"
            )


# useless code
class P2PServiceClient:
    """Клиент P2P сервисов с поддержкой await service.node.domain.method()"""

    def __init__(self, network_layer: P2PNetworkLayer, auth_token: str):
        self.network = network_layer
        self.auth_token = auth_token

    def __getattr__(self, name: str) -> AsyncRPCProxy:
        """Точка входа для цепочки прокси"""
        return AsyncRPCProxy(
            client=self.network,
            base_url="",  # URL определяется динамически
            path=name,
            auth_token=self.auth_token
        )

    async def broadcast_call(self, method_path: str, *args, **kwargs) -> List[Dict[str, Any]]:
        """Широковещательный RPC вызов ко всем узлам"""
        payload = RPCRequest(
            method=method_path.split('/')[-1],
            params=kwargs if kwargs else list(args),
            id=f"broadcast_{uuid.uuid4()}"
        )

        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        return await self.network.broadcast_request(
            endpoint=f"/rpc/{method_path}",
            data=payload.dict(),
            headers=headers
        )

    async def close(self):
        """Закрытие клиента"""
        await self.network.stop()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


class RPCMethods:
    def __init__(self, method_registry):
        self.method_registry = method_registry
        self.services_path = Path("../services")
        self.registered_services = set()
        # Запуск observer в фоновом режиме
        import asyncio
        asyncio.create_task(self.observer())

    async def register_rpc_methods(self, path: str, methods_instance):
        """Регистрация RPC методов для динамической диспетчеризации"""
        for name, method in inspect.getmembers(methods_instance, predicate=inspect.ismethod):
            if not name.startswith('_'):
                method_path = f"{path}/{name}"
                self.method_registry[method_path] = method
                logging.info(f"Зарегистрирован RPC метод: {method_path}")

    def load_core_service(self, service_dir: Path):
        """Загрузка core_service.py из директории сервиса"""
        try:
            core_service_path = service_dir / "core_service.py"

            if not core_service_path.exists():
                return None

            # Создаем уникальное имя модуля
            module_name = f"core_service_{service_dir.name}"

            # Загружаем модуль
            spec = importlib.util.spec_from_file_location(module_name, core_service_path)
            module = importlib.util.module_from_spec(spec)

            # Добавляем в sys.modules чтобы избежать повторных загрузок
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Ищем класс CoreMethods
            if hasattr(module, 'CoreMethods'):
                return module.CoreMethods
            else:
                logging.warning(f"Класс CoreMethods не найден в {core_service_path}")
                return None

        except Exception as e:
            logging.error(f"Ошибка загрузки {service_dir}/core_service.py: {e}")
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

                # Проверяем наличие core_service.py
                core_service_path = service_dir / "core_service.py"
                if not core_service_path.exists():
                    continue

                # Проверяем, что методы сервиса еще не зарегистрированы
                service_methods_exist = any(
                    key.startswith(service_name + "/")
                    for key in self.method_registry.keys()
                )

                if service_methods_exist:
                    self.registered_services.add(service_name)
                    continue

                # Загружаем класс CoreMethods
                core_methods_class = self.load_core_service(service_dir)
                if core_methods_class is None:
                    continue

                # Создаем экземпляр класса и регистрируем методы
                try:
                    methods_instance = core_methods_class()
                    await self.register_rpc_methods(service_name, methods_instance)
                    self.registered_services.add(service_name)
                    logging.info(f"Сервис {service_name} успешно зарегистрирован")

                except Exception as e:
                    logging.error(f"Ошибка создания экземпляра CoreMethods для {service_name}: {e}")

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


# Реестр методов для динамической диспетчеризации


class P2PServiceLayer:
    """Уровень сервисов с FastAPI и RPC диспетчеризацией"""

    def __init__(self, network_layer: P2PNetworkLayer):
        self.network = network_layer
        self.app = FastAPI(
            title="P2P Administrative Service",
            description="Distributed P2P system for local service administration",
            version="1.0.0"
        )
        self.security = P2PAuthBearer()
        self.setup_endpoints()

    def setup_endpoints(self):
        """Настройка FastAPI endpoints"""

        @self.app.post("/rpc/{path:path}")
        async def rpc_endpoint(
                path: str,
                rpc_request: RPCRequest,
                node_id: str = Depends(self.security)
        ):
            """Динамический RPC endpoint"""

            if path not in method_registry:
                raise HTTPException(
                    status_code=404,
                    detail=f"RPC method not found: {path}"
                )

            try:
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
            with open('docs/p2p_admin_dashboard.html', 'r', encoding='utf-8') as f:
                return HTMLResponse(content=f.read())

        # ЗАМЕНИТЕ существующий @self.app.post("/admin/broadcast") на:

        @self.app.post("/admin/broadcast")
        async def admin_broadcast(
                broadcast_request: Dict[str, Any],
                node_id: str = Depends(self.security)
        ):
            """УЛУЧШЕННЫЙ Административный широковещательный запрос с поддержкой доменов"""

            # Debug информация
            print("🚀 BROADCAST DEBUG: New broadcast endpoint called!")
            print(f"   Request: {broadcast_request}")

            method_path = broadcast_request.get('method')
            params = broadcast_request.get('params', {})
            target_role = broadcast_request.get('target_role')

            if not method_path:
                raise HTTPException(status_code=400, detail="method is required")

            # НОВАЯ ЛОГИКА: Извлекаем домен из параметров
            target_domain = params.get('_target_domain')

            if target_domain:
                print(f"🌐 Domain filter detected: {target_domain}")

            # Убираем служебные параметры перед отправкой методу
            clean_params = {k: v for k, v in params.items() if not k.startswith('_target_')}

            print(f"🧹 Original params: {params}")
            print(f"🧹 Cleaned params: {clean_params}")

            # Создание RPC запроса с ЧИСТЫМИ параметрами
            rpc_request = RPCRequest(
                method=method_path.split('/')[-1],
                params=clean_params,  # ← Используем очищенные параметры!
                id=f"broadcast_{uuid.uuid4()}"
            )

            headers = {"Authorization": f"Bearer {self._generate_internal_token(node_id)}"}

            # TODO: Здесь можно добавить фильтрацию по домену
            # Пока отправляем ко всем узлам с target_role
            print(f"📡 Broadcasting method '{method_path}' to role '{target_role}'")
            if target_domain:
                print(f"   Note: Domain filtering '{target_domain}' not yet implemented in network layer")

            results = await self.network.broadcast_request(
                endpoint=f"/rpc/{method_path}",
                data=rpc_request.dict(),
                headers=headers,
                target_role=target_role
            )

            print(f"📊 Broadcast results: {len(results)} responses")
            success_count = len([r for r in results if r.get('success')])
            print(f"   Successful: {success_count}/{len(results)}")

            return {
                "broadcast_id": rpc_request.id,
                "results": results,
                "success_count": success_count,
                "total_count": len(results),
                "target_domain": target_domain  # Добавляем в ответ для debug
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
