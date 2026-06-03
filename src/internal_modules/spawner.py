# GRID/spawner.py

import uuid

from src.internal_modules.base import ModuleGeneric
from src.networking.protocol import MsgPack
from services.rpc import rpc
from src.networking.transport import WebSocketTransport


class Spawner(ModuleGeneric):
    def __init__(self, name, context):
        super().__init__(name, context)
        self.log.info('Spawner registered')

    @rpc
    async def spawn(self, data: dict):
        service_name = data.get('generator_service')  # сервис, где живёт генератор
        generator_name = data.get('generator')  # имя @generator метода
        target_service = data.get('service')  # сервис на remote
        target_method = data.get('method')  # stream_name на remote
        workers_count = data.get('workers_count', 1)
        buff = data.get('buff', 3)
        init_data = data.get('init_data', {})

        # получить генератор из реестра
        gen_fn = self.ctx.services.get_generator(service_name, generator_name)
        if not gen_fn:
            available = self.ctx.services.list_generators(service_name)
            return {
                'error': f'generator not found: {service_name}.{generator_name}',
                'available': available,
            }

        # обернуть — генератор принимает init_data
        def _generator():
            yield from gen_fn(init_data)

        nodes = list(self.ctx.network.nodes_manager.nodes.values())
        if len(nodes) < workers_count:
            return {'error': f'need {workers_count} nodes, have {len(nodes)}'}

        nodes = nodes[:workers_count]
        pipes = [self.ctx.memory.create_pipe(buff=buff) for _ in range(workers_count)]
        dispatcher = self.ctx.memory.create_dispatcher(pipes)
        labels = []

        for index, node in enumerate(nodes):
            label = str(uuid.uuid4())
            template = MsgPack(
                source=self.ctx.NODE,
                dst=node.node_id,
                service=target_service,
                method=target_method,
                label=label,
                data=init_data,
            )
            transport = WebSocketTransport(node.ws)
            self.ctx.memory.attach_transport(
                pipes[index], transport, template, self.ctx.network.router
            )
            labels.append(label)
            self.log.info(f'Pipe → {node.node_id} gen={service_name}.{generator_name}')

        dispatcher.start(_generator)
        self.log.info(f'Spawned {workers_count} workers')
        return {'status': 'started', 'labels': labels, 'count': workers_count}

    @rpc
    def list_generators(self, data: dict):
        """Список доступных генераторов для отладки."""
        service_name = data.get('service') if isinstance(data, dict) else None
        if service_name:
            return {service_name: self.ctx.services.list_generators(service_name)}
        # все сервисы
        return {
            name: self.ctx.services.list_generators(name)
            for name in self.ctx.services.services
        }