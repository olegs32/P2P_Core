# tests/test_multihop_routing.py
#
# Регрессионный тест B1 (docs/analyze.md): обратная маршрутизация на цепочке
# из 3 узлов. Топология: NodeA ← NodeB ← NodeC (B dial A, C dial B), прямого
# линка A—C нет. RESPONSE/ERROR обязаны вернуться по path=[origin,…,responder]:
# каждый хоп выталкивает себя с хвоста (_route_back). До фикса ответ строился
# как reversed(path) → у responder'а следующий хоп вычислялся как origin,
# до которого транспорта нет → "return path broken", RPC по таймауту.

import asyncio
import logging

from src.internal_modules.base import ModuleGeneric
from src.internal_modules.config import Config
from src.internal_modules.context import AppContext
from src.networking.neighbor_table import NeighborStatus
from src.networking.network import NetworkModule
from src.networking.node_connector import NodeConnector
from src.networking.protocol import MsgPack, PackType
from services.rpc import rpc

logging.basicConfig(level=logging.WARNING)

CONNECT_TIMEOUT = 20

NODE_A = 'NodeA'
NODE_B = 'NodeB'
NODE_C = 'NodeC'


class EchoService(ModuleGeneric):
    """echo-RPC для проверки возврата RESPONSE/ERROR через промежуточный хоп."""

    @rpc
    async def echo(self, data):
        return {'echo': data}

    @rpc
    async def boom(self, data):
        raise RuntimeError('intentional failure')


def install_service(ctx: AppContext):
    svc = EchoService('testsvc', ctx)
    ctx.services.register_service(svc)
    for attr_name in dir(type(svc)):
        attr = getattr(type(svc), attr_name)
        if callable(attr) and getattr(attr, '_is_rpc', False):
            ctx.services.register_method(svc, attr_name, getattr(svc, attr_name))


async def make_node(node_id: str):
    ctx = AppContext(config=Config(node=node_id))
    net = NetworkModule('Network', ctx, host='127.0.0.1', port=0)
    ctx.network = net
    install_service(ctx)
    await net.start()

    deadline = asyncio.get_event_loop().time() + CONNECT_TIMEOUT
    while not net._server.started:
        if asyncio.get_event_loop().time() > deadline:
            raise RuntimeError(f'{node_id}: uvicorn did not start')
        await asyncio.sleep(0.05)
    port = net._server.servers[0].sockets[0].getsockname()[1]
    return ctx, net, port


async def wait_connected(net: NetworkModule, peer_id: str,
                         timeout: int = CONNECT_TIMEOUT):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        info = net.neighbor_table.get(peer_id)
        if (info and info.status == NeighborStatus.CONNECTED
                and net.router.get_transport_to(peer_id)):
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f'{net.ctx.NODE}: no connection to {peer_id}')


async def _multihop_scenario():
    ctx_a, net_a, port_a = await make_node(NODE_A)
    ctx_b, net_b, port_b = await make_node(NODE_B)
    ctx_c, net_c, _ = await make_node(NODE_C)
    connectors = []
    try:
        # B → A
        connectors.append(NodeConnector(
            name='Connector_B', context=ctx_b, peer_node_id=NODE_A,
            target_uri=f'ws://127.0.0.1:{port_a}/ws/{NODE_B}'))
        await connectors[-1].start()
        await wait_connected(net_a, NODE_B)
        await wait_connected(net_b, NODE_A)

        # C → B
        connectors.append(NodeConnector(
            name='Connector_C', context=ctx_c, peer_node_id=NODE_B,
            target_uri=f'ws://127.0.0.1:{port_b}/ws/{NODE_C}'))
        await connectors[-1].start()
        await wait_connected(net_b, NODE_C)
        await wait_connected(net_c, NODE_B)

        # A узнаёт о C через GOSSIP от B: C становится KNOWN via=B
        neighbors = net_b.neighbor_table.to_gossip()
        transport_ba = net_b.router.get_transport_to(NODE_A)
        await transport_ba.send(MsgPack(
            type=PackType.GOSSIP,
            source=NODE_B,
            data={'neighbors': neighbors, 'from': NODE_B},
        ))
        deadline = asyncio.get_event_loop().time() + 5
        while asyncio.get_event_loop().time() < deadline:
            info = net_a.neighbor_table.get(NODE_C)
            if info and info.via == NODE_B:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError('A did not learn about C via gossip')

        # прямого линка A→C нет — иначе тест не проверяет multi-hop
        assert net_a.router.get_transport_to(NODE_C) is None

        # --- B1: RPC через промежуточный узел — RESPONSE возвращается --- #
        payload = b'multi-hop-payload' * 10
        result = await net_a.call(dst=NODE_C, service='testsvc',
                                  method='echo', data=payload, timeout=10)
        assert result == {'echo': payload}, result

        # --- ERROR тоже возвращается по chain и поднимается на origin --- #
        try:
            await net_a.call(dst=NODE_C, service='testsvc',
                             method='no_such_method', data=None, timeout=10)
            raise AssertionError('expected MethodNotFound to propagate')
        except Exception as e:
            assert 'no_such_method' in str(e), e

        # --- B3: исключение сервиса → ERROR на origin, WS-соединение живо --- #
        try:
            await net_a.call(dst=NODE_C, service='testsvc',
                             method='boom', data=None, timeout=10)
            raise AssertionError('expected boom failure to propagate')
        except Exception as e:
            assert 'intentional failure' in str(e), e
            assert 'RPC timeout' not in str(e), \
                f'service exception killed the chain (timeout instead of error): {e}'

        # соединения A—B—C не пострадали — последующий RPC проходит
        result = await net_a.call(dst=NODE_C, service='testsvc',
                                  method='echo', data=b'after-boom', timeout=10)
        assert result == {'echo': b'after-boom'}, result

    finally:
        for c in connectors:
            await c.stop()
        await net_a.stop()
        await net_b.stop()
        await net_c.stop()


def test_multihop_rpc_response_and_error():
    asyncio.run(_multihop_scenario())


if __name__ == '__main__':
    test_multihop_rpc_response_and_error()
    print('PASS: multihop_rpc_response_and_error')
