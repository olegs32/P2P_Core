import time
import pytest

from services.deployer.agent.package import sign_request, AUTH_FAILED, BUSY
from services.deployer.agent_api import AgentAuth, BusyError, AgentSessions, BUSY_GRACE
from services.deployer.key_registry import DeployKeyRegistry


@pytest.fixture()
def auth(tmp_path):
    reg = DeployKeyRegistry(tmp_path)
    key, _ = reg.get_or_create("node7")
    a = AgentAuth(reg)
    return a, key


def _req(key, op="ping", node="node7", ts=None, nonce="n-abc", body=None):
    ts = time.time() if ts is None else ts
    return {"op": op, "node_id": node, "ts": ts, "nonce": nonce,
            "sig": sign_request(key, op, node, ts, nonce), "body": body or {}}


def test_valid_request(auth):
    a, key = auth
    got_key, err = a.validate(_req(key))
    assert err is None and got_key == key


def test_bad_sig_uniform(auth):
    a, key = auth
    r = _req(key)
    r["sig"] = "ff" * 32
    k, err = a.validate(r)
    assert err == AUTH_FAILED and k == key      # ключ известен, сообщение единообразно


def test_unknown_node_uniform(auth):
    a, _ = auth
    # ключ другой, в реестре нет — тот же текст, plaintext-ответ соберёт админ
    k, err = a.validate(_req(bytes.fromhex("cd" * 32), node="ghost"))
    assert err == AUTH_FAILED and k is None


def test_stale_and_future_ts_rejected(auth):
    a, key = auth
    _, err = a.validate(_req(key, ts=time.time() - 400))
    assert err == AUTH_FAILED
    _, err = a.validate(_req(key, ts=time.time() + 400, nonce="n-fut"))
    assert err == AUTH_FAILED
    # в пределах окна — ок
    _, err = a.validate(_req(key, ts=time.time() - 200, nonce="n-ok"))
    assert err is None


def test_nonce_replay_rejected(auth):
    a, key = auth
    r = _req(key, nonce="once")
    assert a.validate(r)[1] is None
    assert a.validate(r)[1] == AUTH_FAILED      # повтор nonce


def test_op_bound_sig(auth):
    a, key = auth
    r = _req(key, op="ping")
    r["op"] = "pre_config"                      # подмена op после подписи
    assert a.validate(r)[1] == AUTH_FAILED


def test_case_flip_sig_rejected_but_lookup_canon(auth):
    a, key = auth
    r = _req(key, node="Node7")                 # подпись по RAW 'Node7'
    assert a.validate(r)[1] is None             # canon-lookup находит тот же ключ
    r2 = dict(r, node_id="NODE7")               # а подпись для другого написания не подходит
    assert a.validate(r2)[1] == AUTH_FAILED


def test_rate_limit_control_bucket(auth):
    a, key = auth
    for i in range(60):
        _, err = a.validate(_req(key, nonce=f"c{i}"))
        assert err is None
    _, err = a.validate(_req(key, nonce="c61"))
    assert err == "rate limited"


def test_rate_limit_fetch_bucket_separate(tmp_path):
    reg = DeployKeyRegistry(tmp_path)
    key, _ = reg.get_or_create("node7")
    a = AgentAuth(reg)
    for i in range(60):                         # контрол исчерпан
        a.validate(_req(key, op="ping", nonce=f"p{i}"))
    assert a.validate(_req(key, op="ping", nonce="p61"))[1] == "rate limited"
    for i in range(600):                        # fetch-bucket свой
        _, err = a.validate(_req(key, op="fetch_chunk", nonce=f"f{i}"))
        assert err is None
    assert a.validate(_req(key, op="fetch_chunk", nonce="f601"))[1] == "rate limited"


def test_malformed_requests_rejected(auth):
    a, key = auth
    for bad in (None, "str", {}, {"op": "ping"},                    # без node/ts/nonce/sig
                {"op": "ping", "node_id": "node7", "ts": "x", "nonce": "n", "sig": "ab"}):
        if not isinstance(bad, dict):
            bad = bad or {}
            continue
        assert a.validate(bad)[1] in (AUTH_FAILED,)


def test_sessions_busy_and_terminal(tmp_path):
    s = AgentSessions()
    s.open("node7", "d1", "1.0.0", "C:/x.exe")
    with pytest.raises(BusyError):
        s.open("node7", "d2", "1.0.0", "C:/x.exe")   # живая busy-сессия
    s.record_report("node7", {"stage": "FETCH_EXE", "ok": True, "deploy_id": "d1"})
    assert s.status("node7")["node7"]["history"] == ["FETCH_EXE"]
    s.extend("node7")
    st = s.record_report("node7", {"stage": "done", "ok": True, "deploy_id": "d1"})
    assert s.get("node7") is None                      # терминальная стадия закрыла сессию
    assert s.status("node7")["node7"]["history"][-1] == "done"
    s.open("node7", "d3", "1.0.0", "C:/x.exe")         # после done — новая сессия ок


def test_sessions_grace_expiry(tmp_path):
    clock = {"t": 1000.0}
    s = AgentSessions(now=lambda: clock["t"])
    s.open("n", "d", "v", "p")
    clock["t"] += BUSY_GRACE + 1
    s.open("n", "d2", "v", "p")                        # grace истёк — можно снова
