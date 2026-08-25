# tests/test_b4_producer_error.py
#
# Регрессионный тест B4 (docs/analyze.md): ошибка producer неотличима от
# нормального завершения стрима. Раньше Dispatcher при падении генератора
# делал pipe.close() без маркера → консьюмер получал чистый StopAsyncIteration,
# а PipeTransport отправлял «успешный» STREAM_EOF. После фикса:
#   - локальный консьюмер pipe получает исходное исключение;
#   - удалённый консьюмер получает ERROR-пакет (Router роняет inbound pipe),
#     а не EOF.

import asyncio

from src.internal_modules.memory import (
    Dispatcher,
    Pipe,
    PipeTransport,
)
from src.networking.protocol import MsgPack, PackType
from src.networking.sessions import SessionTable


async def _dispatcher_error_scenario():
    pipe = Pipe(pipe_id='b4_local', buff_len=4)

    def bad_gen():
        yield 1
        yield 2
        raise ValueError('boom-generator')

    dispatcher = Dispatcher([pipe])
    dispatcher.start(bad_gen)

    seen = []
    failure = None
    try:
        async for chunk in pipe:
            seen.append(chunk)
    except Exception as e:
        failure = e

    await asyncio.sleep(0.1)   # дать run()-корутине завершиться
    dispatcher.stop()

    assert seen == [1, 2], seen
    assert isinstance(failure, ValueError), \
        f'B4: consumer saw {type(failure).__name__} instead of producer error'
    assert 'boom-generator' in str(failure), failure


def test_dispatcher_failure_raises_in_consumer():
    asyncio.run(_dispatcher_error_scenario())


class FakeRouter:
    def __init__(self):
        self.sessions = SessionTable()
        self.packs = []

    async def _send_pack(self, pack: MsgPack):
        self.packs.append(pack)


async def _transport_error_scenario():
    pipe = Pipe(pipe_id='b4_net', buff_len=4)
    router = FakeRouter()
    template = MsgPack(source='P', dst='C', service='svc', method='m',
                       label='lbl-b4')
    pt = PipeTransport(pipe, router, template)
    pt.timeout = 2.0

    pump = asyncio.create_task(pt._pump())
    await pipe.put('a')
    await pipe.put('b')
    # producer упал посреди стрима
    pipe.fail(RuntimeError('boom-producer'))
    await asyncio.wait_for(pump, timeout=10)

    types = [p.type for p in router.packs]
    assert types == [PackType.STREAM_CHUNK, PackType.STREAM_CHUNK, PackType.ERROR], \
        f'B4: expected ERROR instead of EOF on producer failure, got {types}'
    err = router.packs[-1]
    assert err.label == 'lbl-b4'
    assert 'boom-producer' in (err.error or '')

    # ack-сессия не течёт
    assert 'ack_lbl-b4' not in router.sessions._table


def test_pipe_transport_sends_error_not_eof_on_failure():
    asyncio.run(_transport_error_scenario())


if __name__ == '__main__':
    test_dispatcher_failure_raises_in_consumer()
    print('PASS: dispatcher_failure_raises_in_consumer')
    test_pipe_transport_sends_error_not_eof_on_failure()
    print('PASS: pipe_transport_sends_error_not_eof_on_failure')
