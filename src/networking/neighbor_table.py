# GRID/neighbor_table.py

import logging
import time
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

log = logging.getLogger('NeighborTable')

PROTOCOL_VERSION = "2.0"


class NeighborStatus(str, Enum):
    CONNECTED   = "connected"    # прямое WS соединение
    KNOWN       = "known"        # известна из gossip, прямого нет
    UNREACHABLE = "unreachable"  # была connected/known, перестала отвечать


class NeighborInfo(BaseModel):
    node_id:    str
    host:       str
    port:       int
    status:     NeighborStatus  = NeighborStatus.KNOWN
    via:        Optional[str]   = None     # через кого слать если KNOWN
    last_ts:    float           = Field(default_factory=time.time)
    session_id: Optional[str]   = None
    version:    str             = PROTOCOL_VERSION
    services:   List[str]       = []       # сервисы на этой ноде

    # UNUSED: свойство uri не используется в проекте.
    # При необходимости: ws://{host}:{port}/ws/{node_id}
    # @property
    # def uri(self) -> str:
    #     return f"ws://{self.host}:{self.port}/ws/{self.node_id}"


class NeighborTable:
    def __init__(self, own_node_id: str):
        self.own_node_id = own_node_id
        self._table: Dict[str, NeighborInfo] = {}

    # ------------------------------------------------------------------ #
    #  Регистрация
    # ------------------------------------------------------------------ #

    def register_connected(self, node_id: str, host: str, port: int,
                           session_id: str, version: str = PROTOCOL_VERSION,
                           services: List[str] = None) -> NeighborInfo:
        info = NeighborInfo(
            node_id    = node_id,
            host       = host,
            port       = port,
            status     = NeighborStatus.CONNECTED,
            via        = None,        # прямое — via не нужен
            last_ts    = time.time(),
            session_id = session_id,
            version    = version,
            services   = services or [],
        )
        self._table[node_id] = info
        log.info(f'Registered connected: {node_id} ({host}:{port})')
        return info

    def register_known(self, node_id: str, host: str, port: int,
                       via: str, version: str = PROTOCOL_VERSION,
                       services: List[str] = None) -> NeighborInfo:
        # не перезаписывать connected более слабым known
        existing = self._table.get(node_id)
        if existing and existing.status == NeighborStatus.CONNECTED:
            return existing

        info = NeighborInfo(
            node_id  = node_id,
            host     = host,
            port     = port,
            status   = NeighborStatus.KNOWN,
            via      = via,
            last_ts  = time.time(),
            version  = version,
            services = services or [],
        )
        self._table[node_id] = info
        log.debug(f'Registered known: {node_id} via {via}')
        return info

    # ------------------------------------------------------------------ #
    #  Обновление
    # ------------------------------------------------------------------ #

    def touch(self, node_id: str):
        """Обновить last_ts при любом входящем трафике от ноды."""
        info = self._table.get(node_id)
        if info:
            info.last_ts = time.time()

    def mark_unreachable(self, node_id: str):
        info = self._table.get(node_id)
        if info:
            info.status = NeighborStatus.UNREACHABLE
            log.warning(f'Marked unreachable: {node_id}')

    def mark_connected(self, node_id: str, session_id: str):
        info = self._table.get(node_id)
        if info:
            info.status     = NeighborStatus.CONNECTED
            info.session_id = session_id
            info.last_ts    = time.time()
            info.via        = None

    def update_services(self, node_id: str, services: List[str]):
        info = self._table.get(node_id)
        if info:
            info.services = services
            log.debug(f'Services updated for {node_id}: {services}')

    def remove(self, node_id: str):
        self._table.pop(node_id, None)
        log.info(f'Removed neighbor: {node_id}')

    # ------------------------------------------------------------------ #
    #  Запросы
    # ------------------------------------------------------------------ #

    def get(self, node_id: str) -> Optional[NeighborInfo]:
        return self._table.get(node_id)

    def has(self, node_id: str) -> bool:
        return node_id in self._table

    def connected(self) -> List[NeighborInfo]:
        return [n for n in self._table.values()
                if n.status == NeighborStatus.CONNECTED]

    def known(self) -> List[NeighborInfo]:
        return [n for n in self._table.values()
                if n.status == NeighborStatus.KNOWN]

    def all(self) -> List[NeighborInfo]:
        return list(self._table.values())

    def find_by_service(self, service: str) -> List[NeighborInfo]:
        """Найти ноды с нужным сервисом."""
        return [n for n in self._table.values() if service in n.services]

    # ------------------------------------------------------------------ #
    #  Gossip
    # ------------------------------------------------------------------ #

    def to_gossip(self) -> List[dict]:
        """Сериализовать для отправки — без UNREACHABLE."""
        return [
            n.model_dump()
            for n in self._table.values()
            if n.status != NeighborStatus.UNREACHABLE
               and n.node_id != self.own_node_id
        ]

    def merge_gossip(self, neighbors: List[dict], from_node: str):
        """Смержить входящую таблицу соседей."""
        added = 0
        for entry in neighbors:
            node_id = entry.get('node_id')
            if not node_id or node_id == self.own_node_id:
                continue
            if node_id in self._table:
                continue  # уже знаем — не перезаписываем
            self.register_known(
                node_id  = node_id,
                host     = entry.get('host', ''),
                port     = entry.get('port', 9000),
                via      = from_node,
                version  = entry.get('version', PROTOCOL_VERSION),
                services = entry.get('services', []),
            )
            added += 1
        if added:
            log.info(f'Gossip from {from_node}: +{added} new neighbors')