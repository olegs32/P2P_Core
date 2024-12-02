import asyncio
import json
from collections import defaultdict
from typing import Dict, List

from fastapi import WebSocket


class AgentStateManager:
    def __init__(self, queue: asyncio.Queue):
        # Храним состояние клиентов в виде словаря
        self.client_states: Dict[str, Dict[str, str]] = defaultdict(dict)
        self.queue = queue

    async def update_operation(self, client_id: str, operation_id: str, state: str):
        """Обновляем состояние операции клиента"""
        self.client_states[client_id][operation_id] = state
        print(f"Обновлено состояние операции: {operation_id} для клиента: {client_id} -> {state}")
        await self.queue.put((client_id, operation_id, state))

    def get_operations(self, client_id: str) -> Dict[str, str]:
        """Возвращаем состояния всех операций клиента"""
        return self.client_states.get(client_id, {})


class AgentProjectManager:
    def __init__(self):
        self.agent_states: Dict[str, Dict[str, Dict[str, str]]] = defaultdict(dict)

    def force_update(self, client_id: str, project_id: str, project: dict):
        """Обновляем состояние проекта клиента"""
        self.agent_states[client_id][project_id] = project
        print(f"Обновлен проект: {project_id} с агента: {client_id}")

    def update(self, client_id: str, project_id: str, param: str, state: str):
        """Обновляем состояние проекта клиента"""
        self.agent_states[client_id][project_id][param] = state
        print(f"Обновлен параметр {param} проекта: {project_id} на агенте: {client_id} -> {state}")

    def get(self, client_id: str, project_id: str = None) -> dict[str, str] | dict[str, dict[str, str]]:
        if project_id is not None:
            return self.agent_states.get(client_id, {}).get(project_id, {})
        else:
            return self.agent_states.get(client_id, {})


# Управление WebSocket-соединениями
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        await self.send_message({'state': 'successfully'})

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_message(self, message: dict):
        """Отправка сообщения всем подключенным клиентам"""
        for connection in self.active_connections:
            await connection.send_text(json.dumps(message))
