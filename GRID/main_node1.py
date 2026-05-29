import asyncio
import logging
from GRID.context import AppContext, app_lifespan
from GRID.core_logging import setup_logging
from GRID.network import NetworkModule
from GRID.memory import MemoryModule
from GRID.node_connector import NodeConnector
from GRID.services.compute_full.service import Compute

setup_logging()

config = {'node': 'Node1'}

async def main():
    ctx = AppContext(config)

    ctx.memory  = ctx.register(MemoryModule(name='Memory', context=ctx))
    ctx.network = ctx.register(NetworkModule(name='Network', context=ctx, port=9001))

    # подключение к Node0
    connector = ctx.register(NodeConnector(
        name       = 'NodeConnector',
        context    = ctx,
        target_uri = 'ws://localhost:9000/ws/Node1',  # регистрируемся как Node1
    ))

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