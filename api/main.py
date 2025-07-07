"""
FastAPI application for P2P Admin System
"""

import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.p2p_node import P2PNode
from core.auth import P2PAuth, get_current_node
from config.settings import Settings
from .routes import create_routes
from .websockets import WebSocketManager

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str
    node_id: str
    version: str = "1.0.0"


class MessageRequest(BaseModel):
    type: str
    data: dict


class MessageResponse(BaseModel):
    status: str
    data: Optional[dict] = None
    error: Optional[str] = None


class RPCRequest(BaseModel):
    service: str
    domain: str
    method: str
    args: list = []
    kwargs: dict = {}


class RPCResponse(BaseModel):
    status: str
    data: Optional[dict] = None
    error: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения"""
    # Startup
    logger.info("FastAPI application starting up")

    # Инициализация WebSocket менеджера
    app.state.ws_manager = WebSocketManager(app.state.p2p_node)

    yield

    # Shutdown
    logger.info("FastAPI application shutting down")

    # Закрытие всех WebSocket соединений
    await app.state.ws_manager.disconnect_all()


def create_app(p2p_node: P2PNode, settings: Settings) -> FastAPI:
    """Создание FastAPI приложения"""

    app = FastAPI(
        title="P2P Admin System API",
        description="Асинхронная P2P система для администрирования локальных сервисов",
        version="1.0.0",
        lifespan=lifespan
    )

    # Сохранение ссылок в состоянии приложения
    app.state.p2p_node = p2p_node
    app.state.settings = settings
    app.state.auth = P2PAuth(secret_key=settings.auth_secret)

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # В продакшене ограничить
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Обработчик ошибок
    @app.exception_handler(Exception)
    async def global_exception_handler(request, exc):
        logger.error(f"Unhandled exception: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"}
        )

    # Health check endpoint
    @app.get("/health", response_model=HealthResponse)
    async def health_check():
        """Проверка здоровья узла"""
        return HealthResponse(
            status="healthy",
            node_id=p2p_node.dht.node_id
        )

    # P2P message endpoint
    @app.post("/p2p/message", response_model=MessageResponse)
    async def receive_message(
            message: MessageRequest,
            current_node: str = Depends(get_current_node)
    ):
        """Прием P2P сообщений"""
        try:
            logger.debug(f"Received P2P message from {current_node}: {message.type}")

            # Обработка различных типов сообщений
            if message.type == "ping":
                return MessageResponse(
                    status="success",
                    data={"pong": True, "node_id": p2p_node.dht.node_id}
                )

            elif message.type == "task_assignment":
                task = message.data.get("task")
                if task:
                    p2p_node.active_tasks[task["id"]] = task
                    return MessageResponse(status="accepted")

            elif message.type == "node_leaving":
                node_id = message.data.get("node_id")
                if node_id:
                    await p2p_node._remove_peer(node_id)

            elif message.type == "broadcast":
                # Переадресация broadcast сообщений через WebSocket
                await app.state.ws_manager.broadcast(message.data)

            return MessageResponse(status="success")

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return MessageResponse(
                status="error",
                error=str(e)
            )

    # RPC endpoint
    @app.post("/rpc", response_model=RPCResponse)
    async def rpc_call(
            request: RPCRequest,
            current_node: str = Depends(get_current_node)
    ):
        """RPC вызовы"""
        try:
            logger.debug(f"RPC call from {current_node}: {request.service}.{request.domain}.{request.method}")

            # Получение сервиса
            service_key = f"{request.service}.{request.domain}"
            service = p2p_node.services.get(service_key)

            if not service:
                raise HTTPException(status_code=404, detail=f"Service {service_key} not found")

            # Получение метода
            handler = service.get("handler")
            if not hasattr(handler, request.method):
                raise HTTPException(status_code=404, detail=f"Method {request.method} not found")

            method = getattr(handler, request.method)

            # Вызов метода
            result = await method(*request.args, **request.kwargs)

            return RPCResponse(
                status="success",
                data=result if isinstance(result, dict) else {"result": result}
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"RPC error: {e}", exc_info=True)
            return RPCResponse(
                status="error",
                error=str(e)
            )

    # WebSocket endpoint
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket соединение для real-time обновлений"""
        await websocket.accept()

        # Добавление в P2P узел
        await p2p_node.add_websocket(websocket)

        # Добавление в менеджер
        connection_id = await app.state.ws_manager.connect(websocket)

        try:
            # Отправка начального статуса
            status = await p2p_node.get_network_status()
            await websocket.send_json({
                "type": "network_status",
                "data": status
            })

            # Обработка сообщений
            while True:
                data = await websocket.receive_text()

                try:
                    message = json.loads(data)

                    # Обработка команд через WebSocket
                    if message.get("type") == "command":
                        command = message.get("command")

                        if command == "get_status":
                            status = await p2p_node.get_network_status()
                            await websocket.send_json({
                                "type": "network_status",
                                "data": status
                            })

                        elif command == "get_tasks":
                            tasks = list(p2p_node.active_tasks.values())
                            await websocket.send_json({
                                "type": "tasks_list",
                                "data": tasks
                            })

                        elif command == "submit_task":
                            task_data = message.get("data", {})
                            task_id = await p2p_node.submit_task(
                                task_data.get("type"),
                                task_data.get("data"),
                                task_data.get("target_node")
                            )
                            await websocket.send_json({
                                "type": "task_submitted",
                                "data": {"task_id": task_id}
                            })

                except json.JSONDecodeError:
                    await websocket.send_json({
                        "type": "error",
                        "data": {"message": "Invalid JSON"}
                    })
                except Exception as e:
                    logger.error(f"WebSocket message error: {e}")
                    await websocket.send_json({
                        "type": "error",
                        "data": {"message": str(e)}
                    })

        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected: {connection_id}")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            # Удаление из P2P узла
            await p2p_node.remove_websocket(websocket)

            # Удаление из менеджера
            app.state.ws_manager.disconnect(connection_id)

    # API информация
    @app.get("/api/info")
    async def api_info():
        """Информация об API"""
        return {
            "version": "1.0.0",
            "node_id": p2p_node.dht.node_id,
            "services": list(p2p_node.services.keys()),
            "task_handlers": list(p2p_node.task_handlers.keys()),
            "peers_count": len(p2p_node.peers),
            "active_tasks": len([t for t in p2p_node.active_tasks.values() if t["status"] == "running"])
        }

    # Статистика узла
    @app.get("/api/stats")
    async def node_stats():
        """Статистика узла"""
        import psutil

        # Системная информация
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')

        # Сетевая статистика
        net_io = psutil.net_io_counters()

        return {
            "system": {
                "cpu_percent": cpu_percent,
                "memory": {
                    "total": memory.total,
                    "available": memory.available,
                    "percent": memory.percent
                },
                "disk": {
                    "total": disk.total,
                    "used": disk.used,
                    "free": disk.free,
                    "percent": disk.percent
                }
            },
            "network": {
                "bytes_sent": net_io.bytes_sent,
                "bytes_recv": net_io.bytes_recv,
                "packets_sent": net_io.packets_sent,
                "packets_recv": net_io.packets_recv
            },
            "p2p": {
                "peers": len(p2p_node.peers),
                "active_tasks": len([t for t in p2p_node.active_tasks.values() if t["status"] == "running"]),
                "completed_tasks": len([t for t in p2p_node.active_tasks.values() if t["status"] == "completed"]),
                "failed_tasks": len([t for t in p2p_node.active_tasks.values() if t["status"] == "failed"])
            }
        }

    # Подключение дополнительных маршрутов
    routes = create_routes(p2p_node)
    app.include_router(routes, prefix="/api/v1")

    return app


# Вспомогательная функция для локального тестирования
if __name__ == "__main__":
    import uvicorn
    from core.p2p_node import P2PNode
    from config.settings import Settings

    # Создание тестового узла
    node = P2PNode()
    settings = Settings()

    # Создание приложения
    app = create_app(node, settings)

    # Запуск сервера
    uvicorn.run(app, host="127.0.0.1", port=8000)