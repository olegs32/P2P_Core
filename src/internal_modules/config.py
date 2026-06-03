# src/internal_modules/config.py

import logging
import socket
from pathlib import Path
from typing import Optional
import yaml
from pydantic import BaseModel, field_validator

log = logging.getLogger('Config')

_HOSTNAME = socket.gethostname()


# ------------------------------------------------------------------ #
#  Модели
# ------------------------------------------------------------------ #

class NetworkConfig(BaseModel):
    host: str = _HOSTNAME
    port: int = 9000


class MemoryConfig(BaseModel):
    default_buff: int = 10


class LoggingConfig(BaseModel):
    level:         str = 'DEBUG'
    uvicorn_level: str = 'WARNING'
    websockets_level:  str = 'WARNING'


class ServicesConfig(BaseModel):
    path: Path = Path('services')


class PeerConfig(BaseModel):
    node_id: str
    uri:     str


class LocalConfig(BaseModel):
    alias:  str              = _HOSTNAME
    secret: Optional[str]   = None
    peers:  list[PeerConfig] = []


class Config(BaseModel):
    node:     str            = 'Node0'
    network:  NetworkConfig  = NetworkConfig()
    memory:   MemoryConfig   = MemoryConfig()
    logging:  LoggingConfig  = LoggingConfig()
    services: ServicesConfig = ServicesConfig()
    local:    LocalConfig    = LocalConfig()

    @field_validator('node')
    @classmethod
    def node_not_empty(cls, v):
        if not v.strip():
            raise ValueError('node name cannot be empty')
        return v


# ------------------------------------------------------------------ #
#  Дефолтные файлы
# ------------------------------------------------------------------ #

_DEFAULT_BASE = """\
node: Node0

network:
  host: 0.0.0.0
  port: 9000

memory:
  default_buff: 10

logging:
  level: DEBUG
  uvicorn_level: WARNING

services:
  path: services/
"""

_DEFAULT_LOCAL = """\
alias: {hostname}
secret: null

peers: []
#  - node_id: Node1
#    uri: ws://192.168.1.10:9001/ws/Node0
"""


def _ensure_file(path: Path, template: str):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            template.format(hostname=_HOSTNAME),
            encoding='utf-8'
        )
        log.info(f'Created default config: {path}')


# ------------------------------------------------------------------ #
#  IO
# ------------------------------------------------------------------ #

def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def _save_yaml(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, encoding='utf-8') as f:
        existing = yaml.safe_load(f) or {} if path.exists() else {}

    # сохраняем комментарии-заголовки если файл уже был
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, allow_unicode=True,
                  default_flow_style=False, sort_keys=False)
    log.debug(f'Saved config: {path}')


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ------------------------------------------------------------------ #
#  ConfigManager
# ------------------------------------------------------------------ #

class ConfigManager:
    """
    Загрузка, хранение и обновление конфига.
    Автосохранение при любой модификации.
    """

    def __init__(self,
                 base_path:  Path = Path('config.yaml'),
                 local_path: Path = Path('config.local.yaml')):
        self._base_path  = base_path
        self._local_path = local_path
        self.cfg: Config = self._load()

    def _load(self) -> Config:
        _ensure_file(self._base_path,  _DEFAULT_BASE)
        _ensure_file(self._local_path, _DEFAULT_LOCAL)

        base  = _load_yaml(self._base_path)
        local = _load_yaml(self._local_path)

        local_section = local.pop('local', local)
        merged = _deep_merge(base, {'local': local_section})
        cfg = Config(**merged)

        log.info(
            f'Config loaded: node={cfg.node} '
            f'port={cfg.network.port} '
            f'alias={cfg.local.alias} '
            f'peers={len(cfg.local.peers)}'
        )
        return cfg

    def reload(self):
        """Перечитать оба файла с диска."""
        self.cfg = self._load()
        log.info('Config reloaded')

    # ------------------------------------------------------------------ #
    #  Обновление base конфига
    # ------------------------------------------------------------------ #

    def update_base(self, **kwargs):
        """
        Обновить поля base конфига и сохранить config.yaml.
        Поддерживает вложенность через '__':
            update_base(network__port=9001)
        """
        data = _load_yaml(self._base_path)
        for key, value in kwargs.items():
            parts = key.split('__')
            d = data
            for part in parts[:-1]:
                d = d.setdefault(part, {})
            d[parts[-1]] = value

        _save_yaml(self._base_path, data)
        self.cfg = Config(**_deep_merge(
            data,
            {'local': _load_yaml(self._local_path)}
        ))
        log.info(f'Base config updated: {kwargs}')

    # ------------------------------------------------------------------ #
    #  Обновление local конфига (пиры, секреты)
    # ------------------------------------------------------------------ #

    def update_local(self, **kwargs):
        """
        Обновить поля local конфига и сохранить config.local.yaml.
        Поддерживает вложенность через '__':
            update_local(alias='my-node')
        """
        data = _load_yaml(self._local_path)
        for key, value in kwargs.items():
            parts = key.split('__')
            d = data
            for part in parts[:-1]:
                d = d.setdefault(part, {})
            d[parts[-1]] = value

        _save_yaml(self._local_path, data)
        base = _load_yaml(self._base_path)
        self.cfg = Config(**_deep_merge(base, {'local': data}))
        log.info(f'Local config updated: {kwargs}')

    # ------------------------------------------------------------------ #
    #  Управление пирами
    # ------------------------------------------------------------------ #

    def add_peer(self, node_id: str, uri: str) -> bool:
        """Добавить пир если его ещё нет."""
        data = _load_yaml(self._local_path)
        peers: list = data.get('peers', [])

        if any(p.get('node_id') == node_id for p in peers):
            log.warning(f'Peer already exists: {node_id}')
            return False

        peers.append({'node_id': node_id, 'uri': uri})
        data['peers'] = peers
        _save_yaml(self._local_path, data)

        self.cfg.local.peers.append(PeerConfig(node_id=node_id, uri=uri))
        log.info(f'Peer added: {node_id} → {uri}')
        return True

    def remove_peer(self, node_id: str) -> bool:
        """Удалить пир по node_id."""
        data = _load_yaml(self._local_path)
        peers = data.get('peers', [])
        new_peers = [p for p in peers if p.get('node_id') != node_id]

        if len(new_peers) == len(peers):
            log.warning(f'Peer not found: {node_id}')
            return False

        data['peers'] = new_peers
        _save_yaml(self._local_path, data)

        self.cfg.local.peers = [
            p for p in self.cfg.local.peers if p.node_id != node_id
        ]
        log.info(f'Peer removed: {node_id}')
        return True

    def list_peers(self) -> list[PeerConfig]:
        return self.cfg.local.peers


# ------------------------------------------------------------------ #
#  Shortcut
# ------------------------------------------------------------------ #

def load_config(base_path:  Path = Path('config.yaml'),
                local_path: Path = Path('config.local.yaml')) -> ConfigManager:
    return ConfigManager(base_path, local_path)



