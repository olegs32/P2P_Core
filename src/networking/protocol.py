# GRID/protocol.py

import uuid
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class PackType(str, Enum):
    REQUEST      = "request"
    RESPONSE     = "response"
    FORWARDED    = "forwarded"
    STREAM_OPEN  = "stream_open"   # ← handshake: подготовить consumer
    STREAM_READY = "stream_ready"  # ← подтверждение: готов к приёму
    STREAM_CHUNK = "stream_chunk"
    STREAM_ACK   = "stream_ack"    # ← клиент → сервер: пришли ещё buff штук
    STREAM_EOF   = "stream_eof"
    ERROR        = "error"
    PING         = "ping"
    PONG         = "pong"
    HELLO        = "hello"  # представление при подключении
    HELLO_ACK    = "hello_ack"  # принято + таблица соседей + сервисы
    HELLO_REJECT = "hello_reject"  # отклонено + причина
    GOSSIP       = "gossip"  # периодическая рассылка топологии
    ANNOUNCE     = "announce"  # периодическая рассылка сервисов


class MsgPack(BaseModel):
    type:     PackType = PackType.REQUEST
    source:   str
    dst:      str | None = None
    service:  str | None = None
    method:   str | None = None  # имя stream для STREAM_OPEN
    data:     Any = None
    label:    str = Field(default_factory=lambda: str(uuid.uuid4()))
    error:    str | None = None
    path: list[str] = Field(default_factory=list)  # [Node0, Node1, ...]
    ttl: int = 16