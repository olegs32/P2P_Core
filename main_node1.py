import asyncio

from main import BASE_DIR
from src.internal_modules.config import load_config
from src.internal_modules.context import AppContext, app_lifespan
from src.internal_modules.setup_logging import setup_logging
from src.networking.network import NetworkModule
from src.internal_modules.memory import MemoryModule
from src.networking.node_connector import NodeConnector
from services.compute_full.service import Compute

setup_logging()

config = {'node': 'Node1'}

async def main():
    cfg_manager = load_config(
        base_path=BASE_DIR / 'config1.yaml',
        local_path=BASE_DIR / 'config1.local.yaml',
    )
    cfg = cfg_manager.cfg

    ctx = AppContext(cfg)
    ctx.config_manager = cfg_manager  # доступен из любого модуля


    ctx.memory  = ctx.register(MemoryModule(name='Memory', context=ctx))
    ctx.network = ctx.register(NetworkModule(name='Network', context=ctx, port=9001))

    # подключение к Node0
    connector = ctx.register(NodeConnector(
        name            = 'NodeConnector',
        context         = ctx,
        peer_node_id    = 'Node0',
        target_uri      = 'ws://localhost:9000/ws/Node1',  # регистрируемся как Node1
    ))

    # connector = ctx.register(NodeConnector(
    #     name=f'Connector_{peer.node_id}',
    #     context=ctx,
    #     peer_node_id=peer.node_id,
    #     target_uri=f'{peer.uri}{ctx.NODE}',
    # ))

    ctx.network.app.state.ctx = ctx

    # регистрация сервиса — только consumer часть нужна Node1
    compute = Compute('compute', ctx)
    ctx.services.register_service(compute)
    ctx.services.register_method(compute, 'start_stream', compute.start_stream)

    async with app_lifespan(ctx):
        await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass