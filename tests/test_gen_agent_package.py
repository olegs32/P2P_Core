import json
from pathlib import Path

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
    """(broad-review 1а) конфиг жертвы обязан содержать админ-пир с каноническим
    uri (ws://h:port/ws/ — main.py склеивает peer.uri + NODE): bootstrap ранее
    неизвестной ноды иначе не даёт node_seen на админе.

    agent.conf при этом хранит admin_ws в исходной bare-форме: агент сам
    дописывает /ws/<src> (machine.py:302), нормализация нужна ТОЛЬКО в peers.
    """
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
    assert admin == [{"node_id": "admin0", "uri": "ws://127.0.0.1:9000/ws/"}]
    assert res["admin_ws"] == "ws://127.0.0.1:9000"          # bare, без /ws/
    # agent.conf жертвы несёт admin_ws той же bare-формой, как её передали
    conf = AgentConf.load(Path(res["packages"][0]["path"]) / "agent.conf")
    assert conf.admin_ws == "ws://127.0.0.1:9000"
    assert not any(p["node_id"] == "node7" for p in peers)   # self не задублирован
    assert any(p["node_id"] == "nodex" for p in peers)       # чужие пиры не теряем
    assert cfg["network"]["port"] == 9000


def test_admin_peer_uri_never_bare(tmp_path, monkeypatch):
    """(broad-fixes 2.1) существующий админ-пир не затирается сломанным bare-адресом:
    и инжект, и dedup-ветка пишут канонический ws://h:port/ws/."""
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    _, cfg = _gen_and_pre_config(
        svc, dist,
        base_cfg={"node": "admin0", "network": {"port": 9000},
                  "local": {"peers": [
                      {"node_id": "Admin0", "uri": "ws://10.0.0.5:9000/ws/"}]}},
        gen_data={"targets": ["Node7"]}, monkeypatch=monkeypatch)
    admin = [p for p in cfg["local"]["peers"] if p["node_id"] == "admin0"]
    assert len(admin) == 1, f"админ-пир задублирован: {cfg['local']['peers']}"
    assert admin == [{"node_id": "admin0", "uri": "ws://127.0.0.1:9000/ws/"}]
    # повторный gen поверх канонического peer — uri остаётся каноническим
    _, cfg2 = _gen_and_pre_config(
        svc, dist,
        base_cfg={"node": "admin0", "network": {"port": 9000},
                  "local": {"peers": list(cfg["local"]["peers"])}},
        gen_data={"targets": ["Node7"]}, monkeypatch=monkeypatch)
    admin2 = [p for p in cfg2["local"]["peers"] if p["node_id"] == "admin0"]
    assert admin2 == [{"node_id": "admin0", "uri": "ws://127.0.0.1:9000/ws/"}]
    assert all(p["uri"].rstrip("/").endswith("/ws")
               for p in cfg2["local"]["peers"] if p["node_id"] == "admin0")


def test_ports_override_scoped_to_gen_session(tmp_path, monkeypatch):
    """(broad-fixes 3) ports-override прошлого деплоя не протекает в следующий gen:
    после gen без ports жертва получает дефолтный порт из config.yaml."""
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    base = {"node": "admin0", "network": {"port": 9000}, "local": {"peers": []}}
    _, cfg1 = _gen_and_pre_config(
        svc, dist, base,
        {"targets": ["Node7"], "ports": {"node7": 9101}}, monkeypatch=monkeypatch)
    assert cfg1["network"]["port"] == 9101
    _, cfg2 = _gen_and_pre_config(
        svc, dist, base, {"targets": ["Node7"]}, monkeypatch=monkeypatch)
    assert cfg2["network"]["port"] == 9000, "override прошлого деплоя протёк в gen"


def test_admin_peer_dedup_and_port_override(tmp_path, monkeypatch):
    """(broad-review 1б) админ уже в peers -> без дубля (uri актуализируется);
    per-target ports {node: port} -> network.port жертвы переопределён."""
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    _, cfg = _gen_and_pre_config(
        svc, dist,
        base_cfg={"node": "admin0", "network": {"port": 9000},
                  "local": {"peers": [{"node_id": "Admin0", "uri": "ws://old:9000"}]}},
        gen_data={"targets": ["Node7"], "ports": {"node7": 9101}},
        monkeypatch=monkeypatch)
    peers = cfg["local"]["peers"]
    admin = [p for p in peers if p["node_id"] == "admin0"]
    assert len(admin) == 1, f"админ-пир задублирован: {peers}"
    # uri актуализируется в канонический вид, а не копируется bare admin_ws
    assert admin[0]["uri"] == "ws://127.0.0.1:9000/ws/"
    assert cfg["network"]["port"] == 9101
    assert cfg["node"] == "node7"
