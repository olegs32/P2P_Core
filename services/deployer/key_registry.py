"""Реестр per-node ключей агента: <work_dir>/deploy/<node_id>.json, атомарная запись."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def canon_node_id(node_id: str) -> str:
    return str(node_id).strip().lower()


class DeployKeyRegistry:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, node_id: str) -> Path:
        return self.root / f"{canon_node_id(node_id)}.json"

    def get(self, node_id: str) -> bytes | None:
        p = self._path(node_id)
        if not p.is_file():
            return None
        try:
            return bytes.fromhex(json.loads(p.read_text(encoding="utf-8"))["key"])
        except Exception:
            return None

    def get_or_create(self, node_id: str) -> tuple[bytes, bool]:
        key = self.get(node_id)
        if key is not None:
            return key, False
        key = os.urandom(32)
        self._write(node_id, key)
        return key, True

    def rotate(self, node_id: str) -> bytes:
        key = os.urandom(32)
        self._write(node_id, key)
        return key

    def touch(self, node_id: str) -> None:
        p = self._path(node_id)
        if not p.is_file():
            return
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            d["last_used"] = time.time()
            self._atomic_write(p, d)
        except Exception:
            pass

    def list(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        if self.root.is_dir():
            for p in self.root.glob("*.json"):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                    out[p.stem] = {"created": d.get("created", 0.0),
                                   "last_used": d.get("last_used", 0.0)}
                except Exception:
                    continue
        return out

    def _write(self, node_id: str, key: bytes) -> None:
        now = time.time()
        self._atomic_write(self._path(node_id),
                           {"key": key.hex(), "created": now, "last_used": now})

    def _atomic_write(self, path: Path, obj: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
