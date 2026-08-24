# src/internal_modules/config.py

import logging
import os
import socket
from pathlib import Path
from typing import Optional, Any

import yaml
from pydantic import BaseModel, field_validator

log = logging.getLogger('Config')

_HOSTNAME = socket.gethostname()


# ------------------------------------------------------------------ #
#  Модели
# ------------------------------------------------------------------ #

class NetworkConfig(BaseModel):
    host: str = '0.0.0.0'
    port: int = 9000
    ip_ttl_sec: int = 60


class MemoryConfig(BaseModel):
    default_buff: int = 10


class LoggingConfig(BaseModel):
    level: str = 'INFO'
    uvicorn_level: str = 'WARNING'
    websockets_level: str = 'WARNING'


class ServicesConfig(BaseModel):
    path: Path = Path('services')


class PeerConfig(BaseModel):
    node_id: str
    uri: str


class LocalConfig(BaseModel):
    alias: str = _HOSTNAME
    name: str = 'Core'
    exe_name: str = 'Node_P2P_Core.exe'
    secret: Optional[str] = None
    work_dir: Path = Path(r'C:\Core')
    os.makedirs(work_dir, exist_ok=True)
    full_path: Path = work_dir / exe_name
    excluded_autoload_services: list = ['webpanel']
    peers: list[PeerConfig] = []


class Config(BaseModel):
    node: str = _HOSTNAME
    network: NetworkConfig = NetworkConfig()
    memory: MemoryConfig = MemoryConfig()
    logging: LoggingConfig = LoggingConfig()
    services: ServicesConfig = ServicesConfig()
    local: LocalConfig = LocalConfig()

    @field_validator('node')
    @classmethod
    def node_not_empty(cls, v):
        if not v.strip():
            raise ValueError('node name cannot be empty')
        return v


def _ensure_config(path: Path):
    if not path.exists():
        default_model_instance = Config()

        # 2. Переводим модель в Python-словарь (dict)
        # mode='json' гарантирует, что специфичные типы (например, Path) превратятся в строки
        config_dict = default_model_instance.model_dump(mode='json')
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w', encoding='utf-8') as f:
            yaml.dump(
                config_dict,
                f,
                sort_keys=False,  # Сохраняет порядок полей из Pydantic-класса
                allow_unicode=True,  # Корректно пишет кириллицу и спецсимволы
                default_flow_style=False  # Генерирует красивый блочный YAML (не инлайн)
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
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    log.debug(f'Saved config: {path}')


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _set_nested(data: dict, keys: list[str], value: Any):
    d = data
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def _get_nested(data: dict, keys: list[str]) -> tuple[Any, dict]:
    """(value, parent_dict) для модификации на месте."""
    d = data
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    return d.get(keys[-1]), d


# ------------------------------------------------------------------ #
#  ConfigManager
# ------------------------------------------------------------------ #

class ConfigManager:
    """
    Загрузка, хранение и обновление конфига.
    Один файл — config.yaml. Автосохранение при любой модификации.
    """

    def __init__(self, config_path: Path = Path('config.yaml')):
        self._config_path = config_path
        self.cfg: Config = self._load()

    # ------------------------------------------------------------------ #
    #  Internal
    # ------------------------------------------------------------------ #

    def _load(self) -> Config:
        _ensure_config(self._config_path)
        raw = _load_yaml(self._config_path)
        cfg = Config(**raw)

        log.info(
            f'Config loaded: node={cfg.node} '
            f'port={cfg.network.port} '
            f'alias={cfg.local.alias} '
            f'peers={len(cfg.local.peers)}'
        )
        return cfg

    def reload(self):
        """Перечитать файл с диска."""
        self.cfg = self._load()
        log.info('Config reloaded')

    def _save(self):
        _save_yaml(self._config_path, _load_yaml(self._config_path))

    # ------------------------------------------------------------------ #
    #  Обновление конфига
    # ------------------------------------------------------------------ #

    def update(self, **kwargs):
        """
        Обновить любое поле конфига и сохранить config.yaml.
        Вложенность через '__':
            update(network__port=9001, logging__level='INFO')
        """
        data = _load_yaml(self._config_path)
        for key, value in kwargs.items():
            parts = key.split('__')
            _set_nested(data, parts, value)
        _save_yaml(self._config_path, data)
        self.cfg = Config(**data)
        log.info(f'Config updated: {kwargs}')

    # ------------------------------------------------------------------ #
    #  Удобные геттеры / сеттеры для local-полей
    # ------------------------------------------------------------------ #

    def get_local(self, key: str, default=None) -> Any:
        data = _load_yaml(self._config_path)
        _, parent = _get_nested(data, ['local'])
        return parent.get(key, default)

    def set_local(self, key: str, value: Any):
        data = _load_yaml(self._config_path)
        _set_nested(data, ['local', key], value)
        _save_yaml(self._config_path, data)
        self.cfg = Config(**data)
        log.info(f'Local config updated: {key} = {value}')

    # ------------------------------------------------------------------ #
    #  Управление пирами
    # ------------------------------------------------------------------ #

    def add_peer(self, node_id: str, uri: str) -> bool:
        data = _load_yaml(self._config_path)
        peers_data = data.setdefault('local', {}).setdefault('peers', [])

        if any(p.get('node_id') == node_id for p in peers_data):
            log.warning(f'Peer already exists: {node_id}')
            return False

        peers_data.append({'node_id': node_id, 'uri': uri})
        _save_yaml(self._config_path, data)
        self.cfg.local.peers.append(PeerConfig(node_id=node_id, uri=uri))
        log.info(f'Peer added: {node_id} → {uri}')
        return True

    def remove_peer(self, node_id: str) -> bool:
        data = _load_yaml(self._config_path)
        peers_data = data.get('local', {}).get('peers', [])
        new_peers = [p for p in peers_data if p.get('node_id') != node_id]

        if len(new_peers) == len(peers_data):
            log.warning(f'Peer not found: {node_id}')
            return False

        data['local']['peers'] = new_peers
        _save_yaml(self._config_path, data)
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

def load_config(config_path: Path = Path('config.yaml')) -> ConfigManager:
    return ConfigManager(config_path)
