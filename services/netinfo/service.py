# GRID/services/netinfo/service.py — просмотр состояния сети через RPC

from src.internal_modules.base import ModuleGeneric
from services.rpc import rpc


class NetInfo(ModuleGeneric):
    def __init__(self, name, context):
        super().__init__(name, context)

    @rpc
    def neighbors(self, data: dict):
        """Полная таблица соседей."""
        table = self.ctx.network.neighbor_table
        return {
            'own':       self.ctx.NODE,
            'connected': [n.model_dump() for n in table.connected()],
            'known':     [n.model_dump() for n in table.known()],
            'all':       [n.model_dump() for n in table.all()],
        }

    @rpc
    def nodes(self, data: dict):
        """Активные WS подключения в NodesManager."""
        nm = self.ctx.network.nodes_manager
        return {
            node_id: {'node_id': node_id}
            for node_id in nm.nodes.keys()
        }

    @rpc
    def services(self, data: dict):
        """Сервисы зарегистрированные локально."""
        return list(self.ctx.services.services.keys())

    @rpc
    def find_service(self, data: dict):
        """Найти ноды с указанным сервисом."""
        service = data.get('service') if isinstance(data, dict) else data
        table   = self.ctx.network.neighbor_table
        found   = table.find_by_service(service)
        return [n.model_dump() for n in found]