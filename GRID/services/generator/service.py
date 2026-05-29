# GRID/services/generator/service.py

import uuid
from GRID.base import ModuleGeneric
from GRID.services.rpc import rpc
from GRID.protocol import MsgPack
from GRID.transport import WebSocketTransport


class Generator(ModuleGeneric):
    def __init__(self, name, context):
        super().__init__(name, context)

    @rpc
    async def start_stream(self, data: dict):
        """
        Запустить генератор и стримить на указанную ноду.
        data = {target: str, count: int, multiplier: int}
        """
        target     = data.get('target', 'DebugClient')
        count      = data.get('count', 10)
        multiplier = data.get('multiplier', 1)

        node = self.ctx.network.nodes_manager.get(target)
        if not node:
            return {'error': f'node {target} not found'}

        # генератор диапазонов
        def compute_ranges():
            for i in range(count):
                self.log.debug(f'generating range {i}')
                yield [i * 100, (i + 1) * 100]

        # pipe + dispatcher
        pipe       = self.ctx.memory.create_pipe(buff=5)
        dispatcher = self.ctx.memory.create_dispatcher([pipe])

        # шаблон пакета — указывает remote сервис/метод для handshake
        template = MsgPack(
            source  = self.ctx.NODE,
            dst     = target,
            service = 'compute',
            method  = 'run_range',
            label   = str(uuid.uuid4()),
            data    = {'multiplier': multiplier},  # для wrapper на remote
        )

        transport = WebSocketTransport(node.ws)
        self.ctx.memory.attach_transport(
            pipe, transport, template, self.ctx.network.router
        )

        dispatcher.start(compute_ranges)
        self.log.info(f'stream started → {target}, label={template.label[:8]}')
        return {'status': 'started', 'label': template.label}