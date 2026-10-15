def test_health_confirm_retries_default():
    from src.internal_modules.config import Config
    cfg = Config()
    assert cfg.update.health_confirm_retries == 3


def test_plan_waves_linear_chain():
    from src.internal_modules.rollout import plan_waves
    snap = {
        'nodes': [{'node_id': n} for n in ('admin', 'b', 'c')],
        'edges': [
            {'src': 'admin', 'dst': 'b'},
            {'src': 'b', 'dst': 'c'},
        ],
    }
    seed, apply = plan_waves(snap, 'admin')
    assert seed == [['b'], ['c']]
    assert apply == [['c'], ['b']]


def test_ensure_update_source_appends_and_idempotent():
    from src.internal_modules.rollout import ensure_update_source
    text = "node: b\nupdate:\n  sources: []\n"
    patched = ensure_update_source(text, 'AdminNode')
    assert patched is not None and 'adminnode' in patched  # lower-канон
    assert ensure_update_source(patched, 'AdminNode') is None


def test_ensure_cdn_share_appends_and_idempotent():
    from src.internal_modules.rollout import ensure_cdn_share
    text = "node: b\nfiles:\n  download_dir: downloads\n  shares: []\n"
    patched = ensure_cdn_share(text, 'downloads')
    assert patched is not None and 'cdn_update' in patched
    assert ensure_cdn_share(patched, 'downloads') is None


def test_journal_roundtrip(tmp_path):
    from src.internal_modules.rollout import RolloutJournal
    j = RolloutJournal(tmp_path, '2.1.0')
    state = {'nodes': {'b': {'phase': 'seeded'}}}
    j.write(state)
    assert (tmp_path / 'rollout_2.1.0.json').is_file()
    assert j.read() == state
