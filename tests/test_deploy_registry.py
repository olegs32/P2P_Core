# Тесты ключевого реестра админа (services/deployer/key_registry.py)
from services.deployer.key_registry import DeployKeyRegistry, canon_node_id


def test_canon():
    assert canon_node_id("  Node7 ") == "node7"


def test_get_or_create_idempotent(tmp_path):
    reg = DeployKeyRegistry(tmp_path / "deploy")
    k1, created = reg.get_or_create("Node7")
    assert created and len(k1) == 32
    k2, created2 = reg.get_or_create("node7")      # регистр не создаёт второй ключ
    assert not created2 and k1 == k2
    assert reg.get("NODE7") == k1
    assert reg.get("unknown") is None


def test_rotate_invalidates_old(tmp_path):
    reg = DeployKeyRegistry(tmp_path)
    k1, _ = reg.get_or_create("n1")
    k2 = reg.rotate("n1")
    assert k2 != k1 and reg.get("n1") == k2


def test_list_hides_key_material(tmp_path):
    reg = DeployKeyRegistry(tmp_path)
    reg.get_or_create("n1")
    reg.get_or_create("n2")
    reg.touch("n1")
    lst = reg.list()
    assert set(lst) == {"n1", "n2"}
    assert "key" not in lst["n1"]
    assert lst["n1"]["last_used"] >= lst["n1"]["created"] > 0


def test_atomic_write_no_tmp_left(tmp_path):
    reg = DeployKeyRegistry(tmp_path)
    reg.get_or_create("n1")
    assert list(tmp_path.glob("*.tmp")) == []
    # файл json валиден
    import json
    d = json.loads((tmp_path / "n1.json").read_text(encoding="utf-8"))
    assert len(d["key"]) == 64
