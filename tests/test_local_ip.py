# tests/test_local_ip.py

import time

from src.internal_modules.local_ip import LocalIPResolver


class NetCfg:
    ip_ttl_sec = 60


class LocalCfg:
    peers = []


class Cfg:
    network = NetCfg()
    local = LocalCfg()


class Ctx:
    config = Cfg()


def make_resolver(live=None, peer=None, psutil=None) -> LocalIPResolver:
    r = LocalIPResolver(Ctx())
    r._from_live_connections = lambda: live
    r._via_peer_route = lambda: peer
    r._psutil_fallback = lambda: psutil
    return r


def test_priority_live_first():
    r = make_resolver(live='10.0.0.5', peer='10.0.0.6', psutil='10.0.0.7')
    assert r.get() == '10.0.0.5'


def test_priority_peer_before_psutil():
    r = make_resolver(live=None, peer='10.0.0.6', psutil='10.0.0.7')
    assert r.get() == '10.0.0.6'


def test_loopback_when_nothing_found():
    r = make_resolver(live=None, peer=None, psutil=None)
    assert r.get() == '127.0.0.1'


def test_cached_within_ttl():
    calls = {'n': 0}

    def peer():
        calls['n'] += 1
        return '10.0.0.6'

    r = make_resolver(live=None, psutil=None)
    r._via_peer_route = peer

    assert r.get() == '10.0.0.6'
    assert r.get() == '10.0.0.6'
    assert calls['n'] == 1


def test_recompute_after_ttl(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, 'monotonic', lambda: now[0])

    calls = {'n': 0}

    def peer():
        calls['n'] += 1
        return f'10.0.0.{calls["n"]}'

    r = make_resolver(live=None, psutil=None)
    r._via_peer_route = peer

    assert r.get() == '10.0.0.1'
    now[0] += 61  # > ip_ttl_sec
    assert r.get() == '10.0.0.2'
    assert calls['n'] == 2
