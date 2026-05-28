# GRID/router.py — только маршрутизация

import asyncio
import logging
from typing import Any, AsyncGenerator

from GRID.executor import LocalExecutor, MethodNotFound
from GRID.protocol import MsgPack, PackType
from GRID.sessions import SessionTable, RPCTimeout
from GRID.transport import WebSocketTransport

log = logging.getLogger('Router')


class NodeNotFound(Exception):
    def __init__(self, node): super().__init__(f'node={node}')


class Router:
    def __init__(self, nodes, context):
        self.context  = context
        self.nodes    = nodes
        self.sessions = SessionTable()
        self.executor = LocalExecutor(context.services)

    # ------------------------------------------------------------------ #
    #  Входящий пакет
    # ------------------------------------------------------------------ #

    async def handle(self, pack: MsgPack, transport: WebSocketTransport):
        match pack.type:
            case PackType.REQUEST:
                response = await self._on_request(pack)
                await transport.send(response)

            case PackType.RESPONSE:
                self.sessions.resolve(pack.label, pack.data)

            case PackType.STREAM_CHUNK:
                self.sessions.resolve(pack.label, pack.data)

            case PackType.STREAM_EOF:
                self.sessions.close_stream(pack.label)

            case PackType.ERROR:
                self.sessions.resolve(pack.label, Exception(pack.error))

            case PackType.PING:
                await transport.send(MsgPack(
                    type=PackType.PONG,
                    source=self.context.NODE,
                    dst=pack.source,
                    label=pack.label,
                ))

    # ------------------------------------------------------------------ #
    #  Обработка входящего запроса
    # ------------------------------------------------------------------ #

    async def _on_request(self, pack: MsgPack) -> MsgPack:
        try:
            return await self.executor.execute(pack)
        except MethodNotFound as e:
            return MsgPack(
                type=PackType.ERROR,
                source=self.context.NODE,
                dst=pack.source,
                label=pack.label,
                error=str(e),
            )

    # ------------------------------------------------------------------ #
    #  Исходящие вызовы
    # ------------------------------------------------------------------ #

    async def call(self, dst: str, service: str, method: str,
                   data: Any = None, timeout: int = 10) -> Any:
        pack = MsgPack(
            type=PackType.REQUEST,
            source=self.context.NODE,
            dst=dst, service=service, method=method, data=data,
        )
        # локальный shortcut — не гонять через сеть
        if dst == self.context.NODE:
            response = await self.executor.execute(pack)
            return response.data

        node = self.nodes.get(dst)
        if not node:
            raise NodeNotFound(dst)

        future = self.sessions.register_single(pack.label)
        transport = WebSocketTransport(node.ws)
        await transport.send(pack)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            if isinstance(result, Exception):
                raise result
            return result
        except asyncio.TimeoutError:
            self.sessions.cancel(pack.label)
            raise RPCTimeout(pack.label, timeout)

    async def stream(self, dst: str, service: str, method: str,
                     data: Any = None) -> AsyncGenerator:
        pack = MsgPack(
            type=PackType.REQUEST,
            source=self.context.NODE,
            dst=dst, service=service, method=method, data=data,
        )
        node = self.nodes.get(dst)
        if not node:
            raise NodeNotFound(dst)

        queue = self.sessions.register_stream(pack.label)
        transport = WebSocketTransport(node.ws)
        await transport.send(pack)

        async def _gen():
            while True:
                chunk = await queue.get()
                if chunk is None:
                    return
                yield chunk

        return _gen()