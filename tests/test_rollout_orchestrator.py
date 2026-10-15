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
async def test_seed_wave_failover_to_admin(tmp_path):
    import json
    u = make_updater(tmp_path)
    ver = '2.1.0'
    dist_ver = tmp_path / 'dist' / ver
    dist_ver.mkdir(parents=True)
    (dist_ver / 'manifest.json').write_text(
        json.dumps({'exe_sha256': 'cafef00d'}), encoding='utf-8')
    u._dist_dir = lambda: tmp_path / 'dist'
    calls = []

    async def fake_call(dst, service, method, data=None, timeout=60):
        calls.append((dst, service, method, data))
        if service == 'files' and method == 'download':
            # оба зеркала предыдущей волны битые: одно raise, другое non-ok
            if (data or {}).get('dst') == 'dead1':
                raise RuntimeError('mirror dead')
            return {'ok': False, 'error': 'gone'}
        if service == 'updater' and method == 'download':
            assert dst == 'c'
            return {'ok': True, 'version': ver}
        if service == 'files' and method == 'stat':
            return {'ok': True, 'manifest': {'id': 'cafef00d'}}
        raise AssertionError((dst, service, method))

    u.ctx.network = type('N', (), {'call': staticmethod(fake_call)})()
    ro = {'version': ver, 'cancelled': False,
          'state': {'version': ver, 'nodes': {
              'dead1': {'hop': 1, 'phase': 'seeded'},
              'dead2': {'hop': 1, 'phase': 'seeded'},
              'c': {'hop': 2, 'phase': 'pending'}}}}
    await u._rollout_seed_wave(ro, ['c'], ['dead1', 'dead2'])
    assert ro['state']['nodes']['c']['phase'] == 'seeded'
    dl_calls = [c for c in calls
                if c[0] == 'c' and c[1] == 'updater' and c[2] == 'download']
    assert dl_calls, 'нет отката на admin-direct (updater.download для c)'


@pytest.mark.asyncio
async def test_cancel_between_waves(tmp_path):
    from unittest.mock import AsyncMock
    u = make_updater(tmp_path, health_confirm_sec=0, health_confirm_retries=1)

    async def fake_phase0(ro):
        ro['state']['waves_seed'] = [['b'], ['c']]
        ro['state']['waves_apply'] = [['c'], ['b']]
        ro['state']['nodes'] = {'b': {'hop': 1, 'phase': 'pending'},
                                'c': {'hop': 2, 'phase': 'pending'}}
    u._rollout_phase0 = AsyncMock(side_effect=fake_phase0)
    u._rollout_ensure_all = AsyncMock()

    seed_calls = []

    async def fake_seed(ro, wave, mirrors):
        seed_calls.append(list(wave))
        # отмена прилетает сразу после первой волны
        ro['cancelled'] = True
        for n in wave:
            ro['state']['nodes'][n]['phase'] = 'seeded'

    u._rollout_seed_wave = AsyncMock(side_effect=fake_seed)
    u._rollout_apply = AsyncMock()
    ro = {'version': '2.1.0', 'cancelled': False,
          'state': {'version': '2.1.0', 'nodes': {}}}
    await u._rollout_run(ro)
    assert seed_calls == [['b']]  # вторая волна не сидировалась
    u._rollout_apply.assert_not_awaited()  # фаза B не стартовала
    assert ro['state']['cancelled'] is True


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
