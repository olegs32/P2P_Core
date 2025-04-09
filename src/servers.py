import asyncio
import logging
from collections import defaultdict
from typing import Dict, List

import httpx


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
        self.host_map = {
            "node1.direct": "127.0.0.1:8001",
            "node2.direct": "127.0.0.1:8002",
        }

    def to_self_node(self, src, dst, data):
        service = dst.split('.')[2]
        key = data['service']
        print('service', service)
        result = None
        if service in self.services:
            method = self.services[service]
            action = data.get('action', None)
            print(method, action, service)

            # try:
            if action == 'getattr':
                act = getattr(method, key, None)
                result = act()
            elif action == 'getitem':
                act = getattr(method, key, None)
                result = act(data['data'])
            elif action == 'call':
                act = getattr(method, key, None)
                result = act(*data['data']['args'], **data['data']['kwargs'])

            # if result:

            # print(act)
            return {'stat': {'host': True,
                             'service': True},
                    'data': result}

            # except Exception as ex:
            #     print(ex)
            #     return {'stat': {'host': True,
            #                      'service': False},
            #             'data': ex}

            # self.route(self.node, src, result)
            # return result
        else:
            return {'stat': {'host': True,
                             'service': False},
                    'data': f'No action: {service} {data.get('action')}'}

        # else:
        #     return {'stat': {'host': True,
        #                  'service': False},
        #         'data': f'No service found: {service}'}

    async def route(self, src: str, dst: str, data):
        """
        dst: service.node.domain
        """
        # service = None
        # parts = dst.split('.')
        # print(parts, dst, self.node)
        #
        # if len(parts) == 2:
        #     broadservice template
        # pass
        # else:
        #     service = parts[0]

        if self.node in dst:
            print('to self node')
            return self.to_self_node(src, dst, data)

        elif self.domain in dst:
            print('sending')
            # msg = {'action': data.get('action'), 'service': service}
            result = await self.push(src, dst, data)
            print(result)
            return result

        else:
            logging.warning(f'No clients with this ID: {dst}')
            return {'success': False, 'data': f'No clients with this ID: {dst}'}

    async def push(self, src, dst, data):
        host = await self.resolve_host(dst)
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(f"http://{host}/route?src={src}&dst={dst}", json=data)
                response.raise_for_status()
                return response.json()
            except Exception as ex:  # Connection refused
                return {'stat': {'host': False,
                                 'service': False},
                        'data': ex}

    # Простейший резолвер: в реальной системе будет искать через DHT или таблицу узлов
    async def resolve_host(self, to_node: str) -> str:
        return self.host_map.get(to_node, "127.0.0.1:8080")
