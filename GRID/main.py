import asyncio
import logging

from GRID.context import AppContext, app_lifespan
from GRID.core_logging import setup_logging
from GRID.services.test import Test
from layers import ServiceManager
from network import NetworkModule
from memory import MemoryModule

config = {'node': 'Node0'}

setup_logging()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("fastapi").setLevel(logging.WARNING)


async def main():
    ctx = AppContext(config)

    # порядок вызовов = порядок загрузки
    ctx.memory = ctx.register(MemoryModule(name='Memory', context=ctx))
    ctx.network = ctx.register(NetworkModule(name='Network', context=ctx, port=9000))

    # регистрация сервисов
    test = Test('test', ctx)
    ctx.services.register_service(test)
    ctx.services.register_method(test, 'echo', test.echo)

    # пробрасываем ctx в роуты FastAPI
    ctx.network.app.state.ctx = ctx

    async with app_lifespan(ctx):
        # всё поднято — основной цикл
        await asyncio.Event().wait()  # бесконечное ожидание


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
