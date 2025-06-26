"""
API routes for P2P Admin System
"""

import logging
from typing import List, Optional, Dict
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

from core.p2p_node import P2PNode
from core.auth import get_current_node, require_trusted_node
from services.process_manager import ProcessManagerService
from services.file_manager import FileManagerService
from services.network_manager import NetworkManagerService
from services.system_monitor import SystemMonitorService

logger = logging.getLogger(__name__)


# Pydantic модели для запросов/ответов
class ProcessStartRequest(BaseModel):
    name: str
    command: str
    cwd: Optional[str] = None
    env: Optional[dict] = None
    restart_policy: Optional[dict] = None


class ProcessActionRequest(BaseModel):
    name: str
    force: bool = False


class CommandExecuteRequest(BaseModel):
    command: str
    timeout: int = 30
    cwd: Optional[str] = None
    node_id: Optional[str] = None


class TaskSubmitRequest(BaseModel):
    type: str
    data: dict
    target_node: Optional[str] = None


class FileOperationRequest(BaseModel):
    path: str
    operation: str
    content: Optional[str] = None
    destination: Optional[str] = None


class NetworkScanRequest(BaseModel):
    target: str
    port_range: Optional[str] = None
    timeout: int = 1


def create_routes(p2p_node: P2PNode) -> APIRouter:
    """Создание маршрутов API"""

    router = APIRouter()

    # Получение сервисов
    process_service = p2p_node.services.get("system.process", {}).get("handler")
    file_service = p2p_node.services.get("system.file", {}).get("handler")
    network_service = p2p_node.services.get("system.network", {}).get("handler")
    monitor_service = p2p_node.services.get("system.monitor", {}).get("handler")

    # === Process Management Routes ===

    @router.get("/processes")
    async def list_processes(
            filter_managed: bool = Query(False),
            node_id: Optional[str] = Query(None),
            current_node: str = Depends(get_current_node)
    ):
        """Получение списка процессов"""
        if node_id and node_id != p2p_node.dht.node_id:
            # Проксирование запроса на другой узел
            proxy = p2p_node.get_proxy(node_id)
            if proxy:
                return await proxy.system.process.list_processes(filter_managed)
            else:
                raise HTTPException(404, f"Node {node_id} not found")

        if not process_service:
            raise HTTPException(503, "Process service not available")

        return await process_service.list_processes(filter_managed)

    @router.post("/processes/start")
    async def start_process(
            request: ProcessStartRequest,
            current_node: str = Depends(get_current_node)
    ):
        """Запуск процесса"""
        if not process_service:
            raise HTTPException(503, "Process service not available")

        return await process_service.start_process(
            request.name,
            request.command,
            request.cwd,
            request.env,
            request.restart_policy
        )

    @router.post("/processes/stop")
    async def stop_process(
            request: ProcessActionRequest,
            current_node: str = Depends(get_current_node)
    ):
        """Остановка процесса"""
        if not process_service:
            raise HTTPException(503, "Process service not available")

        return await process_service.stop_process(request.name, request.force)

    @router.post("/processes/restart")
    async def restart_process(
            request: ProcessActionRequest,
            current_node: str = Depends(get_current_node)
    ):
        """Перезапуск процесса"""
        if not process_service:
            raise HTTPException(503, "Process service not available")

        return await process_service.restart_process(request.name)

    @router.get("/processes/{name}")
    async def get_process_info(
            name: str,
            current_node: str = Depends(get_current_node)
    ):
        """Получение информации о процессе"""
        if not process_service:
            raise HTTPException(503, "Process service not available")

        info = await process_service.get_process_info(name)
        if not info:
            raise HTTPException(404, f"Process {name} not found")

        return info

    @router.get("/processes/{name}/logs")
    async def get_process_logs(
            name: str,
            lines: int = Query(100),
            current_node: str = Depends(get_current_node)
    ):
        """Получение логов процесса"""
        if not process_service:
            raise HTTPException(503, "Process service not available")

        return await process_service.get_process_logs(name, lines)

    # === Command Execution ===

    @router.post("/execute")
    async def execute_command(
            request: CommandExecuteRequest,
            current_node: str = Depends(get_current_node)
    ):
        """Выполнение команды"""
        if request.node_id and request.node_id != p2p_node.dht.node_id:
            # Выполнение на удаленном узле
            result = await p2p_node.submit_task(
                "execute_command",
                {
                    "command": request.command,
                    "timeout": request.timeout,
                    "cwd": request.cwd
                },
                request.node_id
            )
            return {"task_id": result}

        if not process_service:
            raise HTTPException(503, "Process service not available")

        return await process_service.execute_command(
            request.command,
            request.timeout,
            request.cwd
        )

    # === Task Management ===

    @router.post("/tasks")
    async def submit_task(
            request: TaskSubmitRequest,
            current_node: str = Depends(get_current_node)
    ):
        """Отправка задачи"""
        task_id = await p2p_node.submit_task(
            request.type,
            request.data,
            request.target_node
        )
        return {"task_id": task_id}

    @router.get("/tasks")
    async def list_tasks(
            status: Optional[str] = Query(None),
            limit: int = Query(100)
    ):
        """Получение списка задач"""
        tasks = list(p2p_node.active_tasks.values())

        if status:
            tasks = [t for t in tasks if t["status"] == status]

        # Сортировка по времени создания
        tasks.sort(key=lambda x: x.get("created_at", 0), reverse=True)

        return tasks[:limit]

    @router.get("/tasks/{task_id}")
    async def get_task_info(task_id: str):
        """Получение информации о задаче"""
        task = p2p_node.active_tasks.get(task_id)
        if not task:
            raise HTTPException(404, f"Task {task_id} not found")
        return task

    # === File Management ===

    @router.post("/files/operation")
    @require_trusted_node
    async def file_operation(
            request: FileOperationRequest,
            current_node: str = Depends(get_current_node)
    ):
        """Файловые операции"""
        if not file_service:
            raise HTTPException(503, "File service not available")

        if request.operation == "read":
            return await file_service.read_file(request.path)
        elif request.operation == "write":
            return await file_service.write_file(request.path, request.content)
        elif request.operation == "delete":
            return await file_service.delete_file(request.path)
        elif request.operation == "move":
            return await file_service.move_file(request.path, request.destination)
        elif request.operation == "copy":
            return await file_service.copy_file(request.path, request.destination)
        else:
            raise HTTPException(400, f"Unknown operation: {request.operation}")

    @router.get("/files/list")
    async def list_files(
            path: str = Query("/"),
            pattern: Optional[str] = Query(None),
            current_node: str = Depends(get_current_node)
    ):
        """Список файлов в директории"""
        if not file_service:
            raise HTTPException(503, "File service not available")

        return await file_service.list_directory(path, pattern)

    @router.get("/files/info")
    async def file_info(
            path: str = Query(...),
            current_node: str = Depends(get_current_node)
    ):
        """Информация о файле"""
        if not file_service:
            raise HTTPException(503, "File service not available")

        return await file_service.get_file_info(path)

    # === Network Management ===

    @router.get("/network/interfaces")
    async def list_interfaces(
            current_node: str = Depends(get_current_node)
    ):
        """Список сетевых интерфейсов"""
        if not network_service:
            raise HTTPException(503, "Network service not available")

        return await network_service.get_interfaces()

    @router.get("/network/connections")
    async def list_connections(
            kind: str = Query("inet"),
            current_node: str = Depends(get_current_node)
    ):
        """Список сетевых соединений"""
        if not monitor_service:
            raise HTTPException(503, "Monitor service not available")

        return await monitor_service.get_network_connections(kind)

    @router.post("/network/scan")
    @require_trusted_node
    async def network_scan(
            request: NetworkScanRequest,
            current_node: str = Depends(get_current_node)
    ):
        """Сканирование сети"""
        if not network_service:
            raise HTTPException(503, "Network service not available")

        return await network_service.scan_ports(
            request.target,
            request.port_range,
            request.timeout
        )

    # === System Monitoring ===

    @router.get("/monitor/status")
    async def monitor_status():
        """Статус мониторинга"""
        if not monitor_service:
            raise HTTPException(503, "Monitor service not available")

        return await monitor_service.get_status()

    @router.get("/monitor/metrics")
    async def get_metrics(
            metric_type: str = Query(...),
            duration_minutes: int = Query(60)
    ):
        """Получение метрик"""
        if not monitor_service:
            raise HTTPException(503, "Monitor service not available")

        return await monitor_service.get_metrics_history(metric_type, duration_minutes)

    @router.get("/monitor/alerts")
    async def get_alerts():
        """Получение алертов"""
        if not monitor_service:
            raise HTTPException(503, "Monitor service not available")

        return list(monitor_service.alerts)

    @router.delete("/monitor/alerts")
    async def clear_alerts(
            current_node: str = Depends(get_current_node)
    ):
        """Очистка алертов"""
        if not monitor_service:
            raise HTTPException(503, "Monitor service not available")

        monitor_service.clear_alerts()
        return {"status": "success"}

    @router.put("/monitor/thresholds")
    async def update_thresholds(
            thresholds: Dict[str, float],
            current_node: str = Depends(get_current_node)
    ):
        """Обновление порогов"""
        if not monitor_service:
            raise HTTPException(503, "Monitor service not available")

        for metric, value in thresholds.items():
            monitor_service.set_threshold(metric, value)

        return {"status": "success", "thresholds": monitor_service.get_thresholds()}

    # === P2P Network Management ===

    @router.get("/p2p/peers")
    async def list_peers():
        """Список пиров"""
        peers = []
        for peer_id, peer_info in p2p_node.peers.items():
            peers.append({
                "node_id": peer_id,
                "host": peer_info.host,
                "port": peer_info.port,
                "status": peer_info.status,
                "last_contact": peer_info.last_contact
            })
        return peers

    @router.post("/p2p/broadcast")
    async def broadcast_message(
            message: dict,
            current_node: str = Depends(get_current_node)
    ):
        """Широковещательное сообщение"""
        successful = await p2p_node.broadcast_message(message)
        return {
            "status": "success",
            "sent_to": successful,
            "total_peers": len(p2p_node.peers)
        }

    @router.get("/p2p/services")
    async def list_services():
        """Список доступных сервисов"""
        services = []
        for service_name in p2p_node.services:
            services.append({
                "name": service_name,
                "available": True
            })
        return services

    return router