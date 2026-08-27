# tests/test_config_service.py — логика сервиса config без сети и рестартов

import asyncio
import logging
from pathlib import Path

import yaml

from src.internal_modules.config import ConfigManager
from services.config import service as cfg_service
from services.config.service import (
    SECRET_PLACEHOLDER,
    Config,
    MAX_BACKUPS,
)


SEED_YAML = """\
# тестовый конфиг
node: NodeSelf
network:
  host: 0.0.0.0
  port: 9000
logging:
  level: INFO
local:
  secret: top-secret-value
  work_dir: {work}
"""


def make_svc(tmp_path) -> tuple[Config, Path]:
    cfg_path = tmp_path / 'config.yaml'
    cfg_path.write_text(
        SEED_YAML.format(work=(tmp_path / 'work').as_posix()),
        encoding='utf-8')

    cm = ConfigManager(cfg_path)

    class Ctx:
        NODE = 'NodeSelf'

    ctx = Ctx()
    ctx.config = cm.cfg
    ctx.config_manager = cm

    svc = Config('config', ctx)
    svc.ctx = ctx
    return svc, cfg_path


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ #
#  get / маскирование секрета
# ------------------------------------------------------------------ #

def test_get_masks_secret(tmp_path):
    svc, _ = make_svc(tmp_path)
    res = svc.get({})
    assert res['ok'] is True
    assert SECRET_PLACEHOLDER in res['text']
    assert 'top-secret-value' not in res['text']
    assert res['backups'] == []
    assert res['mtime'] > 0


def test_get_without_secret(tmp_path):
    svc, path = make_svc(tmp_path)
    text = path.read_text(encoding='utf-8')
    path.write_text(text.replace('  secret: top-secret-value\n', ''),
                    encoding='utf-8')
    res = svc.get({})
    assert res['ok'] is True and SECRET_PLACEHOLDER not in res['text']


# ------------------------------------------------------------------ #
#  save: успех, валидация, конфликт
# ------------------------------------------------------------------ #

def test_save_valid_edit_syncs_live_objects(tmp_path):
    svc, path = make_svc(tmp_path)
    mtime = svc.get({})['mtime']

    edited = svc.get({})['text'].replace('port: 9000', 'port: 9001')
    res = run(svc.save({'text': edited, 'base_mtime': mtime}))

    assert res['ok'] is True
    assert res['restart_required'] is True
    assert 'network' in res['restart_required_sections']
    # файл записан
    on_disk = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert on_disk['network']['port'] == 9001
    # живые объекты синхронизированы (ctx.config и config_manager.cfg)
    assert svc.ctx.config.network.port == 9001
    assert svc.ctx.config_manager.cfg.network.port == 9001


def test_save_hot_logging_no_restart_required(tmp_path):
    svc, path = make_svc(tmp_path)
    mtime = svc.get({})['mtime']

    edited = svc.get({})['text'].replace('level: INFO', 'level: DEBUG')
    res = run(svc.save({'text': edited, 'base_mtime': mtime}))

    assert res['ok'] is True
    assert res['restart_required'] is False
    assert any('logging.level' in h for h in res['applied_hot'])
    assert logging.root.level == logging.DEBUG
    on_disk = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert on_disk['logging']['level'] == 'DEBUG'


def test_save_invalid_yaml_rejected(tmp_path):
    svc, path = make_svc(tmp_path)
    before = path.read_text(encoding='utf-8')
    res = run(svc.save({'text': 'node: [unclosed'}))
    assert res['ok'] is False and 'YAML' in res['error']
    assert path.read_text(encoding='utf-8') == before


def test_save_pydantic_errors_rejected(tmp_path):
    svc, path = make_svc(tmp_path)
    before = path.read_text(encoding='utf-8')

    edited = svc.get({})['text'].replace('port: 9000', "port: 'abc'")
    res = run(svc.save({'text': edited}))

    assert res['ok'] is False and res.get('errors')
    assert any('network.port' in e for e in res['errors'])
    assert path.read_text(encoding='utf-8') == before


def test_save_conflict_on_stale_mtime(tmp_path):
    svc, _ = make_svc(tmp_path)
    stale = svc.get({})['mtime'] - 100
    res = run(svc.save({'text': svc.get({})['text'], 'base_mtime': stale}))
    assert res['ok'] is False and res.get('conflict') is True


def test_save_warns_unknown_section_and_keeps_it(tmp_path):
    svc, path = make_svc(tmp_path)
    mtime = svc.get({})['mtime']
    edited = svc.get({})['text'] + '\nfuture_section:\n  x: 1\n'
    res = run(svc.save({'text': edited, 'base_mtime': mtime}))
    assert res['ok'] is True and res['warnings']
    on_disk = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert on_disk['future_section'] == {'x': 1}


def test_save_node_rename_warning(tmp_path):
    svc, _ = make_svc(tmp_path)
    mtime = svc.get({})['mtime']
    edited = svc.get({})['text'].replace('node: NodeSelf', 'node: Renamed')
    res = run(svc.save({'text': edited, 'base_mtime': mtime}))
    assert res['ok'] is True
    assert any('рестарта' in w for w in res['warnings'])


# ------------------------------------------------------------------ #
#  Секрет: roundtrip маски
# ------------------------------------------------------------------ #

def test_secret_placeholder_roundtrip(tmp_path):
    svc, path = make_svc(tmp_path)
    mtime = svc.get({})['mtime']
    masked = svc.get({})['text']

    res = run(svc.save({'text': masked, 'base_mtime': mtime}))
    assert res['ok'] is True
    on_disk = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert on_disk['local']['secret'] == 'top-secret-value'


def test_secret_cleared_explicitly(tmp_path):
    svc, path = make_svc(tmp_path)
    mtime = svc.get({})['mtime']
    masked = svc.get({})['text']
    edited = masked.replace(f'secret: {SECRET_PLACEHOLDER}', 'secret: null')
    res = run(svc.save({'text': edited, 'base_mtime': mtime}))
    assert res['ok'] is True
    on_disk = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert on_disk['local']['secret'] is None


def test_save_empty_with_placeholder_fails_when_no_real_secret(tmp_path):
    svc, path = make_svc(tmp_path)
    text = path.read_text(encoding='utf-8')
    path.write_text(text.replace('  secret: top-secret-value',
                                 '  secret: null'), encoding='utf-8')
    svc.ctx.config_manager.reload()
    svc.ctx.config = svc.ctx.config_manager.cfg

    mtime = svc.get({})['mtime']
    masked = svc.get({})['text'].replace(
        'secret: null', f'secret: {SECRET_PLACEHOLDER}')
    res = run(svc.save({'text': masked, 'base_mtime': mtime}))
    assert res['ok'] is False and 'маска' in res['error']


# ------------------------------------------------------------------ #
#  Бэкапы: создание, ротация, restore
# ------------------------------------------------------------------ #

def test_backup_created_and_restore_roundtrip(tmp_path):
    svc, path = make_svc(tmp_path)
    mtime = svc.get({})['mtime']

    edited = svc.get({})['text'].replace('port: 9000', 'port: 7000')
    assert run(svc.save({'text': edited, 'base_mtime': mtime}))['ok']

    backups = svc.backups({})['backups']
    assert len(backups) == 1
    name = backups[0]['name']

    # восстановить исходный порт из бэкапа
    res = run(svc.restore({'name': name}))
    assert res['ok'] is True and res['restored'] == name
    on_disk = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert on_disk['network']['port'] == 9000
    assert svc.ctx.config.network.port == 9000


def test_backup_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_service, 'MAX_BACKUPS', MAX_BACKUPS)
    svc, path = make_svc(tmp_path)

    for i in range(MAX_BACKUPS + 3):
        mtime = svc.get({})['mtime']
        edited = svc.get({})['text'].replace('port: 9000', f'port: {9001 + i}')
        res = run(svc.save({'text': edited, 'base_mtime': mtime}))
        assert res['ok'], res

    backups = svc.backups({})['backups']
    assert len(backups) <= MAX_BACKUPS


def test_restore_unknown_name_rejected(tmp_path):
    svc, _ = make_svc(tmp_path)
    res = run(svc.restore({'name': '../../etc/passwd'}))
    assert res['ok'] is False


def test_read_backup_masks_secret(tmp_path):
    svc, _ = make_svc(tmp_path)
    mtime = svc.get({})['mtime']
    run(svc.save({'text': svc.get({})['text'], 'base_mtime': mtime}))
    name = svc.backups({})['backups'][0]['name']
    res = svc.read_backup({'name': name})
    assert res['ok'] is True
    assert SECRET_PLACEHOLDER in res['text']
    assert 'top-secret-value' not in res['text']
