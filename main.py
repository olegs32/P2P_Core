import asyncio
import logging
import os
import sys
from pathlib import Path

import colorama

colorama.init()

# ВАЖНО: Ставим перехват на самый верх файла, ДО инициализации Click/Typer/Asyncio
# Проверяем, есть ли ключевые слова запуска Streamlit в аргументах
if "-m" in sys.argv and "streamlit" in sys.argv:
    import streamlit.web.cli as stcli

    # Находим, где именно в массиве стоят эти маркеры, и вырезаем их
    try:
        m_index = sys.argv.index("-m")
        # Вырезаем '-m' и следующий за ним 'streamlit'
        if m_index + 1 < len(sys.argv) and sys.argv[m_index + 1] == "streamlit":
            del sys.argv[m_index:m_index + 2]
    except ValueError:
        pass

    # На всякий случай выводим в консоль, что перехват сработал (для теста)
    print("[DEBUG] Streamlit-перехват сработал! Аргументы для cli:", sys.argv)

    # Запускаем Streamlit и намертво гасим процесс для Click/Typer
    sys.exit(stcli.main())

from services.loader import ServiceLoader
from src.internal_modules.config import load_config
from src.internal_modules.context import AppContext, app_lifespan
from src.internal_modules.memory import MemoryModule
from src.internal_modules.setup_logging import setup_logging
from src.internal_modules.spawner import Spawner
from src.networking.network import NetworkModule
from src.networking.node_connector import NodeConnector

BASE_DIR = Path(Path().resolve())

try:
    # PyInstaller creates a temp folder and stores path in _MEIPASS
    SERVICES_DIR = Path(sys._MEIPASS) / 'services'
except Exception as e:
    SERVICES_DIR = os.path.abspath("./services")
    print('Frozen services path not found:', e)

if not os.path.exists(SERVICES_DIR):
    os.makedirs(SERVICES_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("fastapi").setLevel(logging.WARNING)


async def main():
    cfg_manager = load_config(BASE_DIR / 'config.yaml')
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

    if os.path.exists(BASE_DIR / 'services'):
        # автозагрузка всех сервисов из ./services/ (live editing available)
        loader = ServiceLoader(
            services_path=BASE_DIR / 'services',
            context=ctx,
            services_manager=ctx.services,
        )
        loader.scan()
        loader.watch()  # hot reload local services

    # автозагрузка всех сервисов из MEI_/services/
    frozen_loader = ServiceLoader(
        services_path=SERVICES_DIR,
        context=ctx,
        services_manager=ctx.services,
    )
    frozen_loader.scan()

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
