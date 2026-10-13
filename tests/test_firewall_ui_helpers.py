from src.se.services.security_dashboard import policy_view as pv


def test_canon_rule_lower_and_sort():
    r = pv.canon_rule({"service": "Files", "methods": ["B", "a "], "allow_from": ["Trusted_Nodes "], "allow_to": ["SELF"]})
    assert r == {"service": "files", "methods": ["a", "b"], "allow_from": ["trusted_nodes"], "allow_to": ["self"]}


def test_rule_sig_case_insensitive():
    a = pv.rule_sig({"service": "files", "methods": ["A"], "allow_from": ["trusted_nodes"], "allow_to": ["self"]})
    b = pv.rule_sig({"service": "Files ", "methods": ["a"], "allow_from": ["Trusted_Nodes"], "allow_to": ["SELF"]})
    assert a == b


def test_enabled_rules_excludes_disabled():
    bundle = {"rules": [
        {"service": "files", "methods": [], "allow_from": ["trusted_nodes"], "allow_to": ["self"]},
        {"service": "logs", "methods": [], "allow_from": ["trusted_nodes"], "allow_to": ["self"]},
    ]}
    keep = {pv.rule_sig(bundle["rules"][0])}
    assert pv.enabled_rules(bundle, keep) == [pv.canon_rule(bundle["rules"][0])]


def test_diff_detects_missing_and_extra():
    template = {pv.rule_sig({"service": "files", "methods": [], "allow_from": ["trusted_nodes"], "allow_to": ["self"]})}
    node = {"rules": [{"service": "logs", "methods": [], "allow_from": ["trusted_nodes"], "allow_to": ["self"]}]}
    d = pv.diff_against_template(node, template)
    assert d["status"] == "drift"
    assert len(d["missing"]) == 1 and len(d["extra"]) == 1


def test_diff_sync():
    r = {"service": "files", "methods": [], "allow_from": ["trusted_nodes"], "allow_to": ["self"]}
    d = pv.diff_against_template({"rules": [r]}, {pv.rule_sig(r)})
    assert d == {"missing": [], "extra": [], "status": "sync"}


def test_next_version():
    assert pv.next_version(4) == 5


def test_should_reinit_only_without_draft_or_clean_version_change():
    assert pv.should_reinit(has_draft=False, dirty=True, draft_version=3, server_version=3) is True
    assert pv.should_reinit(has_draft=True, dirty=False, draft_version=3, server_version=4) is True
    assert pv.should_reinit(has_draft=True, dirty=False, draft_version=4, server_version=4) is False
    assert pv.should_reinit(has_draft=True, dirty=True, draft_version=3, server_version=4) is False


def test_stale_banner_dirty_divergence_or_flag():
    assert pv.stale_banner(dirty=True, draft_version=3, server_version=4, stale_flag=False) is True
    assert pv.stale_banner(dirty=False, draft_version=3, server_version=4, stale_flag=False) is False
    assert pv.stale_banner(dirty=True, draft_version=4, server_version=4, stale_flag=True) is True
    assert pv.stale_banner(dirty=False, draft_version=4, server_version=4, stale_flag=False) is False


def test_star_means_any_in_every_field():
    import src.se.services.security_dashboard.policy_view as pv2
    r = pv2.canon_rule({"service": "*", "methods": ["*"], "allow_from": ["*"], "allow_to": ["*"]})
    assert r == {"service": "*", "methods": [], "allow_from": [], "allow_to": []}


def test_draft_keys_namespaced_by_node():
    import src.se.services.security_dashboard.web_ui as w
    assert w.draft_key("fw_draft_rules", "AdminNode") == "fw_draft_rules:adminnode"
    assert w.draft_key("fw_draft_rules", "AdminNode") != w.draft_key("fw_draft_rules", "Node2")


def test_star_method_matches_any_method():
    from src.se.network.firewall import FirewallEngine
    from src.networking.protocol import MsgPack
    fw = FirewallEngine(rules_data={"defaults": {"deny_untrusted": True}, "rules": [
        {"service": "*", "methods": ["get_policy"], "allow_from": ["trusted_nodes"], "allow_to": []},
    ]})
    pack = MsgPack(service="security_dashboard", method="get_policy", source="n1")
    assert fw.check(pack, trusted_peers={"n1"}) is True
    pack2 = MsgPack(service="files", method="get_policy", source="n1")
    assert fw.check(pack2, trusted_peers={"n1"}) is True
