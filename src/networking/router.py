# GRID/router.py

import asyncio
import inspect
import logging
from typing import Any

from src.internal_modules.exceptions import RPCTimeout
from src.internal_modules.executor import LocalExecutor, MethodNotFound
from protocol import MsgPack, PackType
from sessions import SessionTable
from stream_registry import StreamRegistry
from transport import WebSocketTransport

log = logging.getLogger('Router')


class NodeNotFound(Exception):
    def __init__(self, node): super().__init__(f'node={node}')


class Router:
    def __init__(self, nodes, context):
        self.context         = context
        self.nodes           = nodes
        self.sessions        = SessionTable()
        self.stream_registry = StreamRegistry()
        # router.py — передать self в executor
        self.executor = LocalExecutor(context.services, self.stream_registry, router_ref=self)

    async def handle(self, pack: MsgPack, transport: WebSocketTransport):
        match pack.type:
            case PackType.STREAM_ACK:
                # разблокировать ожидающий батч на сервере
                self.sessions.resolve(f'ack_{pack.label}', 'ack')

            case PackType.STREAM_OPEN:
                response = await self._on_stream_open(pack)
                await transport.send(response)

            case PackType.STREAM_READY:
                self.sessions.resolve(pack.label, pack.data)

            case PackType.STREAM_CHUNK:
                await self.stream_registry.feed(pack.label, pack.data)

            case PackType.STREAM_EOF:
                await self.stream_registry.close(pack.label)

            case PackType.REQUEST:
                await self._on_request(pack, transport)

            case PackType.RESPONSE:
                self.sessions.resolve(pack.label, pack.data)

            case PackType.ERROR:
                self.sessions.resolve(pack.label, Exception(pack.error))

            case PackType.PING:
                await transport.send(MsgPack(
                    type=PackType.PONG,
                    source=self.context.NODE,
                    dst=pack.source,
                    label=pack.label,
                ))

    async def _on_request(self, pack: MsgPack, transport: WebSocketTransport):
        try:
            result = await self.executor.execute(pack)
            if inspect.isasyncgen(result):
                async for chunk in result:
                    await transport.send(MsgPack(
                        type    = PackType.STREAM_CHUNK,
                        source  = self.context.NODE,
                        dst     = pack.source,
                        label   = pack.label,
                        data    = chunk,
                    ))
                await transport.send(MsgPack(
                    type   = PackType.STREAM_EOF,
                    source = self.context.NODE,
                    dst    = pack.source,
                    label  = pack.label,
                ))
            else:
                await transport.send(result)
        except MethodNotFound as e:
            await transport.send(MsgPack(
                type   = PackType.ERROR,
                source = self.context.NODE,
                dst    = pack.source,
                label  = pack.label,
                error  = str(e),
            ))

    async def _on_stream_open(self, pack: MsgPack) -> MsgPack:
        try:
            return await self.executor.open_stream(pack)
        except MethodNotFound as e:
            return MsgPack(
                type   = PackType.ERROR,
                source = self.context.NODE,
                dst    = pack.source,
                label  = pack.label,
                error  = str(e),
            )

    async def call(self, dst: str, service: str, method: str,
                   data: Any = None, timeout: int = 10) -> Any:
        pack = MsgPack(
            type    = PackType.REQUEST,
            source  = self.context.NODE,
            dst     = dst,
            service = service,
            method  = method,
            data    = data,
        )
        if dst == self.context.NODE:
            response = await self.executor.execute(pack)
            return response.data

        node = self.nodes.get(dst)
        if not node:
            raise NodeNotFound(dst)

        future = self.sessions.register_single(pack.label, service, method)
        await WebSocketTransport(node.ws).send(pack)
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            if isinstance(result, Exception):
                raise result
            return result
        except asyncio.TimeoutError:
            self.sessions.cancel(pack.label)
            raise RPCTimeout(pack.label, timeout)