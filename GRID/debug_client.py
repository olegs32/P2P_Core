import time

import websockets, asyncio


async def forward(message):
    url = 'ws://localhost:9000/ws/1'
    async with websockets.connect(url) as websocket:
        time.sleep(2)
        await websocket.send(message)
        print(await websocket.recv())


def xmit_Loop(message):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(forward(message))

xmit_Loop('123')