import json
import re
import time
import uuid
from pathlib import Path

import pytest

from services.deployer.agent.package import (
    AgentConf, EnvelopeError, PreConfigError,
    sign_request, verify_sig, seal, open_envelope, validate_pre_config,
)

KEY = bytes.fromhex("ab" * 32)
OTHER = bytes.fromhex("cd" * 32)


def test_seal_open_roundtrip():
    env = seal(KEY, "node7", {"ok": True, "data": b"\x00\x01\xff"})
    assert env["v"] == 1 and env["alg"] == "A256GCM" and env["nid"] == "node7"
    assert isinstance(env["nonce"], bytes) and len(env["nonce"]) == 12
    assert isinstance(env["ct"], bytes)
    assert open_envelope(KEY, "node7", env) == {"ok": True, "data": b"\x00\x01\xff"}


def test_wrong_key_fails():
    env = seal(KEY, "node7", {"ok": True})
    with pytest.raises(EnvelopeError):
        open_envelope(OTHER, "node7", env)


def test_nid_mismatch_fails():
    env = seal(KEY, "node7", {"ok": True})
    with pytest.raises(EnvelopeError):
        open_envelope(KEY, "node8", env)          # чужой node_id -> AAD не сойдётся
    env2 = dict(env, nid="node8")
    with pytest.raises(EnvelopeError):
        open_envelope(KEY, "node7", env2)          # подмена nid в заголовке


def test_tampered_ct_fails():
    env = seal(KEY, "node7", {"ok": True})
    env["ct"] = env["ct"][:-1] + bytes([env["ct"][-1] ^ 0xFF])
    with pytest.raises(EnvelopeError):
        open_envelope(KEY, "node7", env)


def test_sig_verify():
    sig = sign_request(KEY, "ping", "node7", 1728000000.0, "aabbcc")
    assert verify_sig(KEY, "ping", "node7", 1728000000.0, "aabbcc", sig)
    assert not verify_sig(KEY, "ping", "node7", 1728000000.0, "aabbcc", "00" * 32)
    assert not verify_sig(OTHER, "ping", "node7", 1728000000.0, "aabbcc", sig)
    assert not verify_sig(KEY, "pre_config", "node7", 1728000000.0, "aabbcc", sig)  # другой op
    assert not verify_sig(KEY, "ping", "node7", 1728000000.0, "other-nonce", sig)


def _pc(**over):
    pc = {
        "v": 1, "mode": "se", "node": "node7", "deploy_id": str(uuid.uuid4()),
        "exp": time.time() + 3600, "config": "node: node7\n",
        "install": {"work_dir": r"C:\Core", "exe_name": "Node_P2P_Core.exe",
                    "full_path": r"C:\Core\Node_P2P_Core.exe"},
        "exe": {"name": "Node_P2P_Core.exe", "sha256": "ab", "size": 1, "version": "1.0.0"},
    }
    pc.update(over)
    return pc


def test_pre_config_ok():
    validate_pre_config(_pc(), "node7", now=time.time())


def test_pre_config_expired():
    with pytest.raises(PreConfigError):
        validate_pre_config(_pc(exp=time.time() - 1), "node7", now=time.time())


def test_pre_config_node_mismatch():
    with pytest.raises(PreConfigError):
        validate_pre_config(_pc(node="node8"), "node7", now=time.time())


def test_pre_config_missing_fields():
    pc = _pc()
    del pc["install"]
    with pytest.raises(PreConfigError):
        validate_pre_config(pc, "node7", now=time.time())


def test_conf_from_dict_ok_and_invalid():
    d = {"protocol": 1, "mode": "open", "admin_ws": "ws://10.0.0.1:9000",
         "admin_node": "node0", "node_id": "node7", "key": "ab" * 32,
         "version": "1.2.3-build45", "deploy_timeout": 120}
    conf = AgentConf.from_dict(d)
    assert conf.key == bytes.fromhex("ab" * 32)
    assert conf.deploy_timeout == 120 and conf.mode == "open"
    with pytest.raises(ValueError):
        AgentConf.from_dict({**d, "protocol": 2})
    with pytest.raises(ValueError):
        AgentConf.from_dict({**d, "key": "ab" * 16})
    with pytest.raises(ValueError):
        AgentConf.from_dict({**d, "mode": "secc"})
    with pytest.raises(ValueError):
        AgentConf.from_dict({**d, "admin_ws": "http://x"})


def test_pre_config_bad_v_type():
    # контракт: любое нечисловое v -> PreConfigError, не ValueError/TypeError
    for bad in ("x", {}, None, [1]):
        with pytest.raises(PreConfigError):
            validate_pre_config(_pc(v=bad), "node7", now=time.time())


def test_conf_bad_types():
    # контракт from_dict: любой битый тип -> ValueError (не TypeError)
    d = {"protocol": 1, "mode": "open", "admin_ws": "ws://10.0.0.1:9000",
         "admin_node": "node0", "node_id": "node7", "key": "ab" * 32,
         "version": "1.2.3-build45", "deploy_timeout": 120}
    for bad in ({}, "x", None, [1]):
        with pytest.raises(ValueError):
            AgentConf.from_dict({**d, "protocol": bad})
    with pytest.raises(ValueError):
        AgentConf.from_dict({**d, "deploy_timeout": {}})


def test_conf_load_roundtrip(tmp_path):
    d = {"protocol": 1, "mode": "open", "admin_ws": "ws://10.0.0.1:9000",
         "admin_node": "node0", "node_id": "node7", "key": "ab" * 32,
         "version": "1.2.3-build45", "deploy_timeout": 120}
    p = tmp_path / "agent.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    assert AgentConf.load(p) == AgentConf.from_dict(d)


def test_agent_dir_is_self_contained():
    # open-flavor guard: пакет агента автономен — на top-level импортов нет
    # ссылок на внутреннее дерево исходников узла. Исключение — wire-кодек:
    # src.networking.protocol/neighbor_table входят в agent-сборку
    # (AGENT_HIDDEN в compile.py) и нужны AgentApiClient для кадров.
    # Отступленные (ленивые, внутри функций/методов) импорты src.* разрешены —
    # open-сборка их просто не выполняет (например, storage-стратегии агента).
    agent_dir = Path(__file__).resolve().parents[1] / "services" / "deployer" / "agent"
    files = sorted(agent_dir.rglob("*.py"))
    assert files, "agent package sources not found"
    allowed = ("src.networking.protocol", "src.networking.neighbor_table")
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith(("from ", "import ")):
                continue
            # токен src (не подстрока "src."): ловит и `from src import se`,
            # и `import src`, и прочие формы без точки после src
            if not re.search(r"\bsrc\b", stripped):
                continue
            if len(line) - len(line.lstrip()) > 0:
                continue                          # ленивый импорт внутри функции — ок
            assert any(a in stripped for a in allowed), \
                f"{f.name}: node-internal src imports are forbidden in agent package: {stripped}"
