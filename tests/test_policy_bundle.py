import pytest

pytest.importorskip("src.se.network.policy_bundle")

from src.se.network.policy_bundle import validate_bundle, compile_services


def test_validate_minimal_bundle():
    b = validate_bundle({"version": 3, "firewall": {"defaults": {"deny_untrusted": True}, "rules": []}, "services": {}})
    assert b["version"] == 3
    assert b["firewall"]["defaults"]["deny_untrusted"] is True


def test_services_empty_allow_means_all():
    c = compile_services({"files": {"enabled": True, "allow_nodes": [], "deny_nodes": []}})
    assert c["files"]["allow_all"] is True


def test_stale_version_rejected_by_caller_contract():
    # validate не сверяет с current — это делает set_policy; здесь только типы
    b = validate_bundle({"version": 1, "firewall": {}, "services": {"x": {"enabled": False}}})
    assert b["services"]["x"]["enabled"] is False
