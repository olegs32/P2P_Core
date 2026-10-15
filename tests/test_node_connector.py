# tests/test_node_connector.py

import asyncio

from src.networking.node_connector import NodeConnector


class Entry:
    def __init__(self, status: str):
        self.status = type('Status', (), {'value': status})()


class NeighborTable:
    def __init__(self, entry=None):
        self._entry = entry

    def get(self, node_id):
        return self._entry


class Router:
    def __init__(self, has_transport: bool = False, has_client_ws: bool = False):
        self._has_transport = has_transport
        self._has_client_ws = has_client_ws

    def get_transport_to(self, node_id):
        # полный резолв (включая маршруты через via) — _already_connected
        # его сознательно НЕ использует, только прямой канал
        return object() if self._has_transport else None

    def has_client_ws(self, node_id):
        return self._has_client_ws


class NodesManager:
    def __init__(self, has_inbound: bool = False):
        self._has_inbound = has_inbound

    def get(self, node_id):
        return object() if self._has_inbound else None


class Net:
    def __init__(self, table, router, nodes_manager=None):
        self.neighbor_table = table
        self.router = router
        self.nodes_manager = nodes_manager or NodesManager()


class Ctx:
    def __init__(self, node, net):
        self.NODE = node
        self.network = net


def make_connector(node='A', peer='B', table_status=None,
                   transport=False, client_ws=False, inbound=False):
    entry = Entry(table_status) if table_status else None
    ctx = Ctx(node, Net(NeighborTable(entry),
                        Router(transport, client_ws),
                        NodesManager(inbound)))
    return NodeConnector(
        f'Connector_{peer}', ctx,
        peer_node_id=peer, target_uri=f'ws://localhost:9000/ws/{node}',
    )


def test_already_connected_via_table():
    c = make_connector(table_status='connected')
    assert c._already_connected() is True


def test_already_connected_via_client_ws_only():
    c = make_connector(table_status=None, client_ws=True)
    assert c._already_connected() is True


def test_already_connected_via_inbound_only():
    c = make_connector(table_status=None, inbound=True)
    assert c._already_connected() is True


def test_via_route_does_not_block_direct_dial():
    # есть только маршрут через via (полный резолв), прямого канала нет —
    # прямой dial НЕ подавляется (возрождение исходящего подключения)
    c = make_connector(table_status='known', transport=True)
    assert c._already_connected() is False


def test_not_connected():
    c = make_connector(table_status='unreachable', transport=False)
    assert c._already_connected() is False


def test_retry_backoff_cap():
    # потолок тихого ретрая — 10 минут
    from src.networking.node_connector import MAX_RETRY_BACKOFF
    assert MAX_RETRY_BACKOFF == 600


async def _run_start(connector):
    await connector.start()
    tasks = [connector._connect_task, connector._keepalive_task]
    for t in tasks:
        if t:
            t.cancel()
    for t in tasks:
        if t:
            try:
                await t
            except asyncio.CancelledError:
                pass


def test_start_passive_when_lesser_node():
    # Reverse-HELLO: HELLO уходит всегда, lex проверяется на сервере
    # (HELLO_REJECT+reverse dial). Поэтому даже меньший узел создаёт dial.
    c = make_connector(node='A', peer='B')
    asyncio.run(_run_start(c))
    assert c._connect_task is not None
    assert c._keepalive_task is not None


def test_start_active_when_greater_node():
    # 'B' > 'A' — dial разрешён
    c = make_connector(node='B', peer='A')
    asyncio.run(_run_start(c))
    assert c._connect_task is not None
