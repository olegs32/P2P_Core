import asyncio

import pytest

pytest.importorskip("src.se.services.security_dashboard.service")
from src.se.services.security_dashboard.service import SecurityDashboard


class _Node:
    def __init__(self, node_id):
        self.node_id = node_id


class _Table:
    def __init__(self, ids):
        self._ids = ids

    def connected(self):
        return [_Node(i) for i in self._ids]


class _Net:
    def __init__(self, table, calls):
        self.neighbor_table = table
        self._calls = calls
        self.calls_made = 0

    async def call(self, dst, service, method, data, timeout=6):
        self.calls_made += 1
        if dst in self._calls and isinstance(self._calls[dst], Exception):
            raise self._calls[dst]
        return self._calls[dst]


class _Ctx:
    NODE = "adminnode"

    def __init__(self, net):
        self.network = net


def _svc(calls, ids=("n1", "n2")):
    return SecurityDashboard("security_dashboard", _Ctx(_Net(_Table(list(ids)), calls)))


def test_fleet_policies_partial_failure_renders_grey():
    svc = _svc({"n1": {"bundle": {"version": 3, "rules": []}}, "n2": TimeoutError("slow")})
    res = asyncio.run(svc.fleet_policies({}))
    assert res["ok"] is True
    assert res["policies"]["n1"]["version"] == 3
    assert res["policies"]["n1"]["error"] is None
    assert res["policies"]["n2"]["error"] == "slow"


def test_fleet_policies_cache_serves_without_refresh():
    svc = _svc({"n1": {"bundle": {"version": 1, "rules": []}}}, ids=("n1",))
    asyncio.run(svc.fleet_policies({}))
    first_calls = svc.ctx.network.calls_made
    asyncio.run(svc.fleet_policies({}))
    assert svc.ctx.network.calls_made == first_calls
    asyncio.run(svc.fleet_policies({"refresh": True}))
    assert svc.ctx.network.calls_made > first_calls
