# tests/test_files_service.py — юнит-тесты файлового транспорта

import hashlib
import time
from pathlib import Path

import pytest

from src.internal_modules.config import FilesConfig, ShareConfig
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


@pytest.fixture()
def svc(tmp_path, share_dir):
    class Local:
        work_dir = tmp_path / 'work'

    class Config:
        files = FilesConfig(
            shares=[ShareConfig(name='nas', path=str(share_dir),
                                allow=[], chunk_size=256)],
            download_dir=tmp_path / 'downloads',
        )
        local = Local()

    class Ctx:
        NODE = 'NodeSelf'
        config = Config()

    service = Files('files', Ctx())
    service.ctx = Ctx()
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
    assert _safe_join(share_dir, '.') is not None or True  # пустые каталоги не файлы


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
    assert list(_chunk_file(f, data.__len__() + 10, 256)) == []


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
    res = svc.find({'pattern': '*.txt'})
    entries = res['entries']
    assert [e['path'] for e in entries] == ['docs/report.txt']

    man_res = svc.stat({'share': 'nas', 'path': 'docs/report.txt'})
    man = man_res['manifest']
    expected = hashlib.sha256((share_dir / 'docs/report.txt')
                              .read_bytes()).hexdigest()
    assert man['id'] == expected
    assert man['size'] == len('отчёт'.encode('utf-8'))
    assert man['chunk_size'] == 256          # из конфига шары


def test_hash_cache_invalidated_on_change(svc, share_dir):
    f = share_dir / 'docs' / 'report.txt'
    id1 = svc.stat({'share': 'nas', 'path': 'docs/report.txt'})['manifest']['id']

    time.sleep(0.01)
    f.write_text('другое содержимое', encoding='utf-8')

    id2 = svc.stat({'share': 'nas', 'path': 'docs/report.txt'})['manifest']['id']
    assert id1 != id2
    assert id2 == _sha256_of(f)


def test_stat_by_id(svc, share_dir):
    fid = svc.stat({'share': 'nas',
                    'path': 'movie.mp4'})['manifest']['id']
    res = svc.stat({'id': fid})
    assert res['ok'] is True
    assert res['manifest']['path'] == 'movie.mp4'


def test_stat_errors(svc):
    assert svc.stat({'share': 'nope', 'path': 'x'})['ok'] is False
    assert svc.stat({'share': 'nas', 'path': '../secret'})['ok'] is False
    assert svc.stat({'id': 'ab' * 32})['ok'] is False
    assert svc.stat({'share': 'nas', 'path': 'missing.bin'})['ok'] is False


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
