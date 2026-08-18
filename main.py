import asyncio
import logging
import os
import sys
from pathlib import Path

import colorama
colorama.init()

# --- Frozen exe: streamlit subprocess mode ---
# When the exe is launched with --streamlit <app_path>, run streamlit
# directly instead of the full server. Used by WebPanel in frozen builds.
if '--streamlit' in sys.argv:
    idx = sys.argv.index('--streamlit')
    _app_path = sys.argv[idx + 1]
    _port = sys.argv[idx + 2] if idx + 2 < len(sys.argv) else '8501'

    # В frozen exe файл лежит в _MEIPASS (благодаря --add-data)
    if getattr(sys, 'frozen', False):
        _meipass = Path(getattr(sys, '_MEIPASS', ''))
        _app_path = str(_meipass / 'services' / 'webpanel' / '_streamlit_app.py')

    from streamlit.web import cli as stcli
    sys.argv = ['streamlit', 'run', _app_path,
                '--server.port', _port,
                '--server.headless', 'true',
                '--global.developmentMode', 'false',
                '--browser.gatherUsageStats', 'false']
    try:
        stcli.main_run()
    except SystemExit:
        pass
    sys.exit(0)

from services.loader import ServiceLoader
from src.internal_modules.config import load_config
from src.internal_modules.context import AppContext, app_lifespan
from src.internal_modules.memory import MemoryModule
from src.internal_modules.setup_logging import setup_logging
from src.internal_modules.spawner import Spawner
from src.networking.network import NetworkModule
from src.networking.node_connector import NodeConnector

BASE_DIR = Path(Path().resolve())

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
    ctx.config_manager = cfg_manager

    # порядок вызовов = порядок загрузки
    ctx.memory = ctx.register(MemoryModule(name='memory', context=ctx))
    ctx.network = ctx.register(NetworkModule(name='network',
                                             context=ctx,
                                             host=cfg.network.host,
                                             port=cfg.network.port, ))

    # Spawner — не в services/, регистрируем вручную
    ctx.spawn = ctx.register(Spawner(name='spawner', context=ctx))
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

    for peer in cfg.local.peers:
        connector = ctx.register(NodeConnector(
            name=f'Connector_{peer.node_id}',
            context=ctx,
            peer_node_id=peer.node_id,
            target_uri=f'{peer.uri}{ctx.NODE}',
        ))

    async with app_lifespan(ctx):
        # всё поднято — основной цикл
        await asyncio.Event().wait()  # бесконечное ожидание


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
