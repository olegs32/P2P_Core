# tests/test_files_service.py — юнит-тесты файлового транспорта

import asyncio
import hashlib
import os
import time
from pathlib import Path

import pytest

from src.internal_modules.config import Config, FilesConfig, ShareConfig
from services.files.service import (
    Files, STREAM_NAME, _chunk_file, _safe_join, _sha256_of,
)


# ------------------------------------------------------------------ #
#  Фикстуры
# ------------------------------------------------------------------ #

@pytest.fixture()
def share_dir(tmp_path):
    src = tmp_path / 'nas'
    src.mkdir()
    (src / 'movie.mp4').write_bytes(b'\x00\x01' * 300)          # 600 B
    (src / 'docs').mkdir()
    (src / 'docs' / 'report.txt').write_text('отчёт', encoding='utf-8')
    return src


class _Local:
    work_dir = None   # подставляется в фикстурах


def _make_ctx(tmp_path, share_dir, with_manager: bool):
    class Local:
        work_dir = tmp_path / 'work'

    cfg = Config(files=FilesConfig(
        shares=[ShareConfig(name='nas', path=str(share_dir),
                            allow=[], chunk_size=256)],
        download_dir=tmp_path / 'downloads',
    ))
    cfg.local.work_dir = tmp_path / 'work'

    class Ctx:
        NODE = 'NodeSelf'

    ctx = Ctx()
    ctx.config = cfg
    if with_manager:
        ctx.config_manager = FakeConfigManager(cfg)
    return ctx


class FakeConfigManager:
    """Эмуляция ConfigManager.update(): создаёт НОВЫЙ cfg-объект,
    как настоящий — проверяем синхронизацию ctx.config."""

    def __init__(self, cfg):
        self.cfg = cfg

    def update(self, **kwargs):
        data = self.cfg.model_dump(mode='json')
        for key, value in kwargs.items():
            parts = key.split('__')
            d = data
            for part in parts[:-1]:
                d = d.setdefault(part, {})
            d[parts[-1]] = value
        self.cfg = Config(**data)


@pytest.fixture()
def svc(tmp_path, share_dir):
    ctx = _make_ctx(tmp_path, share_dir, with_manager=False)
    service = Files('files', ctx)
    service.ctx = ctx
    return service


@pytest.fixture()
def svc_cm(tmp_path, share_dir):
    ctx = _make_ctx(tmp_path, share_dir, with_manager=True)
    service = Files('files', ctx)
    service.ctx = ctx
    return service


# ------------------------------------------------------------------ #
#  Безопасность путей
# ------------------------------------------------------------------ #

def test_safe_join_ok(tmp_path, share_dir):
    p = _safe_join(share_dir, 'docs/report.txt')
    assert p is not None and p.is_file()


def test_safe_join_traversal_denied(share_dir):
    assert _safe_join(share_dir, '../outside.txt') is None
    assert _safe_join(share_dir, 'docs/../../x') is None
    assert _safe_join(share_dir, str(Path('C:/abs/x'))) is None
    assert _safe_join(share_dir, '') is None


# ------------------------------------------------------------------ #
#  Чанкинг
# ------------------------------------------------------------------ #

def test_chunk_file_full_and_offset(share_dir):
    f = share_dir / 'movie.mp4'
    data = f.read_bytes()

    chunks = list(_chunk_file(f, 0, 256))
    assert b''.join(chunks) == data
    assert all(len(c) <= 256 for c in chunks)

    tail = list(_chunk_file(f, 500, 256))
    assert b''.join(tail) == data[500:]

    # offset за пределами файла — пусто
    assert list(_chunk_file(f, len(data) + 10, 256)) == []


# ------------------------------------------------------------------ #
#  Каталог и хеши
# ------------------------------------------------------------------ #

def test_list_shares_hides_local_paths(svc):
    res = svc.list_shares({})
    assert res['ok'] is True
    s = res['shares'][0]
    assert s['name'] == 'nas' and s['files'] == 2
    assert 'path' not in s and str(svc._shares()[0].path) not in str(res)


def test_find_and_stat_manifest(svc, share_dir):
    res = asyncio.run(svc.find({'pattern': '*.txt'}))
    entries = res['entries']
    assert [e['path'] for e in entries] == ['docs/report.txt']

    man = asyncio.run(
        svc.stat({'share': 'nas', 'path': 'docs/report.txt'}))['manifest']
    expected = hashlib.sha256((share_dir / 'docs/report.txt')
                              .read_bytes()).hexdigest()
    assert man['id'] == expected
    assert man['size'] == len('отчёт'.encode('utf-8'))
    assert man['chunk_size'] == 256          # из конфига шары


def test_hash_cache_invalidated_on_change(svc, share_dir):
    f = share_dir / 'docs' / 'report.txt'
    id1 = asyncio.run(
        svc.stat({'share': 'nas', 'path': 'docs/report.txt'}))['manifest']['id']

    time.sleep(0.01)
    f.write_text('другое содержимое', encoding='utf-8')

    id2 = asyncio.run(
        svc.stat({'share': 'nas', 'path': 'docs/report.txt'}))['manifest']['id']
    assert id1 != id2
    assert id2 == _sha256_of(f)


def test_stat_by_id(svc, share_dir):
    fid = asyncio.run(
        svc.stat({'share': 'nas', 'path': 'movie.mp4'}))['manifest']['id']
    res = asyncio.run(svc.stat({'id': fid}))
    assert res['ok'] is True
    assert res['manifest']['path'] == 'movie.mp4'


def test_stat_errors(svc):
    assert asyncio.run(svc.stat({'share': 'nope', 'path': 'x'}))['ok'] is False
    assert asyncio.run(
        svc.stat({'share': 'nas', 'path': '../secret'}))['ok'] is False
    assert asyncio.run(svc.stat({'id': 'ab' * 32}))['ok'] is False
    assert asyncio.run(
        svc.stat({'share': 'nas', 'path': 'missing.bin'}))['ok'] is False


# ------------------------------------------------------------------ #
#  read: маленькие файлы целиком (манифесты релизов)
# ------------------------------------------------------------------ #

def test_read_small_file_bytes(svc, share_dir):
    res = asyncio.run(svc.read({'share': 'nas', 'path': 'docs/report.txt'}))
    assert res['ok'] is True
    assert res['data'].decode('utf-8') == 'отчёт'
    assert res['size'] == len('отчёт'.encode('utf-8'))


def test_read_limit_enforced(svc, share_dir):
    # 600 Б > лимита 100 → отказ
    res = asyncio.run(svc.read({'share': 'nas', 'path': 'movie.mp4',
                                'limit': 100}))
    assert res['ok'] is False and 'лимит' in res['error']

    # ровно в лимит — ок
    ok_res = asyncio.run(svc.read({'share': 'nas', 'path': 'movie.mp4',
                                   'limit': 1024}))
    assert ok_res['ok'] is True and ok_res['size'] == 600


def test_read_traversal_and_missing(svc):
    assert asyncio.run(svc.read({'share': 'nas',
                                 'path': '../x'}))['ok'] is False
    assert asyncio.run(svc.read({'share': 'nas',
                                 'path': 'nope.txt'}))['ok'] is False


# ------------------------------------------------------------------ #
#  ACL
# ------------------------------------------------------------------ #

def test_acl(svc):
    class Share:
        name = 's'
        allow = []

    s_all = Share()
    assert svc._acl_ok(s_all, 'Anyone') is True     # пустой allow = всем

    s_restricted = Share()
    s_restricted.allow = ['NodeA']
    assert svc._acl_ok(s_restricted, 'NodeA') is True
    assert svc._acl_ok(s_restricted, 'NodeB') is False


# ------------------------------------------------------------------ #
#  Приём: finalize / fail / cancel
# ------------------------------------------------------------------ #

def _make_state(svc, tmp_content: bytes) -> dict:
    dl = svc._download_dir()
    final = dl / 'file.bin'
    tmp = final.with_name(final.name + '.part')
    dl.mkdir(parents=True, exist_ok=True)

    label = 'lbl-1234'
    svc._downloads[label] = {
        'label': label, 'src': 'NodeX', 'name': 'file.bin',
        'path': str(final), 'tmp': str(tmp),
        'id': hashlib.sha256(tmp_content).hexdigest(),
        'size': len(tmp_content), 'received': 0, 'resumed_from': 0,
        'status': 'running', 'error': '',
        'started_at': time.time(), 'finished_at': 0,
    }
    tmp.write_bytes(tmp_content)
    return svc._downloads[label]


def test_finalize_success_atomic_rename(svc):
    content = b'transferred payload'
    st = _make_state(svc, content)

    svc._finalize(st['label'], Path(st['tmp']))

    assert st['status'] == 'done'
    assert not Path(st['tmp']).exists()
    assert Path(st['path']).read_bytes() == content


def test_finalize_hash_mismatch_keeps_nothing(svc):
    st = _make_state(svc, b'corrupted data')
    st['id'] = hashlib.sha256(b'expected payload').hexdigest()  # ожидание другое

    svc._finalize(st['label'], Path(st['tmp']))

    assert st['status'] == 'error'
    assert 'sha256' in st['error']
    assert not Path(st['tmp']).exists()
    assert not Path(st['path']).exists()


def test_finalize_size_mismatch(svc):
    st = _make_state(svc, b'short')      # size в состоянии = 5, но заявлено другое
    st['size'] = 100

    svc._finalize(st['label'], Path(st['tmp']))

    assert st['status'] == 'error'
    assert 'размер' in st['error']


def test_downloads_listing_pct(svc):
    st = _make_state(svc, b'data')
    st['received'] = st['size'] // 2

    res = svc.downloads({})
    row = res['downloads'][0]
    assert row['pct'] == 50
    assert row['status'] == 'running'


def test_downloads_speed_window(svc):
    """speed_bps считается по окну и не утекает в RPC-ответ (deque)."""
    from collections import deque
    st = _make_state(svc, b'x' * 10)
    now = time.time()
    # 1000 байт за 2 секунды внутри окна → 500 Б/с
    win = deque(maxlen=64)
    for i in range(3):
        win.append((now - 2 + i, i * 500))
    st['received'] = 1000
    st['speed_win'] = win

    row = svc.downloads({})['downloads'][0]
    assert row['status'] == 'running'
    assert 400 <= row['speed_bps'] <= 600
    assert 'speed_win' not in row          # deque не уходит по сети

    # завершённая загрузка — скорость не считается
    done = _make_state(svc, b'y' * 10)
    done['status'] = 'done'
    assert svc.downloads({})['downloads'][0]['speed_bps'] == 0


def test_downloads_stale_speed_samples_ignored(svc):
    """Сэмплы старше окна не учитываются (загрузка зависла → скорость 0)."""
    from collections import deque
    st = _make_state(svc, b'z' * 10)
    now = time.time()
    st['speed_win'] = deque([(now - 60, 100), (now - 59, 900)])

    row = svc.downloads({})['downloads'][0]
    assert row['speed_bps'] == 0


def test_cancel_download_removes_part(svc):
    st = _make_state(svc, b'partial')

    res = svc.cancel_download({'label': st['label']})

    assert res['ok'] is True
    assert st['label'] not in svc._downloads
    assert not Path(st['tmp']).exists()


# ------------------------------------------------------------------ #
#  Имя сохранения: без путей
# ------------------------------------------------------------------ #

def test_target_path_strips_directories(svc, tmp_path):
    p = svc._target_path(r'..\evil\../../etc/passwd')
    assert p.parent == svc._download_dir()
    assert '/' not in p.name and '\\' not in p.name


# ------------------------------------------------------------------ #
#  Расшаривание из UI: add/remove/persist + браузер каталогов
# ------------------------------------------------------------------ #

def test_add_share_persists_and_visible(svc_cm, tmp_path):
    new_dir = tmp_path / 'movies'
    new_dir.mkdir()

    res = asyncio.run(svc_cm.add_share({'path': str(new_dir)}))

    assert res['ok'] is True
    entry = res['share']
    assert entry['name'] == 'movies'
    # виден сразу и через менеджер, и через ctx.config
    names_mgr = [s.name for s in
                 svc_cm._shares()]          # _cfg() читает config_manager.cfg
    assert names_mgr == ['nas', 'movies']
    assert [s.name for s in svc_cm.ctx.config.files.shares] == \
        ['nas', 'movies']


def test_add_share_custom_name_and_acl(svc_cm, tmp_path):
    new_dir = tmp_path / 'vault'
    new_dir.mkdir()

    res = asyncio.run(svc_cm.add_share({'path': str(new_dir), 'name': 'секрет',
                                        'allow': ['NodeA'],
                                        'chunk_size': 999999999}))

    assert res['ok'] is True
    s = next(s for s in svc_cm._shares() if s.name == 'секрет')
    assert s.allow == ['NodeA']
    assert s.chunk_size <= svc_cm._cfg().max_chunk      # потолок применён


def test_add_share_duplicates_rejected(svc_cm, share_dir, tmp_path):
    assert asyncio.run(svc_cm.add_share(
        {'path': str(share_dir)}))['ok'] is False  # имя nas занято

    nested_dup = tmp_path / 'dup'
    nested_dup.mkdir()
    r = asyncio.run(svc_cm.add_share({'path': str(nested_dup), 'name': 'x'}))
    assert r['ok'] is True
    asyncio.run(svc_cm.remove_share({'name': 'x'}))

    same_path_alias = asyncio.run(svc_cm.add_share(
        {'path': str(share_dir) + os.sep + '.', 'name': 'alias'}))
    assert same_path_alias['ok'] is False               # уже расшарена как nas


def test_add_share_not_a_directory(svc_cm, tmp_path):
    f = tmp_path / 'file.txt'
    f.write_text('x')
    assert asyncio.run(svc_cm.add_share({'path': str(f)}))['ok'] is False
    assert asyncio.run(svc_cm.add_share(
        {'path': str(tmp_path / 'nope')}))['ok'] is False
    assert asyncio.run(svc_cm.add_share({}))['ok'] is False


def test_remove_share_roundtrip(svc_cm):
    assert asyncio.run(svc_cm.remove_share({'name': 'nope'}))['ok'] is False

    res = asyncio.run(svc_cm.remove_share({'name': 'nas'}))
    assert res['ok'] is True
    assert [s.name for s in svc_cm._shares()] == []
    assert asyncio.run(
        svc_cm.stat({'share': 'nas', 'path': 'movie.mp4'}))['ok'] is False


def test_list_local_dirs_navigation(svc_cm, tmp_path, share_dir):
    # корневые точки — мгновенно, без опроса каждого диска I/O
    roots = asyncio.run(svc_cm.list_local_dirs({}))
    assert roots['ok'] is True and roots['parent'] is None
    assert len(roots['dirs']) > 0
    if os.name == 'nt':
        assert all(len(d) >= 3 and d[1:] == ':\\' for d in roots['dirs'])

    # навигация внутрь tmp_path (там подкаталог nas)
    step = asyncio.run(svc_cm.list_local_dirs({'path': str(tmp_path)}))
    assert step['ok'] is True
    assert any(d.endswith('nas') for d in step['dirs'])
    assert step['parent'] is not None            # у tmp_path есть родитель

    up = asyncio.run(svc_cm.list_local_dirs({'path': step['parent']}))
    assert up['ok'] is True

    # несуществующий путь
    assert asyncio.run(svc_cm.list_local_dirs(
        {'path': str(tmp_path / 'no_such')}))['ok'] is False


def test_without_config_manager_graceful(svc, tmp_path):
    # у svc нет config_manager → add_share честно отказывает
    d = tmp_path / 'x'
    d.mkdir()
    res = asyncio.run(svc.add_share({'path': str(d)}))
    assert res['ok'] is False
    assert 'конфиг' in res['error']


def test_win_drives_instant(svc_cm):
    """GetLogicalDrives не делает I/O по каждому диску (источник таймаута)."""
    if os.name != 'nt':
        pytest.skip('windows only')
    drives = Files._win_drives()
    assert isinstance(drives, list)
    for d in drives:
        assert d[1:] == ':\\'


# ------------------------------------------------------------------ #
#  Регистрация stream-обработчиков (регрессия: методы с '_' на имени
#  игнорировались get_stream_handlers → CHUNK dropped, ACK timeout)
# ------------------------------------------------------------------ #

def test_stream_handlers_registered(svc_cm):
    from services.rpc import get_stream_handlers
    from services.files.service import STREAM_NAME as SN

    h = get_stream_handlers(svc_cm)
    assert SN in h, f'стрим {SN} не зарегистрирован'
    assert 'wrapper' in h[SN] and 'consumer' in h[SN]


def test_stream_handler_names_not_private():
    import inspect
    from services.files.service import Files
    for name in ('prepare_file_in', 'consume_file_in'):
        fn = getattr(Files, name, None)
        assert fn is not None, f'метод {name} пропал'
        assert not name.startswith('_'), \
            'get_stream_handlers пропускает приватные имена!'
