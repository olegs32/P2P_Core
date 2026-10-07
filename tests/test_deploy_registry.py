# Тесты ключевого реестра админа (services/deployer/key_registry.py)
import json

import pytest

from services.deployer.key_registry import DeployKeyRegistry, canon_node_id

TRAVERSAL_IDS = ("..\\..\\name", "a/b", "C:x")


def test_canon():
    assert canon_node_id("  Node7 ") == "node7"


def test_canon_valid_ids():
    assert canon_node_id("node-1") == "node-1"
    assert canon_node_id("node_1") == "node_1"
    assert canon_node_id("node.1") == "node.1"


@pytest.mark.parametrize("bad", TRAVERSAL_IDS)
def test_canon_rejects_traversal(bad):
    with pytest.raises(ValueError):
        canon_node_id(bad)


@pytest.mark.parametrize("bad", TRAVERSAL_IDS)
def test_get_or_create_rejects_raw_traversal(tmp_path, bad):
    reg = DeployKeyRegistry(tmp_path)
    with pytest.raises(ValueError):
        reg.get_or_create(bad)
    assert list(tmp_path.iterdir()) == []


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
    d = json.loads((tmp_path / "n1.json").read_text(encoding="utf-8"))
    assert len(d["key"]) == 64


def test_get_raises_on_corrupt_file(tmp_path):
    reg = DeployKeyRegistry(tmp_path)
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        reg.get("bad")
    (tmp_path / "badhex.json").write_text(json.dumps({"key": "zz"}), encoding="utf-8")
    with pytest.raises(ValueError):
        reg.get("badhex")


def test_get_or_create_never_overwrites_corrupt_file(tmp_path):
    reg = DeployKeyRegistry(tmp_path)
    corrupt = tmp_path / "n1.json"
    corrupt.write_text("{broken", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        reg.get_or_create("n1")
    assert corrupt.read_text(encoding="utf-8") == "{broken"
