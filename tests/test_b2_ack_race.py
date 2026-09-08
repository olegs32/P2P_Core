# tests/test_b2_ack_race.py
#
# Регрессионный тест B2 (docs/analyze.md): гонка ACK в PipeTransport.
# _pump раньше регистрировал ack-future ПОСЛЕ отправки всего батча; быстрый
# консьюмер отвечал на первый чанк батча — ACK приходил до регистрации,
# sessions.resolve молча ронял его, и _pump замирал на полный timeout
# (стрим умирал после первого батча). Фикс: регистрация ДО отправки батча
# и перерегистрация сразу после получения ACK.
#
# Тест детерминированный: fake-router при отправке ПЕРВОГО чанка каждого
# батча тут же резолвит ack-сессию. Со старым кодом этот ACK всегда падает
# в незарегистрированную сессию → после 1-го батча timeout → вместо 6 чанков
# уходит только buff_size. С фиксом — все 6 чанков + EOF.

import asyncio

from src.internal_modules.memory import Pipe, PipeTransport, _SENTINEL
from src.networking.protocol import MsgPack, PackType
from src.networking.sessions import SessionTable

BUFF_LEN = 2          # размер батча
TOTAL_CHUNKS = 6      # 3 батча


class FakeRouter:
    """Эмуляция Router: считает пакеты, мгновенно отвечает ACK на первый
    чанк каждого батча (хуже реального консьюмера не бывает)."""

    def __init__(self):
        self.sessions = SessionTable()
        self.chunks_sent = 0
        self.eof_sent = False

    async def _send_pack(self, pack: MsgPack):
        if pack.type == PackType.STREAM_CHUNK:
            self.chunks_sent += 1
            if self.chunks_sent % BUFF_LEN == 1:
                # консьюмер получил первый чанк батча и мгновенно подтвердил;
                # на старом коде сессии ещё нет — resolve молча теряет ACK
                self.sessions.resolve(f'ack_{pack.label}', 'ack')
        elif pack.type == PackType.STREAM_EOF:
            self.eof_sent = True


async def _ack_race_scenario():
    pipe = Pipe(pipe_id='b2_test', buff_len=BUFF_LEN)
    router = FakeRouter()
    template = MsgPack(
        source='Producer', dst='Consumer',
        service='testsvc', method='push', label='label-b2',
    )
    pt = PipeTransport(pipe, router, template)
    pt.timeout = 2.0  # короткий таймаут: старый код падает быстро

    async def feeder():
        for i in range(TOTAL_CHUNKS):
            await pipe.put(i)
        await pipe.put(_SENTINEL)
        pipe.close()

    pump_task = asyncio.create_task(pt._pump())
    feed_task = asyncio.create_task(feeder())
    await asyncio.wait_for(pump_task, timeout=10)
    await asyncio.wait_for(feed_task, timeout=10)

    assert router.chunks_sent == TOTAL_CHUNKS, (
        f'B2 race: stream died after first batch — '
        f'{router.chunks_sent}/{TOTAL_CHUNKS} chunks sent')
    assert router.eof_sent, 'no STREAM_EOF after last chunk'

    # ack-сессия не должна течь в таблице
    assert 'ack_label-b2' not in router.sessions._table, 'ack session leaked'


def test_pump_registers_ack_future_before_batch():
    asyncio.run(_ack_race_scenario())


if __name__ == '__main__':
    test_pump_registers_ack_future_before_batch()
    print('PASS: pump_registers_ack_future_before_batch')
