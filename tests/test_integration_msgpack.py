# tests/test_integration_msgpack.py
#
# РРЅС‚РµРіСЂР°С†РёРѕРЅРЅС‹Р№ С‚РµСЃС‚ wire-РїСЂРѕС‚РѕРєРѕР»Р°: РґРІР° СѓР·Р»Р° in-process РЅР° СЌС„РµРјРµСЂРЅС‹С… РїРѕСЂС‚Р°С….
# РЎС†РµРЅР°СЂРёР№: HELLO в†’ RPC СЃ binary data в†’ mesh-СЃС‚СЂРёРј 10 000 Р±Р°Р№С‚РѕРІС‹С… С‡Р°РЅРєРѕРІ
# СЃ ACK/backpressure в†’ GOSSIP в†’ CERT_SYNC в†’ PING/PONG в†’ Р»РѕРєР°Р»СЊРЅС‹Р№ shortcut.
# РћС‚РґРµР»СЊРЅРѕ: robustness (РјСѓСЃРѕСЂ/unknown type/text-РєР°РґСЂ Р»РµРіР°СЃРё).

import asyncio
import json
import logging
import uuid

import pytest
import websockets

from src.internal_modules.base import ModuleGeneric
from src.internal_modules.config import Config
from src.internal_modules.context import AppContext
from src.internal_modules.memory import MemoryModule, Pipe, _SENTINEL
from src.networking.neighbor_table import NeighborInfo, NeighborStatus
from src.networking.network import NetworkModule
from src.networking.node_connector import NodeConnector
from src.networking.protocol import (
    MsgPack,
    PackType,
    decode_pack,
    encode_pack,
)
from services.rpc import rpc, stream_wrapper, stream_consumer

logging.basicConfig(level=logging.WARNING)

CONNECT_TIMEOUT = 20
STREAM_CHUNKS = 10_000
CHUNK_SIZE = 128


# ------------------------------------------------------------------ #
#  РўРµСЃС‚РѕРІС‹Р№ СЃРµСЂРІРёСЃ: echo-RPC + РїРѕС‚СЂРµР±РёС‚РµР»СЊ Р±Р°Р№С‚РѕРІРѕРіРѕ СЃС‚СЂРёРјР°
# ------------------------------------------------------------------ #

class StreamCollector:
    def __init__(self):
        self.count = 0
        self.total_len = 0
        self.first_chunk = None
        self.last_chunk = None
        self.done = asyncio.Event()


class StreamEchoService(ModuleGeneric):
    """РЎРµСЂРІРёСЃ СЃ @rpc РјРµС‚РѕРґРѕРј Рё РїР°СЂРѕР№ wrapper/consumer РґР»СЏ РІС…РѕРґСЏС‰РµРіРѕ СЃС‚СЂРёРјР°."""

    def __init__(self, name: str, context, collector: StreamCollector | None = None):
        super().__init__(name, context)
        self.collector = collector

    @rpc
    async def echo(self, data):
        return {'echo': data}

    @rpc
    def ping_local(self, data):
        return 'local-shortcut-ok'

    @stream_wrapper('push_bytes')
    async def prepare_push(self, data):
        return {}

    @stream_consumer('push_bytes')
    async def consume_bytes(self, pipe: Pipe, ctx: dict):
        router = self.ctx.network.router
        label = ctx.get('label')
        buff = 10
        if label:
            await router.send_stream_ack(label, buff)

        # батчевый кумулятивный ACK: раз на buff чанков
        since_ack = 0

        async for chunk in pipe:
            c = self.collector
            c.count += 1
            c.total_len += len(chunk)
            if c.first_chunk is None:
                c.first_chunk = chunk
            c.last_chunk = chunk
            if label:
                since_ack += 1
                if since_ack >= buff:
                    await router.send_stream_ack(label, buff)
                    since_ack = 0

        self.collector.done.set()


def install_service(ctx: AppContext, svc: StreamEchoService):
    ctx.services.register_service(svc)
    for attr_name in dir(type(svc)):
        attr = getattr(type(svc), attr_name)
        if callable(attr) and getattr(attr, '_is_rpc', False):
            ctx.services.register_method(svc, attr_name, getattr(svc, attr_name))


# ------------------------------------------------------------------ #
#  РРЅС„СЂР°СЃС‚СЂСѓРєС‚СѓСЂР°: РґРІР° СѓР·Р»Р° in-process
# ------------------------------------------------------------------ #

async def make_node(node_id: str, collector: StreamCollector | None = None):
    ctx = AppContext(config=Config(node=node_id))
    net = NetworkModule('Network', ctx, host='127.0.0.1', port=0)
    ctx.network = net
    svc = StreamEchoService('testsvc', ctx, collector)
    install_service(ctx, svc)
    await net.start()

    # Р¶РґР°С‚СЊ РїРѕРґРЅСЏС‚РёСЏ uvicorn Рё СѓР·РЅР°С‚СЊ С„Р°РєС‚РёС‡РµСЃРєРёР№ РїРѕСЂС‚ (port=0)
    deadline = asyncio.get_event_loop().time() + CONNECT_TIMEOUT
    while not net._server.started:
        if asyncio.get_event_loop().time() > deadline:
            raise RuntimeError(f'{node_id}: uvicorn did not start')
        await asyncio.sleep(0.05)
    port = net._server.servers[0].sockets[0].getsockname()[1]
    return ctx, net, port


async def connect_nodes(net_dialer, port_listener) -> NodeConnector:
    """РСЃС…РѕРґСЏС‰РµРµ РїРѕРґРєР»СЋС‡РµРЅРёРµ NodeB в†’ NodeA С‡РµСЂРµР· РЅР°СЃС‚РѕСЏС‰РёР№ NodeConnector."""
    connector = NodeConnector(
        name=f'Connector_{net_dialer.ctx.NODE}',
        context=net_dialer.ctx,
        peer_node_id=LISTENER_ID,
        target_uri=f'ws://127.0.0.1:{port_listener}/ws/{net_dialer.ctx.NODE}',
    )
    await connector.start()
    return connector


LISTENER_ID = 'NodeA'
DIALER_ID = 'NodeB'


async def wait_connected(net_a, net_b, timeout=CONNECT_TIMEOUT):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        info = net_a.neighbor_table.get(DIALER_ID)
        if (info and info.status == NeighborStatus.CONNECTED
                and net_a.router.get_transport_to(DIALER_ID)):
            info_b = net_b.neighbor_table.get(LISTENER_ID)
            if info_b and info_b.status == NeighborStatus.CONNECTED:
                return
        await asyncio.sleep(0.1)
    raise TimeoutError('nodes did not connect')


# ------------------------------------------------------------------ #
#  РћСЃРЅРѕРІРЅРѕР№ СЃС†РµРЅР°СЂРёР№
# ------------------------------------------------------------------ #

async def _full_scenario():
    collector = StreamCollector()
    ctx_a, net_a, port_a = await make_node(LISTENER_ID, collector)
    ctx_b, net_b, _ = await make_node(DIALER_ID)
    mem_b = MemoryModule('Memory', ctx_b)
    connector = None
    try:
        # --- HELLO/handshake С‡РµСЂРµР· NodeConnector --- #
        connector = await connect_nodes(net_b, port_a)
        await wait_connected(net_a, net_b)

        info = net_a.neighbor_table.get(DIALER_ID)
        assert info.version == '2.0', f'peer version: {info.version}'
        assert 'testsvc' in info.services

        # --- RPC СЃ bytes РІ data (РіР»Р°РІРЅР°СЏ РјРѕС‚РёРІР°С†РёСЏ РјРёРіСЂР°С†РёРё) --- #
        payload = bytes(range(256)) * 4
        result = await net_b.call(dst=LISTENER_ID, service='testsvc',
                                  method='echo', data=payload)
        assert result == {'echo': payload}
        assert isinstance(result['echo'], bytes)

        # --- mesh-СЃС‚СЂРёРј: 10 000 Р±Р°Р№С‚РѕРІС‹С… С‡Р°РЅРєРѕРІ СЃ ACK/backpressure --- #
        pipe = mem_b.create_pipe(buff=10)
        label = str(uuid.uuid4())
        template = MsgPack(
            source=DIALER_ID,
            dst=LISTENER_ID,
            service='testsvc',
            method='push_bytes',
            label=label,
            data={'chunks': STREAM_CHUNKS},
        )
        mem_b.attach_transport(pipe, template, net_b.router)

        for i in range(STREAM_CHUNKS):
            await pipe.put(bytes([i % 251]) * CHUNK_SIZE)
        await pipe.put(_SENTINEL)
        pipe.close()

        await asyncio.wait_for(collector.done.wait(), timeout=120)
        assert collector.count == STREAM_CHUNKS, collector.count
        assert collector.total_len == STREAM_CHUNKS * CHUNK_SIZE
        assert collector.first_chunk == bytes([0]) * CHUNK_SIZE
        assert collector.last_chunk == bytes([(STREAM_CHUNKS - 1) % 251]) * CHUNK_SIZE

        # --- GOSSIP: NodeA СЃРѕРѕР±С‰Р°РµС‚ Рѕ РїСЂРёР·СЂР°С‡РЅРѕРј СЃРѕСЃРµРґРµ --- #
        ghost = NeighborInfo(node_id='Ghost', host='10.0.0.9', port=9000).model_dump()
        transport_a = net_a.router.get_transport_to(DIALER_ID)
        await transport_a.send(MsgPack(
            type=PackType.GOSSIP,
            source=LISTENER_ID,
            data={'neighbors': [ghost], 'from': LISTENER_ID},
        ))
        deadline = asyncio.get_event_loop().time() + 5
        while asyncio.get_event_loop().time() < deadline:
            g = net_b.neighbor_table.get('Ghost')
            if g and g.status == NeighborStatus.KNOWN and g.via == LISTENER_ID:
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError('GOSSIP merge failed')

        # --- CERT_SYNC: digest РѕС‚ NodeB РґРѕРµР·Р¶Р°РµС‚ РґРѕ NodeA --- #
        transport_b = net_b.router.get_transport_to(LISTENER_ID)
        await transport_b.send(MsgPack(
            type=PackType.CERT_SYNC,
            source=DIALER_ID,
            data={'certs': [{'thumbprint': 'TP01', 'subject_cn': 'it.test',
                             'valid_to': '2040-01-01'}],
                  'sync_version': 3},
        ))
        deadline = asyncio.get_event_loop().time() + 5
        while asyncio.get_event_loop().time() < deadline:
            entry = ctx_a.certs_index._entries.get('TP01')
            if entry and DIALER_ID in entry.available_on and entry.sync_version == 3:
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError('CERT_SYNC merge failed')

        # --- PING/PONG С‡РµСЂРµР· mesh --- #
        lbl = str(uuid.uuid4())
        fut = net_b.router.sessions.register_single(lbl, '', '')
        await transport_b.send(MsgPack(type=PackType.PING, source=DIALER_ID,
                                       dst=LISTENER_ID, label=lbl))
        pong = await asyncio.wait_for(fut, timeout=5)
        assert pong == 'pong'

        # --- Р»РѕРєР°Р»СЊРЅС‹Р№ shortcut: dst=self РёРґС‘С‚ РјРёРјРѕ РєРѕРґРёСЂРѕРІР°РЅРёСЏ --- #
        res = await net_b.call(dst=DIALER_ID, service='testsvc',
                               method='ping_local', data=None)
        assert res == 'local-shortcut-ok'

    finally:
        if connector:
            await connector.stop()
        await net_a.stop()
        await net_b.stop()


def test_full_mesh_scenario_msgpack():
    asyncio.run(_full_scenario())


# ------------------------------------------------------------------ #
#  Robustness
# ------------------------------------------------------------------ #

async def _garbage_frame_kills_connection_only():
    ctx_a, net_a, port_a = await make_node(LISTENER_ID)
    try:
        # Р±РёС‚С‹Р№ binary-РєР°РґСЂ РїРµСЂРІС‹Рј РїР°РєРµС‚РѕРј в†’ С‡РёСЃС‚РѕРµ Р·Р°РєСЂС‹С‚РёРµ 1002
        ws = await websockets.connect(f'ws://127.0.0.1:{port_a}/ws/Intruder',
                                      max_size=32 * 1024 * 1024)
        await ws.send(b'\xde\xad\xbe\xef' * 16)
        try:
            await asyncio.wait_for(ws.recv(), timeout=5)
            raise AssertionError('expected ConnectionClosed')
        except websockets.exceptions.ConnectionClosed as e:
            assert e.rcvd is not None and e.rcvd.code == 1002, e.rcvd
        finally:
            await ws.close()

        # РјСѓСЃРѕСЂ РџРћРЎР›Р• СѓСЃРїРµС€РЅРѕРіРѕ HELLO в†’ С‚РѕР¶Рµ Р·Р°РєСЂС‹С‚РёРµ, СѓР·РµР» Р¶РёРІС‘С‚
        ws = await websockets.connect(f'ws://127.0.0.1:{port_a}/ws/{DIALER_ID}',
                                      max_size=32 * 1024 * 1024)
        hello = MsgPack(type=PackType.HELLO, source=DIALER_ID, dst=LISTENER_ID,
                        data={'node_id': DIALER_ID})
        await ws.send(encode_pack(hello))
        ack = decode_pack(await asyncio.wait_for(ws.recv(), timeout=5))
        assert ack.type == PackType.HELLO_ACK

        await ws.send(b'not-a-msgpack-frame')
        try:
            await asyncio.wait_for(ws.recv(), timeout=5)
            raise AssertionError('expected ConnectionClosed')
        except websockets.exceptions.ConnectionClosed as e:
            assert e.rcvd is not None and e.rcvd.code == 1002, e.rcvd
        finally:
            await ws.close()

        # СѓР·РµР» Р¶РёРІ Рё РїСЂРёРЅРёРјР°РµС‚ РЅРѕРІС‹С… РєР»РёРµРЅС‚РѕРІ
        ws = await websockets.connect(f'ws://127.0.0.1:{port_a}/ws/NewGuy',
                                      max_size=32 * 1024 * 1024)
        hello = MsgPack(type=PackType.HELLO, source='NewGuy', dst=LISTENER_ID,
                        data={'node_id': 'NewGuy'})
        await ws.send(encode_pack(hello))
        ack = decode_pack(await asyncio.wait_for(ws.recv(), timeout=5))
        assert ack.type == PackType.HELLO_ACK
        await ws.close()
    finally:
        await net_a.stop()


def test_garbage_frame_closes_cleanly_node_survives():
    asyncio.run(_garbage_frame_kills_connection_only())


async def _unknown_type_dropped_connection_alive():
    ctx_a, net_a, port_a = await make_node(LISTENER_ID)
    try:
        ws = await websockets.connect(f'ws://127.0.0.1:{port_a}/ws/{DIALER_ID}',
                                      max_size=32 * 1024 * 1024)
        import msgpack
        hello = MsgPack(type=PackType.HELLO, source=DIALER_ID, dst=LISTENER_ID,
                        data={'node_id': DIALER_ID})
        await ws.send(encode_pack(hello))
        ack = decode_pack(await asyncio.wait_for(ws.recv(), timeout=5))
        assert ack.type == PackType.HELLO_ACK

        # РІР°Р»РёРґРЅС‹Р№ msgpack СЃ РЅРµРѕРїРѕР·РЅР°РЅРЅС‹Рј type вЂ” РїР°РєРµС‚ РґСЂРѕРїР°РµС‚СЃСЏ,
        # СЃРѕРµРґРёРЅРµРЅРёРµ РќР• СЂРІС‘С‚СЃСЏ (forward-compat)
        d = hello.model_dump()
        d['type'] = 'future_packet_kind'
        d['data'] = {'whatever': 1}
        await ws.send(msgpack.packb(d, use_bin_type=True))

        lbl = str(uuid.uuid4())
        await ws.send(encode_pack(MsgPack(
            type=PackType.PING, source=DIALER_ID, dst=LISTENER_ID, label=lbl)))
        while True:
            pack = decode_pack(await asyncio.wait_for(ws.recv(), timeout=5))
            if pack.type == PackType.PONG:
                break
        await ws.close()
    finally:
        await net_a.stop()


def test_unknown_type_dropped_connection_alive():
    asyncio.run(_unknown_type_dropped_connection_alive())


async def _legacy_json_hello_rejected():
    ctx_a, net_a, port_a = await make_node(LISTENER_ID)
    try:
        ws = await websockets.connect(f'ws://127.0.0.1:{port_a}/ws/OldNode')
        legacy_hello = {
            'type': 'hello', 'source': 'OldNode', 'dst': LISTENER_ID,
            'data': {'node_id': 'OldNode'}, 'label': str(uuid.uuid4()),
        }
        await ws.send(json.dumps(legacy_hello))          # TEXT-РєР°РґСЂ
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        assert isinstance(raw, bytes)                    # РѕС‚РІРµС‚ вЂ” binary msgpack
        pack = decode_pack(raw)
        assert pack.type == PackType.HELLO_REJECT
        assert 'upgrade' in (pack.data or {}).get('reason', '')

        # СЃРµСЂРІРµСЂ Р·Р°РєСЂР»СЏРµС‚ СЃРѕРµРґРёРЅРµРЅРёРµ РїРѕСЃР»Рµ reject
        try:
            await asyncio.wait_for(ws.recv(), timeout=5)
            raise AssertionError('expected ConnectionClosed')
        except websockets.exceptions.ConnectionClosed:
            pass
    finally:
        await net_a.stop()


def test_legacy_json_hello_rejected_with_upgrade_required():
    asyncio.run(_legacy_json_hello_rejected())

