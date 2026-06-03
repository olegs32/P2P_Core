import asyncio
import logging
from pathlib import Path

from src.internal_modules.context import AppContext, app_lifespan
from src.internal_modules.setup_logging import setup_logging
from services.loader import ServiceLoader
from src.networking.network import NetworkModule
from src.internal_modules.spawner import Spawner
from src.internal_modules.memory import MemoryModule

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
    ctx.spawn =  ctx.register(Spawner(name='spawner', context=ctx))

    # # регистрация сервисов
    ctx.services.register_service(ctx.spawn)
    ctx.services.register_method(ctx.spawn, 'spawn', ctx.spawn.spawn)
    ctx.services.register_method(ctx.spawn, 'list_generators', ctx.spawn.list_generators)


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
