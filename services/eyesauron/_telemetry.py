# services/eyesauron/_telemetry.py — телеметрия скролла (docs/eyesauron_storage.md §7)
#
# Назначение: измерить на живом трафике ДОЛЮ скролл-кадров, чтобы принять
# обоснованное решение о включении CDC-томов (chunker v2). Детектор дешёвый:
# кадр даунскейлится до ~96×128 градаций серого, сравнивается с предыдущим
# кадром того же хоста вертикальными сдвигами; выраженный минимум расхождения
# при сдвиге ≠ 0 трактуется как прокрутка.
#
# Персистентность на собирающей ноде (формат выбран автором реализации):
#   telemetry/<YYYY-MM-DD>.jsonl  — по строке на наблюдение:
#       {"ts": <epoch>, "host": "...", "scroll": bool, "shift": int|null}
#     append-only, читается любым инструментом; ротация по дате.
#   telemetry/summary.json        — компактные агрегаты по дням/хостам,
#     переписывается атомарно раз в минуту при изменениях; источник для UI.
# JSONL старше RETENTION_DAYS удаляется при старте.

import json
import logging
import time
from collections import defaultdict
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
except ImportError:
    np = None
    Image = None

log = logging.getLogger('eyesauron.telemetry')

SMALL_H, SMALL_W = 96, 128      # даунскейл для корреляции
MAX_SHIFT_SMALL = 15            # ±15 малых строк ≈ ±180 px при 1080p
STATIC_DIFF = 2.0               # средняя |разница| ниже — экран статичен
SCROLL_RATIO = 0.55             # best_diff < ratio*base_diff → скролл
FLUSH_SEC = 60.0                # период перезаписи summary.json
RETENTION_DAYS = 90


class ScrollTelemetry:
    def __init__(self, dir_path: Path):
        self.dir = Path(dir_path)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._file = None                    # текущий jsonl-хендл
        self._file_day = None
        # days[date][host] = [frames, scroll_frames]
        self.days = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        self._last_small = {}                # host -> small gray ndarray
        self._dirty = False
        self._last_flush = 0.0
        self._load_summary()
        self._cleanup_old()
        self._rewrite_summary()

    # ------------------------------------------------------------------ #
    #  Наблюдение
    # ------------------------------------------------------------------ #

    def observe(self, host: str, arr_rgb) -> dict:
        """Учесть кадр. Возвращает {'scroll': bool, 'shift': int|None}."""
        result = {'scroll': False, 'shift': None}
        small = self._downscale(arr_rgb)

        prev = self._last_small.get(host)
        if prev is not None and prev.shape == small.shape:
            # малая строка ≈ real_h/SMALL_H пикселей реального кадра
            scale = float(arr_rgb.shape[0]) / SMALL_H
            shift = self._detect_shift(prev, small, scale)
            if shift is not None:
                result['scroll'] = True
                result['shift'] = shift
        self._last_small[host] = small

        day = time.strftime('%Y-%m-%d')
        counters = self.days[day][host]
        counters[0] += 1
        if result['scroll']:
            counters[1] += 1
        self._append_jsonl(day, host, result)
        self._maybe_flush()
        return result

    @staticmethod
    def _downscale(arr_rgb):
        img = Image.fromarray(arr_rgb).convert('L')
        return np.asarray(img.resize((SMALL_W, SMALL_H)), dtype=np.float32)

    @staticmethod
    def _detect_shift(prev, cur, scale: float) -> int | None:
        """Вертикальный сдвиг с минимальным расхождением или None.

        scale — реальных пикселей на малую строку. Возвращает сдвиг в
        ПИКСЕЛЯХ кадра (знак: контент уехал вниз).
        """
        base = float(np.abs(cur - prev).mean())
        if base < STATIC_DIFF:
            return None                       # статичный экран — не скролл

        best_s, best_diff = 0, base
        for s in range(-MAX_SHIFT_SMALL, MAX_SHIFT_SMALL + 1):
            if s == 0 or abs(s) >= SMALL_H // 2:
                continue
            if s > 0:
                diff = float(np.abs(cur[s:] - prev[:-s]).mean())
            else:
                diff = float(np.abs(cur[:s] - prev[-s:]).mean())
            if diff < best_diff:
                best_s, best_diff = s, diff

        if best_s != 0 and best_diff < SCROLL_RATIO * base:
            return int(round(best_s * scale / 4.0)) * 4   # шаг 4 px
        return None

    # ------------------------------------------------------------------ #
    #  Персистентность
    # ------------------------------------------------------------------ #

    def _append_jsonl(self, day: str, host: str, result: dict):
        try:
            if self._file_day != day or self._file is None:
                if self._file is not None:
                    self._file.close()
                self._file = open(self.dir / f'{day}.jsonl', 'a',
                                  encoding='utf-8')
                self._file_day = day
            self._file.write(json.dumps(
                {'ts': round(time.time(), 3), 'host': host,
                 'scroll': result['scroll'], 'shift': result['shift']},
                ensure_ascii=False) + '\n')
            self._file.flush()
        except OSError as e:
            log.warning('jsonl не записан: %s', e)

    def _maybe_flush(self):
        now = time.time()
        if self._dirty and now - self._last_flush >= FLUSH_SEC:
            self._rewrite_summary()

    def _rewrite_summary(self):
        self._last_flush = time.time()
        self._dirty = False
        total_f = sum(c[0] for d in self.days.values() for c in d.values())
        total_s = sum(c[1] for d in self.days.values() for c in d.values())
        payload = {
            'updated': round(self._last_flush, 3),
            'total': {'frames': total_f, 'scroll_frames': total_s},
            'days': {day: {h: {'frames': c[0], 'scroll_frames': c[1]}
                           for h, c in hosts.items()}
                     for day, hosts in sorted(self.days.items())},
        }
        tmp = self.dir / 'summary.json.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            tmp.replace(self.dir / 'summary.json')
        except OSError as e:
            log.warning('summary не записан: %s', e)

    def _load_summary(self):
        path = self.dir / 'summary.json'
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            for day, hosts in data.get('days', {}).items():
                for h, c in hosts.items():
                    cell = self.days[day][h]
                    cell[0] += int(c.get('frames', 0))
                    cell[1] += int(c.get('scroll_frames', 0))
        except (OSError, ValueError, AttributeError) as e:
            log.warning('summary.json повреждён, начинаю с нуля: %s', e)

    def _cleanup_old(self):
        cutoff = time.time() - RETENTION_DAYS * 86400
        try:
            for p in self.dir.glob('*.jsonl'):
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    log.info('удалён старый лог телеметрии: %s', p.name)
        except OSError:
            pass

    # ------------------------------------------------------------------ #
    #  Наружу
    # ------------------------------------------------------------------ #

    def summary(self, last_days: int = 7) -> dict:
        """Компактная сводка для status(): доли скролла по хостам за N дней."""
        cutoff = time.strftime('%Y-%m-%d',
                               time.localtime(time.time() - last_days * 86400))
        per_host = defaultdict(lambda: [0, 0])
        for day, hosts in self.days.items():
            if day < cutoff:
                continue
            for h, c in hosts.items():
                per_host[h][0] += c[0]
                per_host[h][1] += c[1]
        hosts_out = {}
        for h, (f, s) in sorted(per_host.items()):
            hosts_out[h] = {
                'frames': f, 'scroll_frames': s,
                'share_pct': round(100.0 * s / f, 1) if f else 0.0,
            }
        tf = sum(v['frames'] for v in hosts_out.values())
        ts_ = sum(v['scroll_frames'] for v in hosts_out.values())
        return {
            'period_days': last_days,
            'frames': tf,
            'scroll_frames': ts_,
            'scroll_pct': round(100.0 * ts_ / tf, 1) if tf else 0.0,
            'hosts': hosts_out,
        }

    def close(self):
        self._rewrite_summary()
        if self._file is not None:
            self._file.close()
            self._file = None
