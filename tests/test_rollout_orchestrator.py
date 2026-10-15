import asyncio

import pytest

from src.internal_modules.config import Config, UpdateConfig, UpdateSource
from src.internal_modules.updater import Updater


def make_updater(tmp_path, **update_kw):
    class Ctx:
        NODE = 'NodeSelf'
    kw = {'health_confirm_sec': 5, 'auto_check': False}
    kw.update(update_kw)
    cfg = Config(update=UpdateConfig(
        sources=[UpdateSource(node='AdminNode', share='releases')],
        **kw))
    cfg.local.work_dir = tmp_path / 'work'
    ctx = Ctx()
    ctx.config = cfg
    svc = Updater('updater', ctx)
    svc.ctx = ctx
    return svc


@pytest.mark.asyncio
async def test_rollout_start_rejects_concurrent(tmp_path):
    u = make_updater(tmp_path)
    # Живой (незавершённый) таск — только так _rollout считается активным:
    # _done(None) is True, поэтому 'task': None означал бы "завершён".
    live = asyncio.create_task(asyncio.sleep(3600))
    u._rollout = {'plan_id': 'x', 'task': live, 'cancelled': False, 'state': {}}
    try:
        res = await u.rollout_start({'version': '9.9.9'})
        assert res.get('ok') is False and 'уже идёт' in res.get('error', '')
    finally:
        live.cancel()
        try:
            await live
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_ensure_appends_source_and_cdn(tmp_path):
    u = make_updater(tmp_path)
    calls = {}

    async def fake_call(dst, service, method, data=None, timeout=10):
        if service == 'config' and method == 'get':
            return {'ok': True, 'text': 'node: b\nupdate:\n  sources: []\nfiles:\n  shares: []\n', 'mtime': 1.0}
        if service == 'config' and method == 'save':
            calls['saved'] = data['text']
            return {'ok': True}
        raise AssertionError((service, method))

    u.ctx.network = type('N', (), {'call': staticmethod(fake_call)})()
    res = await u._rollout_ensure_node('b', admin='adminnode', share='releases', download_dir='downloads')
    assert res['phase'] == 'pending'
    assert 'adminnode' in calls['saved'] and 'cdn_update' in calls['saved']


@pytest.mark.asyncio
async def test_phase0_status_fanout_before_versions(tmp_path):
    u = make_updater(tmp_path)

    async def fake_call(dst, service, method, data=None, timeout=10):
        if service == 'netinfo' and method == 'topology':
            return {'ok': True, 'edges': [
                {'src': 'NodeSelf', 'dst': 'a'},
                {'src': 'a', 'dst': 'b'},
            ]}
        if service == 'updater' and method == 'status':
            if dst == 'a':
                return {'ok': True, 'current': '2.0.0'}
            raise RuntimeError('boom')
        raise AssertionError((service, method, dst))

    u.ctx.network = type('N', (), {'call': staticmethod(fake_call)})()
    ro = {'version': '9.9.9', 'include_self': False,
          'state': {'version': '9.9.9', 'nodes': {}}}
    await u._rollout_phase0(ro)
    nodes = ro['state']['nodes']
    assert nodes['a'].get('version_before') == '2.0.0'
    assert nodes['a']['phase'] == 'pending'
    assert nodes['b']['phase'] == 'unreachable'


@pytest.mark.asyncio
async def test_ensure_retry_uses_fresh_download_dir(tmp_path):
    u = make_updater(tmp_path)
    saves = []
    gets = {'n': 0}

    async def fake_call(dst, service, method, data=None, timeout=10):
        if service == 'config' and method == 'get':
            gets['n'] += 1
            if gets['n'] == 1:
                return {'ok': True,
                        'text': 'node: b\nupdate:\n  sources: []\nfiles:\n  download_dir: old_dl\n  shares: []\n',
                        'mtime': 1.0}
            return {'ok': True,
                    'text': 'node: b\nupdate:\n  sources: []\nfiles:\n  download_dir: new_dl\n  shares: []\n',
                    'mtime': 2.0}
        if service == 'config' and method == 'save':
            saves.append(data['text'])
            if len(saves) == 1:
                return {'ok': False, 'conflict': True}
            return {'ok': True}
        raise AssertionError((service, method))

    u.ctx.network = type('N', (), {'call': staticmethod(fake_call)})()
    res = await u._rollout_ensure_node('b', admin='adminnode', share='releases', download_dir='downloads')
    assert res['phase'] == 'pending'
    assert len(saves) == 2
    assert 'new_dl' in saves[1]


@pytest.mark.asyncio
async def test_canary_failure_aborts_fanout(tmp_path):
    from unittest.mock import patch

    import src.internal_modules.updater as upd_mod
    u = make_updater(tmp_path, health_confirm_sec=0, health_confirm_retries=1)
    seen = []

    async def fake_call(dst, service, method, data=None, timeout=300):
        seen.append((dst, method))
        if method == 'apply':
            return {'ok': True, 'note': 'устанавливается'}
        if service == 'updater' and method == 'status':
            return {'ok': True, 'current': '2.0.0',
                    'state': {'pending_boot_confirm': True, 'boot_ok': False, 'rolled_back': True, 'to': '2.1.0'}}
        raise AssertionError((service, method))
    u.ctx.network = type('N', (), {'call': staticmethod(fake_call)})()
    state = {'version': '2.1.0', 'nodes': {'b': {'hop': 1, 'phase': 'seeded'}, 'c': {'hop': 2, 'phase': 'seeded'}}}
    with patch.object(upd_mod, 'ROLLOUT_POLL_SEC', 0):
        await u._rollout_apply(state, admin='a')
    assert state['nodes']['b']['phase'] == 'rolled_back'
    assert state['nodes']['c']['phase'] == 'seeded'  # веер не стартовал
    assert not [s for s in seen if s == ('c', 'apply')]


@pytest.mark.asyncio
async def test_apply_status_mapping(tmp_path):
    from unittest.mock import patch

    import src.internal_modules.updater as upd_mod
    u = make_updater(tmp_path, health_confirm_sec=0, health_confirm_retries=1)

    async def fake_call(dst, service, method, data=None, timeout=300):
        if method == 'apply':
            return {'ok': True, 'note': 'устанавливается'}
        if service == 'updater' and method == 'status':
            if dst == 'b':
                return {'ok': True, 'current': '2.1.0', 'state': {'boot_ok': True, 'to': '2.1.0'}}
            return {'ok': True, 'current': '2.0.0',
                    'state': {'pending_boot_confirm': True, 'boot_ok': False, 'rolled_back': True, 'to': '2.1.0'}}
        raise AssertionError((service, method))
    u.ctx.network = type('N', (), {'call': staticmethod(fake_call)})()
    state = {'version': '2.1.0', 'nodes': {'b': {'hop': 1, 'phase': 'seeded'}, 'c': {'hop': 2, 'phase': 'seeded'}}}
    with patch.object(upd_mod, 'ROLLOUT_POLL_SEC', 0):
        await u._rollout_apply(state, admin='a')
    assert state['nodes']['b']['phase'] == 'updated'
    assert state['nodes']['c']['phase'] == 'rolled_back'


@pytest.mark.asyncio
async def test_rollout_run_resumes_from_journal(tmp_path):
    from unittest.mock import AsyncMock, patch

    import src.internal_modules.updater as upd_mod
    from src.internal_modules.rollout import RolloutJournal
    u = make_updater(tmp_path, health_confirm_sec=0, health_confirm_retries=1)
    # fake journal: same version, b already updated, c seeded
    j = RolloutJournal(u._work_dir(), '2.1.0')
    j.write({'version': '2.1.0', 'nodes': {
        'b': {'hop': 1, 'phase': 'updated'},
        'c': {'hop': 2, 'phase': 'seeded'}}})

    async def fake_phase0(ro):
        ro['state']['waves_seed'] = [['b', 'd'], ['c']]
        ro['state']['waves_apply'] = [['c'], ['b', 'd']]
        ro['state']['nodes'] = {'b': {'hop': 1, 'phase': 'pending'},
                                'c': {'hop': 2, 'phase': 'pending'},
                                'd': {'hop': 1, 'phase': 'pending'}}
    u._rollout_phase0 = AsyncMock(side_effect=fake_phase0)
    u._rollout_ensure_all = AsyncMock()
    u._rollout_seed_wave = AsyncMock()
    u._rollout_apply = AsyncMock()
    ro = {'version': '2.1.0', 'cancelled': False,
          'state': {'version': '2.1.0', 'nodes': {}}}
    with patch.object(upd_mod, 'ROLLOUT_POLL_SEC', 0):
        await u._rollout_run(ro)
    # resume restore: wave1 todo skips updated b → only fresh d is seeded;
    # wave2 (c, already seeded) has empty todo → seed_wave not called for it
    assert u._rollout_seed_wave.await_count == 1
    seed_args = u._rollout_seed_wave.await_args.args
    assert seed_args[1] == ['d']
    assert seed_args[2] == []
    # apply sees restored phases: b stays updated, c stays seeded
    u._rollout_apply.assert_awaited_once()
    apply_state = u._rollout_apply.await_args.args[0]
    assert apply_state['nodes']['b']['phase'] == 'updated'
    assert apply_state['nodes']['c']['phase'] == 'seeded'


@pytest.mark.asyncio
async def test_seed_rejects_bad_mirror_sha(tmp_path):
    u = make_updater(tmp_path)

    async def fake_call(dst, service, method, data=None, timeout=60):
        if service == 'files' and method == 'stat':
            return {'ok': True, 'manifest': {'id': 'deadbeef'}}
        if service == 'files' and method == 'downloads':
            return {'ok': True, 'downloads': [{'label': 'L', 'status': 'done'}]}
        if service == 'files' and method == 'download':
            return {'ok': True, 'label': 'L', 'size': 10}
        raise AssertionError((service, method))

    u.ctx.network = type('N', (), {'call': staticmethod(fake_call)})()
    res = await u._rollout_seed_node('c', mirror='b', staged='2.1.0_exe',
                                     expect_sha='cafef00d', version='2.1.0')
    assert res['phase'] == 'seed_failed'  # зеркало битое — не раздаем дальше
