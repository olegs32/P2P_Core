# debug_client.py — полная версия с HELLO и тестами сети

import asyncio
import json
import time
import uuid
import websockets

from src.networking.protocol import MsgPack, PackType
from src.networking.neighbor_table import PROTOCOL_VERSION

URI      = "ws://localhost:9000/ws/DebugClient"
BUFF     = 3
OWN_NODE = "DebugClient"
DST_NODE = "Node0"


# ------------------------------------------------------------------ #
#  Handshake
# ------------------------------------------------------------------ #

async def do_handshake(websocket) -> bool:
    """Отправить HELLO, получить HELLO_ACK или HELLO_REJECT."""
    hello = MsgPack(
        type   = PackType.HELLO,
        source = OWN_NODE,
        dst    = DST_NODE,
        data   = {
            'node_id':    OWN_NODE,
            'host':       'localhost',
            'port':       0,            # debug client — порта нет
            'version':    PROTOCOL_VERSION,
            'session_id': str(uuid.uuid4()),
            'services':   [],           # debug client сервисов не имеет
        }
    )
    print(f"[HELLO →]      node_id={OWN_NODE} version={PROTOCOL_VERSION}")
    await websocket.send(hello.model_dump_json())

    try:
        raw  = await asyncio.wait_for(websocket.recv(), timeout=5)
        pack = MsgPack(**json.loads(raw))

        if pack.type == PackType.HELLO_ACK:
            data      = pack.data or {}
            neighbors = data.get('neighbors', [])
            services  = data.get('services', [])
            session   = data.get('session_id', '')[:8]
            print(f"[HELLO_ACK ←]  session={session} "
                  f"neighbors={len(neighbors)} "
                  f"services={services}")
            if neighbors:
                print("[NEIGHBORS]    от сервера:")
                for n in neighbors:
                    print(f"               {n.get('node_id')} "
                          f"{n.get('host')}:{n.get('port')} "
                          f"status={n.get('status')} "
                          f"via={n.get('via')}")
            return True

        elif pack.type == PackType.HELLO_REJECT:
            reason = (pack.data or {}).get('reason', 'unknown')
            print(f"[HELLO_REJECT ←] reason={reason}")
            return False

    except asyncio.TimeoutError:
        print("[ERROR] HELLO timeout")
    return False


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

                case PackType.GOSSIP:
                    neighbors = (pack.data or {}).get('neighbors', [])
                    from_node = (pack.data or {}).get('from', pack.source)
                    print(f"\n[GOSSIP ←]     from={from_node} "
                          f"neighbors={len(neighbors)}")
                    for n in neighbors:
                        print(f"               {n.get('node_id')} "
                              f"{n.get('host')}:{n.get('port')} "
                              f"status={n.get('status')} "
                              f"via={n.get('via')}")

                case PackType.ANNOUNCE:
                    services  = (pack.data or {}).get('services', [])
                    from_node = (pack.data or {}).get('from', pack.source)
                    print(f"\n[ANNOUNCE ←]   from={from_node} services={services}")

                case PackType.PING:
                    print(f"[PING ←]       label={label_short} — sending PONG")
                    pong = MsgPack(
                        type   = PackType.PONG,
                        source = OWN_NODE,
                        dst    = pack.source,
                        label  = pack.label,
                    )
                    await websocket.send(pong.model_dump_json())

                case PackType.PONG:
                    print(f"[PONG ←]       label={label_short}")

                case PackType.STREAM_OPEN:
                    stream_info['label'] = pack.label
                    stream_info['eof']   = False
                    print(f"\n[STREAM_OPEN]  label={label_short} "
                          f"service={pack.service}.{pack.method}")
                    await websocket.send(MsgPack(
                        type   = PackType.STREAM_READY,
                        source = OWN_NODE,
                        dst    = pack.source,
                        label  = pack.label,
                        data   = 'ready',
                    ).model_dump_json())
                    print(f"[STREAM_READY →] label={label_short}")

                case PackType.STREAM_CHUNK:
                    print(f"[CHUNK ←]      label={label_short} "
                          f"data={pack.data} pipe_queue={pipe.qsize()}")
                    await pipe.put(pack.data)

                case PackType.STREAM_EOF:
                    print(f"[EOF ←]        label={label_short}")
                    stream_info['eof'] = True
                    await pipe.put(None)

                case PackType.STREAM_READY:
                    print(f"[STREAM_READY ←] label={label_short}")

                case PackType.ERROR:
                    print(f"[ERROR]        label={label_short} error={pack.error}")

                case _:
                    print(f"[UNKNOWN]      type={pack.type} label={label_short}")

            slot = received.get(pack.label)
            if slot:
                slot['pack'] = pack
                slot['event'].set()

    except websockets.exceptions.ConnectionClosedOK:
        print("[INFO] Connection closed OK")
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"[INFO] Connection closed: {e}")
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
        print(f"[TIMEOUT]      label={label[:8]}")
        return None
    finally:
        received.pop(label, None)


async def rpc(websocket, received, service, method, data=None,
              dst=DST_NODE) -> dict | None:
    pack = MsgPack(
        source  = OWN_NODE,
        dst     = dst,
        service = service,
        method  = method,
        data    = data,
    )
    await websocket.send(pack.model_dump_json())
    response = await wait_for_label(received, pack.label)
    return response.data if response else None


async def wait_for_stream_open(stream_info: dict, timeout: int = 5) -> bool:
    for _ in range(timeout * 10):
        if 'label' in stream_info:
            return True
        await asyncio.sleep(0.1)
    print("[ERROR] STREAM_OPEN не получен")
    return False


# ------------------------------------------------------------------ #
#  Slow consumer (для локального stream теста)
# ------------------------------------------------------------------ #

async def slow_consumer(pipe: asyncio.Queue, websocket, stream_info: dict):
    label = stream_info['label']
    index = 0
    start = time.time()

    print(f"[CONSUMER]     started buff={BUFF} delay=0.1s/chunk\n")
    await websocket.send(MsgPack(
        type   = PackType.STREAM_ACK,
        source = OWN_NODE,
        dst    = DST_NODE,
        label  = label,
        data   = BUFF,
    ).model_dump_json())

    while True:
        chunk = await pipe.get()
        if chunk is None:
            elapsed = time.time() - start
            print(f"\n[CONSUMER DONE] chunks={index} elapsed={elapsed:.1f}s")
            break

        index += 1
        queue_size = pipe.qsize()

        if queue_size < BUFF and not stream_info.get('eof', False):
            await websocket.send(MsgPack(
                type   = PackType.STREAM_ACK,
                source = OWN_NODE,
                dst    = DST_NODE,
                label  = label,
                data   = BUFF,
            ).model_dump_json())

        await asyncio.sleep(0.1)
        print(f"  [DONE]       #{index} result={chunk[0] * 2}")


# ------------------------------------------------------------------ #
#  Тесты
# ------------------------------------------------------------------ #

async def test_ping(websocket, received):
    print("\n" + "="*50)
    print("TEST 0: Ping")
    print("="*50)
    pack = MsgPack(type=PackType.PING, source=OWN_NODE, dst=DST_NODE)
    await websocket.send(pack.model_dump_json())
    r = await wait_for_label(received, pack.label, timeout=3)
    print(f"[RESULT]       {'pong ok' if r else 'timeout'}")


async def test_neighbors(websocket, received):
    print("\n" + "="*50)
    print("TEST 1: NeighborTable на Node0")
    print("="*50)
    result = await rpc(websocket, received, 'netinfo', 'neighbors')
    if result:
        print(f"[OWN]          {result.get('own')}")
        print(f"[CONNECTED]    {len(result.get('connected', []))} nodes:")
        for n in result.get('connected', []):
            print(f"               {n['node_id']} {n['host']}:{n['port']} "
                  f"session={str(n.get('session_id',''))[:8]}")
        print(f"[KNOWN]        {len(result.get('known', []))} nodes:")
        for n in result.get('known', []):
            print(f"               {n['node_id']} via={n.get('via')} "
                  f"services={n.get('services')}")


async def test_active_nodes(websocket, received):
    print("\n" + "="*50)
    print("TEST 2: NodesManager (активные WS)")
    print("="*50)
    result = await rpc(websocket, received, 'netinfo', 'nodes')
    if result:
        print(f"[NODES]        {list(result.keys())}")


async def test_services(websocket, received):
    print("\n" + "="*50)
    print("TEST 3: Сервисы на Node0")
    print("="*50)
    result = await rpc(websocket, received, 'netinfo', 'services')
    if result:
        print(f"[SERVICES]     {result}")


async def test_find_service(websocket, received):
    print("\n" + "="*50)
    print("TEST 4: Найти ноды с сервисом 'compute'")
    print("="*50)
    result = await rpc(websocket, received, 'netinfo', 'find_service',
                       {'service': 'compute'})
    if result is not None:
        print(f"[FOUND]        {len(result)} nodes with 'compute':")
        for n in result:
            print(f"               {n['node_id']} status={n['status']}")


async def test_duplicate_connection():
    """
    Попытаться подключиться с тем же node_id — должен получить REJECT.
    Запускается отдельным соединением.
    """
    print("\n" + "="*50)
    print("TEST 5: Дублирующее подключение (ожидаем REJECT)")
    print("="*50)
    try:
        async with websockets.connect(URI) as ws2:
            hello = MsgPack(
                type   = PackType.HELLO,
                source = OWN_NODE,   # тот же node_id
                dst    = DST_NODE,
                data   = {
                    'node_id':    OWN_NODE,
                    'host':       'localhost',
                    'port':       0,
                    'version':    PROTOCOL_VERSION,
                    'session_id': str(uuid.uuid4()),
                    'services':   [],
                }
            )
            await ws2.send(hello.model_dump_json())
            raw  = await asyncio.wait_for(ws2.recv(), timeout=5)
            pack = MsgPack(**json.loads(raw))
            if pack.type == PackType.HELLO_REJECT:
                print(f"[REJECT ←]     reason={pack.data.get('reason')} ✓")
            else:
                print(f"[UNEXPECTED]   type={pack.type}")
    except Exception as e:
        print(f"[ERROR]        {e}")


async def test_local_stream(websocket, received, pipe, stream_info):
    print("\n" + "="*50)
    print("TEST 6: Generator(Node0) → DebugClient (backpressure)")
    print(f"         count=9 buff={BUFF} delay=0.1s/chunk")
    print("="*50)
    result = await rpc(websocket, received, 'compute', 'start_stream', {
        'target':     OWN_NODE,
        'count':      9,
        'multiplier': 1,
        'buff':       BUFF,
    })
    if result:
        print(f"[TRIGGER ACK]  {result}\n")

    if not await wait_for_stream_open(stream_info):
        return
    await slow_consumer(pipe, websocket, stream_info)


async def test_node_to_node(websocket, received):
    print("\n" + "="*50)
    print("TEST 7: Generator(Node0) → Node1")
    print("         [Логи на Node0 и Node1]")
    print("="*50)
    result = await rpc(websocket, received, 'compute', 'start_stream', {
        'target':     'Node1',
        'count':      9,
        'multiplier': 2,
        'buff':       3,
    })
    if result:
        print(f"[TRIGGER ACK]  {result}")


# ------------------------------------------------------------------ #
#  Main
# ------------------------------------------------------------------ #

async def main():
    received    = {}
    pipe        = asyncio.Queue()
    stream_info = {}

    print(f"Connecting to {URI}...")

    try:
        async with websockets.connect(URI) as websocket:

            # handshake первым делом
            accepted = await do_handshake(websocket)
            if not accepted:
                print("[FATAL] Handshake failed — exit")
                return

            print(f"[INFO]         Connected as {OWN_NODE}\n")

            recv_task = asyncio.create_task(
                receive_loop(websocket, received, pipe, stream_info)
            )

            await test_ping(websocket, received)
            await asyncio.sleep(0.3)

            await test_neighbors(websocket, received)
            await asyncio.sleep(0.3)

            await test_active_nodes(websocket, received)
            await asyncio.sleep(0.3)

            await test_services(websocket, received)
            await asyncio.sleep(0.3)

            await test_find_service(websocket, received)
            await asyncio.sleep(0.3)

            # тест дубля — отдельное соединение
            await test_duplicate_connection()
            await asyncio.sleep(0.3)

            await test_local_stream(websocket, received, pipe, stream_info)
            await asyncio.sleep(0.3)

            await test_node_to_node(websocket, received)
            await asyncio.sleep(5)

            recv_task.cancel()

    except ConnectionRefusedError:
        print("Connection refused — сервер недоступен")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())