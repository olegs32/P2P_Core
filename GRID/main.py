import asyncio
from context import AppContext, app_lifespan
from network import NetworkModule
from memory import MemoryModule
NODE = 'test1'

async def main():
    ctx = AppContext()
    ctx.network = NetworkModule(port=9000)
    ctx.memory = MemoryModule(NODE)

    # пробрасываем ctx в роуты FastAPI
    ctx.network.app.state.ctx = ctx

    async with app_lifespan(ctx):
        # всё поднято — основной цикл
        await asyncio.Event().wait()  # бесконечное ожидание

if __name__ == "__main__":
    asyncio.run(main())
