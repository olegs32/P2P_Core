# tests/test_memory_dispatcher.py
# Регрессия: поток-продюсер Dispatcher не должен зависать на полном
# gen_queue после остановки (иначе процесс не завершается — executor
# не-daemon, asyncio.run джойнит потоки на выходе).

import asyncio
import threading
import time

from src.internal_modules.memory import Dispatcher, Pipe, _SENTINEL


def test_producer_thread_exits_on_stop():
    """stop() при полном gen_queue и мёртвом потребителе: продюсер
    выходит по флагу, а не блокируется навсегда на queue.put."""

    async def main():
        loop = asyncio.get_running_loop()
        pipe = Pipe('t1', buff_len=1)
        d = Dispatcher([pipe])

        def endless_gen():
            i = 0
            while True:
                i += 1
                yield i

        task = d.start(endless_gen)
        await asyncio.sleep(0.4)        # продюсер забил очередь и ждёт слота

        d.stop()
        await asyncio.wait_for(task, timeout=5)

        # джойн потоков дефолтного executor'а: до фикса висел бы вечно
        await asyncio.wait_for(loop.shutdown_default_executor(), timeout=5)

    asyncio.run(main())


def test_normal_exhaustion_still_sends_sentinel():
    """Штатный путь: генератор исчерпался → sentinel в pipe → EOF."""

    async def main():
        pipe = Pipe('t2', buff_len=4)
        d = Dispatcher([pipe])

        def small_gen():
            yield 1
            yield 2

        await asyncio.wait_for(d.run(small_gen), timeout=5)
        assert pipe._closed

        # всё содержимое (2 элемента + sentinel) доступно
        items = []
        while not pipe.empty():
            items.append(pipe._queue.get_nowait())
        assert items == [1, 2, _SENTINEL]

    asyncio.run(main())


def test_producer_failure_closes_pipes_without_sentinel():
    """Ошибка генератора → pipes помечены как failed (обрыв цепочки)."""

    async def main():
        pipe = Pipe('t3', buff_len=4)
        d = Dispatcher([pipe])

        def broken_gen():
            yield 1
            raise ValueError('boom')

        await asyncio.wait_for(d.run(broken_gen), timeout=5)
        assert pipe._closed
        # pipe помечен как failed, потребитель получит исходное исключение
        assert pipe.failed
        assert isinstance(pipe.error, ValueError)
        # sentinel кладётся fail() для пробуждения потребителя, затем raise
        # (ранее sentinel не клался, теперь — кладётся, но с ошибкой)
        assert pipe._error is not None

    asyncio.run(main())
