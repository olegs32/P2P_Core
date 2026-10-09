import json
import pytest

from services.deployer.agent.package import AgentConf

try:
    from tests.test_agent_api_integration import make_deployer, run, VER, EXE
except ModuleNotFoundError:
    # на этой машине site-packages содержит пакет 'tests' (shadowing) — sibling-import
    from test_agent_api_integration import make_deployer, run, VER, EXE


@pytest.fixture()
def agent_exe(tmp_path, monkeypatch):
    import services.deployer.service as ds
    # подложим агент- exe в тот же DIST, что и monkeypatch в make_deployer
    dist = tmp_path / "dist"
    src = dist / "DeployAgent.exe"
    # make_deployer создаст dist; монтим заранее
    dist.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"MZ-agent")
    monkeypatch.setenv("P2P_DUMMY_BUILD", "")     # сброс, если был
    return src


def test_gen_agent_package(tmp_path, monkeypatch):
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    (dist / "DeployAgent.exe").write_bytes(b"MZ-agent")
    res = run(svc.gen_agent_package({"targets": ["Node7"]}))
    assert res["ok"] and res["version"] == VER and res["mode"] == "open"
    assert res["admin_ws"].startswith("ws://")
    pkg = res["packages"][0]
    assert pkg["node"] == "node7" and pkg["key_status"] == "created"
    pdir = __import__("pathlib").Path(pkg["path"])
    assert pdir == dist / VER / "agent" / "node7"
    conf = AgentConf.load(pdir / "agent.conf")
    assert conf.node_id == "node7" and conf.version == VER and conf.mode == "open"
    assert len(conf.key) == 32 and conf.admin_node == ctx.NODE
    assert (pdir / "agent.exe").read_bytes() == b"MZ-agent"
    # идемпотентность ключа
    res2 = run(svc.gen_agent_package({"targets": ["Node7"]}))
    assert res2["packages"][0]["key_status"] == "existing"
    # ротация -> старые пакеты получат auth-fail
    run(svc.rotate_agent_key({"node": "node7"}))
    res3 = run(svc.gen_agent_package({"targets": ["node7"]}))
    assert res3["packages"][0]["key_status"] == "existing"
    assert res3["packages"][0]["path"]              # папка перезаписана новым conf


def test_gen_agent_package_errors(tmp_path, monkeypatch):
    svc, dist, _ = make_deployer(tmp_path, monkeypatch)
    res = run(svc.gen_agent_package({"targets": []}))
    assert res["ok"] is False and "targets" in res["error"]
    # артефакта агента нет -> business-error с инструкцией
    res = run(svc.gen_agent_package({"targets": ["n1"]}))
    assert res["ok"] is False and "compile.py --type agent" in res["error"]
    # dummy-режим создаёт артефакт
    monkeypatch.setenv("P2P_DUMMY_BUILD", "1")
    res = run(svc.gen_agent_package({"targets": ["n1"]}))
    assert res["ok"] is True
    # невалидный per-target port override -> бизнес-отказ (D9), не исключение
    res = run(svc.gen_agent_package({"targets": ["n1"], "ports": {"n1": "abc"}}))
    assert res["ok"] is False and "port" in res["error"]
    res = run(svc.gen_agent_package({"targets": ["n1"], "ports": {"n1": 99999}}))
    assert res["ok"] is False and "port" in res["error"]
    res = run(svc.gen_agent_package({"targets": ["n1"], "ports": "9101"}))
    assert res["ok"] is False and "ports" in res["error"]


def test_deploy_rpc_is_package_generation(tmp_path, monkeypatch):
    svc, dist, _ = make_deployer(tmp_path, monkeypatch)
    (dist / "DeployAgent.exe").write_bytes(b"MZ-agent")
    res = run(svc.deploy({"version": VER, "targets": ["Node7"]}))
    assert res["ok"] and res["packages"][0]["node"] == "node7"
    assert "note" in res


def test_deploy_one_removed():
    from services.deployer.service import Deployer
    assert not hasattr(Deployer, "_deploy_one")
    assert not hasattr(Deployer, "_remote_path_for")


def _gen_and_pre_config(svc, dist, base_cfg: dict, gen_data: dict, monkeypatch):
    """Базовый config.yaml изолируется (ds.ROOT -> tmp), затем gen + pre_config."""
    import yaml
    import services.deployer.service as ds
    root = dist.parent
    monkeypatch.setattr(ds, "ROOT", root)
    (root / "config.yaml").write_text(
        yaml.safe_dump(base_cfg, allow_unicode=True), encoding="utf-8")
    (dist / "DeployAgent.exe").write_bytes(b"MZ-agent")
    res = run(svc.gen_agent_package(gen_data))
    assert res["ok"], res
    target = res["packages"][0]["node"]
    pc = run(svc._build_pre_config(target, VER, dist / VER / "Node_P2P_Core.exe"))
    return res, yaml.safe_load(pc["config"])


def test_pre_config_injects_admin_peer(tmp_path, monkeypatch):
    """(broad-review 1а) конфиг жертвы обязан содержать админ-пир (uri == admin_ws):
    bootstrap ранее неизвестной ноды иначе не даёт node_seen на админе."""
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    res, cfg = _gen_and_pre_config(
        svc, dist,
        base_cfg={"node": "admin0", "network": {"port": 9000},
                  "local": {"peers": [
                      {"node_id": "node7", "uri": "ws://stale:9000"},  # self — убрать
                      {"node_id": "nodex", "uri": "ws://x:9000"},      # чужой — сохранить
                  ]}},
        gen_data={"targets": ["Node7"]}, monkeypatch=monkeypatch)
    peers = cfg["local"]["peers"]
    admin = [p for p in peers if p["node_id"] == "admin0"]
    assert admin == [{"node_id": "admin0", "uri": res["admin_ws"]}]
    assert not any(p["node_id"] == "node7" for p in peers)   # self не задублирован
    assert any(p["node_id"] == "nodex" for p in peers)       # чужие пиры не теряем
    assert cfg["network"]["port"] == 9000


def test_admin_peer_dedup_and_port_override(tmp_path, monkeypatch):
    """(broad-review 1б) админ уже в peers -> без дубля (uri актуализируется);
    per-target ports {node: port} -> network.port жертвы переопределён."""
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    res, cfg = _gen_and_pre_config(
        svc, dist,
        base_cfg={"node": "admin0", "network": {"port": 9000},
                  "local": {"peers": [{"node_id": "Admin0", "uri": "ws://old:9000"}]}},
        gen_data={"targets": ["Node7"], "ports": {"node7": 9101}},
        monkeypatch=monkeypatch)
    peers = cfg["local"]["peers"]
    admin = [p for p in peers if p["node_id"] == "admin0"]
    assert len(admin) == 1, f"админ-пир задублирован: {peers}"
    assert admin[0]["uri"] == res["admin_ws"]
    assert cfg["network"]["port"] == 9101
    assert cfg["node"] == "node7"
