# GRID/protocol.py — типы пакетов и MsgPack

import time
import uuid
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class PackType(str, Enum):
    REQUEST      = "request"
    RESPONSE     = "response"
    STREAM_CHUNK = "stream_chunk"
    STREAM_EOF   = "stream_eof"
    ERROR        = "error"
    PING         = "ping"
    PONG         = "pong"


class MsgPack(BaseModel):
    type:     PackType = PackType.REQUEST
    source:   str
    dst:      str | None = None
    service:  str | None = None
    method:   str | None = None
    data:     Any = None
    label:    str = Field(default_factory=lambda: str(uuid.uuid4()))
    error:    str | None = None