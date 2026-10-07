# tests/test_control_panel_rpc.py — Task 2 (network-control): dashboard connections/policy RPC.
# SE imports via importorskip per repo ruling (src/se/ is git-ignored by design).
import pytest

pb = pytest.importorskip("src.se.network.policy_bundle")
validate_bundle = pb.validate_bundle


def test_bundle_contract_for_dashboard():
    b = validate_bundle({"version": 5, "firewall": {"defaults": {"deny_untrusted": True}, "rules": [{"service": "files", "methods": ["download"], "allow_from": ["trusted_nodes"], "allow_to": []}]}, "services": {"files": {"enabled": True}}})
    assert b["version"] == 5
    assert b["firewall"]["rules"][0]["service"] == "files"


def test_dashboard_has_new_rpcs():
    m = pytest.importorskip("src.se.services.security_dashboard.service")
    for name in ["get_connections", "get_policy", "set_policy", "fleet_status", "push_policy"]:
        assert hasattr(m.SecurityDashboard, name), f"missing {name}"
        assert getattr(getattr(m.SecurityDashboard, name), "_is_rpc", False) is True
