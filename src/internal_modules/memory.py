# GRID/memory.py

import asyncio
import logging
from typing import Callable, Dict, Optional

from src.internal_modules.base import ModuleGeneric
from src.networking.protocol import MsgPack, PackType

log = logging.getLogger('Memory')
_SENTINEL = object()


class Pipe:
    def __init__(self, pipe_id: str, buff_len: int = 10):
        self.pipe_id = pipe_id
        self.buff_len = buff_len
        self.low_watermark = max(1, buff_len // 3)  # may be truncated
        self._queue = asyncio.Queue(maxsize=buff_len)
        self._closed = False
        self._refill_cb: Optional[Callable[[str], None]] = None

    def set_refill_callback(self, cb: Callable[[str], None]):  # may be truncated
        self._refill_cb = cb

    async def put(self, item):
        await self._queue.put(item)

    async def get(self):
        item = await self._queue.get()
        if self._queue.qsize() <= self.low_watermark and self._refill_cb:
            self._refill_cb(self.pipe_id)
        return item

    def is_full(self) -> bool:
        return self._queue.full()

    def empty(self) -> bool:
        return self._queue.empty()

    @property
    def size(self) -> int:
        return self._queue.qsize()

    def close(self):
        self._closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._closed and self._queue.empty():
            raise StopAsyncIteration
        item = await self.get()
        if item is _SENTINEL:
            raise StopAsyncIteration
        return item


class PipeTransport:
    def __init__(self, pipe: Pipe, transport, pack_template: MsgPack,
                 router, timeout: int = 30):
        self.pipe = pipe
        self.transport = transport
        self.template = pack_template
        self.router = router
        self.timeout = timeout
        self.buff_size = pipe.buff_len  # размер батча = размер буфера pipe
        self._task: Optional[asyncio.Task] = None

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self._handshake_and_pump())
        return self._task

    async def _handshake_and_pump(self):
        # handshake
        open_pack = MsgPack(
            type=PackType.STREAM_OPEN,
            source=self.template.source,
            dst=self.template.dst,
            service=self.template.service,
            method=self.template.method,
            label=self.template.label,
            data=self.template.data,
        )
        future = self.router.sessions.register_single(
            self.template.label,
            self.template.service,
            self.template.method or '',
        )
        await self.transport.send(open_pack)

        try:
            await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError:
            log.error(f'[pipe_transport] handshake timeout {self.template.label[:8]}')
            return

        log.info(f'[pipe_transport] handshake ok, buff_size={self.buff_size}')
        await self._pump()

    async def _pump(self):
        sent_in_batch = 0
        ack_label = f'ack_{self.template.label}'

        async for chunk in self.pipe:
            await self.transport.send(MsgPack(
                type=PackType.STREAM_CHUNK,
                source=self.template.source,
                dst=self.template.dst,
                label=self.template.label,
                data=chunk,
            ))
            sent_in_batch += 1
            log.debug(f'[pipe_transport] sent #{sent_in_batch}/{self.buff_size}')

            if sent_in_batch >= self.buff_size:
                # батч отправлен — ждём ACK от remote
                log.debug(f'[pipe_transport] batch done ({self.buff_size} chunks) — waiting ACK')
                ack_future = self.router.sessions.register_single(ack_label, '', '')
                try:
                    await asyncio.wait_for(ack_future, timeout=self.timeout)
                    log.debug(f'[pipe_transport] ACK received — next batch')
                except asyncio.TimeoutError:
                    log.error(f'[pipe_transport] ACK timeout — stopping')
                    break
                sent_in_batch = 0

        await self.transport.send(MsgPack(
            type=PackType.STREAM_EOF,
            source=self.template.source,
            dst=self.template.dst,
            label=self.template.label,
        ))
        log.info(f'[pipe_transport] EOF sent')

    def stop(self):
        if self._task:
            self._task.cancel()


class Dispatcher:
    """
    Единая точка входа от генератора → распределяет по pipe'ам.
    Паузит генератор когда все pipe полные.
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
        self._running = True
        loop = asyncio.get_event_loop()

        total_buff = sum(p.buff_len for p in self.pipes.values())
        gen_queue: asyncio.Queue = asyncio.Queue(maxsize=total_buff)

        _producer_failed = False

        def _produce():
            try:
                for item in generator():
                    if not self._running:
                        break
                    asyncio.run_coroutine_threadsafe(gen_queue.put(item), loop).result()
            except Exception as e:
                log.error(f'[dispatcher] generator error: {e}')
                nonlocal _producer_failed
                _producer_failed = True
            finally:
                asyncio.run_coroutine_threadsafe(gen_queue.put(_SENTINEL), loop).result()

        producer_future = loop.run_in_executor(None, _produce)
        log.info(f'[dispatcher] started → {len(self.pipes)} pipes')

        while self._running:
            item = await gen_queue.get()
            if item is _SENTINEL:
                if _producer_failed:
                    log.error('[dispatcher] producer failed — closing all pipes')
                else:
                    log.debug('[dispatcher] generator exhausted')
                break

            target = None
            while target is None and self._running:
                target = self._least_loaded()
                if target is None:
                    self._resume.clear()
                    log.debug('[dispatcher] all pipes full — paused')
                    await self._resume.wait()

            if target:
                await target.put(item)

        # При ошибке producer — закрыть pipes без sentinel (прервать цепочку)
        if _producer_failed:
            for pipe in self.pipes.values():
                pipe.close()
            log.error('[dispatcher] aborted due to producer failure')
        else:
            # закрываем все pipes sentinel'ом чтобы PipeTransport отправил EOF
            for pipe in self.pipes.values():
                await pipe.put(_SENTINEL)
                pipe.close()

        log.info('[dispatcher] finished')

    def start(self, generator: Callable) -> asyncio.Task:
        self._task = asyncio.create_task(self.run(generator))
        return self._task

    def stop(self):
        self._running = False
        self._resume.set()


class MemoryModule(ModuleGeneric):
    def __init__(self, name: str, context):
        super().__init__(name, context)
        self.pipes: Dict[str, Pipe] = {}
        self.dispatchers: list[Dispatcher] = []
        self._transports: list[PipeTransport] = []
        self._counter = 0

    async def start(self):
        self.log.info(f'Started (node={self.name})')

    async def stop(self):
        for t in self._transports:
            t.stop()
        for d in self.dispatchers:
            d.stop()
        for pipe in self.pipes.values():
            pipe.close()
        self.log.info('Stopped')

    # ------------------------------------------------------------------ #
    #  Pipe management
    # ------------------------------------------------------------------ #

    def create_pipe(self, buff: int = 10) -> Pipe:
        self._counter += 1
        pipe_id = f'{self.name}_{self._counter}'
        pipe = Pipe(pipe_id, buff)
        self.pipes[pipe_id] = pipe
        log.debug(f'pipe created: {pipe_id}')
        return pipe

    def create_pipes(self, buff: int = 10, count: int = 1) -> list:
        return [self.create_pipe(buff) for _ in range(count)]

    def create_dispatcher(self, pipes: list[Pipe]) -> Dispatcher:
        d = Dispatcher(pipes)
        self.dispatchers.append(d)
        return d

    # ------------------------------------------------------------------ #
    #  Network pipe: outbound (локальный генератор → remote)
    # ------------------------------------------------------------------ #

    def attach_transport(self, pipe: Pipe, transport, pack_template: MsgPack, router) -> PipeTransport:
        """
        Подключить сетевой транспорт к pipe.
        Чанки из pipe потекут как STREAM_CHUNK на remote.
        """
        pt = PipeTransport(pipe, transport, pack_template, router)
        self._transports.append(pt)
        pt.start()
        return pt

    # ------------------------------------------------------------------ #
    #  Network pipe: inbound (remote → локальный pipe)
    # ------------------------------------------------------------------ #

    def pipe_from_stream(self, label: str, buff: int = 10) -> Pipe:
        """
        Создать pipe привязанный к входящему стриму по label.
        Router будет класть STREAM_CHUNK в эту pipe через feed_chunk().
        """
        pipe = self.create_pipe(buff)
        pipe._stream_label = label  # маркер для Router
        self.log.debug(f'inbound pipe created for label={label[:8]}')
        return pipe

    async def feed_chunk(self, pipe: Pipe, chunk):
        """Router вызывает это при получении STREAM_CHUNK."""
        await pipe.put(chunk)

    async def close_stream(self, pipe: Pipe):
        """Router вызывает это при получении STREAM_EOF."""
        await pipe.put(_SENTINEL)
        pipe.close()


"""
Пример использования — outbound:
python# локальный генератор → 3 remote worker'а через сеть

def compute_ranges():
    for i in range(100):
        yield (i * 100, (i + 1) * 100)

pipes = [ctx.memory.create_pipe(buff=10) for _ in range(3)]
dispatcher = ctx.memory.create_dispatcher(pipes)

# каждый pipe → свой remote worker
for i, pipe in enumerate(pipes):
    node   = ctx.network.nodes_manager.get(f'Worker{i}')
    transport = WebSocketTransport(node.ws)
    template  = MsgPack(
        source  = ctx.NODE,
        dst     = f'Worker{i}',
        service = 'compute',
        method  = 'run_range',
        label   = str(uuid.uuid4()),
    )
    ctx.memory.attach_transport(pipe, transport, template)

dispatcher.start(compute_ranges)        


Пример использования — inbound:
python# получить стрим с remote в локальный pipe

stream_label = str(uuid.uuid4())
pipe = ctx.memory.pipe_from_stream(stream_label, buff=10)

# запросить стрим у remote
await ctx.network.router.call(
    dst='Node1', service='data', method='stream_data',
    data={'label': stream_label}
)

# читать локально
async for chunk in pipe:
    print(chunk)

        
"""
