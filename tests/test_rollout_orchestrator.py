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
