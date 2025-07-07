"""
WebSocket manager for P2P Admin System
"""

import asyncio
import json
import time
import logging
from typing import Dict, Set, Optional, List
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from core.p2p_node import P2PNode

logger = logging.getLogger(__name__)


class WebSocketMessage(BaseModel):
    """WebSocket сообщение"""
    type: str
    data: dict
    timestamp: Optional[float] = None

    def __init__(self, **data):
        super().__init__(**data)
        if self.timestamp is None:
            self.timestamp = time.time()


class WebSocketConnection:
    """WebSocket соединение"""

    def __init__(self, connection_id: str, websocket: WebSocket):
        self.id = connection_id
        self.websocket = websocket
        self.connected_at = time.time()
        self.last_activity = time.time()
        self.subscriptions: Set[str] = set()
        self.metadata: dict = {}

    async def send_message(self, message: WebSocketMessage):
        """Отправка сообщения"""
        try:
            await self.websocket.send_text(message.json())
            self.last_activity = time.time()
        except Exception as e:
            logger.error(f"Failed to send message to {self.id}: {e}")
            raise

    async def send_json(self, data: dict):
        """Отправка JSON данных"""
        message = WebSocketMessage(type="data", data=data)
        await self.send_message(message)

    def subscribe(self, topic: str):
        """Подписка на топик"""
        self.subscriptions.add(topic)

    def unsubscribe(self, topic: str):
        """Отписка от топика"""
        self.subscriptions.discard(topic)

    def is_subscribed(self, topic: str) -> bool:
        """Проверка подписки"""
        return topic in self.subscriptions or "*" in self.subscriptions


class WebSocketManager:
    """Менеджер WebSocket соединений"""

    def __init__(self, p2p_node: P2PNode):
        self.p2p_node = p2p_node
        self.connections: Dict[str, WebSocketConnection] = {}
        self.topic_subscribers: Dict[str, Set[str]] = {}

        # Запуск фоновых задач
        asyncio.create_task(self._heartbeat_loop())
        asyncio.create_task(self._event_broadcaster())

    async def connect(self, websocket: WebSocket) -> str:
        """Подключение нового клиента"""
        connection_id = str(uuid4())
        connection = WebSocketConnection(connection_id, websocket)

        self.connections[connection_id] = connection

        # Отправка приветственного сообщения
        await connection.send_message(WebSocketMessage(
            type="connection",
            data={
                "connection_id": connection_id,
                "node_id": self.p2p_node.dht.node_id,
                "timestamp": time.time()
            }
        ))

        logger.info(f"WebSocket client connected: {connection_id}")

        # Автоматическая подписка на основные события
        connection.subscribe("network_status")
        connection.subscribe("peer_updates")
        connection.subscribe("task_updates")

        return connection_id

    def disconnect(self, connection_id: str):
        """Отключение клиента"""
        if connection_id in self.connections:
            connection = self.connections[connection_id]

            # Удаление из всех подписок
            for topic in connection.subscriptions:
                if topic in self.topic_subscribers:
                    self.topic_subscribers[topic].discard(connection_id)

            del self.connections[connection_id]
            logger.info(f"WebSocket client disconnected: {connection_id}")

    async def disconnect_all(self):
        """Отключение всех клиентов"""
        for connection_id in list(self.connections.keys()):
            try:
                connection = self.connections[connection_id]
                await connection.websocket.close()
            except Exception as e:
                logger.error(f"Error closing connection {connection_id}: {e}")
            finally:
                self.disconnect(connection_id)

    async def send_to_connection(self, connection_id: str, message: WebSocketMessage):
        """Отправка сообщения конкретному соединению"""
        if connection_id in self.connections:
            try:
                await self.connections[connection_id].send_message(message)
            except Exception as e:
                logger.error(f"Failed to send to {connection_id}: {e}")
                self.disconnect(connection_id)

    async def broadcast(self, data: dict, topic: str = None):
        """Широковещательная рассылка"""
        message = WebSocketMessage(
            type="broadcast",
            data={
                "topic": topic or "general",
                "content": data
            }
        )

        # Определение получателей
        if topic:
            recipients = [
                conn_id for conn_id, conn in self.connections.items()
                if conn.is_subscribed(topic)
            ]
        else:
            recipients = list(self.connections.keys())

        # Отправка сообщений
        disconnected = []
        for connection_id in recipients:
            try:
                await self.send_to_connection(connection_id, message)
            except Exception:
                disconnected.append(connection_id)

        # Удаление отключенных
        for connection_id in disconnected:
            self.disconnect(connection_id)

        logger.debug(f"Broadcast to {len(recipients)} connections, topic: {topic}")

    async def handle_message(self, connection_id: str, raw_message: str):
        """Обработка входящего сообщения"""
        try:
            data = json.loads(raw_message)
            message_type = data.get("type")

            if message_type == "subscribe":
                # Подписка на топик
                topic = data.get("topic")
                if topic:
                    self.connections[connection_id].subscribe(topic)

                    if topic not in self.topic_subscribers:
                        self.topic_subscribers[topic] = set()
                    self.topic_subscribers[topic].add(connection_id)

                    await self.send_to_connection(
                        connection_id,
                        WebSocketMessage(
                            type="subscribed",
                            data={"topic": topic}
                        )
                    )

            elif message_type == "unsubscribe":
                # Отписка от топика
                topic = data.get("topic")
                if topic:
                    self.connections[connection_id].unsubscribe(topic)

                    if topic in self.topic_subscribers:
                        self.topic_subscribers[topic].discard(connection_id)

                    await self.send_to_connection(
                        connection_id,
                        WebSocketMessage(
                            type="unsubscribed",
                            data={"topic": topic}
                        )
                    )

            elif message_type == "ping":
                # Пинг-понг для проверки соединения
                await self.send_to_connection(
                    connection_id,
                    WebSocketMessage(type="pong", data={})
                )

            elif message_type == "command":
                # Обработка команд
                await self._handle_command(connection_id, data)

            else:
                # Неизвестный тип сообщения
                await self.send_to_connection(
                    connection_id,
                    WebSocketMessage(
                        type="error",
                        data={"message": f"Unknown message type: {message_type}"}
                    )
                )

        except json.JSONDecodeError:
            await self.send_to_connection(
                connection_id,
                WebSocketMessage(
                    type="error",
                    data={"message": "Invalid JSON"}
                )
            )
        except Exception as e:
            logger.error(f"Error handling message from {connection_id}: {e}")
            await self.send_to_connection(
                connection_id,
                WebSocketMessage(
                    type="error",
                    data={"message": str(e)}
                )
            )

    async def _handle_command(self, connection_id: str, data: dict):
        """Обработка команд через WebSocket"""
        command = data.get("command")
        params = data.get("params", {})

        try:
            if command == "get_network_status":
                status = await self.p2p_node.get_network_status()
                await self.send_to_connection(
                    connection_id,
                    WebSocketMessage(
                        type="command_response",
                        data={
                            "command": command,
                            "result": status
                        }
                    )
                )

            elif command == "get_tasks":
                tasks = list(self.p2p_node.active_tasks.values())
                await self.send_to_connection(
                    connection_id,
                    WebSocketMessage(
                        type="command_response",
                        data={
                            "command": command,
                            "result": tasks
                        }
                    )
                )

            elif command == "submit_task":
                task_id = await self.p2p_node.submit_task(
                    params.get("type"),
                    params.get("data", {}),
                    params.get("target_node")
                )
                await self.send_to_connection(
                    connection_id,
                    WebSocketMessage(
                        type="command_response",
                        data={
                            "command": command,
                            "result": {"task_id": task_id}
                        }
                    )
                )

            else:
                await self.send_to_connection(
                    connection_id,
                    WebSocketMessage(
                        type="command_error",
                        data={
                            "command": command,
                            "error": f"Unknown command: {command}"
                        }
                    )
                )

        except Exception as e:
            await self.send_to_connection(
                connection_id,
                WebSocketMessage(
                    type="command_error",
                    data={
                        "command": command,
                        "error": str(e)
                    }
                )
            )

    async def _heartbeat_loop(self):
        """Цикл отправки heartbeat сообщений"""
        while True:
            try:
                await asyncio.sleep(30)  # Каждые 30 секунд

                # Отправка heartbeat всем соединениям
                disconnected = []
                for connection_id, connection in self.connections.items():
                    try:
                        await connection.send_message(
                            WebSocketMessage(type="heartbeat", data={})
                        )
                    except Exception:
                        disconnected.append(connection_id)

                # Удаление отключенных
                for connection_id in disconnected:
                    self.disconnect(connection_id)

            except Exception as e:
                logger.error(f"Heartbeat loop error: {e}")

    async def _event_broadcaster(self):
        """Цикл рассылки событий P2P узла"""

        # Обработчики событий
        async def on_peer_joined(peer):
            await self.broadcast({
                "event": "peer_joined",
                "peer": {
                    "node_id": peer.node_id,
                    "host": peer.host,
                    "port": peer.port
                }
            }, topic="peer_updates")

        async def on_peer_left(peer):
            await self.broadcast({
                "event": "peer_left",
                "peer": {
                    "node_id": peer.node_id
                }
            }, topic="peer_updates")

        async def on_task_completed(task):
            await self.broadcast({
                "event": "task_completed",
                "task": task
            }, topic="task_updates")

        # Регистрация обработчиков
        self.p2p_node.on_peer_joined = on_peer_joined
        self.p2p_node.on_peer_left = on_peer_left
        self.p2p_node.on_task_completed = on_task_completed

        # Периодическая отправка статуса сети
        while True:
            try:
                await asyncio.sleep(5)  # Каждые 5 секунд

                if self.connections:
                    status = await self.p2p_node.get_network_status()
                    await self.broadcast(status, topic="network_status")

            except Exception as e:
                logger.error(f"Event broadcaster error: {e}")
                await asyncio.sleep(10)

    def get_connection_info(self, connection_id: str) -> Optional[dict]:
        """Получение информации о соединении"""
        if connection_id not in self.connections:
            return None

        connection = self.connections[connection_id]
        return {
            "id": connection.id,
            "connected_at": connection.connected_at,
            "last_activity": connection.last_activity,
            "subscriptions": list(connection.subscriptions),
            "metadata": connection.metadata
        }

    def get_all_connections(self) -> List[dict]:
        """Получение информации о всех соединениях"""
        return [
            self.get_connection_info(conn_id)
            for conn_id in self.connections
        ]

    def get_topic_subscribers(self, topic: str) -> List[str]:
        """Получение подписчиков топика"""
        return list(self.topic_subscribers.get(topic, []))