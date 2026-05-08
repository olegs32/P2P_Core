# modules/memory.py
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Optional

log = logging.getLogger('Memory')

_SENTINEL = object()


class Pipe:
    def __init__(self, pipe_id: str, buff_len: int = 10):
        self.pipe_id = pipe_id
        self.buff_len = buff_len
        self.low_watermark = max(1, buff_len // 3)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=buff_len)
        self._closed = False
        self._refill_cb: Optional[Callable[[str], None]] = None

    def set_refill_callback(self, cb: Callable[[str], None]):
        self._refill_cb = cb

    async def put(self, item):
        await self._queue.put(item)

    async def get(self):
        """Читается NetworkModule'ом и отправляется на remote."""
        # WRONG
        item = await self._queue.get()
        if self._queue.qsize() <= self.low_watermark and self._refill_cb:
            self._refill_cb(self.pipe_id)
        return item

    def is_full(self) -> bool:
        return self._queue.full()

    @property
    def size(self) -> int:
        return self._queue.qsize()

    def close(self):
        self._closed = True


class Dispatcher:
    """
    Единая точка входа от генератора → распределяет по pipe'ам.
    Паузит генератор когда все pipe полные.
    Возобновляет когда любой pipe падает ниже low_watermark.
    """

    def __init__(self, pipes: list[Pipe]):
        self.pipes: Dict[str, Pipe] = {p.pipe_id: p for p in pipes}
        self._resume = asyncio.Event()
        self._resume.set()
        self._running = False
        self._task: Optional[asyncio.Task] = None

        for pipe in pipes:
            pipe.set_refill_callback(self._on_refill_needed)

    def _on_refill_needed(self, pipe_id: str):
        log.debug(f'[dispatcher] refill from {pipe_id}')
        self._resume.set()

    def _least_loaded(self) -> Optional[Pipe]:
        candidates = [p for p in self.pipes.values() if not p.is_full()]
        return min(candidates, key=lambda p: p.size) if candidates else None

    async def run(self, generator: Callable):
        """
        Запустить генератор и начать распределение.
        generator — sync callable возвращающий итерируемое.
        """
        self._running = True
        loop = asyncio.get_event_loop()

        total_buff = sum(p.buff_len for p in self.pipes.values())
        gen_queue: asyncio.Queue = asyncio.Queue(maxsize=total_buff)

        # Sync генератор в threadpool — блокируется автоматически когда gen_queue полный
        def _produce():
            try:
                for item in generator():
                    if not self._running:
                        break
                    fut = asyncio.run_coroutine_threadsafe(gen_queue.put(item), loop)
                    fut.result()
            except Exception as e:
                log.error(f'[dispatcher] generator error: {e}')
            finally:
                asyncio.run_coroutine_threadsafe(gen_queue.put(_SENTINEL), loop).result()

        loop.run_in_executor(None, _produce)
        log.info(f'[dispatcher] started, managing {len(self.pipes)} pipes')

        while self._running:
            item = await gen_queue.get()
            if item is _SENTINEL:
                log.info('[dispatcher] generator exhausted')
                break

            # ждём пока освободится хотя бы один pipe
            target = None
            while target is None and self._running:
                target = self._least_loaded()
                if target is None:
                    self._resume.clear()
                    log.debug('[dispatcher] all pipes full — paused')
                    await self._resume.wait()

            if target:
                await target.put(item)

        for pipe in self.pipes.values():
            pipe.close()
        log.info('[dispatcher] finished')

    def start(self, generator: Callable) -> asyncio.Task:
        self._task = asyncio.create_task(self.run(generator))
        return self._task

    def stop(self):
        self._running = False
        self._resume.set()


class MemoryModule:
    def __init__(self, node: str):
        self.node = node
        self.pipes: Dict[str, Pipe] = {}
        self.dispatchers: list[Dispatcher] = []
        self._counter = 0

    async def start(self):
        log.info(f'[memory] started (node={self.node})')

    async def stop(self):
        for d in self.dispatchers:
            d.stop()
        for pipe in self.pipes.values():
            pipe.close()
        log.info('[memory] stopped')

    def create_pipe(self, buff: int = 10) -> Pipe:
        self._counter += 1
        pipe_id = f'{self.node}_{self._counter}'
        pipe = Pipe(pipe_id, buff)
        self.pipes[pipe_id] = pipe
        log.debug(f'[memory] pipe created: {pipe_id}')
        return pipe

    def create_dispatcher(self, pipes: list[Pipe]) -> Dispatcher:
        d = Dispatcher(pipes)
        self.dispatchers.append(d)
        return d