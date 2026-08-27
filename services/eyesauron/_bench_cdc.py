# services/eyesauron/_bench_cdc.py — офлайн-бенчмарк стратегий чанкинга EyeSauron
#
# Вопрос, на который отвечает скрипт: что взять за chunker v1 пакованного
# дедуп-хранилища — фиксированную сетку 256x256 или CDC (content-defined
# chunking). Считает реальную дедупликацию на живом корпусе кадров с NAS,
# включая стресс-сценарий «скролл», на котором сетка теоретически проседает.
#
# Стратегии:
#   grid256  — тайлы 256x256 с нулевым паддингом, хеш sha256(padded RGB),
#              payload = PNG тайла            (наследие vendor ChunkStore)
#   cdc_png  — FastCDC-подобные разрезы ПОВЕРХ готового PNG-потока кадра,
#              payload = срез байт             (сборка = конкатенация)
#   cdc_raw  — разрезы сырого RGB-буфера, payload = zlib(level 1) чанка
#              (если установлен zstandard — используется он)
#
# Запуск:
#   python -m services.eyesauron._bench_cdc --source \\nas\photo\screens \
#       --host vgn --dates 3 --frames 500
#
# Результат: таблица в stdout + JSON (--out). Скрипт автономен, лоадером
# не загружается ('_'-префикс), к сервису отношения кроме темы не имеет.

import argparse
import hashlib
import io
import json
import random
import sys
import time
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
except ImportError:
    sys.exit('нужны numpy и pillow')

try:
    import zstandard                                  # опционально
    _ZSTD_COMPRESSOR = zstandard.ZstdCompressor(level=3)
except ImportError:
    _ZSTD_COMPRESSOR = None

CS = 256                     # размер тайла фиксированной сетки
U64 = (1 << 64) - 1
_CS_RNG = random.Random(20260826)
_GEAR = [_CS_RNG.getrandbits(64) for _ in range(256)]   # ОДИН генератор на все
                                                        # 256 значений таблицы


# ------------------------------------------------------------------ #
#  CDC: Gear-hash, упрощённый FastCDC (min-skip + одна маска)
# ------------------------------------------------------------------ #

def cdc_slices(data: bytes, min_s: int = 4096, bits: int = 13,
               max_s: int = 65536):
    """Точки разреза детерминированы содержимым: сдвиг контента портит
    максимум один-два чанка вокруг изменения, дальше поток ресинхронизируется.
    Ожидаемый средний размер ≈ min_s + 2^bits ≈ 12 КБ."""
    gear = _GEAR
    mask = (1 << bits) - 1
    n = len(data)
    pos = 0
    while pos < n:
        end = pos + max_s
        if end >= n:
            end = n
            if end - pos <= min_s:
                yield data[pos:end]
                break
        h = 0
        i = pos + min_s
        cut = end
        while i < end:
            h = ((h << 1) + gear[data[i]]) & U64
            if not h & mask:
                cut = i + 1
                break
            i += 1
        yield data[pos:cut]
        pos = cut


_ZLIB_LEVEL = 1


def _compress(payload: bytes) -> bytes:
    if _ZSTD_COMPRESSOR is not None:
        return _ZSTD_COMPRESSOR.compress(payload)
    return zlib_compress1(payload)


def zlib_compress1(payload: bytes) -> bytes:
    import zlib
    return zlib.compress(payload, _ZLIB_LEVEL)


# ------------------------------------------------------------------ #
#  Стратегия 1: фиксированная сетка 256x256
# ------------------------------------------------------------------ #

def grid_chunks(arr) -> list[tuple[str, bytes]]:
    """[(hash, png_tile)] — паддинг до кратности CS, как в vendor."""
    h, w, _ = arr.shape
    ph = -h % CS
    pw = -w % CS
    if ph or pw:
        padded = np.zeros((h + ph, w + pw, 3), dtype=np.uint8)
        padded[:h, :w] = arr
    else:
        padded = arr
    out = []
    for y in range(0, padded.shape[0], CS):
        for x in range(0, padded.shape[1], CS):
            tile = np.ascontiguousarray(padded[y:y + CS, x:x + CS])
            digest = hashlib.sha256(tile.tobytes()).hexdigest()
            buf = io.BytesIO()
            Image.fromarray(tile).save(buf, format='PNG', compress_level=1)
            out.append((digest, buf.getvalue()))
    return out


# ------------------------------------------------------------------ #
#  Стратегии 2-3: CDC поверх PNG-потока / поверх сырых пикселей
# ------------------------------------------------------------------ #

def cdc_png_chunks(png: bytes, bits: int = 13) -> list[tuple[str, bytes]]:
    return [(hashlib.sha256(s).hexdigest(), s) for s in cdc_slices(png, bits=bits)]


def cdc_raw_chunks(arr, bits: int = 13) -> list[tuple[str, bytes]]:
    raw = arr.tobytes()                               # C-смежный RGB
    return [(hashlib.sha256(s).hexdigest(), _compress(s))
            for s in cdc_slices(raw, bits=bits)]


def up_filtered(arr) -> bytes:
    """Байтовый поток кадра с PNG-фильтром 'Up': каждый байт минус байт
    строкой выше (mod 256). Сохраняет локальность при сдвиге контента
    (фильтр строки зависит только от своей и соседней строки), но делает
    экранное изображение сжимаемым, как это делает PNG."""
    a = np.ascontiguousarray(arr)
    if a.shape[0] < 2:
        return a.tobytes()
    diff = (a[1:].astype(np.int16) - a[:-1].astype(np.int16)).astype(np.uint8)
    return a[0].tobytes() + diff.tobytes()


def cdc_up_chunks(arr, bits: int = 13) -> list[tuple[str, bytes]]:
    return [(hashlib.sha256(s).hexdigest(), _compress(s))
            for s in cdc_slices(up_filtered(arr), bits=bits)]


def _local_up_filter(chunk: bytes, stride: int) -> bytes:
    """Up-фильтр внутри чанка: строки относительно предыдущей СТРОКИ ЧАНКА
    (первая строка — как есть). Дедуп-разрезы при этом не участвуют —
    фильтрация происходит после разреза сырого потока."""
    if len(chunk) <= stride:
        return chunk
    body = np.frombuffer(chunk, dtype=np.uint8)
    rows = len(body) // stride
    a = body[:rows * stride].reshape(rows, stride)
    diff = (a[1:].astype(np.int16) - a[:-1].astype(np.int16)).astype(np.uint8)
    return a[0].tobytes() + diff.tobytes() + body[rows * stride:].tobytes()


def cdc_upc_chunks(arr, bits: int = 13, stride: int = 1) -> list[tuple[str, bytes]]:
    """CDC по сырым байтам (те же хеши/границы, что у cdc_raw),
    payload = локально Up-фильтрованный и сжатый чанк."""
    out = []
    for s in cdc_slices(arr.tobytes(), bits=bits):
        out.append((hashlib.sha256(s).hexdigest(),
                    _compress(_local_up_filter(s, stride))))
    return out


# ------------------------------------------------------------------ #
#  Корпус: последовательные кадры с NAS
# ------------------------------------------------------------------ #

def load_frames(root: Path, host: str, dates: int, max_frames: int,
                max_mb: int) -> list[tuple[str, bytes]]:
    host_dir = root / host
    day_dirs = sorted((d for d in host_dir.iterdir() if d.is_dir()),
                      key=lambda p: p.name, reverse=True)[:dates]
    frames, total = [], 0
    budget = max_mb * 1024 * 1024
    for day in reversed(day_dirs):                    # старые дни вперёд
        for f in sorted(day.glob('*.png'), key=lambda p: p.name):
            if len(frames) >= max_frames or total >= budget:
                return frames
            try:
                data = f.read_bytes()
            except OSError:
                continue
            frames.append((f'{day.name}/{f.name}', data))
            total += len(data)
    return frames


# ------------------------------------------------------------------ #
#  Стресс-сценарий «скролл»: плавные сдвиги одного кадра
# ------------------------------------------------------------------ #

def build_scroll_series(arr, step_px: int, count: int):
    """Кадр, последовательно уезжающий вверх (как прокрутка страницы):
    сверху появляется чёрная полоса — так же выглядит реальный скролл."""
    series = []
    h, w, _ = arr.shape
    for k in range(1, count + 1):
        s = min(k * step_px, h)
        shifted = np.zeros_like(arr)
        shifted[s:] = arr[:h - s]
        buf = io.BytesIO()
        Image.fromarray(shifted).save(buf, format='PNG')
        series.append((shifted, buf.getvalue()))
    return series


# ------------------------------------------------------------------ #
#  Движок бенчмарка
# ------------------------------------------------------------------ #

class Accum:
    def __init__(self, name):
        self.name = name
        self.seen = set()
        self.chunks = 0
        self.stored = 0          # уникальные байты на диске
        self.input = 0           # входные байты (PNG кадров)

    def feed(self, chunks, input_bytes):
        self.input += input_bytes
        for digest, payload in chunks:
            self.chunks += 1
            if digest not in self.seen:
                self.seen.add(digest)
                self.stored += len(payload)

    def row(self, elapsed):
        dedup = 100.0 * (1.0 - self.stored / self.input) if self.input else 0.0
        mb = self.stored / (1024 * 1024)
        idx_gb = (self.seen and len(self.seen) * 44 / max(self.stored, 1) * (1 << 30) / (1024 * 1024)) or 0
        return {
            'strategy': self.name,
            'frames_input_mb': round(self.input / (1024 * 1024), 1),
            'stored_mb': round(mb, 1),
            'dedup_pct': round(dedup, 1),
            'chunks': self.chunks,
            'unique_chunks': len(self.seen),
            'idx_mb_per_gb': round(idx_gb, 1),
            'sec': round(elapsed, 1),
        }


def run_benchmark(frames, scroll_arr, args):
    scroll_series = build_scroll_series(scroll_arr,
                                        args.scroll_step, args.scroll_count)
    strategies = [s.strip() for s in args.strategies.split(',')
                  if s.strip() in ('grid256', 'cdc_png', 'cdc_raw',
                                   'cdc_up', 'cdc_upc')]
    phases = [('natural', frames), ('scroll', scroll_series)]
    accs = {(name, strat): Accum(f'{strat}@{name}')
            for name, _ in phases for strat in strategies}

    t0 = time.perf_counter()
    done = 0
    for phase_name, seq in phases:
        for item in seq:
            if phase_name == 'natural':
                fname, png = item
                arr = np.asarray(Image.open(io.BytesIO(png)).convert('RGB'))
            else:
                arr, png = item
            if 'grid256' in strategies:
                accs[(phase_name, 'grid256')].feed(grid_chunks(arr), len(png))
            if 'cdc_png' in strategies:
                accs[(phase_name, 'cdc_png')].feed(
                    cdc_png_chunks(png, args.cdc_bits), len(png))
            if 'cdc_raw' in strategies:
                accs[(phase_name, 'cdc_raw')].feed(
                    cdc_raw_chunks(arr, args.cdc_bits), len(png))
            if 'cdc_up' in strategies:
                accs[(phase_name, 'cdc_up')].feed(
                    cdc_up_chunks(arr, args.cdc_bits), len(png))
            if 'cdc_upc' in strategies:
                accs[(phase_name, 'cdc_upc')].feed(
                    cdc_upc_chunks(arr, args.cdc_bits,
                                   stride=arr.shape[1] * 3), len(png))
            done += 1
            if done % 25 == 0:
                print(f'  … обработано {done} кадров '
                      f'({time.perf_counter() - t0:.0f} с)', flush=True)
    elapsed = time.perf_counter() - t0
    return accs, elapsed


ROWS_HEADER = (f"{'стратегия':<18}{'вход МБ':>9}{'хранение':>10}{'дедуп %':>9}"
               f"{'чанков':>9}{'уникальных':>12}{'idx МБ/ГБ':>11}{'время с':>9}")


def print_table(rows):
    print(ROWS_HEADER)
    print('-' * len(ROWS_HEADER))
    for r in rows:
        print(f"{r['strategy']:<18}{r['frames_input_mb']:>9}{r['stored_mb']:>10}"
              f"{r['dedup_pct']:>9}{r['chunks']:>9}{r['unique_chunks']:>12}"
              f"{r['idx_mb_per_gb']:>11}{r['sec']:>9}")


def main():
    ap = argparse.ArgumentParser(description='EyeSauron chunking benchmark')
    ap.add_argument('--source', default=r'\\192.168.53.21\photo\screens')
    ap.add_argument('--host', default='vgn')
    ap.add_argument('--dates', type=int, default=3,
                    help='сколько последних дней задействовать')
    ap.add_argument('--frames', type=int, default=500)
    ap.add_argument('--max-mb', type=int, default=300,
                    help='бюджет входных данных, МБ')
    ap.add_argument('--scroll-step', type=int, default=24)
    ap.add_argument('--scroll-count', type=int, default=15)
    ap.add_argument('--cdc-bits', type=int, default=13,
                    help='маска CDC: средний чанк ~ min_s + 2^bits '
                         '(13 ≈ 12КБ, 16 ≈ 80КБ)')
    ap.add_argument('--zlib-level', type=int, default=1,
                    help='уровень zlib для cdc_raw (если нет zstandard)')
    ap.add_argument('--strategies', default='grid256,cdc_png,cdc_raw')
    ap.add_argument('--out', default='', help='путь для JSON-результата')
    args = ap.parse_args()

    if _ZSTD_COMPRESSOR is not None:
        print('компрессор cdc_raw: zstandard')
    else:
        print(f'компрессор cdc_raw: zlib level {args.zlib_level} '
              f'(zstandard не установлен; реальные показатели будут лучше)')
        import services.eyesauron._bench_cdc as _self
        _self._ZLIB_LEVEL = args.zlib_level

    frames = load_frames(Path(args.source), args.host,
                         args.dates, args.frames, args.max_mb)
    if len(frames) < 50:
        sys.exit(f'слишком мало кадров ({len(frames)}) — выберите другой хост')
    print(f'корпус: {args.host}, {len(frames)} кадров, '
          f'{sum(len(p) for _, p in frames) / (1024 * 1024):.0f} МБ '
          f'({frames[0][0]} … {frames[-1][0]})')

    mid = np.asarray(Image.open(io.BytesIO(frames[len(frames) // 2][1])).convert('RGB'))

    accs, elapsed = run_benchmark(frames, mid, args)
    strats = [s.strip() for s in args.strategies.split(',')]
    rows_natural = [accs[('natural', s)].row(elapsed) for s in strats]
    rows_scroll = [accs[('scroll', s)].row(elapsed // 2 or 1) for s in strats]

    print(f'\n=== ОБЫЧНЫЙ ПОТОК ({len(frames)} кадров подряд) ===')
    print_table(rows_natural)
    print(f'\n=== СТРЕСС «СКРОЛЛ» ({args.scroll_count} сдвигов по '
          f'{args.scroll_step}px одного кадра) ===')
    print_table(rows_scroll)

    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            json.dump({'natural': rows_natural, 'scroll': rows_scroll},
                      f, ensure_ascii=False, indent=2)
        print(f'\nJSON: {args.out}')


if __name__ == '__main__':
    main()
