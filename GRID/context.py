import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from typing import TYPE_CHECKING

from GRID.services.manager import ServiceManager
from GRID.templates import ModuleGeneric

if TYPE_CHECKING:
    from GRID.memory import MemoryModule
    from GRID.network import NetworkModule


class AppContext:
    def __init__(self, config: dict):
        self.config = config
        self.NODE = config.get('node', 'Node0')
        self._modules: list[ModuleGeneric] = []  # порядок важен

        # модули
        self.services = ServiceManager()

        self.network: NetworkModule | None = None
        self.memory: MemoryModule | None = None

    def register(self, module: ModuleGeneric):
        """Регистрация в порядке вызова = порядок startup."""
        self._modules.append(module)
        return module  # чтобы можно было присваивать в одну строку

    async def startup(self):
        for module in self._modules:
            module.log.info('Starting...')
            await module.start()

    async def shutdown(self):
        for module in reversed(self._modules):  # shutdown в обратном порядке
            module.log.info('Stopping...')
            await module.stop()


@asynccontextmanager
async def app_lifespan(ctx: AppContext) -> AsyncGenerator[AppContext, None]:
    await ctx.startup()
    try:
        yield ctx
    finally:
        await ctx.shutdown()
