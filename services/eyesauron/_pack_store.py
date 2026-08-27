# services/eyesauron/_pack_store.py — пакованное дедуп-хранилище EyeSauron
# Реализация спеки docs/eyesauron_storage.md (chunker v1 = grid256).
#
# Топология: локальный root (staging + кэш готовых томов + карты + манифест),
# NAS root (зеркало готовых томов + манифеста). Инжест никогда не ждёт NAS.
#
# Файлы тома: vol-<node>-<seq>.pack / .idx / .bloom — иммутабельны после seal.
# Активный том: volumes/staging.pack + staging.journal (коммит на каждый кадр).
#
# Модуль не загружается ServiceLoader ('_'-префикс); все методы синхронные,
# сервис вызывает их через asyncio.to_thread; единственный писатель — RLock.

import hashlib
import io
import json
import logging
import os
import re
import struct
import threading
import time
from collections import OrderedDict
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
except ImportError:                                   # pragma: no cover
    np = None
    Image = None

log = logging.getLogger('eyesauron.store')

MAGIC_PACK = b'EYEPACK\0'
MAGIC_BLOOM = b'EYEBLOOM\0'
MARK_REC = b'CHNK'
MARK_SEAL = b'SEAL'
CHUNKER_V1 = 1                        # grid256-cs256-png
HDR_LEN = 64                          # заголовок .pack
REC_HEAD = struct.Struct('<4sHHI32s')  # mark, flags, resv, payload_len, sha32
IDX_ENTRY = struct.Struct('<32sQI')   # hash, payload_off, payload_len
IDX_FOOTER = struct.Struct('<QI')     # count, crc32
FOOTER_PAYLOAD = struct.Struct('<QdI32sII')  # count, created, chunker, sha, crc, resv
COPY_CHUNK = 8 * 1024 * 1024
# хвост .pack, не входящий в манифестный sha256 (футер 'SEAL' пишется ПОСЛЕ
# вычисления дайджеста данных — спека §2.1)
FOOTER_TAIL = REC_HEAD.size + FOOTER_PAYLOAD.size

_store_err_lock = threading.Lock()


class PackStoreError(Exception):
    pass


def iter_tiles(arr, cs: int = 256):
    """Кадр → [(hash_bytes, tile_arr)] row-major; крайние тайлы добиваются
    нулями до cs×cs. Хеш = sha256(padded RGB) — совместим с vendor."""
    h, w, _ = arr.shape
    ph, pw = -h % cs, -w % cs
    if ph or pw:
        padded = np.zeros((h + ph, w + pw, 3), dtype=np.uint8)
        padded[:h, :w] = arr
    else:
        padded = arr
    out = []
    for y in range(0, padded.shape[0], cs):
        for x in range(0, padded.shape[1], cs):
            tile = np.ascontiguousarray(padded[y:y + cs, x:x + cs])
            out.append((hashlib.sha256(tile.tobytes()).digest(), tile))
    return out


def _png_tile(tile) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(tile).save(buf, format='PNG', compress_level=1)
    return buf.getvalue()


def _encode_frame(canvas, w: int, h: int) -> bytes:
    img = Image.fromarray(np.ascontiguousarray(canvas[:h, :w]))
    buf = io.BytesIO()
    img.save(buf, format='PNG', compress_level=1)
    return buf.getvalue()


class _VolFile:
    """Хендл тома с блокировкой (seek+read из разных потоков)."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.f = open(path, 'rb')

    def pread(self, off: int, ln: int) -> bytes:
        with self.lock:
            self.f.seek(off)
            return self.f.read(ln)

    def close(self):
        with self.lock:
            if not self.f.closed:
                self.f.close()


class _Idx:
    """Сортированный индекс тома: mmap недоступен кроссплатформенно чисто —
    читаем целиком в bytes и ищем бинарно по 44-байтовым записям."""

    def __init__(self, path: Path):
        self.data = path.read_bytes()
        n = (len(self.data) - IDX_FOOTER.size) // IDX_ENTRY.size
        cnt, crc = IDX_FOOTER.unpack_from(self.data, n * IDX_ENTRY.size)
        import zlib
        if cnt != n or zlib.crc32(self.data[:n * IDX_ENTRY.size]) != crc:
            raise PackStoreError(f'битый индекс {path.name}')
        self.n = n

    def find(self, hsh: bytes) -> tuple[int, int] | None:
        lo, hi = 0, self.n
        while lo < hi:
            mid = (lo + hi) // 2
            key = self.data[mid * 44:mid * 44 + 32]
            if key < hsh:
                lo = mid + 1
            elif key > hsh:
                hi = mid
            else:
                _, off, ln = IDX_ENTRY.unpack_from(self.data, mid * 44)
                return off, ln
        return None


class _Bloom:
    def __init__(self, path: Path):
        raw = path.read_bytes()
        magic_len = len(MAGIC_BLOOM)
        if raw[:magic_len] != MAGIC_BLOOM:
            raise PackStoreError(f'битый bloom {path.name}')
        self.n, self.m_bits, self.k = struct.unpack_from('<QQI', raw, magic_len)
        self.bits = raw[magic_len + 20:]

    def contains(self, hsh: bytes) -> bool:
        h1 = int.from_bytes(hsh[:8], 'little')
        h2 = int.from_bytes(hsh[8:16], 'little') | 1
        m = self.m_bits
        bits = self.bits
        for i in range(self.k):
            pos = (h1 + i * h2) % m
            if not bits[pos >> 3] & (0x80 >> (pos & 7)):
                return False
        return True

    @classmethod
    def build(cls, hashes: list[bytes]) -> 'tuple[bytes, _Bloom-like]':
        n = max(len(hashes), 1)
        m_bits = ((int(n * 9.6) + 7) // 8) * 8
        k = 7
        bitmap = bytearray(m_bits // 8)
        for hsh in hashes:
            h1 = int.from_bytes(hsh[:8], 'little')
            h2 = int.from_bytes(hsh[8:16], 'little') | 1
            for i in range(k):
                pos = (h1 + i * h2) % m_bits
                bitmap[pos >> 3] |= 0x80 >> (pos & 7)
        head = MAGIC_BLOOM + struct.pack('<QQI', len(hashes), m_bits, k)
        return head + bytes(bitmap)


class PackStore:
    """Движок пакованного дедуп-хранилища. Потокобезопасность: все мутации
    под self._wlock; сервис зовёт синхронные методы через asyncio.to_thread."""

    def __init__(self, local_root: Path, nas_root: Path | None,
                 node: str, seen_cache: int = 400_000,
                 local_cache_bytes: int = 0):
        if np is None or Image is None:
            raise PackStoreError('нужны numpy и pillow')
        self.local_root = Path(local_root)
        self.nas_root = Path(nas_root) if nas_root else None
        safe = re.sub(r'[^\w\-]', '', node)[:24] or 'node'
        self.node = safe
        self.local_cache_bytes = max(0, int(local_cache_bytes))
        self.node = safe
        self.volumes_dir = self.local_root / 'volumes'
        self.maps_dir = self.local_root / 'maps'
        self.manifest_path = self.local_root / 'volumes.json'
        self.staging_path = self.volumes_dir / 'staging.pack'
        self.journal_path = self.volumes_dir / 'staging.journal'

        self._wlock = threading.RLock()
        self._handles: OrderedDict[str, _VolFile] = OrderedDict()
        self._idx_cache: dict[str, _Idx] = {}
        self._bloom_cache: dict[str, _Bloom | None] = {}
        self._manifest: list[dict] = []
        self._seq = 0
        # staging
        self._pack_file = None
        self._data_off = HDR_LEN
        self._staging: dict[bytes, tuple[int, int]] = {}
        self._staging_created = time.time()
        self._sha_live = False               # инкрементальный sha возможен?
        self._pack_sha = None
        self._journal_f = None
        # карты
        self.catalog = {}                    # host -> {date: frames}
        self._seg_files: dict[str, object] = {}
        self._cur_meta: dict[tuple, dict] = {}   # (date,) -> {name:[off,len,size]}
        self._meta_cache: OrderedDict = OrderedDict()  # LRU прошлых дней
        self._dirty_meta = False
        self._dirty_catalog = False
        # дедуп-кэш
        self._seen: OrderedDict = OrderedDict()
        self._seen_cap = seen_cache
        self.nas_ok = True
        self.last_nas_err = ''
        self._opened = False

    # ------------------------------------------------------------------ #
    #  Открытие / восстановление
    # ------------------------------------------------------------------ #

    def open(self):
        with self._wlock:
            self.volumes_dir.mkdir(parents=True, exist_ok=True)
            self.maps_dir.mkdir(parents=True, exist_ok=True)
            self._load_manifest()
            self._rebuild_catalog()
            self._open_staging()
            self._opened = True
            log.info('packstore открыт: %s | томов %d | staging %.1f МБ',
                     self.local_root, len(self._manifest),
                     (self._data_off - HDR_LEN) / (1024 * 1024))

    def _load_manifest(self):
        if self.manifest_path.is_file():
            try:
                self._manifest = json.loads(
                    self.manifest_path.read_text(encoding='utf-8'))
            except (OSError, ValueError) as e:
                log.error('манифест повреждён (%s), восстанавливаю сканом', e)
                self._manifest = []
        known = {m['id'] for m in self._manifest}
        # усыновление осиротевших локальных томов (crash между rename и манифестом)
        for p in sorted(self.volumes_dir.glob('vol-*.pack')):
            vid = p.stem
            if vid in known:
                continue
            info = self._parse_footer(p)
            if info is None:
                log.warning('осиротевший том %s не парсится — пропущен', vid)
                continue
            log.warning('усыновляю том %s (crash до записи манифеста)', vid)
            self._manifest.append({
                'id': vid, 'node': self.node,
                'seq': int(vid.rsplit('-', 1)[-1]),
                'state': 'sealed-local', **info})
        # проверка локальных файлов для ready-томов
        for m in self._manifest:
            p = self.volumes_dir / f"{m['id']}.pack"
            if m['state'] == 'ready' and not p.is_file():
                m['state'] = 'evicted-local'   # возможно удалён вручную
        self._seq = max((m.get('seq', 0) for m in self._manifest), default=0)
        self._save_manifest()

    def _parse_footer(self, pack_path: Path) -> dict | None:
        try:
            size = pack_path.stat().st_size
            with open(pack_path, 'rb') as f:
                if f.read(HDR_LEN)[:8] != MAGIC_PACK:
                    return None
                f.seek(size - REC_HEAD.size - FOOTER_PAYLOAD.size)
                head = REC_HEAD.unpack(f.read(REC_HEAD.size))
                if head[0] != MARK_SEAL:
                    return None
                payload = f.read(FOOTER_PAYLOAD.size)
                if hashlib.sha256(payload).digest() != head[4]:
                    return None
            cnt, created, chunker, sha, _crc, _r = FOOTER_PAYLOAD.unpack(payload)
            return {'size': size, 'chunks': cnt, 'created': created,
                    'sha256': sha.hex(), 'chunker': chunker}
        except (OSError, struct.error):
            return None

    def _rebuild_catalog(self):
        """Каталог host→date→count выводится из карт дней (meta.json +
        доскан хвостов сегментов) — источник истины переживает краш."""
        catalog = {}
        for sp in self.maps_dir.glob('seg-*.mseg'):
            date = sp.stem.removeprefix('seg-')
            try:
                names = self._day_names(date)
            except OSError as e:
                log.warning('каталог: сегмент %s не прочитан: %s', date, e)
                continue
            for name in names:
                parts = name.split('/')
                if len(parts) != 3:
                    continue
                catalog.setdefault(parts[0], {}).setdefault(date, 0)
                catalog[parts[0]][date] += 1
        self.catalog = catalog

    def _open_staging(self):
        if self.staging_path.is_file():
            entries = self._replay_journal()
            actual = self.staging_path.stat().st_size
            good_off = self._data_off
            if actual < good_off or good_off < HDR_LEN:
                log.error('staging короче журнала (%d < %d) — отбрасываю '
                          'незапечатанные ссылки', actual, good_off)
                entries, good_off = [], HDR_LEN
            if actual > good_off:
                with open(self.staging_path, 'r+b') as f:
                    f.truncate(good_off)
            self._data_off = good_off
            self._staging = {bytes.fromhex(h): (o, ln) for h, o, ln in entries}
            self._pack_file = open(self.staging_path, 'r+b')
            self._pack_file.seek(0, os.SEEK_END)
            self._sha_live = False              # хеш префикса неизвестен
            hdr = self._read_header()
            if hdr[0] != MAGIC_PACK or hdr[2] != CHUNKER_V1:
                raise PackStoreError('чужой/битый staging.pack')
            # свежий хвост журнала после последнего коммита больше не нужен
            self._truncate_journal()
        else:
            self._create_staging()
        self._journal_f = open(self.journal_path, 'a', encoding='utf-8')

    def _read_header(self):
        with open(self.staging_path, 'rb') as f:
            return struct.unpack('<8sII48s', f.read(HDR_LEN))

    def _create_staging(self):
        self._pack_file = open(self.staging_path, 'w+b')  # r/w: чтение чанков
        self._pack_file.write(struct.pack(
            '<8sII48s', MAGIC_PACK, 1, CHUNKER_V1, b'\0' * 48))
        self._pack_file.flush()
        os.fsync(self._pack_file.fileno())
        self._data_off = HDR_LEN
        self._staging_created = time.time()
        self._staging.clear()
        self._sha_live = True
        self._pack_sha = hashlib.sha256()
        self._pack_sha.update(struct.pack(
            '<8sII48s', MAGIC_PACK, 1, CHUNKER_V1, b'\0' * 48))
        if self.journal_path.exists():
            self.journal_path.unlink()

    def _replay_journal(self) -> list:
        """Восстановить последний валидный коммит; поднимает self._data_off."""
        entries: list = []
        if not self.journal_path.is_file():
            self._data_off = HDR_LEN
            self._staging_created = time.time()
            return entries
        good = None
        buf = []
        with open(self.journal_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    assert isinstance(obj.get('off'), int)
                    assert isinstance(obj.get('ent'), list)
                    good = obj
                except (ValueError, AssertionError):
                    log.warning('журнал: обрываю хвост на невалидной строке')
                    break
        if good is None:
            self._data_off = HDR_LEN
            self._staging_created = time.time()
            return entries
        self._data_off = good['off']
        self._staging_created = good.get('created', time.time())
        return good['ent']

    def _truncate_journal(self):
        # журнал перезаписывается с нуля: история до seal не нужна
        self.journal_path.write_text('', encoding='utf-8')

    # ------------------------------------------------------------------ #
    #  Запись кадра
    # ------------------------------------------------------------------ #

    def _known_anywhere(self, hsh: bytes) -> bool:
        cached = self._seen.get(hsh)
        if cached is not None:
            self._seen.move_to_end(hsh)
            return cached
        result = hsh in self._staging or self._find_in_volumes(hsh) is not None
        self._seen[hsh] = result
        if len(self._seen) > self._seen_cap:
            self._seen.popitem(last=False)
        return result

    def _find_in_volumes(self, hsh: bytes) -> tuple[int, int] | None:
        for m in reversed(self._manifest):
            state = m['state']
            if state not in ('sealed-local', 'ready', 'evicted-local'):
                continue
            bloom = self._get_bloom(m)
            if bloom is not None and not bloom.contains(hsh):
                continue
            idx = self._get_idx(m)
            if idx is None:
                continue
            found = idx.find(hsh)
            if found is not None:
                return found
        return None

    def _get_idx(self, m: dict) -> _Idx | None:
        vid = m['id']
        if vid in self._idx_cache:
            return self._idx_cache[vid]
        path = self._resolve_local(vid, '.idx')
        if path is None:
            self._idx_cache[vid] = None
            return None
        try:
            idx = _Idx(path)
        except (PackStoreError, OSError) as e:
            log.warning('индекс %s не читается: %s', vid, e)
            self._idx_cache[vid] = None
            return None
        self._idx_cache[vid] = idx
        return idx

    def _get_bloom(self, m: dict):
        vid = m['id']
        if vid in self._bloom_cache:
            return self._bloom_cache[vid]
        path = self._resolve_local(vid, '.bloom')
        bloom = None
        if path is not None:
            try:
                bloom = _Bloom(path)
            except (PackStoreError, OSError):
                bloom = None
        self._bloom_cache[vid] = bloom
        return bloom

    def _resolve_local(self, vid: str, ext: str) -> Path | None:
        """Локальный путь файла тома или NAS (для evicted-local)."""
        p = self.volumes_dir / f'{vid}{ext}'
        if p.is_file():
            return p
        if self.nas_root is not None:
            pn = self.nas_root / 'volumes' / f'{vid}{ext}'
            try:
                if pn.is_file():
                    return pn
            except OSError:
                pass
        return None

    def _handle(self, path: Path) -> _VolFile:
        key = str(path)
        h = self._handles.get(key)
        if h is None:
            h = _VolFile(path)
            self._handles[key] = h
        self._handles.move_to_end(key)
        while len(self._handles) > 12:
            _, old = self._handles.popitem(last=False)
            old.close()
        return h

    # ------------------------------------------------------------------ #

    def put_frame(self, name: str, w: int, h: int, cs: int,
                  tiles: list, png_size: int) -> dict:
        """tiles = [(hash_bytes, tile_arr)] от iter_tiles (row-major).

        Дедуплицирует, новые тайлы аппендит в staging, пишет карту кадра
        в сегмент дня. Один кадр = один журнальный коммит.
        Возвращает {'new': n, 'dup': d, 'dedup_pct': x}.
        """
        if not self._opened:
            raise PackStoreError('хранилище не открыто')
        parts = name.split('/')
        if len(parts) != 3:
            raise PackStoreError(f'неверное имя кадра: {name}')
        host, date = parts[0], parts[1]
        with self._wlock:
            new = [(hsh, tile) for hsh, tile in tiles
                   if not self._known_anywhere(hsh)]

            payloads = []
            for hsh, tile in new:
                payload = _png_tile(tile)
                head = REC_HEAD.pack(MARK_REC, 0, 0, len(payload),
                                     hashlib.sha256(payload).digest())
                self._pack_file.write(head)
                self._pack_file.write(payload)
                if self._sha_live:
                    self._pack_sha.update(head)
                    self._pack_sha.update(payload)
                self._staging[hsh] = (self._data_off + REC_HEAD.size,
                                      len(payload))
                payloads.append((hsh.hex(),
                                 self._data_off + REC_HEAD.size, len(payload)))
                self._data_off += REC_HEAD.size + len(payload)
                # кэш дедупа: теперь хеш точно известен
                self._seen[hsh] = True
                self._seen.move_to_end(hsh)
                if len(self._seen) > self._seen_cap:
                    self._seen.popitem(last=False)

            # карта кадра — ссылка на ВСЕ тайлы (включая дубли)
            record = json.dumps(
                {'name': name, 'w': w, 'h': h, 'cs': cs, 'chunker': CHUNKER_V1,
                 'size': png_size,
                 'grid': [hsh.hex() for hsh, _ in tiles]},
                ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            off, ln = self._write_map_record(date, record)
            self._update_meta(date, name, off, ln, png_size)

            self.catalog.setdefault(host, {}).setdefault(date, 0)
            self.catalog[host][date] += 1
            self._dirty_meta = True

            # коммит кадра: данные уже в буфере Python → fsync + журнал
            self._pack_file.flush()
            os.fsync(self._pack_file.fileno())
            self._journal_f.write(json.dumps(
                {'off': self._data_off, 'created': self._staging_created,
                 'ent': payloads}, separators=(',', ':')) + '\n')
            self._journal_f.flush()
            os.fsync(self._journal_f.fileno())

            dup = len(tiles) - len(new)
            return {'new': len(new), 'dup': dup,
                    'dedup_pct': round(100.0 * dup / len(tiles), 1)
                    if tiles else 0.0}

    # ------------------------------------------------------------------ #
    #  Карты: сегменты дня
    # ------------------------------------------------------------------ #

    def _seg_path(self, date: str) -> Path:
        return self.maps_dir / f'seg-{date}.mseg'

    def _write_map_record(self, date: str, record: bytes) -> tuple[int, int]:
        """Дописать карту в сегмент дня; вернуть (payload_off, 4+len)."""
        path = self._seg_path(date)
        f = self._seg_files.get(date)
        if f is None or f.closed:
            f = open(path, 'ab')
            self._seg_files[date] = f
        f.write(struct.pack('<I', len(record)))
        off = f.tell()                       # позиция payload после заголовка
        f.write(record)
        f.flush()
        os.fsync(f.fileno())
        return off, 4 + len(record)

    def _update_meta(self, date: str, name: str, off: int, ln: int,
                     size: int):
        meta = self._cur_meta.get(date)
        if meta is None:
            meta = self._load_day_meta(date)
            self._cur_meta[date] = meta
            self._dirty_meta = True
        meta['names'][name] = [off, ln, size]
        self._dirty_meta = True

    def _persist_meta(self, date: str):
        meta = self._cur_meta.get(date)
        if meta is None:
            return
        payload = {'appended': self._seg_appended(date),
                   'names': meta['names']}
        tmp = self._seg_path(date).with_suffix('.meta.tmp')
        tmp.write_text(json.dumps(payload, separators=(',', ':')),
                       encoding='utf-8')
        tmp.replace(self._seg_path(date).with_suffix('.meta.json'))

    def _seg_appended(self, date: str) -> int:
        f = self._seg_files.get(date)
        if f is not None and not f.closed:
            pos = f.tell()
            return pos
        p = self._seg_path(date)
        return p.stat().st_size if p.is_file() else 0

    def _load_day_meta(self, date: str) -> dict:
        """names дня: персистнутый meta + досканирование хвоста сегмента
        после 'appended' (восстановление кадров, записанных после крайнего
        flush — переживает жёсткий краш узла)."""
        return {'names': self._day_names(date)}

    def _day_names(self, date: str) -> dict:
        names = {}
        appended = 0
        mp = self._seg_path(date).with_suffix('.meta.json')
        if mp.is_file():
            try:
                obj = json.loads(mp.read_text(encoding='utf-8'))
                names = obj.get('names', {})
                appended = int(obj.get('appended', 0))
            except (OSError, ValueError):
                pass
        seg = self._seg_path(date)
        if seg.is_file():
            actual = seg.stat().st_size
            if actual > appended:
                try:
                    with open(seg, 'rb') as f:
                        f.seek(appended)
                        names.update(self._scan_records(f, appended))
                except OSError as e:
                    log.warning('доскан сегмента %s не удался: %s', date, e)
        return names

    @staticmethod
    def _scan_records(f, base_off: int) -> dict:
        """Хвост сегмента: [u32 len][json] до конца/обрыва."""
        names = {}
        while True:
            head = f.read(4)
            if len(head) < 4:
                break
            (ln,) = struct.unpack('<I', head)
            payload = f.read(ln)
            if len(payload) < ln:
                break                                  # обрыв при краше
            try:
                obj = json.loads(payload)
                # off = позиция payload (конвенция _write_map_record)
                names[obj['name']] = [f.tell() - ln, 4 + ln,
                                      obj.get('size', 0)]
            except (ValueError, KeyError, struct.error):
                continue
        return names

    def _day_meta_cached(self, date: str) -> dict:
        if date in self._cur_meta:
            return self._cur_meta[date]
        got = self._meta_cache.get(date)
        if got is not None:
            self._meta_cache.move_to_end(date)
            return got
        got = self._load_day_meta(date)
        self._meta_cache[date] = got
        while len(self._meta_cache) > 6:
            self._meta_cache.popitem(last=False)
        return got

    # ------------------------------------------------------------------ #
    #  Seal
    # ------------------------------------------------------------------ #

    def maybe_seal(self, size_bytes: int, max_age_sec: float) -> str | None:
        with self._wlock:
            used = self._data_off - HDR_LEN
            if used <= 0:
                return None
            age = time.time() - self._staging_created
            if used >= size_bytes or age >= max_age_sec:
                return self.seal()
            return None

    def seal(self) -> str | None:
        """Запечатать активный том. Возвращает id нового тома или None
        (пустой staging). Спека §5: порядок rename — idx/bloom раньше pack."""
        with self._wlock:
            if self._data_off <= HDR_LEN:
                return None
            t0 = time.time()
            chunks_count = len(self._staging)
            used_bytes = self._data_off - HDR_LEN
            self._pack_file.flush()
            os.fsync(self._pack_file.fileno())
            self._pack_file.close()

            seq = self._seq + 1
            vid = f'vol-{self.node}-{seq:06d}'
            created = self._staging_created

            final_digest = (self._pack_sha.copy().digest()
                            if self._sha_live else self._readback_sha())
            idx_crc = self._write_idx(vid)
            self._write_bloom(vid)

            footer_payload = FOOTER_PAYLOAD.pack(
                len(self._staging), created, CHUNKER_V1, final_digest,
                idx_crc, 0)
            footer = REC_HEAD.pack(MARK_SEAL, 0, 0, len(footer_payload),
                                   hashlib.sha256(footer_payload).digest())
            with open(self.staging_path, 'r+b') as f:
                f.seek(0, os.SEEK_END)
                f.write(footer)
                f.write(footer_payload)
                f.flush()
                os.fsync(f.fileno())

            final_size = self.staging_path.stat().st_size
            # Windows: rename невозможен, пока файл держит кто-то ещё
            # (LRU-хендл после чтений из staging + транзиентные AV-сканы)
            stale = self._handles.pop(str(self.staging_path), None)
            if stale:
                stale.close()
            self._rename_with_retry(self.staging_path,
                                    self.volumes_dir / f'{vid}.pack')
            self._seq = seq
            self._manifest.append({
                'id': vid, 'node': self.node, 'seq': seq,
                'state': 'sealed-local', 'size': final_size,
                'sha256': final_digest.hex(), 'chunks': len(self._staging),
                'created': created, 'chunker': CHUNKER_V1})
            self._save_manifest()

            if self._journal_f is not None:
                try:
                    self._journal_f.close()
                except OSError:
                    pass
                self._journal_f = None
            self._create_staging()
            self._journal_f = open(self.journal_path, 'a', encoding='utf-8')
            self._flush_maps_dirty()
            self._mirror_manifest_bg()
            log.info('запечатан %s: %d чанков, %.1f МБ за %.1f с',
                     vid, chunks_count, used_bytes / (1024 * 1024),
                     time.time() - t0)
            return vid

    @staticmethod
    def _rename_with_retry(src: Path, dst: Path, attempts: int = 5):
        for i in range(attempts):
            try:
                os.replace(src, dst)
                return
            except PermissionError:
                if i == attempts - 1:
                    raise
                time.sleep(0.3 * (i + 1))

    def _readback_sha(self) -> bytes:
        """sha всего staging после восстановления (инкрементальный потерян)."""
        log.info('вычисляю sha восстановленного staging чтением назад…')
        h = hashlib.sha256()
        with open(self.staging_path, 'rb') as f:
            h.update(f.read(HDR_LEN))
            while True:
                block = f.read(COPY_CHUNK)
                if not block:
                    break
                h.update(block)
        return h.digest()

    def _write_idx(self, vid: str) -> int:
        import zlib
        items = sorted(self._staging.items())
        body = b''.join(IDX_ENTRY.pack(hsh, off, ln)
                        for hsh, (off, ln) in items)
        crc = zlib.crc32(body)
        path = self.volumes_dir / f'{vid}.idx'
        with open(path, 'wb') as f:
            f.write(body)
            f.write(IDX_FOOTER.pack(len(items), crc))
            f.flush()
            os.fsync(f.fileno())
        return crc

    def _write_bloom(self, vid: str):
        data = _Bloom.build(list(self._staging.keys()))
        path = self.volumes_dir / f'{vid}.bloom'
        with open(path, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

    # ------------------------------------------------------------------ #
    #  Заливка / выселение
    # ------------------------------------------------------------------ #

    def upload_pending(self) -> bool:
        """Один цикл фонового воркера: заливка старейших sealed-local на NAS,
        верификация sha256, выселение сверх local_cache. False = NAS недоступен."""
        if self.nas_root is None:
            return True
        progressed = False
        for m in sorted([x for x in self._manifest
                         if x['state'] == 'sealed-local'],
                        key=lambda x: x['seq']):
            if not self._upload_one(m):
                return progressed
            progressed = True
        self._evict_if_needed()
        self._mirror_closed_segments()
        return progressed

    def _nas_probe(self) -> bool:
        try:
            (self.nas_root / 'volumes').mkdir(parents=True, exist_ok=True)
            self.nas_ok = True
            return True
        except OSError as e:
            self.nas_ok = False
            with _store_err_lock:
                if self.last_nas_err != str(e):
                    self.last_nas_err = str(e)
                    log.warning('NAS недоступен: %s (заливка отложена)', e)
            return False

    def _upload_one(self, m: dict) -> bool:
        if not self._nas_probe():
            return False
        vid = m['id']
        src = self.volumes_dir / f'{vid}.pack'
        if not src.is_file():
            log.warning('%s нет локально — помечаю evicted', vid)
            m['state'] = 'evicted-local'
            self._save_manifest()
            return True
        dst_dir = self.nas_root / 'volumes'
        part = dst_dir / f'{vid}.pack.part'
        final = dst_dir / f'{vid}.pack'
        try:
            expected = bytes.fromhex(m['sha256'])
            self._copy_resume_verify(src, part, expected, FOOTER_TAIL)
            os.replace(part, final)
            for ext in ('.idx', '.bloom'):
                s = self.volumes_dir / f'{vid}{ext}'
                d = dst_dir / f'{vid}{ext}'
                if s.is_file() and not (d.is_file() and
                                        d.stat().st_size == s.stat().st_size):
                    d.write_bytes(s.read_bytes())
            m['state'] = 'ready'
            self._save_manifest()
            self._mirror_manifest()
            log.info('залит %s (%.1f МБ)', vid, m['size'] / (1024 * 1024))
            return True
        except OSError as e:
            self.nas_ok = False
            log.warning('заливка %s не удалась: %s', vid, e)
            return False
        except PackStoreError as e:
            log.error('верификация %s провалилась: %s', vid, e)
            return False

    @staticmethod
    def _copy_resume_verify(src: Path, part: Path, expected: bytes,
                            tail_skip: int = 0):
        """Докачка .part со смещения; верификация чтением назад.
        tail_skip — байты хвоста файла, исключённые из sha256 (футер)."""
        src_size = src.stat().st_size
        done = part.stat().st_size if part.is_file() else 0
        if done > src_size or (done and done < HDR_LEN):
            done = 0
        if done != src_size:
            with open(src, 'rb') as s, open(part, 'ab') as d:
                s.seek(done)
                while True:
                    block = s.read(COPY_CHUNK)
                    if not block:
                        break
                    d.write(block)
            # доводим точно до размера источника (обрыв хвоста)
            cur = part.stat().st_size
            if cur != src_size:
                with open(src, 'rb') as s, open(part, 'r+b') as d:
                    s.seek(cur)
                    d.seek(cur)
                    d.truncate()
                    while True:
                        block = s.read(COPY_CHUNK)
                        if not block:
                            break
                        d.write(block)
        # верификация: sha области данных (без хвостового футера)
        h = hashlib.sha256()
        remaining = max(src_size - tail_skip, 0)
        with open(part, 'rb') as d:
            while remaining > 0:
                block = d.read(min(COPY_CHUNK, remaining))
                if not block:
                    break
                h.update(block)
                remaining -= len(block)
        if h.digest() != expected:
            part.unlink(missing_ok=True)
            raise PackStoreError('sha256 копии не совпал с манифестом')

    def _evict_if_needed(self):
        """Выселить старейшие локальные копии готовых томов сверх бюджета D2.
        Бюджет 0 = не хранить готовые тома локально вовсе; < 0 — выключено."""
        budget = self.local_cache_bytes
        if budget < 0:
            return
        changed = False
        ready = sorted([x for x in self._manifest if x['state'] == 'ready'],
                       key=lambda x: x['seq'])
        total = sum(x['size'] for x in ready)
        for m in ready:
            if total <= budget:
                break
            vid = m['id']
            for ext in ('.pack', '.idx', '.bloom'):
                p = self.volumes_dir / f'{vid}{ext}'
                try:
                    if p.is_file():
                        p.unlink()
                except OSError as e:
                    log.warning('выселение %s: %s', p.name, e)
            m['state'] = 'evicted-local'
            total -= m['size']
            changed = True
            self._idx_cache.pop(vid, None)
            self._bloom_cache.pop(vid, None)
            h = self._handles.pop(str(self.volumes_dir / f'{vid}.pack'), None)
            if h:
                h.close()
            log.info('выселён из локального кэша %s (копия на NAS)', vid)
        if changed:
            self._save_manifest()

    # ------------------------------------------------------------------ #
    #  Чтение
    # ------------------------------------------------------------------ #

    def read_chunk(self, hsh: bytes) -> bytes | None:
        with self._wlock:
            found = self._staging.get(hsh)
            if found is not None:
                off, ln = found
                # читаем через ЕДИНСТВЕННЫЙ управляемый хендл: лишний
                # открытый дескриптор не даст Windows переименовать файл
                # при seal (WinError 32)
                try:
                    self._pack_file.flush()
                except (OSError, ValueError):
                    pass
                self._pack_file.seek(off)
                data = self._pack_file.read(ln)
                if len(data) == ln:
                    return data
                return None
            loc = self._find_in_volumes(hsh)
            if loc is None:
                return None
            off, ln = loc
            for m in reversed(self._manifest):
                if m['state'] not in ('sealed-local', 'ready',
                                      'evicted-local'):
                    continue
                idx = self._get_idx(m)
                if idx is None:
                    continue
                if idx.find(hsh) == loc:
                    path = self._resolve_local(m['id'], '.pack')
                    if path is None:
                        return None
                    return self._handle(path).pread(off, ln)
            return None

    def assemble(self, name: str, verify_tiles: bool = False) -> bytes:
        """Собрать кадр по карте. name без расширения ('host/date/ts__title')."""
        parts = name.split('/')
        if len(parts) != 3:
            raise PackStoreError(f'неверное имя кадра: {name}')
        date = parts[1]
        meta = self._day_meta_cached(date)['names'].get(name)
        if meta is None:
            raise PackStoreError(f'карта не найдена: {name}')
        off, ln, _size = meta
        with self._wlock:
            f = self._seg_files.get(date)
            if f is not None and not f.closed:
                f.flush()
            path = self._seg_path(date)
            h = self._handle(path)
            # meta хранит офсет начала payload (= начало JSON) и длину 4+payload
            obj = json.loads(h.pread(off, ln - 4))
        grid, w, hh, cs = obj['grid'], obj['w'], obj['h'], obj['cs']
        ph, pw = -hh % cs, -w % cs
        canvas = np.zeros((hh + ph, w + pw, 3), dtype=np.uint8)
        i = 0
        for y in range(0, canvas.shape[0], cs):
            for x in range(0, canvas.shape[1], cs):
                hsh = bytes.fromhex(grid[i])
                i += 1
                payload = self.read_chunk(hsh)
                if payload is None:
                    raise PackStoreError(f'чанк отсутствует: {grid[i - 1]}')
                if verify_tiles and hashlib.sha256(payload).digest() != hsh:
                    raise PackStoreError(f'чанк повреждён: {grid[i - 1]}')
                tile = np.asarray(Image.open(io.BytesIO(payload)).convert(
                    'RGB'))
                canvas[y:y + cs, x:x + cs] = tile
        return _encode_frame(canvas, w, hh)

    # ------------------------------------------------------------------ #
    #  Браузинг / статистика
    # ------------------------------------------------------------------ #

    def browse_hosts(self) -> list[str]:
        return sorted(self.catalog.keys())

    def browse_dates(self, host: str) -> list[str]:
        return sorted(self.catalog.get(host, {}).keys(), reverse=True)

    def browse_images(self, host: str, date: str,
                      flt: str = '') -> list[dict]:
        names = self._day_meta_cached(date)['names']
        prefix_ok = lambda n: n.startswith(f'{host}/{date}/')  # noqa: E731
        out = []
        for n, (_off, _ln, size) in names.items():
            if not prefix_ok(n):
                continue
            stem = n.rsplit('/', 1)[-1]
            if flt and flt.lower() not in stem.lower():
                continue
            out.append({'file': n + '.png', 'name': stem, 'size': size,
                        'mtime': self._name_mtime(stem)})
        out.sort(key=lambda r: r['file'])
        return out

    @staticmethod
    def _name_mtime(stem: str) -> float:
        try:
            return time.mktime(time.strptime(stem[:19],
                                             '%Y-%m-%d_%H-%M-%S'))
        except ValueError:
            return 0.0

    def stats(self) -> dict:
        total_frames = sum(c for dates in self.catalog.values()
                           for c in dates.values())
        logical = sum(m['size'] for m in self._manifest)
        local_ready = sum(m['size'] for m in self._manifest
                          if m['state'] == 'ready')
        return {
            'frames': total_frames,
            'hosts': len(self.catalog),
            'logical_gb': round(logical / (1024 ** 3), 2),
            'local_ready_gb': round(local_ready / (1024 ** 3), 2),
            'volumes': len(self._manifest),
        }

    def info(self) -> dict:
        states = {}
        for m in self._manifest:
            states[m['state']] = states.get(m['state'], 0) + 1
        st = self.stats()
        return {
            'mode': 'pack',
            'root': str(self.local_root),
            'nas_root': str(self.nas_root) if self.nas_root else '',
            'nas_ok': self.nas_ok,
            'staging_mb': round((self._data_off - HDR_LEN) / (1024 * 1024), 1),
            'staging_chunks': len(self._staging),
            'states': states,
            'stats': st,
        }

    # ------------------------------------------------------------------ #
    #  Персистентность мелочей
    # ------------------------------------------------------------------ #

    def _save_manifest(self):
        tmp = self.manifest_path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(self._manifest, indent=1),
                       encoding='utf-8')
        tmp.replace(self.manifest_path)

    def flush_dirty(self):
        """Периодический сброс meta сегментов (зовётся из тика сервиса)."""
        with self._wlock:
            if self._dirty_meta:
                self._flush_maps_dirty()

    def _flush_maps_dirty(self):
        for date in list(self._cur_meta.keys()):
            try:
                self._persist_meta(date)
            except OSError as e:
                log.warning('meta %s не сохранена: %s', date, e)
        self._dirty_meta = False

    def _mirror_manifest(self):
        if self.nas_root is None:
            return
        try:
            (self.nas_root / 'volumes').mkdir(parents=True, exist_ok=True)
            dst = self.nas_root / 'volumes.json'
            tmp = dst.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(self._manifest, indent=1),
                           encoding='utf-8')
            tmp.replace(dst)
        except OSError as e:
            log.warning('манифест не отражён на NAS: %s', e)

    def _mirror_manifest_bg(self):
        try:
            self._mirror_manifest()
        except Exception:                                 # noqa: BLE001
            pass

    def _mirror_closed_segments(self):
        """Прошлые дни карт + их meta — на NAS (текущий день живёт локально)."""
        if self.nas_root is None:
            return
        today = time.strftime('%Y-%m-%d')
        try:
            ndir = self.nas_root / 'maps'
            ndir.mkdir(parents=True, exist_ok=True)
            for p in self.maps_dir.glob('*.mseg'):
                date = p.stem.removeprefix('seg-')
                if date >= today:
                    continue
                dst = ndir / p.name
                if not (dst.is_file() and
                        dst.stat().st_size == p.stat().st_size):
                    dst.write_bytes(p.read_bytes())
                mp = p.with_suffix('.meta.json')
                if mp.is_file():
                    dm = ndir / mp.name
                    if not (dm.is_file() and
                            dm.stat().st_size == mp.stat().st_size):
                        dm.write_bytes(mp.read_bytes())
        except OSError as e:
            log.warning('сегменты карт не отражены на NAS: %s', e)

    # ------------------------------------------------------------------ #
    #  Закрытие
    # ------------------------------------------------------------------ #

    def close(self):
        with self._wlock:
            try:
                if self._pack_file is not None and not self._pack_file.closed:
                    self._pack_file.flush()
                    os.fsync(self._pack_file.fileno())
                    self._pack_file.close()
            except OSError:
                pass
            for date, f in self._seg_files.items():
                try:
                    if not f.closed:
                        f.flush()
                        os.fsync(f.fileno())
                        f.close()
                except OSError:
                    pass
            self._flush_maps_dirty()
            try:
                self._save_manifest()
            except OSError:
                pass
            try:
                self._mirror_manifest()
            except Exception:                             # noqa: BLE001
                pass
            for h in self._handles.values():
                h.close()
            self._handles.clear()
            self._opened = False
            log.info('packstore закрыт')

    # конфиг-флаг поиска по bloom (файлы пишутся всегда, спека D6)
    bloom_enabled_flag = False
