import asyncio
import logging
import os.path
from pathlib import Path

from GRID.context import AppContext, app_lifespan
from GRID.setup_logging import setup_logging
from GRID.services.loader import ServiceLoader
from layers import ServiceManager
from network import NetworkModule
from memory import MemoryModule

config = {'node': 'Node0'}
BASE_DIR = Path(Path().resolve())
print(BASE_DIR)
# exit()

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

    # # регистрация сервисов
    # test = Test('test', ctx)
    # ctx.services.register_service(test)
    # ctx.services.register_method(test, 'echo', test.echo)
    # ctx.services.register_method(test, 'echo_stream', test.echo_stream)

    # пробрасываем ctx в роуты FastAPI
    ctx.network.app.state.ctx = ctx

    # автозагрузка всех сервисов из ./services/
    loader = ServiceLoader(
        services_path=BASE_DIR / 'services',
        context=ctx,
        services_manager=ctx.services,
    )
    loader.scan()
    loader.watch()  # hot reload

    async with app_lifespan(ctx):
        # всё поднято — основной цикл
        await asyncio.Event().wait()  # бесконечное ожидание


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
