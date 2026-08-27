# tests/test_updater_service.py — логика updater без сети и frozen-хвостов

import asyncio
from pathlib import Path

import pytest

from src.internal_modules.config import Config, UpdateConfig, UpdateSource
from services.updater.service import STATE_FILE, Updater, _sha256


class _Local:
    work_dir = None   # подставляется в фикстуре
    exe_name = 'Node_P2P_Core.exe'


def make_updater(tmp_path, **update_kw) -> Updater:
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


# ------------------------------------------------------------------ #
#  Переходы версий
# ------------------------------------------------------------------ #

def test_validate_transition(tmp_path):
    u = make_updater(tmp_path)

    ok, _ = u._validate_transition('2.0.0-build1', '2.1.0', False)
    assert ok is True

    ok, err = u._validate_transition('2.0.0', '2.0.0', False)
    assert ok is False and 'уже установлена' in err

    ok, err = u._validate_transition('2.1.0', '2.0.0', False)
    assert ok is False and 'запрещено' in err          # даунгрейд закрыт

    ok, _ = u._validate_transition('2.1.0', '2.0.0', True)
    assert ok is True                                  # allow_downgrade

    ok, _ = u._validate_transition('2.1.0', '2.0.0', False, force=True)
    assert ok is True                                  # ручной force


# ------------------------------------------------------------------ #
#  State-файл: pending → confirm
# ------------------------------------------------------------------ #

def test_state_roundtrip(tmp_path):
    u = make_updater(tmp_path)
    st = {'from': '2.0.0', 'to': '2.1.0', 'attempts': 0,
          'pending_boot_confirm': True, 'boot_ok': False}
    u._save_state(st)
    assert u._load_state() == st


def test_status_reports_current_and_config(tmp_path):
    u = make_updater(tmp_path)
    res = u.status({})
    assert res['ok'] is True
    assert isinstance(res['current'], str)
    assert res['config']['sources'] == ['AdminNode']
    assert res['state'] in (None, {})


def test_confirm_boot_marks_ok(tmp_path):
    """_confirm_boot_later ставит boot_ok после задержки."""
    u = make_updater(tmp_path)
    u._save_state({'from': '2.0.0', 'to': '2.1.0',
                   'pending_boot_confirm': True, 'boot_ok': False,
                   'attempts': 1})

    asyncio.run(u._confirm_boot_later(
        type('C', (), {'health_confirm_sec': 1})()))

    st = u._load_state()
    assert st['boot_ok'] is True and 'confirmed_at' in st


def test_start_increments_attempts_and_schedules_confirm(tmp_path):
    """Старт новой версии инкрементирует attempts и планирует подтверждение."""
    u = make_updater(tmp_path)
    u.ctx.config.update.health_confirm_sec = 3600   # в тесте не дожидаемся
    u._save_state({'from': '2.0.0', 'to': '2.1.0',
                   'pending_boot_confirm': True, 'boot_ok': False,
                   'attempts': 0})

    async def main():
        await u.start()
        st = u._load_state()
        assert st['attempts'] == 1
        # подчистить фоновые задачи до закрытия loop
        for t in u._tasks:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        assert all(t.cancelled() or t.done() for t in u._tasks)

    asyncio.run(main())


# ------------------------------------------------------------------ #
#  Хеш / подпись
# ------------------------------------------------------------------ #

def test_sha256_matches_files_transport(tmp_path):
    from services.files.service import _sha256_of
    f = tmp_path / 'a.bin'
    f.write_bytes(b'same-content' * 100)
    assert _sha256(f) == _sha256_of(f)


def test_verify_signature_shape(tmp_path):
    """Функция не падает: на Windows unsigned-файл не проходит проверку."""
    from services.updater.verify import verify_signature

    f = tmp_path / 'unsigned.exe'
    f.write_bytes(b'MZ fake binary')
    ok, detail = verify_signature(f)
    assert isinstance(ok, bool) and isinstance(detail, str)

    missing_ok, missing_detail = verify_signature(
        tmp_path / 'nope.exe')
    assert missing_ok is False and missing_detail


def test_state_file_name_constant():
    assert STATE_FILE == 'update_state.json'
