# services/system/service.py — управление системой: подключение к узлам, диагностика

import asyncio

from src.internal_modules.base import ModuleGeneric
from services.rpc import rpc


class System(ModuleGeneric):
    def __init__(self, name, context):
        super().__init__(name, context)

    @rpc
    async def connect_to_node(self, data: dict):
        """Инициировать исходящее подключение к удалённому узлу.

        Подключение разрешено, если удалённый узел НЕ подключен к локальному
        (по NeighborTable). При успехе — сохраняет пира в config.local.yaml
        для автоматического переподключения при рестарте.

        data: {host, port, node_id}
        """
        host = data.get('host', '')
        port = data.get('port', 9000)
        node_id = data.get('node_id', '')

        if not host or not node_id:
            return {'ok': False, 'error': 'host и node_id обязательны'}

        nt = self.ctx.network.neighbor_table
        existing = nt.get(node_id)
        if existing and existing.status.value == 'connected':
            return {'ok': False, 'error': f'Узел {node_id} уже подключен'}

        uri = f'ws://{host}:{port}/ws/{self.ctx.NODE}'
        config_uri = f'ws://{host}:{port}/ws/'

        try:
            await self.ctx.network.connect_to(node_id=node_id, target_uri=uri)
        except Exception as e:
            return {'ok': False, 'error': str(e)}

        # Дождаться подтверждения подключения (до 5 сек)
        connected = False
        for _ in range(10):
            await asyncio.sleep(0.5)
            entry = nt.get(node_id)
            if entry and entry.status.value == 'connected':
                connected = True
                break

        if connected:
            try:
                self.ctx.config_manager.add_peer(node_id, config_uri)
                self.log.info(f'Peer saved to config: {node_id} → {config_uri}')
            except Exception as e:
                self.log.warning(f'Failed to save peer to config: {e}')
            return {'ok': True, 'node_id': node_id, 'uri': uri, 'saved': True}

        return {'ok': True, 'node_id': node_id, 'uri': uri, 'saved': False,
                'note': 'Подключение в процессе (коннектор будет повторять попытки)'}

    @rpc
    def list_connectors(self, data: dict):
        """Список активных исходящих коннекторов."""
        result = []
        for mod in self.ctx._modules:
            if 'Connector_' in mod.name:
                result.append({
                    'name': mod.name,
                    'peer': getattr(mod, 'peer_node_id', '?'),
                    'uri': getattr(mod, 'target_uri', '?'),
                })
        return result

    @rpc
    def node_detail(self, data: dict):
        """Детальная информация об узле: соседи, сервисы, активные WS."""
        nt = self.ctx.network.neighbor_table
        nm = self.ctx.network.nodes_manager

        connected = [n.model_dump() for n in nt.connected()]
        known = [n.model_dump() for n in nt.known()]

        ws_nodes = list(nm.nodes.keys())

        return {
            'own': self.ctx.NODE,
            'connected': connected,
            'known': known,
            'ws_connections': ws_nodes,
            'services': list(self.ctx.services.services.keys()),
        }

    @rpc
    def config_peers(self, data: dict):
        """Список пиров из config.local.yaml."""
        peers = self.ctx.config_manager.list_peers()
        return [{'node_id': p.node_id, 'uri': p.uri} for p in peers]
