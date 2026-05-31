# GRID/executor.py

import asyncio
import inspect
import logging
from typing import Any, Callable, AsyncGenerator

from GRID.exceptions import MethodNotFound
from GRID.protocol import MsgPack, PackType
from GRID.stream_registry import StreamRegistry
from GRID.memory import Pipe

log = logging.getLogger('Executor')





class LocalExecutor:
    def __init__(self, services, stream_registry, router_ref=None):
        self.services        = services
        self.stream_registry = stream_registry
        self._router_ref     = router_ref

    async def execute(self, pack: MsgPack) -> MsgPack | AsyncGenerator:
        """Обычный RPC вызов."""
        method: Callable | None = (
            self.services.get_method(pack.service, pack.method)
            if pack.method
            else self.services.get_service(pack.service)
        )
        if not method:
            raise MethodNotFound(pack.service, pack.method)

        log.debug(f'execute {pack.service}.{pack.method}')

        if inspect.isasyncgenfunction(method):
            return method(pack.data)

        result = await method(pack.data) if asyncio.iscoroutinefunction(method) else method(pack.data)

        return MsgPack(
            type=PackType.RESPONSE,
            source=pack.dst,
            dst=pack.source,
            service=pack.service,
            method=pack.method,
            label=pack.label,
            data=result,
        )

    # GRID/executor.py — open_stream передаёт ws и label в ctx

    async def open_stream(self, pack: MsgPack) -> MsgPack:
        service_obj = self.services.get_service(pack.service)
        if not service_obj:
            raise MethodNotFound(pack.service, pack.method)

        from GRID.services.rpc import get_stream_handlers
        handlers = get_stream_handlers(service_obj)
        handler = handlers.get(pack.method)

        if not handler or 'consumer' not in handler:
            raise MethodNotFound(pack.service, f'stream:{pack.method}')

        wrapper = handler.get('wrapper')
        consumer = handler['consumer']

        pipe = Pipe(pipe_id=f'inbound_{pack.label[:8]}', buff_len=10)
        inbound = self.stream_registry.register(pack.label, pipe)

        # получить upstream ws из router для ACK
        upstream_ws = getattr(self._router_ref, 'upstream_ws', None)

        asyncio.create_task(
            self._run_consumer(wrapper, consumer, pipe, pack.data, inbound,
                               ws=upstream_ws, label=pack.label)
        )

        return MsgPack(
            type=PackType.STREAM_READY,
            source=pack.dst,
            dst=pack.source,
            label=pack.label,
            data='ready',
        )

    async def _run_consumer(self, wrapper, consumer, pipe, data, inbound,
                            ws=None, label=None):
        ctx = None
        if wrapper:
            ctx = await wrapper(data) if asyncio.iscoroutinefunction(wrapper) else wrapper(data)

        # пробросить ws и label в ctx для ACK
        if isinstance(ctx, dict):
            ctx['ws'] = ws
            ctx['label'] = label
            ctx['eof'] = False

        inbound.ready.set()
        try:
            await consumer(pipe, ctx)
        except Exception as e:
            log.error(f'consumer error: {e}')
