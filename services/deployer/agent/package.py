# services/deployer/agent/package.py
"""Форматы Safe Deploy: agent.conf, HMAC-запросы, envelope-ответы, валидация pre_config.

Единственный источник форматов — используют и агент (services/deployer/agent/*),
и админ-сервис (services/deployer/service.py, agent_api.py).
Plaintext envelope = msgpack-словарь (bytes допустимы, use_bin_type/raw=False).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import msgpack
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AAD_PREFIX = b"p2p-deploy-v1|"
SIG_PREFIX = "v1"
CONF_PROTOCOL = 1
PRE_CONFIG_TTL = 24 * 3600
AUTH_FAILED = "auth failed"
BUSY = "deploy in progress"


class EnvelopeError(Exception):
    """Битый/чужой/подделанный envelope."""


class PreConfigError(Exception):
    """pre_config не прошёл проверки (v/node/exp/поля)."""


@dataclass
class AgentConf:
    protocol: int
    mode: str            # 'open' | 'se'
    admin_ws: str        # ws://host:port (без /ws — суффикс добавляет клиент)
    admin_node: str      # HELLO dst, обязан совпасть с NODE/alias сервера
    node_id: str
    key: bytes           # 32B, hex в файле
    version: str
    deploy_timeout: int = 120

    @classmethod
    def from_dict(cls, d: dict) -> "AgentConf":
        if not isinstance(d, dict):
            raise ValueError("agent.conf: not an object")
        try:
            proto_ok = int(d.get("protocol", -1)) == CONF_PROTOCOL
        except (ValueError, TypeError):
            proto_ok = False
        if not proto_ok:
            raise ValueError(f"agent.conf: unsupported protocol {d.get('protocol')!r}")
        mode = d.get("mode")
        if mode not in ("open", "se"):
            raise ValueError(f"agent.conf: bad mode {mode!r}")
        key_hex = str(d.get("key", ""))
        if len(key_hex) != 64:
            raise ValueError("agent.conf: key must be 64 hex chars")
        ws = str(d.get("admin_ws", ""))
        if not (ws.startswith("ws://") or ws.startswith("wss://")):
            raise ValueError("agent.conf: admin_ws must be ws:// or wss://")
        try:
            deploy_timeout = int(d.get("deploy_timeout", 120))
        except (ValueError, TypeError):
            raise ValueError(f"agent.conf: bad deploy_timeout {d.get('deploy_timeout')!r}")
        return cls(
            protocol=CONF_PROTOCOL, mode=mode, admin_ws=ws,
            admin_node=str(d["admin_node"]), node_id=str(d["node_id"]),
            key=bytes.fromhex(key_hex), version=str(d["version"]),
            deploy_timeout=deploy_timeout,
        )

    @classmethod
    def load(cls, path: Path | str) -> "AgentConf":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def sign_request(key: bytes, op: str, node_id: str, ts: float, nonce: str) -> str:
    msg = f"{SIG_PREFIX}|{op}|{node_id}|{ts}|{nonce}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def verify_sig(key: bytes, op: str, node_id: str, ts: float, nonce: str, sig: str) -> bool:
    if not isinstance(sig, str) or len(sig) != 64:
        return False
    return hmac.compare_digest(sign_request(key, op, node_id, ts, nonce), sig)


def seal(key: bytes, node_id: str, payload: dict) -> dict:
    """Шифрует payload в envelope; ключи payload обязаны быть str (unpackb(raw=False) строг к int-ключам)."""
    if len(key) != 32:
        raise EnvelopeError("key must be 32 bytes")
    nonce = os.urandom(12)
    aad = AAD_PREFIX + node_id.encode()
    ct = AESGCM(key).encrypt(nonce, msgpack.packb(payload, use_bin_type=True), aad)
    return {"v": 1, "alg": "A256GCM", "nid": node_id, "nonce": nonce, "ct": ct}


def open_envelope(key: bytes, node_id: str, env: dict) -> dict:
    try:
        if not isinstance(env, dict) or env.get("alg") != "A256GCM" or int(env.get("v", 0)) != 1:
            raise EnvelopeError("bad envelope header")
        if env.get("nid") != node_id:
            raise EnvelopeError("nid mismatch")
        nonce, ct = env.get("nonce"), env.get("ct")
        if not isinstance(nonce, bytes) or len(nonce) != 12 or not isinstance(ct, bytes):
            raise EnvelopeError("bad envelope fields")
        pt = AESGCM(key).decrypt(nonce, ct, AAD_PREFIX + node_id.encode())
        out = msgpack.unpackb(pt, raw=False)
    except EnvelopeError:
        raise
    except Exception as e:                      # InvalidTag, ValueError...
        raise EnvelopeError(f"decrypt failed: {e}") from e
    if not isinstance(out, dict):
        raise EnvelopeError("payload is not a dict")
    return out


def validate_pre_config(pc: dict, node_id: str, now: float | None = None) -> None:
    if not isinstance(pc, dict):
        raise PreConfigError("bad pre_config")
    try:
        v_ok = int(pc.get("v", 0)) == 1
    except (ValueError, TypeError):
        v_ok = False
    if not v_ok:
        raise PreConfigError("bad pre_config")
    if pc.get("node") != node_id:
        raise PreConfigError("node_id mismatch (conf vs envelope)")
    exp = pc.get("exp")
    t = now if now is not None else time.time()
    if not isinstance(exp, (int, float)) or exp < t:
        raise PreConfigError("pre_config expired")
    for k in ("config", "install", "exe", "deploy_id", "mode"):
        if k not in pc:
            raise PreConfigError(f"pre_config missing {k}")
    try:
        uuid.UUID(str(pc["deploy_id"]))
    except ValueError:
        raise PreConfigError("bad deploy_id")
    if pc.get("mode") not in ("open", "se"):
        raise PreConfigError("bad mode")
