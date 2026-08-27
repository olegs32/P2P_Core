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
    def __init__(self, has_transport: bool):
        self._has_transport = has_transport

    def get_transport_to(self, node_id):
        return object() if self._has_transport else None


class Net:
    def __init__(self, table, router):
        self.neighbor_table = table
        self.router = router


class Ctx:
    def __init__(self, node, net):
        self.NODE = node
        self.network = net


def make_connector(node='A', peer='B', table_status=None, transport=False):
    entry = Entry(table_status) if table_status else None
    ctx = Ctx(node, Net(NeighborTable(entry), Router(transport)))
    return NodeConnector(
        f'Connector_{peer}', ctx,
        peer_node_id=peer, target_uri=f'ws://localhost:9000/ws/{node}',
    )


def test_already_connected_via_table():
    c = make_connector(table_status='connected')
    assert c._already_connected() is True


def test_already_connected_via_transport_only():
    c = make_connector(table_status=None, transport=True)
    assert c._already_connected() is True


def test_not_connected():
    c = make_connector(table_status='unreachable', transport=False)
    assert c._already_connected() is False


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
    # 'A' < 'B' — исходящий dial не создаётся, keepalive работает
    c = make_connector(node='A', peer='B')
    asyncio.run(_run_start(c))
    assert c._connect_task is None
    assert c._keepalive_task is not None


def test_start_active_when_greater_node():
    # 'B' > 'A' — dial разрешён
    c = make_connector(node='B', peer='A')
    asyncio.run(_run_start(c))
    assert c._connect_task is not None
