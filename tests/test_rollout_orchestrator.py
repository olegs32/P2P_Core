import asyncio

import pytest

from src.internal_modules.config import Config, UpdateConfig, UpdateSource
from src.internal_modules.updater import Updater


def make_updater(tmp_path, **update_kw):
    class Ctx:
        NODE = 'NodeSelf'
    cfg = Config(update=UpdateConfig(
        sources=[UpdateSource(node='AdminNode', share='releases')],
        health_confirm_sec=5,
        auto_check=False,
        **update_kw))
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
