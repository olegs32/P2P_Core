import asyncio
import logging
from pathlib import Path

from services.loader import ServiceLoader
from src.internal_modules.config import load_config
from src.internal_modules.context import AppContext, app_lifespan
from src.internal_modules.memory import MemoryModule
from src.internal_modules.setup_logging import setup_logging
from src.internal_modules.spawner import Spawner
from src.networking.network import NetworkModule

config = {'node': 'Node0'}
BASE_DIR = Path(Path().resolve())
print(BASE_DIR)
# exit()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("fastapi").setLevel(logging.WARNING)


async def main():
    cfg_manager = load_config(
        base_path=BASE_DIR / 'config.yaml',
        local_path=BASE_DIR / 'config.local.yaml',
    )
    cfg = cfg_manager.cfg
    setup_logging(cfg.logging)

    ctx = AppContext(cfg)
    ctx.config_manager = cfg_manager  # доступен из любого модуля

    # порядок вызовов = порядок загрузки
    ctx.memory = ctx.register(MemoryModule(name='memory', context=ctx))
    ctx.network = ctx.register(NetworkModule(name='network',
                                             context=ctx,
                                             host=cfg.network.host,
                                             port=cfg.network.port, ))
    ctx.spawn = ctx.register(Spawner(name='spawner', context=ctx))

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
