# GRID/executor.py — вызов локальных методов

import asyncio
import logging
from typing import Any, Callable
from GRID.protocol import MsgPack, PackType

log = logging.getLogger('Executor')


class MethodNotFound(Exception):
    def __init__(self, service, method):
        super().__init__(f'{service}.{method}')

class LocalExecutor:
    def __init__(self, services):
        self.services = services

    async def execute(self, pack: MsgPack) -> MsgPack:
        method: Callable | None = (
            self.services.get_method(pack.service, pack.method)
            if pack.method
            else self.services.get_service(pack.service)
        )
        if not method:
            raise MethodNotFound(pack.service, pack.method)

        log.debug(f'execute {pack.service}.{pack.method}')
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