"""Auth и сессии agent_api (админ-сторона). Форматы — services/deployer/agent/package.py."""
from __future__ import annotations

import collections
import time
from typing import Callable

from services.deployer.agent.package import verify_sig, AUTH_FAILED, BUSY
from services.deployer.key_registry import DeployKeyRegistry, canon_node_id

TS_WINDOW = 300.0
NONCE_TTL = 600.0
RATE_WINDOW = 60.0
CTRL_RATE = 60
FETCH_RATE = 600
BUSY_GRACE = 600.0
FETCH_MAX = 4 * 1024 * 1024
TERMINAL_STAGES = {"done", "failed", "rolled_back"}


class BusyError(Exception):
    """Живая busy-сессия — message = BUSY."""


class AgentAuth:
    def __init__(self, registry: DeployKeyRegistry, now: Callable[[], float] = time.time):
        self.registry = registry
        self.now = now
        self._nonces: dict[str, float] = {}
        self._rates: dict[tuple[str, str], collections.deque] = {}

    def validate(self, req: dict) -> tuple[bytes | None, str | None]:
        """(key, None) = ок; (key|None, error) = отказ.

        Единое сообщение AUTH_FAILED для unknown-node/bad-sig/stale-ts/replay.
        'rate limited' — отдельный отказ (не про ключ).
        """
        if not isinstance(req, dict):
            return None, AUTH_FAILED
        op = req.get("op")
        raw_node = req.get("node_id")
        ts, nonce, sig = req.get("ts"), req.get("nonce"), req.get("sig")
        if (not isinstance(op, str) or not isinstance(raw_node, str)
                or not isinstance(ts, (int, float)) or isinstance(ts, bool)
                or not isinstance(nonce, str) or not isinstance(sig, str)):
            return None, AUTH_FAILED
        try:
            node = canon_node_id(raw_node)
        except ValueError:
            return None, AUTH_FAILED               # traversal/битый id = неизвестный узел
        key = self.registry.get(node)
        if key is None:
            return None, AUTH_FAILED
        # sig считается по RAW node_id — подмена регистра ломает подпись
        if not verify_sig(key, op, raw_node, ts, nonce, sig):
            return key, AUTH_FAILED
        if abs(ts - self.now()) > TS_WINDOW:
            return key, AUTH_FAILED
        self._prune_nonces()
        if nonce in self._nonces:
            return key, AUTH_FAILED
        bucket = "fetch" if op == "fetch_chunk" else "ctrl"
        limit = FETCH_RATE if bucket == "fetch" else CTRL_RATE
        if not self._allow(node, bucket, limit):
            return key, "rate limited"
        self._nonces[nonce] = self.now() + NONCE_TTL
        self.registry.touch(node)
        return key, None

    def _allow(self, node: str, bucket: str, limit: int) -> bool:
        now = self.now()
        dq = self._rates.setdefault((node, bucket), collections.deque())
        while dq and now - dq[0] > RATE_WINDOW:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True

    def _prune_nonces(self) -> None:
        now = self.now()
        for n in [k for k, exp in self._nonces.items() if exp <= now]:
            del self._nonces[n]


class AgentSessions:
    def __init__(self, now: Callable[[], float] = time.time):
        self.now = now
        self._sessions: dict[str, dict] = {}
        self._status: dict[str, dict] = {}

    def open(self, node_id: str, deploy_id: str, version: str, exe_path: str) -> None:
        node = canon_node_id(node_id)
        cur = self._sessions.get(node)
        if cur and cur["busy_until"] > self.now():
            raise BusyError(BUSY)
        self._sessions[node] = {
            "deploy_id": deploy_id, "version": version, "exe_path": exe_path,
            "busy_until": self.now() + BUSY_GRACE, "last_report": None,
        }
        # новый deploy_id -> чистый timeline
        self._status[node] = {"deploy_id": deploy_id, "stage": None, "ok": None,
                              "error": None, "warning": None, "pid": None,
                              "duration": None, "ts": self.now(), "history": [],
                              "last_report": None}

    def get(self, node_id: str) -> dict | None:
        return self._sessions.get(canon_node_id(node_id))

    def extend(self, node_id: str) -> None:
        cur = self.get(node_id)
        if cur:
            cur["busy_until"] = self.now() + BUSY_GRACE

    def close(self, node_id: str) -> None:
        self._sessions.pop(canon_node_id(node_id), None)

    def record_report(self, node_id: str, body: dict) -> dict:
        node = canon_node_id(node_id)
        st = self._status.setdefault(node, {
            "deploy_id": body.get("deploy_id"), "stage": None, "ok": None,
            "error": None, "warning": None, "pid": None, "duration": None,
            "ts": self.now(), "history": [], "last_report": None})
        stage = body.get("stage")
        # warning — top-level статуса: UI читает s['warning'] напрямую.
        # Кладём ТОЛЬКО при наличии ключа: отчёт без warning не затирает
        # ранее накопленный (сбрасывается только новым deploy в open())
        upd = {"deploy_id": body.get("deploy_id") or st.get("deploy_id"),
               "stage": stage, "ok": body.get("ok"), "error": body.get("error"),
               "pid": body.get("pid"), "duration": body.get("duration"),
               "ts": self.now()}
        if "warning" in body:
            upd["warning"] = body.get("warning")
        st.update(upd)
        if isinstance(stage, str) and (not st["history"] or st["history"][-1] != stage):
            st["history"].append(stage)
        st["last_report"] = dict(body)
        if stage in TERMINAL_STAGES:
            self.close(node)
        return st

    def status(self, node_id: str | None = None) -> dict:
        if node_id is None:
            return {k: dict(v) for k, v in self._status.items()}
        node = canon_node_id(node_id)
        v = self._status.get(node)
        return {node: dict(v)} if v else {}
