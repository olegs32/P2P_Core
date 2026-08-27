# tests/test_router_ack.py
# Скользящий TTL маршрутов стрима + тихие поздние ACK после EOF.

import asyncio
import time

from src.networking.network import NodesManager
from src.networking.router import Router, StreamRoute
from services.manager import ServiceManager


def make_router() -> Router:
    class Ctx:
        NODE = 'NodeSelf'
        services = ServiceManager()

    return Router(NodesManager(), Ctx())


# ------------------------------------------------------------------ #
#  Скользящий TTL
# ------------------------------------------------------------------ #

def test_get_stream_route_sliding_ttl():
    r = make_router()
    route = StreamRoute(label='L1', source='A', dst='Self',
                        forward_path=['A'], backward_path=['Self'])
    route.established_at = time.monotonic() - 299   # почти истёк
    r._stream_routes['L1'] = route

    got = r.get_stream_route('L1')
    assert got is route
    # TTL продлён обращением — маршрут не умрёт при долгой передаче
    assert time.monotonic() - got.established_at < 5


def test_get_stream_route_expired_popped():
    r = make_router()
    route = StreamRoute(label='L2', source='A', dst='Self')
    route.established_at = time.monotonic() - 301   # точно истёк
    r._stream_routes['L2'] = route

    assert r.get_stream_route('L2') is None
    assert 'L2' not in r._stream_routes


# ------------------------------------------------------------------ #
#  Поздние ACK: после EOF маршрут удалён и стрим закрыт — это штатно
# ------------------------------------------------------------------ #

def test_late_ack_after_eof_is_debug_not_warning(caplog):
    r = make_router()
    # нет ни маршрута, ни живого стрима (как после STREAM_EOF)
    with caplog.at_level('DEBUG', logger='Router'):
        asyncio.run(r.send_stream_ack('4273dc24-0000-0000-0000-000000000000', 8))

    warnings = [rec for rec in caplog.records if rec.levelname == 'WARNING']
    assert not warnings, 'поздний ACK после EOF не должен warning-ать'


def test_ack_without_route_on_live_stream_warns(caplog):
    r = make_router()
    # стрим зарегистрирован, но маршрут потерян — реальная аномалия
    from src.internal_modules.memory import Pipe
    r.stream_registry.register(
        'live-0000-0000-0000-000000000000', Pipe('p', buff_len=4))

    with caplog.at_level('DEBUG', logger='Router'):
        asyncio.run(r.send_stream_ack(
            'live-0000-0000-0000-000000000000', 8))

    assert any(rec.levelname == 'WARNING' and 'no cached route' in rec.message
               for rec in caplog.records)
