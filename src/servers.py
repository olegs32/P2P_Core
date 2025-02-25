import asyncio
import logging
from collections import defaultdict
from typing import Dict, List


class LongPollServer:
    def __init__(self):
        self.history_limit = 100
        self.timeout = 60
        self.clients: Dict[str, Dict[str, int | asyncio.Queue | List[Dict[str, int | str]]]] = {}

    async def add_client(self, client_id: str) -> asyncio.Queue:
        """Инициализирует очередь для нового клиента."""
        queue = asyncio.Queue()
        self.clients[client_id] = {
            "queue": queue,
            "delivered_messages": [],  # последние 15 доставленных сообщений
            "undelivered_messages": [],  # все недоставленные сообщения
            "last_id": 0,  # идентификатор последнего доставленного сообщения
        }
        return queue

    def push(self, src: str, dst: str, msg: dict):
        """Отправляет сообщение клиенту с уникальным идентификатором."""
        if dst in self.clients:
            client_data = self.clients[dst]
            message_id = client_data["last_id"] + 1

            # Добавляем новое сообщение в список недоставленных
            client_data["undelivered_messages"].append({'sender': src, "id": message_id, "msg": msg})
            client_data["last_id"] = message_id

            # Уведомляем клиента о новом сообщении через очередь
            client_data["queue"].put_nowait(msg)
            return {'success': True, msg: f"Message '{msg}' sent to client {dst} with id {message_id}",
                    'id': message_id}
        else:
            return {'success': False, msg: f"Client {dst} not found"}

    async def get_message(self, client_id: str, last_id: int):
        """Возвращает новые сообщения для клиента и обновляет список доставленных."""
        if client_id not in self.clients:
            await self.add_client(client_id)

        client_data = self.clients[client_id]
        queue = client_data["queue"]

        # Извлекаем недоставленные сообщения
        new_messages = [
            m for m in client_data["undelivered_messages"] if m["id"] > last_id
        ]

        # Если есть новые сообщения, перемещаем их в список доставленных
        if new_messages:
            client_data["delivered_messages"].extend(new_messages)
            client_data["undelivered_messages"] = [
                m for m in client_data["undelivered_messages"] if m["id"] <= last_id
            ]

            # Оставляем только последние 15 доставленных сообщений
            if len(client_data["delivered_messages"]) > self.history_limit:
                client_data["delivered_messages"] = client_data["delivered_messages"][-self.history_limit:]

            return new_messages

        try:
            # Ждем новых сообщений, если их пока нет
            await asyncio.wait_for(queue.get(), timeout=self.timeout)
            # Повторяем проверку на новые сообщения
            new_messages = [
                m for m in client_data["undelivered_messages"] if m["id"] > last_id
            ]
            return new_messages
        except asyncio.TimeoutError:
            return []  # Таймаут — возвращаем пустой список

    def get_clients(self):
        print({client: self.clients.get(client).get('last_id') for client in self.clients})
        return {client: self.clients.get(client).get('last_id') for client in self.clients}

    def client_id(self, client):
        return self.clients.get(client).get('last_id')

    def state(self):
        return {'state': 'Running',
                'last_id_msg': self.get_clients()
                }


class Router:
    def __init__(self, domain, node, services):
        self.paths: Dict[str: Dict[str: str]] = defaultdict(dict)
        self.services = services
        self.domain = domain
        self.node = node
        self.neighbors: Dict[str, str] = defaultdict(str)

    def to_self_node(self, src, service, data):
        if service in self.services:
            method = self.services[service]
            action = data.get('action', None)
            act = getattr(method, action, None)
            if act:
                try:
                    # print(act)
                    result = {'successfully': True, 'data': act()}

                except Exception as ex:
                    print(ex)
                    result = {'successfully': False, 'data': ex}
                    return result
                self.route(self.node, src, service, result)
                return result
            else:
                return {'successfully': False, 'data': f'No action :{method} {data.get('action')}'}
        else:
            return {'successfully': False, 'data': f'No service found: {service}'}

    def route(self, src: str, dst: str, data):
        """Routes messages between nodes and services"""
        service = src.split('_')[0] if '_' in src else 'agent'

        if dst == self.node:
            return self.to_self_node(src, service, data)

        if not self.domain in dst:
            logging.warning(f'No clients with this ID: {dst}')
            return {'success': False, 'data': f'No clients with this ID: {dst}'}

        msg = {
            'action': data.get('action'),
            'service': service,
            'payload': data.get('payload', {})
        }
        result = self.push(src, dst, msg)
        return {'success': True, 'data': result}

    def push(self, sender: str, to: str, data: dict) -> dict:
        """Pushes message to long polling service"""
        lp_service = self.services.get('lp')
        if not lp_service:
            return {'success': False, 'data': 'Long polling service not available'}
        return lp_service.push(src=sender, dst=to, msg=data)
