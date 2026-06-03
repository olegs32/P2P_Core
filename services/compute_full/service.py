# GRID/services/compute_full/service.py
# один сервис — и генератор и вычислитель

import uuid
import asyncio
from src.internal_modules.base import ModuleGeneric
from services.rpc import rpc, stream_wrapper, stream_consumer, generator
from src.networking.protocol import MsgPack
from src.networking.transport import WebSocketTransport, send_ack
from src.internal_modules.memory import Pipe


class Compute(ModuleGeneric):
    def __init__(self, name, context):
        super().__init__(name, context)


    @generator
    def compute_ranges(self, data: dict):
        count = data.get('count', 20) if isinstance(data, dict) else 20
        for i in range(count):
            self.log.debug(f'generate #{i}')
            yield [i * 100, (i + 1) * 100]

    @generator
    def compute_squares(self, data: dict):
        count = data.get('count', 20) if isinstance(data, dict) else 20
        for i in range(count):
            yield i * i
    # ------------------------------------------------------------------ #
    #  Генератор — вызывается по RPC, стримит на target ноду
    # ------------------------------------------------------------------ #

    @rpc
    async def start_stream(self, data: dict):
        target     = data.get('target')
        count      = data.get('count', 20)
        multiplier = data.get('multiplier', 1)
        buff       = data.get('buff', 3)

        node = self.ctx.network.nodes_manager.get(target)
        if not node:
            return {'error': f'node {target} not found'}

        generated = 0

        def compute_ranges():
            nonlocal generated
            for i in range(count):
                self.log.info(f'GENERATE #{i}')
                generated += 1
                yield [i * 100, (i + 1) * 100]
            self.log.info(f'Generator exhausted — total: {generated}')

        pipe       = self.ctx.memory.create_pipe(buff=buff)
        dispatcher = self.ctx.memory.create_dispatcher([pipe])

        template = MsgPack(
            source  = self.ctx.NODE,
            dst     = target,
            service = 'compute',       # имя сервиса на Node1
            method  = 'run_range',     # stream_name
            label   = str(uuid.uuid4()),
            data    = {'multiplier': multiplier, 'buff': buff},
        )

        transport = WebSocketTransport(node.ws)
        self.ctx.memory.attach_transport(
            pipe, transport, template, self.ctx.network.router
        )
        dispatcher.start(compute_ranges)

        self.log.info(f'Stream started → {target} count={count} buff={buff}')
        return {'status': 'started', 'label': template.label, 'count': count}

    # ------------------------------------------------------------------ #
    #  Потребитель — принимает стрим от генератора
    # ------------------------------------------------------------------ #

    @stream_wrapper('run_range')
    async def prepare_run(self, data: dict):
        multiplier = data.get('multiplier', 1) if isinstance(data, dict) else 1
        buff       = data.get('buff', 3) if isinstance(data, dict) else 3
        self.log.info(f'Prepare consumer: multiplier={multiplier} buff={buff}')
        return {
            'multiplier': multiplier,
            'buff':       buff,
            'results':    [],
            'index':      0,
        }

    @stream_consumer('run_range')
    async def consume_ranges(self, pipe: Pipe, ctx: dict):
        multiplier = ctx['multiplier']
        buff       = ctx['buff']
        results    = ctx['results']
        ws         = ctx.get('ws')      # websocket обратно к Node0 для ACK
        label      = ctx.get('label')

        # первый запрос порции
        if ws and label:
            await send_ack(self.ctx.NODE, 'Node0', ws, label, buff)

        async for chunk in pipe:
            ctx['index'] += 1
            index      = ctx['index']
            queue_size = pipe.size

            self.log.info(f'CONSUME #{index} data={chunk} queue={queue_size}')

            # prefetch — запросить следующую порцию пока считаем
            if ws and label and queue_size < buff and not ctx.get('eof'):
                await send_ack(self.ctx.NODE, 'Node0', ws, label, buff)

            # вычисление с задержкой
            await asyncio.sleep(0.1)
            result = chunk[0] * multiplier
            results.append(result)
            self.log.info(f'RESULT  #{index} = {result}')

        self.log.info(f'Consumer done — total={len(results)} results={results}')

