# services/eyesauron/service.py — мониторинг экранов EyeSauron
#
# Порт функциональности проекта EyeSauron в mesh (docs/eyeSauron.md, фазы 1-2).
# Две независимые роли, включаются в config.yaml → eyesauron:
#
#   collect — КОЛЛЕКТОР: принимает кадры от агентов сети (RPC ingest) и пишет
#             raw PNG в store_path (<host>/<date>/<ts>__<title>.png).
#   capture — АГЕНТ: захватывает экраны ЭТОЙ машины. Узел живёт в session 0,
#             поэтому через WTS-инъекцию (_wts.py) запускает лёгкий хелпер
#             (_session_helper.py) в каждой активной сессии пользователя.
#             Хелпер кладёт кадры в spool-очередь, сервис разбирает её и шлёт
#             коллектору по mesh; при недоступности коллектора кадры копятся
#             (офлайн-буфер с потолком max_spool_mb).
#
# По умолчанию ВЫКЛЮЧЕН (eyesauron.enabled: false) — как purge.
#
# TODO(dedup): raw PNG сильно грузит NAS тысячами мелких файлов. Следующий
#  этап — пакованное дедуп-хранилище (тома .pack + .idx + .bloom, seal →
#  заливка на NAS). Спека УТВЕРЖДЕНА: docs/eyesauron_storage.md
#  (chunker v1 = grid256; телеметрия скролла решит, когда включать CDC-тома).
#  Старый ChunkStore сохранён как образец: services/eyesauron/_vendor_chunk_store.py.

import asyncio
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from services.rpc import rpc
from src.internal_modules.base import ModuleGeneric

try:
    from services.eyesauron import _wts
    from services.eyesauron._capture_core import (
        MIN_SCREENSHOT_SIZE, grab_frame, png_bytes, spool_files, spool_read,
        spool_remove, window_title, frame_timestamp, _SANITIZE_RE,
    )
    from services.eyesauron._pack_store import PackStore, iter_tiles
    from services.eyesauron._telemetry import ScrollTelemetry
except (ImportError, OSError, AttributeError):
    # не-Windows хост: WinDLL недоступен — сервис бесплезен,
    # но узел не должен падать при загрузке
    _wts = None

POLL_SEC = 2.0                    # такт цикла агента (reconcile+ship)
MAX_INGEST_BYTES = 20 * 1024 * 1024   # кадров больше нет смысла ждать
_HOSTNAME_RE = re.compile(r'[^A-Za-z0-9._-]')
_TS_FORMAT = '%Y-%m-%d_%H-%M-%S'


class Eyesauron(ModuleGeneric):
    def __init__(self, name, context):
        super().__init__(name, context)
        self._tasks: list[asyncio.Task] = []
        self._stopping = False
        # session_id → (pid, process_handle) — хелперы захвата
        self._helpers: dict[int, tuple[int, int]] = {}
        # пакованное дедуп-хранилище + телеметрия скролла
        self._store: PackStore | None = None
        self._telemetry: ScrollTelemetry | None = None

    # ------------------------------------------------------------------ #
    #  Конфиг / пути
    # ------------------------------------------------------------------ #

    def _cfg(self):
        cm = getattr(self.ctx, 'config_manager', None)
        root = getattr(cm, 'cfg', None) if cm else None
        cfg = getattr(root or self.ctx.config, 'eyesauron', None)
        return cfg

    def _enabled(self) -> bool:
        cfg = self._cfg()
        return bool(cfg and cfg.enabled)

    def _spool_dir(self) -> Path:
        work_dir = getattr(self.ctx.config.local, 'work_dir', '.')
        return Path(work_dir) / 'eyesauron' / 'spool'

    def _helper_cmdline(self) -> str:
        """Команда запуска хелпера в пользовательской сессии."""
        cfg = self._cfg()
        interval = max(1.0, float(getattr(cfg, 'interval_sec', 5.0)))
        spool = self._spool_dir()
        if getattr(sys, 'frozen', False):
            # тот же exe с ключом — перехват в начале main.py
            return (f'"{sys.executable}" --eye-sauron-helper '
                    f'--spool "{spool}" --interval {interval}')
        script = Path(__file__).resolve().parent / '_session_helper.py'
        return (f'"{sys.executable}" "{script}" '
                f'--spool "{spool}" --interval {interval}')

    def _helper_directory(self) -> str | None:
        """cwd для CreateProcessAsUserW (dev-режиму нужен корень репозитория)."""
        if getattr(sys, 'frozen', False):
            return None
        return str(Path(__file__).resolve().parents[2])

    # ------------------------------------------------------------------ #
    #  Жизненный цикл
    # ------------------------------------------------------------------ #

    async def start(self):
        if not self._enabled():
            self.log.info('отключён (config.yaml → eyesauron.enabled: true), RPC отвечают отказом')
            return
        cfg = self._cfg()
        roles = []
        if cfg.collect:
            roles.append('коллектор')
        if cfg.capture:
            roles.append('агент')
        self.log.info('включён: %s | store=%s collector=%s',
                      '+'.join(roles) or 'роли не выбраны',
                      cfg.store_path, cfg.collector_node or '—')
        if cfg.capture and _wts is None:
            self.log.warning('capture недоступна: не Windows (нет WTS API)')
        elif cfg.capture:
            await asyncio.to_thread(self._spool_dir().mkdir,
                                    parents=True, exist_ok=True)
            self._tasks.append(asyncio.create_task(self._agent_loop()))

        # пакованное дедуп-хранилище (docs/eyesauron_storage.md)
        store_cfg = getattr(cfg, 'store', None)
        if store_cfg and store_cfg.enabled:
            try:
                local_root = (Path(getattr(self.ctx.config.local,
                                           'work_dir', '.'))
                              / 'eyesauron' / 'store')
                budget = int(store_cfg.local_cache_gb) * 1024 ** 3
                self._store = PackStore(local_root, Path(store_cfg.root),
                                        self.ctx.NODE,
                                        local_cache_bytes=budget)
                if store_cfg.bloom_enabled:
                    self._store.bloom_enabled_flag = True
                await asyncio.to_thread(self._store.open)
                self._telemetry = ScrollTelemetry(
                    Path(getattr(self.ctx.config.local, 'work_dir', '.'))
                    / 'eyesauron' / 'telemetry')
                self._tasks.append(asyncio.create_task(self._store_loop()))
                self.log.info('packstore включён: локальный %s, NAS %s',
                              local_root, store_cfg.root)
            except Exception as e:
                self.log.error('packstore не поднялся (%s) — ingest уйдёт '
                               'в raw-PNG режим', e)
                self._store = None
                self._telemetry = None

    async def stop(self):
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        if self._helpers and _wts is not None:
            await asyncio.to_thread(self._kill_all_helpers)
        if self._store is not None:
            store, self._store = self._store, None
            try:
                await asyncio.to_thread(store.close)
            except Exception:
                self.log.exception('packstore.close не удался')
        if self._telemetry is not None:
            try:
                self._telemetry.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  Цикл обслуживания packstore: seal-триггеры, заливка, flush
    # ------------------------------------------------------------------ #

    async def _store_loop(self):
        cfg = self._cfg()
        store_cfg = getattr(cfg, 'store', None) if cfg else None
        size_limit = int(getattr(store_cfg, 'volume_size_gb', 10)) * 1024 ** 3 \
            if store_cfg else 10 * 1024 ** 3
        max_age = float(getattr(store_cfg, 'max_age_hours', 24)) * 3600 \
            if store_cfg else 24 * 3600
        tick = 0
        while not self._stopping:
            try:
                await asyncio.to_thread(self._store.maybe_seal,
                                        size_limit, max_age)
                # заливку проверяем раз в ~20с (5 тиков по 4с), meta — каждый
                if tick % 5 == 0:
                    await asyncio.to_thread(self._store.upload_pending)
                await asyncio.to_thread(self._store.flush_dirty)
                tick += 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log.debug('цикл packstore: %s', e)
            await asyncio.sleep(4.0)

    def _kill_all_helpers(self):
        for sid, (_pid, handle) in list(self._helpers.items()):
            try:
                if _wts.is_alive(handle):
                    _wts.kill_process(handle)
                else:
                    _wts.KERNEL32.CloseHandle(handle)
            except Exception:
                pass
            self.log.info('хелпер сессии %s остановлен', sid)
        self._helpers.clear()

    # ------------------------------------------------------------------ #
    #  Цикл агента: reconcile хелперов + разбор spool
    # ------------------------------------------------------------------ #

    async def _agent_loop(self):
        while not self._stopping:
            try:
                await asyncio.to_thread(self._reconcile_helpers)
                await self._ship_frames()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log.debug('цикл агента: %s', e)
            await asyncio.sleep(POLL_SEC)

    def _reconcile_helpers(self):
        """Порт monitor() из launcher.py: держим хелпер в каждой активной сессии."""
        active = set(_wts.get_active_sessions())

        for sid in list(self._helpers):
            pid, handle = self._helpers[sid]
            if sid not in active:
                if _wts.is_alive(handle):
                    self.log.info('сессия %s исчезла — глушу хелпер PID %s', sid, pid)
                    _wts.kill_process(handle)
                else:
                    _wts.KERNEL32.CloseHandle(handle)
                del self._helpers[sid]
            elif not _wts.is_alive(handle):
                self.log.warning('хелпер сессии %s умер сам — перезапускаю', sid)
                _wts.KERNEL32.CloseHandle(handle)
                del self._helpers[sid]

        for sid in active:
            if sid in self._helpers:
                continue
            try:
                pid, handle = _wts.launch_in_session(
                    self._helper_cmdline(), sid,
                    directory=self._helper_directory())
                self._helpers[sid] = (pid, handle)
                self.log.info('хелпер запущен в сессии %s (PID %s)', sid, pid)
            except Exception as e:
                self.log.error('запуск хелпера в сессии %s не удался: %s', sid, e)

    async def _ship_frames(self):
        """Отправка кадров из spool коллектору; сбой → кадры ждут дальше."""
        cfg = self._cfg()
        collector = (getattr(cfg, 'collector_node', '') or '').strip()
        if not collector or collector == self.ctx.NODE:
            return                          # некуда слать / сам себе коллектор:
                                            # кадры оседают в spool до настройки

        spool = self._spool_dir()
        await asyncio.to_thread(self._enforce_spool_limit, spool,
                                int(getattr(cfg, 'max_spool_mb', 500)))

        files = await asyncio.to_thread(spool_files, spool)
        delay = max(0.0, float(getattr(cfg, 'send_delay_sec', 0.5)))
        for path in files:
            if self._stopping:
                return
            payload = await asyncio.to_thread(spool_read, path)
            if payload is None:
                # битая пара без .meta — выкидываем, чтобы не отравляла очередь
                await asyncio.to_thread(spool_remove, path)
                continue
            png, meta = payload
            try:
                resp = await self.ctx.network.call(
                    dst=collector, service='eyesauron', method='ingest',
                    data={'meta': meta, 'png': png}, timeout=30)
            except Exception:
                # коллектор недоступен — офлайн-режим, копим буфер
                return
            if isinstance(resp, dict) and resp.get('ok'):
                await asyncio.to_thread(spool_remove, path)
                if delay:
                    await asyncio.sleep(delay)
            else:
                err = (resp or {}).get('error') if isinstance(resp, dict) else resp
                self.log.warning('ingest отклонил кадр (%s) — пауза до следующего такта', err)
                return

    @staticmethod
    def _enforce_spool_limit(spool: Path, max_mb: int):
        """Переполнение офлайн-буфера → старейшие кадры удаляются."""
        if not spool.is_dir() or max_mb <= 0:
            return
        files = spool_files(spool)
        total = sum(f.stat().st_size for f in files)
        limit = max_mb * 1024 * 1024
        dropped = 0
        i = 0
        while total > limit and i < len(files):
            f = files[i]
            try:
                total -= f.stat().st_size
            except OSError:
                pass
            spool_remove(f)
            dropped += 1
            i += 1
        if dropped:
            logging.getLogger('eyesauron.spool').warning(
                'spool переполнен: удалено старейших кадров: %d', dropped)

    # ------------------------------------------------------------------ #
    #  RPC — статус и диагностика
    # ------------------------------------------------------------------ #

    @rpc
    async def status(self, data: dict = None) -> dict:
        """Состояние сервиса на узле: роли, хелперы сессий, spool-очередь."""
        cfg = self._cfg()
        base = {
            'ok': True,
            'node': self.ctx.NODE,
            'enabled': bool(cfg and cfg.enabled),
            'collect': bool(cfg and cfg.collect),
            'capture': bool(cfg and cfg.capture),
            'collector_node': getattr(cfg, 'collector_node', '') or '',
            'store_path': str(getattr(cfg, 'store_path', '')),
            'interval_sec': getattr(cfg, 'interval_sec', None),
            'frozen': bool(getattr(sys, 'frozen', False)),
            'pid': None,
        }
        if not base['enabled'] or _wts is None:
            return base

        base['pid'] = _wts.current_session_id()

        helpers = {}
        for sid, (pid, handle) in self._helpers.items():
            helpers[str(sid)] = {'pid': pid, 'alive': _wts.is_alive(handle)}
        base['helpers'] = helpers

        spool = self._spool_dir()
        files = await asyncio.to_thread(spool_files, spool)
        oldest = None
        if files:                       # spool_files сортирует старейшими первыми
            oldest = time.time() - files[0].stat().st_mtime
        total = await asyncio.to_thread(
            lambda: sum(f.stat().st_size for f in files))
        base['spool'] = {
            'dir': str(spool),
            'frames': len(files),
            'bytes': total,
            'oldest_age_sec': round(oldest) if oldest is not None else None,
        }

        if self._store is not None:
            try:
                base['store'] = await asyncio.to_thread(self._store.info)
                if self._telemetry is not None:
                    base['telemetry'] = self._telemetry.summary()
            except Exception as e:
                base['store_error'] = str(e)
        return base

    @rpc
    async def test_capture(self, data: dict = None) -> dict:
        """Диагностика: один кадр прямо из процесса узла.

        Работает только когда узел живёт в интерактивной сессии (dev-режим);
        у SYSTEM-узла (session 0) рабочего стола нет — используется хелпер.
        В data несёт bytes (PNG) — см. конвенцию glm.md.
        """
        if not self._enabled():
            return {'ok': False, 'error': 'сервис отключён '
                    '(config.yaml → eyesauron.enabled: true)'}
        if _wts is None:
            return {'ok': False, 'error': 'доступно только на Windows'}
        if _wts.current_session_id() == 0:
            return {'ok': False,
                    'error': 'узел в session 0 (SYSTEM): захват невозможен, '
                             'работает хелпер в сессиях пользователей'}
        img = await asyncio.to_thread(grab_frame)
        if img is None:
            return {'ok': False, 'error': 'ни один метод захвата не сработал'}
        shot = await asyncio.to_thread(png_bytes, img)
        if shot is None:
            return {'ok': False, 'error': 'кадр невалиден (мал размер/чёрный экран)'}
        return {'ok': True, 'png': shot, 'title': window_title(),
                'timestamp': frame_timestamp(), 'size': len(shot)}

    # ------------------------------------------------------------------ #
    #  RPC — роль коллектора
    # ------------------------------------------------------------------ #

    @rpc
    async def ingest(self, data: dict) -> dict:
        """Принять кадр от агента и сохранить raw PNG.

        data = {'meta': {hostname, timestamp, title}, 'png': bytes}
        В data приходят bytes (PNG) — допустимо протоколом (glm.md §4).
        Бизнес-отказ (NAS недоступен и т.п.) → {'ok': False, 'error'}.
        """
        cfg = self._cfg()
        if not cfg or not cfg.enabled:
            return {'ok': False, 'error': 'сервис отключён на этом узле'}
        if not cfg.collect:
            return {'ok': False, 'error': 'роль collect не активна '
                    '(config.yaml → eyesauron.collect: true)'}

        png = data.get('png')
        if not isinstance(png, (bytes, bytearray)):
            return {'ok': False, 'error': 'ожидаются bytes в поле png'}
        if len(png) < MIN_SCREENSHOT_SIZE:
            return {'ok': False, 'error': f'кадр подозрительно мал ({len(png)} байт)'}
        if len(png) > MAX_INGEST_BYTES:
            return {'ok': False, 'error': f'кадр слишком велик ({len(png)} байт)'}

        meta = data.get('meta') or {}
        hostname = _HOSTNAME_RE.sub('_', str(meta.get('hostname', '')))[:64] \
            or 'unknown-host'
        title = _SANITIZE_RE.sub('', str(meta.get('title', '')))[:60] or 'NoTitle'
        ts = str(meta.get('timestamp', ''))
        try:
            datetime.strptime(ts, _TS_FORMAT)
        except ValueError:
            ts = datetime.now().strftime(_TS_FORMAT)
        date = ts[:10]

        # режим пакованного дедуп-хранилища
        if self._store is not None:
            return await self._ingest_packed(hostname, date, ts, title,
                                             png)

        rel = f'{hostname}/{date}/{ts}__{title}.png'
        try:
            await asyncio.to_thread(self._save_raw_sync, rel, png)
        except OSError as e:
            self.log.error('запись кадра не удалась: %s', e)
            return {'ok': False, 'error': f'хранение недоступно: {e}'}
        return {'ok': True, 'path': rel, 'size': len(png)}

    async def _ingest_packed(self, hostname: str, date: str, ts: str,
                             title: str, png: bytes) -> dict:
        """Дедуп тайлами 256×256 в тома packstore (спека §3)."""
        import io as _io
        from PIL import Image as _Image
        try:
            arr = await asyncio.to_thread(
                lambda: np.asarray(
                    _Image.open(_io.BytesIO(png)).convert('RGB')))
        except Exception:
            return {'ok': False, 'error': 'кадр не декодируется как PNG'}

        if self._telemetry is not None:
            try:
                self._telemetry.observe(hostname, arr)
            except Exception:
                pass

        tiles = iter_tiles(arr)
        name = f'{hostname}/{date}/{ts}__{title}'
        try:
            stats = await asyncio.to_thread(
                self._store.put_frame, name, arr.shape[1], arr.shape[0],
                256, tiles, len(png))
        except Exception as e:
            self.log.error('put_frame не удался: %s', e)
            return {'ok': False, 'error': f'хранилище: {e}'}
        return {'ok': True, 'path': name + '.png', 'size': len(png),
                'new_chunks': stats['new'], 'dup_chunks': stats['dup'],
                'dedup_pct': stats['dedup_pct']}

    def _save_raw_sync(self, rel: str, png: bytes):
        full = Path(self._cfg().store_path) / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(png)

    @rpc
    async def browse(self, data: dict) -> dict:
        """Навигация по архиву кадров.

        data = {'level': 'hosts'|'dates'|'images',
                'host'?, 'date'?, 'filter'?}
        images → [{file, name, size, mtime}] (file — относительный путь
        для RPC image). В режиме packstore источник — карты томов,
        иначе — ФС store_path. Тяжёлый обход — в отдельном потоке.
        """
        if not self._enabled():
            return {'ok': False, 'error': 'сервис отключён на этом узле'}
        if not self._cfg().collect:
            return {'ok': False, 'error': 'роль collect не активна'}
        return await asyncio.to_thread(self._browse_sync, data or {})

    def _browse_sync(self, data: dict) -> dict:
        level = data.get('level') or 'hosts'

        if self._store is not None:
            host = _safe_component(data.get('host'))
            date = _safe_component(data.get('date'))
            flt = str(data.get('filter') or '')
            if level == 'hosts':
                items = self._store.browse_hosts()
                return {'ok': True, 'level': 'hosts', 'items': items}
            if not host:
                return {'ok': False, 'error': 'не указан host'}
            if level == 'dates':
                return {'ok': True, 'level': 'dates', 'host': host,
                        'items': self._store.browse_dates(host)}
            if level == 'images':
                if not date:
                    return {'ok': False, 'error': 'не указана date'}
                images = self._store.browse_images(host, date, flt)
                return {'ok': True, 'level': 'images', 'host': host,
                        'date': date, 'count': len(images),
                        'items': images[:2000]}
            return {'ok': False, 'error': f'неизвестный level: {level}'}

        root = Path(self._cfg().store_path)
        host = _safe_component(data.get('host'))
        date = _safe_component(data.get('date'))

        if level == 'hosts':
            items = sorted({p.name for p in root.iterdir() if p.is_dir()}
                           ) if root.is_dir() else []
            return {'ok': True, 'level': 'hosts', 'items': items}

        if not host:
            return {'ok': False, 'error': 'не указан host'}
        host_dir = root / host

        if level == 'dates':
            items = sorted({p.name for p in host_dir.iterdir() if p.is_dir()}
                           ) if host_dir.is_dir() else []
            return {'ok': True, 'level': 'dates', 'host': host, 'items': items}

        if level == 'images':
            if not date:
                return {'ok': False, 'error': 'не указана date'}
            day_dir = host_dir / date
            needle = str(data.get('filter') or '').lower()
            images = []
            if day_dir.is_dir():
                for p in day_dir.glob('*.png'):
                    if needle and needle not in p.name.lower():
                        continue
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    images.append({
                        'file': p.relative_to(root).as_posix(),
                        'name': p.stem,
                        'size': st.st_size,
                        'mtime': int(st.st_mtime),
                    })
            images.sort(key=lambda r: r['name'])
            return {'ok': True, 'level': 'images', 'host': host,
                    'date': date, 'count': len(images),
                    'items': images[:2000]}
        return {'ok': False, 'error': f'неизвестный level: {level}'}

    @rpc
    async def image(self, data: dict) -> dict:
        """Отдать кадр: data={'file': '<host>/<date>/<name>.png'}.

        В режиме packstore кадр собирается из дедуп-чанков тома.
        В data несёт bytes (PNG в поле 'png').
        """
        if not self._enabled():
            return {'ok': False, 'error': 'сервис отключён на этом узле'}
        if not self._cfg().collect:
            return {'ok': False, 'error': 'роль collect не активна'}

        rel = _safe_rel(str((data or {}).get('file', '')))
        if rel is None:
            return {'ok': False, 'error': 'некорректный путь кадра'}

        if self._store is not None:
            try:
                blob = await asyncio.to_thread(
                    self._store.assemble, rel[:-4])   # без '.png'
            except Exception as e:
                return {'ok': False, 'error': f'кадр недоступен: {e}'}
            return {'ok': True, 'file': rel, 'png': blob, 'size': len(blob)}

        try:
            blob = await asyncio.to_thread(
                (Path(self._cfg().store_path) / rel).read_bytes)
        except OSError as e:
            return {'ok': False, 'error': f'кадр недоступен: {e}'}
        return {'ok': True, 'file': rel, 'png': blob, 'size': len(blob)}

    @rpc
    async def stats(self, data: dict = None) -> dict:
        """Объёмы архива (packstore — из манифеста/каталога; raw — обход ФС)."""
        if not self._enabled():
            return {'ok': False, 'error': 'сервис отключён на этом узле'}
        if not self._cfg().collect:
            return {'ok': False, 'error': 'роль collect не активна'}
        if self._store is not None:
            st = await asyncio.to_thread(self._store.stats)
            hosts = await asyncio.to_thread(
                lambda: [{'host': h,
                          'files': sum(d.values()),
                          'bytes': 0}
                         for h, d in self._store.catalog.items()])
            return {'ok': True, 'total_files': st['frames'],
                    'total_bytes': int(st['logical_gb'] * 1024 ** 3),
                    'volumes': st['volumes'],
                    'hosts': {h['host']: {'files': h['files'],
                                          'bytes': h['bytes']}
                              for h in hosts}}
        return await asyncio.to_thread(self._stats_sync)

    @rpc
    async def seal_now(self, data: dict = None) -> dict:
        """Принудительно запечатать активный том packstore (спека §5)."""
        if not self._enabled():
            return {'ok': False, 'error': 'сервис отключён на этом узле'}
        if self._store is None:
            return {'ok': False, 'error': 'packstore не включён '
                    '(eyesauron.store.enabled: true)'}
        vid = await asyncio.to_thread(self._store.seal)
        if vid is None:
            return {'ok': True, 'sealed': None,
                    'note': 'активный том пуст — печатать нечего'}
        return {'ok': True, 'sealed': vid}

    def _stats_sync(self) -> dict:
        root = Path(self._cfg().store_path)
        hosts = {}
        total_files = total_bytes = 0
        if root.is_dir():
            for host_dir in root.iterdir():
                if not host_dir.is_dir():
                    continue
                files = mb = 0
                for day in host_dir.iterdir():
                    if not day.is_dir():
                        continue
                    for f in day.glob('*.png'):
                        try:
                            files += 1
                            mb += f.stat().st_size
                        except OSError:
                            continue
                hosts[host_dir.name] = {'files': files, 'bytes': mb}
                total_files += files
                total_bytes += mb
        return {'ok': True, 'hosts': hosts,
                'total_files': total_files, 'total_bytes': total_bytes}


# ------------------------------------------------------------------ #
#  Защита путей (кадр/навигация принимают только относительные пути)
# ------------------------------------------------------------------ #

def _safe_component(value) -> str | None:
    """Один компонент пути (имя host/date) или None."""
    s = str(value or '').strip()
    if not s or s in ('.', '..') or any(c in s for c in '\\/:*?"<>|'):
        return None
    return s


def _safe_rel(rel: str) -> str | None:
    """Относительный posix-путь к .png внутри store или None."""
    rel = rel.strip().replace('\\', '/')
    if not rel or rel.startswith('/') or ':' in rel:
        return None
    parts = [p for p in rel.split('/') if p not in ('', '.')]
    if not parts or any(p == '..' for p in parts):
        return None
    clean = '/'.join(parts)
    if not clean.lower().endswith('.png'):
        return None
    return clean
