# tests/test_agent_api_integration.py
"""Полный протокол через agent_api напрямую (без WS и без реальных процессов)."""
import asyncio
import json
import time
import uuid

from src.internal_modules.config import Config
from services.deployer.agent.package import (
    sign_request, open_envelope,
)

EXE = b"NODE-EXE-BYTES-" * 4096        # 64KB
VER = "9.9.9-build1"


def run(coro):
    return asyncio.run(coro)


async def drain_deliveries(svc):
    """Дождаться всех фоновых задач доставки (async-контракт Task 15).

    gen/retry отвечают сразу, не дожидаясь копирования; тесты дренируют
    созданные задачи, чтобы проверить состояние _delivery_status.
    """
    seen: set = set()
    while True:
        pend = [t for t in list(getattr(svc, "_deliver_tasks", {}).values())
                if t is not None and not t.done() and t not in seen]
        last = getattr(svc, "_deliver_task", None)
        if last is not None and not last.done() and last not in seen:
            pend.append(last)
        if not pend:
            return
        await asyncio.gather(*pend)
        seen.update(pend)


def run_gen(svc, data, method="gen_agent_package"):
    """RPC генерации/доставки + дренаж фоновых задач доставки."""
    async def _go():
        res = await getattr(svc, method)(data)
        await drain_deliveries(svc)
        return res
    return run(_go())


class _NT:
    def __init__(self):
        self.entries = {}

    def get(self, node_id):
        return self.entries.get(str(node_id).strip().lower())


class _Info:
    def __init__(self, status):
        self.status = type("S", (), {"value": status})()


class _Net:
    def __init__(self):
        self.neighbor_table = _NT()

    def local_ip(self):
        return "127.0.0.1"


def make_deployer(tmp_path, monkeypatch):
    import services.deployer.service as ds
    from services.deployer.service import Deployer

    dist = tmp_path / "dist"
    vdir = dist / VER
    vdir.mkdir(parents=True)
    (vdir / "Node_P2P_Core.exe").write_bytes(EXE)
    (vdir / "manifest.json").write_text(json.dumps({"version": VER}), encoding="utf-8")
    monkeypatch.setattr(ds, "DIST", dist)

    class Ctx:
        NODE = "admin0"
    ctx = Ctx()
    cfg = Config(node="admin0")
    cfg.local.work_dir = str(tmp_path / "work")
    cfg.network.port = 9000
    ctx.config = cfg
    ctx.network = _Net()
    svc = Deployer("deployer", ctx)
    return svc, dist, ctx


def _req(key, op, body=None, node="node7", ts=None, nonce=None):
    ts = time.time() if ts is None else ts
    nonce = nonce or uuid.uuid4().hex[:16]
    return {"op": op, "node_id": node, "ts": ts, "nonce": nonce,
            "sig": sign_request(key, op, node, ts, nonce), "body": body or {}}


def _open(key, node, resp):
    assert "ct" in resp, f"ответ не envelope: {resp}"
    return open_envelope(key, node, resp)


def test_full_protocol_reassembly(tmp_path, monkeypatch):
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    key, _ = svc._agent_registry.get_or_create("node7")

    # ping
    r = _open(key, "node7", run(svc.agent_api(_req(key, "ping"))))
    assert r["ok"] is True and isinstance(r["server_ts"], float)

    # pre_config (conf пинит версию)
    pc = _open(key, "node7", run(svc.agent_api(_req(key, "pre_config", {"version": VER}))))
    assert pc["v"] == 1 and pc["node"] == "node7" and pc["mode"] == "open"
    assert pc["exe"] == {"name": "Node_P2P_Core.exe",
                         "sha256": __import__("hashlib").sha256(EXE).hexdigest(),
                         "size": len(EXE), "version": VER}
    assert pc["install"]["exe_name"] and pc["install"]["full_path"]
    assert pc["exp"] > time.time() + 86000
    assert uuid.UUID(pc["deploy_id"])
    assert "ca_cert" not in pc                     # open-админ не отдаёт certs

    # fetch_chunk x N -> побайтовая реконструкция
    out = bytearray()
    offset = 0
    while offset < len(EXE):
        r = _open(key, "node7", run(svc.agent_api(
            _req(key, "fetch_chunk", {"offset": offset, "size": 4 * 1024 * 1024}))))
        assert r["ok"] and r["offset"] == offset
        out += r["data"]
        offset += len(r["data"])
    assert bytes(out) == EXE

    # progress-отчёты + терминальный
    run(svc.agent_api(_req(key, "report",
                           {"stage": "FETCH_EXE", "ok": True, "deploy_id": pc["deploy_id"]})))
    st = run(svc.agent_status({}))
    assert st["statuses"]["node7"]["history"] == ["FETCH_EXE"]
    run(svc.agent_api(_req(key, "report",
                           {"stage": "done", "ok": True, "deploy_id": pc["deploy_id"]})))
    st = run(svc.agent_status({"node": "node7"}))
    assert st["statuses"]["node7"]["stage"] == "done"

    # сессия закрыта -> повторный pre_config не busy
    pc2 = _open(key, "node7", run(svc.agent_api(_req(key, "pre_config", {"version": VER}))))
    assert pc2["deploy_id"] != pc["deploy_id"]


def test_pre_config_busy(tmp_path, monkeypatch):
    svc, _, _ = make_deployer(tmp_path, monkeypatch)
    key, _ = svc._agent_registry.get_or_create("node7")
    run(svc.agent_api(_req(key, "pre_config", {"version": VER})))
    r2 = run(svc.agent_api(_req(key, "pre_config", {"version": VER})))
    body = _open(key, "node7", r2)
    assert body == {"ok": False, "error": "deploy in progress"}


def test_pre_config_no_artifact(tmp_path, monkeypatch):
    svc, _, _ = make_deployer(tmp_path, monkeypatch)
    key, _ = svc._agent_registry.get_or_create("node7")
    r = run(svc.agent_api(_req(key, "pre_config", {"version": "1.2.3-мусор"})))
    assert _open(key, "node7", r) == {"ok": False, "error": "no artifact"}


def test_unknown_node_plain_auth_failed(tmp_path, monkeypatch):
    svc, _, _ = make_deployer(tmp_path, monkeypatch)
    # узла нет в реестре -> plaintext, единообразный текст
    ghost = _req(bytes.fromhex("cd" * 32), "ping", node="ghost")
    r = run(svc.agent_api(ghost))
    assert r == {"ok": False, "error": "auth failed"} and "ct" not in r


def test_bad_sig_enclosed_auth_failed(tmp_path, monkeypatch):
    svc, _, _ = make_deployer(tmp_path, monkeypatch)
    key, _ = svc._agent_registry.get_or_create("node7")
    r = _req(key, "ping")
    r["sig"] = "00" * 32
    body = _open(key, "node7", run(svc.agent_api(r)))
    assert body == {"ok": False, "error": "auth failed"}


def test_fetch_chunk_rejections(tmp_path, monkeypatch):
    svc, _, _ = make_deployer(tmp_path, monkeypatch)
    key, _ = svc._agent_registry.get_or_create("node7")
    # без сессии
    r = _open(key, "node7", run(svc.agent_api(
        _req(key, "fetch_chunk", {"offset": 0, "size": 10}))))
    assert r["ok"] is False
    # сессия есть
    run(svc.agent_api(_req(key, "pre_config", {"version": VER})))
    r = _open(key, "node7", run(svc.agent_api(
        _req(key, "fetch_chunk", {"offset": 0, "size": 5 * 1024 * 1024}))))
    assert r["ok"] is False                       # cap 4MB
    r = _open(key, "node7", run(svc.agent_api(
        _req(key, "fetch_chunk", {"offset": len(EXE) + 1, "size": 10}))))
    assert r == {"ok": False, "error": "out of range"}


def test_report_warning_hoisted_to_status(tmp_path, monkeypatch):
    """warning из тела отчёта агента поднимается в top-level статуса (UI читает его)."""
    svc, _, _ = make_deployer(tmp_path, monkeypatch)
    key, _ = svc._agent_registry.get_or_create("node7")
    st = run(svc.agent_status({}))
    assert st["statuses"] == {}                          # до отчётов статуса нет
    run(svc.agent_api(_req(key, "report",
                           {"stage": "DETECT", "ok": True, "deploy_id": "d1",
                            "warning": "legacy storage unreadable under SYSTEM"})))
    st = run(svc.agent_status({"node": "node7"}))
    assert st["statuses"]["node7"]["warning"] == "legacy storage unreadable under SYSTEM"
    # терминальный отчёт агента несёт warning повторно -> остаётся в статусе
    run(svc.agent_api(_req(key, "report",
                           {"stage": "done", "ok": True, "deploy_id": "d1",
                            "warning": "legacy storage unreadable under SYSTEM"})))
    st = run(svc.agent_status({"node": "node7"}))
    assert st["statuses"]["node7"]["warning"] == "legacy storage unreadable under SYSTEM"
    assert st["statuses"]["node7"]["stage"] == "done"


def test_report_warning_survives_report_without_key(tmp_path, monkeypatch):
    """(broad-fixes 4) отчёт БЕЗ ключа warning не затирает ранее накопленный:
    warning — накапливаемое состояние статуса, сбрасывается только новым deploy (open)."""
    svc, _, _ = make_deployer(tmp_path, monkeypatch)
    key, _ = svc._agent_registry.get_or_create("node7")
    run(svc.agent_api(_req(key, "report",
                           {"stage": "DETECT", "ok": True, "deploy_id": "d1",
                            "warning": "legacy storage unreadable under SYSTEM"})))
    run(svc.agent_api(_req(key, "report",
                           {"stage": "FETCH_EXE", "ok": True, "deploy_id": "d1"})))
    st = run(svc.agent_status({"node": "node7"}))
    assert st["statuses"]["node7"]["warning"] == "legacy storage unreadable under SYSTEM"
    assert st["statuses"]["node7"]["stage"] == "FETCH_EXE"


def test_node_seen_from_neighbor_table(tmp_path, monkeypatch):
    svc, _, ctx = make_deployer(tmp_path, monkeypatch)
    key, _ = svc._agent_registry.get_or_create("node7")
    r = _open(key, "node7", run(svc.agent_api(_req(key, "node_seen"))))
    assert r == {"ok": True, "seen": False}
    ctx.network.neighbor_table.entries["node7"] = _Info("connected")
    r = _open(key, "node7", run(svc.agent_api(_req(key, "node_seen"))))
    assert r == {"ok": True, "seen": True}
    ctx.network.neighbor_table.entries["node7"] = _Info("known")
    r = _open(key, "node7", run(svc.agent_api(_req(key, "node_seen"))))
    assert r == {"ok": True, "seen": False}
