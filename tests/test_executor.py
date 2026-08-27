# tests/test_executor.py

import asyncio

import pytest

from src.internal_modules.executor import LocalExecutor
from src.networking.protocol import MsgPack, PackType


class FakeServices:
    def __init__(self):
        self.svc = Service()

    def get_method(self, service, method):
        return getattr(self.svc, method, None)


class Service:
    def no_args(self):
        return {'ok': True}

    async def async_no_args(self):
        return {'ok': True}

    def with_data(self, data):
        return {'echo': data}

    async def async_with_data(self, data):
        return {'echo': data}


def make_pack(method):
    return MsgPack(
        type=PackType.REQUEST,
        source='peer', dst='self',
        service='svc', method=method,
        label='lbl', data={'x': 1},
    )


@pytest.mark.parametrize('method', ['no_args', 'async_no_args', 'with_data', 'async_with_data'])
def test_execute_matches_signature(method):
    ex = LocalExecutor(services=FakeServices(), stream_registry=None)
    result = asyncio.run(ex.execute(make_pack(method)))
    if 'no_args' in method:
        assert result.data == {'ok': True}
    else:
        assert result.data == {'echo': {'x': 1}}
