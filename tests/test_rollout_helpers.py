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
