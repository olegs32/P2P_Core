from contextlib import asynccontextmanager
from typing import AsyncGenerator

class AppContext:
    """Центральный контекст приложения"""
    def __init__(self):
        self.network = None
        self.memory = None
        # добавляй модули сюда

    async def startup(self):
        await self.network.start()
        await self.memory.start()

    async def shutdown(self):
        await self.memory.stop()
        await self.network.stop()  # последним

@asynccontextmanager
async def app_lifespan(ctx: AppContext) -> AsyncGenerator[AppContext, None]:
    await ctx.startup()
    try:
        yield ctx
    finally:
        await ctx.shutdown()
