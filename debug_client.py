# debug_client.py

import asyncio
import json
import time
import websockets

from src.networking.protocol import MsgPack, PackType

URI  = "ws://localhost:9000/ws/DebugClient"
BUFF = 3


# ------------------------------------------------------------------ #
#  Receive loop
# ------------------------------------------------------------------ #

async def receive_loop(websocket, received: dict, pipe: asyncio.Queue,
                       stream_info: dict):
    try:
        async for raw in websocket:
            data = json.loads(raw)
            pack = MsgPack(**data)
            label_short = pack.label[:8]

            match pack.type:
                case PackType.RESPONSE:
                    print(f"[RESPONSE]     label={label_short} data={pack.data}")

                case PackType.STREAM_OPEN:
                    stream_info['label'] = pack.label
                    stream_info['eof']   = False
                    print(f"\n[STREAM_OPEN]  label={label_short} "
                          f"service={pack.service}.{pack.method} "
                          f"wrapper_data={pack.data}")
                    ready = MsgPack(
                        type   = PackType.STREAM_READY,
                        source = 'DebugClient',
                        dst    = pack.source,
                        label  = pack.label,
                        data   = 'ready',
                    )
                    await websocket.send(ready.model_dump_json())
                    print(f"[STREAM_READY →] label={label_short}\n")

                case PackType.STREAM_CHUNK:
                    print(f"[CHUNK ←]      label={label_short} "
                          f"data={pack.data} pipe_queue={pipe.qsize()}")
                    await pipe.put(pack.data)

                case PackType.STREAM_EOF:
                    print(f"[EOF ←]        label={label_short}")
                    stream_info['eof'] = True
                    await pipe.put(None)  # sentinel

                case PackType.STREAM_READY:
                    print(f"[STREAM_READY] label={label_short}")

                case PackType.STREAM_ACK:
                    print(f"[ACK]          label={label_short}")

                case PackType.ERROR:
                    print(f"[ERROR]        label={label_short} error={pack.error}")

                case PackType.PONG:
                    print(f"[PONG]         label={label_short}")

                case _:
                    print(f"[UNKNOWN]      {pack.model_dump_json()}")

            slot = received.get(pack.label)
            if slot:
                slot['pack'] = pack
                slot['event'].set()

    except websockets.exceptions.ConnectionClosedOK:
        print("[INFO] Connection closed OK")
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"[INFO] Connection closed with error: {e}")
    except Exception as e:
        print(f"[RECEIVE ERROR] {e}")


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #

async def wait_for_label(received: dict, label: str,
                         timeout: int = 5) -> MsgPack | None:
    slot = {'event': asyncio.Event(), 'pack': None}
    received[label] = slot
    try:
        await asyncio.wait_for(slot['event'].wait(), timeout=timeout)
        return slot['pack']
    except asyncio.TimeoutError:
        print(f"[TIMEOUT] label={label[:8]}")
        return None
    finally:
        received.pop(label, None)


async def wait_for_stream_open(stream_info: dict, timeout: int = 5) -> bool:
    for _ in range(timeout * 10):
        if 'label' in stream_info:
            return True
        await asyncio.sleep(0.1)
    print("[ERROR] STREAM_OPEN не получен")
    return False


# ------------------------------------------------------------------ #
#  Slow consumer (для локального теста клиент ← Node0)
# ------------------------------------------------------------------ #

async def slow_consumer(pipe: asyncio.Queue, websocket,
                        stream_info: dict):
    label  = stream_info['label']
    index  = 0
    start  = time.time()

    print(f"[CONSUMER]     started, buff={BUFF} delay=0.1s/chunk\n")

    # первый запрос порции
    print(f"[ACK →]        initial request buff={BUFF}")
    await websocket.send(MsgPack(
        type   = PackType.STREAM_ACK,
        source = 'DebugClient',
        dst    = 'Node0',
        label  = label,
        data   = BUFF,
    ).model_dump_json())

    while True:
        chunk = await pipe.get()

        if chunk is None:
            elapsed = time.time() - start
            avg     = elapsed / index if index else 0
            print(f"\n{'='*50}")
            print(f"[CONSUMER DONE] chunks={index} "
                  f"elapsed={elapsed:.1f}s "
                  f"avg={avg:.2f}s/chunk")
            break

        index += 1
        queue_size = pipe.qsize()
        print(f"  [CONSUMING]  #{index} data={chunk} "
              f"pipe_queue={queue_size}")

        # prefetch — до вычисления, пока считаем летит запрос
        if queue_size < BUFF and not stream_info.get('eof', False):
            print(f"  [ACK →]      pipe_queue={queue_size} < buff={BUFF} — prefetch")
            await websocket.send(MsgPack(
                type   = PackType.STREAM_ACK,
                source = 'DebugClient',
                dst    = 'Node0',
                label  = label,
                data   = BUFF,
            ).model_dump_json())

        await asyncio.sleep(0.1)
        print(f"  [DONE]       #{index} result={chunk[0] * 2}")


# ------------------------------------------------------------------ #
#  Тесты
# ------------------------------------------------------------------ #

async def test_rpc(websocket, received: dict):
    """Обычный RPC вызов."""
    print("\n" + "="*50)
    print("TEST 1: RPC — compute.status")
    print("="*50)

    pack = MsgPack(
        source  = 'DebugClient',
        dst     = 'Node0',
        service = 'compute',
        method  = 'status',
    )
    await websocket.send(pack.model_dump_json())
    response = await wait_for_label(received, pack.label)
    if response:
        print(f"[RESULT] status={response.data}")


async def test_local_stream(websocket, received: dict,
                            pipe: asyncio.Queue, stream_info: dict):
    """
    Генератор на Node0, потребитель — этот клиент.
    Тестирует backpressure и prefetch.
    """
    print("\n" + "="*50)
    print("TEST 2: Generator(Node0) → DebugClient (backpressure)")
    print(f"         count=12 buff={BUFF} delay=0.1s/chunk")
    print("="*50)

    trigger = MsgPack(
        source  = 'DebugClient',
        dst     = 'Node0',
        service = 'compute',
        method  = 'start_stream',
        data    = {
            'target':     'DebugClient',
            'count':      12,
            'multiplier': 1,
            'buff':       BUFF,
        },
    )
    await websocket.send(trigger.model_dump_json())
    response = await wait_for_label(received, trigger.label, timeout=5)
    if response:
        print(f"[TRIGGER ACK]  {response.data}\n")

    if not await wait_for_stream_open(stream_info):
        return

    await slow_consumer(pipe, websocket, stream_info)


async def test_node_to_node(websocket, received: dict):
    """
    Генератор на Node0, потребитель — Node1.
    Клиент только триггерит, логи смотреть на Node0 и Node1.
    """
    print("\n" + "="*50)
    print("TEST 3: Generator(Node0) → Node1 (node-to-node)")
    print("         count=12 buff=3 delay=0.1s/chunk")
    print("         [Логи на Node0 и Node1]")
    print("="*50)

    trigger = MsgPack(
        source  = 'DebugClient',
        dst     = 'Node0',
        service = 'Spawner',
        method  = 'spawn',
        data    = {
            'target':     'Node1',
            'count':      120,
            'multiplier': 2,
            'buff':       3,
        },
    )
    await websocket.send(trigger.model_dump_json())
    response = await wait_for_label(received, trigger.label, timeout=5)
    if response:
        print(f"[TRIGGER ACK]  {response.data}")
    print("[INFO] Дальнейшие логи — на Node0 и Node1")


async def test_ping(websocket, received: dict):
    """Ping-pong."""
    print("\n" + "="*50)
    print("TEST 0: Ping")
    print("="*50)

    pack = MsgPack(
        type   = PackType.PING,
        source = 'DebugClient',
        dst    = 'Node0',
    )
    await websocket.send(pack.model_dump_json())
    response = await wait_for_label(received, pack.label, timeout=3)
    if response:
        print(f"[RESULT] pong received")


async def test_spawner(websocket, received: dict):
    print("\n" + "="*50)
    print("TEST 4: Spawner с GeneratorRegistry")
    print("="*50)

    # сначала посмотреть доступные генераторы
    list_pack = MsgPack(
        source  = 'DebugClient',
        dst     = 'Node0',
        service = 'spawner',
        method  = 'list_generators',
        data    = {'service': 'compute'},
    )
    await websocket.send(list_pack.model_dump_json())
    response = await wait_for_label(received, list_pack.label, timeout=3)
    if response:
        print(f"[GENERATORS]   {response.data}")

    # запустить spawn
    trigger = MsgPack(
        source  = 'DebugClient',
        dst     = 'Node0',
        service = 'spawner',
        method  = 'spawn',
        data    = {
            'generator_service': 'compute',       # где живёт генератор
            'generator':         'compute_ranges', # @generator метод
            'service':           'compute',        # сервис на Node1
            'method':            'run_range',      # stream_name на Node1
            'workers_count':     1,
            'buff':              3,
            'init_data':         {'multiplier': 2, 'buff': 3, 'count': 12},
        },
    )
    await websocket.send(trigger.model_dump_json())
    response = await wait_for_label(received, trigger.label, timeout=5)
    if response:
        print(f"[TRIGGER ACK]  {response.data}")
    print("[INFO] Логи на Node0 и Node1")
# ------------------------------------------------------------------ #
#  Main
# ------------------------------------------------------------------ #

async def main():
    received    = {}
    pipe        = asyncio.Queue()
    stream_info = {}

    try:
        async with websockets.connect(URI) as websocket:
            print(f"Connected to {URI}")

            recv_task = asyncio.create_task(
                receive_loop(websocket, received, pipe, stream_info)
            )

            # await test_ping(websocket, received)
            # await asyncio.sleep(0.3)
            #
            # await test_rpc(websocket, received)
            # await asyncio.sleep(0.3)
            #
            # await test_local_stream(websocket, received, pipe, stream_info)
            await asyncio.sleep(0.3)

            # await test_node_to_node(websocket, received)
            await test_spawner(websocket, received)

            # ждём завершения node-to-node стрима
            # (12 чанков × 0.1с + сетевые задержки)
            await asyncio.sleep(5)

            recv_task.cancel()

    except ConnectionRefusedError:
        print("Connection refused — сервер недоступен")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())