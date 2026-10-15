# src/internal_modules/updater.py
# Перенесено из services/updater/service.py — ядерный модуль обновления.
# =============================================================================
#  Обновление узла по mesh.
#
#  Поток: check (манифесты релизов с узлов-источников через files.read)
#         → download (files.download: resume + sha256)
#         → apply (verify → копия _update.exe → самовызов updater ядра)
#         → boot confirm (новая версия N сек живая → boot_ok; иначе rollback)
#
#  Новая схема: apply не трогает running exe. Делает копию себя в
#    Node_P2P_Core_update.exe, запускает frozen _update.exe с ключами
#    (--updater --old-pid --old-exe --staged --health-confirm-sec).
#  Ядро обновления src/internal_modules/update.py ждёт завершения старого
#  процесса (pid+имя), убивает streamlit-дочки через psutil, заменяет
#  бинарник, запускает новую версию, ждёт health_confirm_sec, иначе откат.
#
#  Откат (boot-marker): state-файл фиксирует pending_boot_confirm. Новая
#  версия после health_confirm_sec здоровой работы ставит boot_ok. Если
#  процесс упал до подтверждения — при следующем старте attempts++ и после
#  MAX_ATTEMPTS возвращается .old, версия блокируется.
#
#  В dev-режиме (не frozen) apply/rollback запрещены — обновлять нечего.
# =============================================================================

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from src.internal_modules.app_version import (
    compare_versions,
    is_newer,
    parse_version,
    read_version,
)
from src.internal_modules.base import ModuleGeneric
from services.rpc import rpc
from src.internal_modules import updater_verify as verify

STATE_FILE = 'update_state.json'
MANIFEST_NAME = 'manifest.json'
MAX_ATTEMPTS = 2            # неудачных загрузок новой версии до отката
RESTART_DELAY_SEC = 3       # legacy (для _do_rollback совместимости)
EXIT_DELAY_SEC = 1          # сколько живём после запуска _update.exe
ROLLOUT_POLL_SEC = 30       # пауза между кругами опроса updater.status в фазе B


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


class Updater(ModuleGeneric):
    def __init__(self, name, context):
        super().__init__(name, context)
        self._known: dict[str, dict] = {}   # version -> {manifest, node, share, exe_rel}
        self._last_check: dict | None = None
        self._tasks: list[asyncio.Task] = []
        self._rollout = None

    # ------------------------------------------------------------------ #
    #  Пути и состояние
    # ------------------------------------------------------------------ #

    def _work_dir(self) -> Path:
        work = getattr(self.ctx.config.local, 'work_dir', None)
        return Path(work) if work else Path('.')

    def _updates_dir(self) -> Path:
        return self._work_dir() / 'updates'

    def _dist_dir(self) -> Path:
        if self._is_frozen():
            return Path(sys.executable).parent / 'dist'
        return Path('dist')

    def _resolve_dst(self, node_or_host: str) -> str:
        """Разрешить node_id/alias/host/IP в реальный node_id для маршрутизации."""
        if node_or_host in (self.ctx.NODE, self.ctx.config.local.alias):
            return self.ctx.NODE
        resolved = self._resolve_by_host(node_or_host)
        if resolved:
            return resolved
        return node_or_host

    def _resolve_by_host(self, host: str) -> str | None:
        """Найти node_id в NeighborTable по host/IP/alias."""
        for info in self.ctx.network.neighbor_table.all():
            if info.host == host or info.node_id == host:
                return info.node_id
        # alias текущего узла мог быть указан как destination
        if host == self.ctx.config.local.alias:
            return self.ctx.NODE
        return None

    def _state_path(self) -> Path:
        return self._work_dir() / STATE_FILE

    def _is_frozen(self) -> bool:
        return getattr(sys, 'frozen', False)

    def _exe_path(self):
        return Path(sys.executable) if self._is_frozen() else None

    def _load_state(self) -> dict:
        try:
            return json.loads(self._state_path().read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def _save_state(self, st: dict):
        p = self._state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(st, ensure_ascii=False, indent=2), encoding='utf-8')

    @staticmethod
    def _validate_transition(current: str, target: str,
                             allow_downgrade: bool, force: bool = False):
        """(ok, error). Целевая версия должна быть новее (или разрешён откат)."""
        if compare_versions(target, current) == 0:
            return False, f'версия {target} уже установлена'
        if is_newer(target, current):
            return True, ''
        if allow_downgrade or force:
            return True, ''
        return False, (f'понижение версии {current} → {target} запрещено '
                       f'(allow_downgrade=false; для ручного даунгрейда '
                       f'передайте force=true)')

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self):
        self._updates_dir().mkdir(parents=True, exist_ok=True)
        cfg = getattr(self.ctx.config, 'update', None)
        st = self._load_state()

        if st.get('pending_boot_confirm') and not st.get('boot_ok'):
            # мы — новая версия, поставленная apply(): подтверждаем здоровье
            st['attempts'] = int(st.get('attempts', 0)) + 1
            st['attempt_started_at'] = time.time()
            self._save_state(st)
            if st['attempts'] > MAX_ATTEMPTS:
                self.log.error(
                    f"версия {st.get('to')} не подтвердила запуск "
                    f"{st['attempts']} раз — ОТКАТ на {st.get('from')}")
                await self._do_rollback(st)
                return
            self.log.info(
                f'boot confirm: попытка {st["attempts"]}/{MAX_ATTEMPTS} '
                f'для версии {st.get("to")}, подтверждение через '
                f'{cfg.health_confirm_sec}с')
            t = asyncio.create_task(self._confirm_boot_later(cfg))
            self._tasks.append(t)

        if cfg.auto_check and cfg.enabled and cfg.sources and self._is_frozen():
            t = asyncio.create_task(self._check_loop())
            self._tasks.append(t)

        self.log.info(f'Updater started: v{read_version()}, '
                      f'frozen={self._is_frozen()}, '
                      f'sources={[s.node for s in cfg.sources]}')

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        self.log.info('Updater stopped')

    async def _confirm_boot_later(self, cfg):
        await asyncio.sleep(max(5, int(cfg.health_confirm_sec)))
        st = self._load_state()
        if not st.get('pending_boot_confirm') or st.get('boot_ok'):
            return
        st['boot_ok'] = True
        st['confirmed_at'] = time.time()
        self._save_state(st)
        self.log.info(f"boot OK: версия {st.get('to')} подтверждена "
                      f"(была {st.get('from')})")

    async def _check_loop(self):
        cfg = self.ctx.config.update
        while True:
            try:
                await self.check({})
            except Exception as e:
                self.log.warning(f'auto-check failed: {e}')
            await asyncio.sleep(max(5, cfg.check_interval_min) * 60)

    # ------------------------------------------------------------------ #
    #  RPC: статус / проверка наличия
    # ------------------------------------------------------------------ #

    @rpc
    def status(self, data: dict = None) -> dict:
        """Сводка для панели: версия, состояние апдейта, конфиг."""
        cfg = self.ctx.config.update
        st = self._load_state()
        staged = self._find_staged_file(st.get('to')) \
            if st.get('to') else None
        return {
            'ok': True,
            'current': read_version(),
            'frozen': self._is_frozen(),
            'state': st or None,
            'last_check': self._last_check,
            'downloaded': staged is not None,
            'config': {
                'sources': [s.node for s in cfg.sources],
                'auto_apply': cfg.auto_apply,
                'require_signed': cfg.require_signed,
                'allow_downgrade': cfg.allow_downgrade,
            },
        }

    def _find_staged_file(self, version: str):
        """Скачанный exe версии в download_dir files-сервиса."""
        name = f'{version}_{self.ctx.config.local.exe_name}'
        p = self._files_download_dir() / name
        return p if p.is_file() else None

    def _files_download_dir(self) -> Path:
        raw = Path(getattr(self.ctx.config.files, 'download_dir',
                           'downloads') or 'downloads')
        if raw.is_absolute():
            return raw
        work = getattr(self.ctx.config.local, 'work_dir', None)
        return (Path(work) / raw) if work else raw

    @rpc
    async def check(self, data: dict = None) -> dict:
        """Опросить источники: доступные версии из манифестов releases."""
        cfg = self.ctx.config.update
        sources = list(cfg.sources)
        wanted = (data or {}).get('node')
        if wanted:
            sources = [s for s in sources if s.node == wanted]
        if not sources:
            return {'ok': False, 'error': 'в update.sources нет источников'}

        current = read_version()

        async def _probe(s):
            """Опрос одного источника: find + параллельные read по entries."""
            dst = self._resolve_dst(s.node)
            local_avail = []
            local_known = {}
            try:
                found = await self.ctx.network.call(
                    dst=dst, service='files', method='find',
                    data={'share': s.share,
                          'pattern': f'*/{MANIFEST_NAME}'},
                    timeout=20)
                if not isinstance(found, dict) or not found.get('ok'):
                    raise RuntimeError((found or {}).get('error',
                                                         'files.find failed')
                                    if isinstance(found, dict) else 'нет ответа')
                entries = found.get('entries', []) or []

                async def _read_one(entry):
                    try:
                        man_res = await self.ctx.network.call(
                            dst=dst, service='files', method='read',
                            data={'share': s.share, 'path': entry['path']},
                            timeout=15)
                    except Exception:
                        return None
                    if not (isinstance(man_res, dict) and man_res.get('ok')):
                        return None
                    try:
                        man = json.loads(man_res['data'].decode('utf-8'))
                    except (ValueError, UnicodeDecodeError):
                        return None
                    ver = man.get('version', '')
                    if parse_version(ver) is None:
                        return None
                    rel_dir = str(Path(entry['path']).parent).replace('\\', '/')
                    exe_rel = f'{rel_dir}/{man.get("exe_name") or self.ctx.config.local.exe_name}'\
                        .replace('//', '/')
                    local_known[ver] = {
                        'manifest': man, 'node': s.node, 'share': s.share,
                        'exe_rel': exe_rel,
                    }
                    return {
                        'version': ver,
                        'node': s.node,
                        'size': man.get('size'),
                        'notes': man.get('notes', ''),
                        'min_compatible': man.get('min_compatible', ''),
                        'newer': is_newer(ver, current),
                    }

                if entries:
                    results = await asyncio.gather(*[_read_one(e) for e in entries])
                    for r in results:
                        if r is not None:
                            local_avail.append(r)
                return local_avail, local_known, {}
            except Exception as e:
                return [], {}, {dst: str(e)}

        # Параллельный опрос всех источников — суммарное время = max, а не sum.
        # Без этого 2 источника * 20с + N*15с > 45с внешнего таймаута web_ui.
        probes = await asyncio.gather(*[_probe(s) for s in sources])
        available = []
        errors = {}
        for local_avail, local_known, local_err in probes:
            available.extend(local_avail)
            self._known.update(local_known)
            errors.update(local_err)

        seen, uniq = set(), []
        for a in sorted(available, key=lambda x: x['version'], reverse=True):
            if a['version'] in seen:
                continue
            seen.add(a['version'])
            uniq.append(a)

        self._last_check = {'at': time.time(),
                            'available': uniq, 'errors': errors}
        return {'ok': True, 'current': current,
                'available': uniq, 'errors': errors}

    # ------------------------------------------------------------------ #
    #  RPC: скачивание / установка / откат
    # ------------------------------------------------------------------ #

    @rpc
    async def download(self, data: dict) -> dict:
        """Скачать exe указанной версии с источника (files.download)."""
        version = (data or {}).get('version')
        info = self._known.get(version)
        if info is None:
            chk = await self.check({})
            if chk.get('errors') and not chk.get('available'):
                return {'ok': False,
                        'error': f'check не удался: {chk["errors"]}'}
            info = self._known.get(version)
        if info is None:
            return {'ok': False, 'error': f'версия {version} не найдена у источников'}

        res = await self.ctx.network.call(
            dst=self.ctx.NODE, service='files', method='download',
            data={'dst': info['node'], 'share': info['share'],
                  'path': info['exe_rel'],
                  'save_as': f'{version}_{self.ctx.config.local.exe_name}'},
            timeout=60)
        if not isinstance(res, dict) or not res.get('ok'):
            err = (res or {}).get('error', 'download failed')
            return {'ok': False, 'error': err}

        # files.download для удалённого источника - асинхронный push-стрим:
        # возвращается сразу с label, файл появляется позже. Ждём staged файл.
        if res.get('done') and res.get('path'):
            return {'ok': True, 'version': version, 'path': str(res.get('path'))}

        expected = (info['manifest'].get('exe_sha256')
                    or info['manifest'].get('id'))
        # ожидание появления файла (poll) - до 60с
        for _ in range(120):
            staged = self._find_staged_file(version)
            if staged:
                if expected and _sha256(staged) != expected:
                    staged.unlink(missing_ok=True)
                    return {'ok': False, 'error': 'sha256 скачанного файла не совпал'}
                return {'ok': True, 'version': version, 'path': str(staged)}
            # проверить, не упал ли files.download в ошибку
            try:
                files_svc = self.ctx.services.get_service('files')
                if files_svc:
                    d_st = (files_svc._downloads or {}).get(res.get('label') or '', {})
                    if d_st.get('status') == 'error':
                        return {'ok': False, 'error': d_st.get('error') or 'download failed'}
            except Exception:
                pass
            await asyncio.sleep(0.5)

        staged = self._find_staged_file(version)
        if staged:
            if expected and _sha256(staged) != expected:
                staged.unlink(missing_ok=True)
                return {'ok': False, 'error': 'sha256 скачанного файла не совпал'}
            return {'ok': True, 'version': version, 'path': str(staged)}
        return {'ok': False, 'error': 'таймаут ожидания файла (проверьте files.downloads)'}

    @rpc
    async def apply(self, data: dict) -> dict:
        """Установить версию: verify → копия _update.exe → запуск ядра обновления.

        data: {version, force?: bool}
        """
        version = (data or {}).get('version')
        force = bool((data or {}).get('force'))
        cfg = self.ctx.config.update

        if not cfg.enabled:
            return {'ok': False, 'error': 'updater отключён (update.enabled)'}
        if not self._is_frozen():
            return {'ok': False, 'error':
                    'apply работает только в frozen-сборке '
                    '(dev-узел обновляется перезапуском из исходников)'}

        current = read_version()
        ok, err = self._validate_transition(
            current, version, cfg.allow_downgrade, force)
        if not ok:
            return {'ok': False, 'error': err}
        st = self._load_state()
        if st.get('pending_boot_confirm') and not st.get('boot_ok'):
            return {'ok': False, 'error':
                    'предыдущее обновление ещё не подтверждено '
                    '(дождитесь или выполните rollback)'}

        info = self._known.get(version)
        if info is None:
            dl = await self.check({})
            if not any(a['version'] == version for a in dl.get('available', [])):
                return {'ok': False, 'error': f'версия {version} недоступна'}

        # 1. скачать если нет
        staged = self._find_staged_file(version)
        if staged is None:
            dl = await self.download({'version': version})
            if not dl.get('ok'):
                return dl
            staged = self._find_staged_file(version)
            if staged is None:
                return {'ok': False, 'error': 'файл не появился после download'}

        # 2. hash + подпись
        expected = (info['manifest'].get('exe_sha256')
                    if info else None) or None
        if expected and _sha256(staged) != expected:
            return {'ok': False, 'error': 'sha256 staged-файла не совпал'}
        if cfg.require_signed:
            sig_ok, sig_detail = verify.verify_signature(staged)
            if not sig_ok:
                return {'ok': False,
                        'error': f'подпись не прошла проверку: {sig_detail}'}

        # 3. подготовить _update.exe и state
        exe = self._exe_path()
        if exe is None:
            return {'ok': False, 'error': 'не удалось определить путь exe'}
        try:
            from src.internal_modules.update import get_updater_exe_path
            updater_exe = get_updater_exe_path(exe)
            shutil.copyfile(exe, updater_exe)
        except OSError as e:
            return {'ok': False, 'error': f'не удалось создать _update.exe: {e}'}

        # сохраняем state ДО запуска updater (watchdog ядра его дополнит)
        old_for_state = exe.with_name(exe.name + '.old')
        self._save_state({
            'from': current, 'to': version,
            'exe': str(exe), 'old_exe': str(old_for_state),
            'staged_from': str(staged),
            'attempts': 0,
            'pending_boot_confirm': True, 'boot_ok': False,
            'rolled_back': False,
            'staged_at': time.time(),
        })

        # 4. запуск _update.exe с ключами (без окна)
        health_sec = int(getattr(cfg, 'health_confirm_sec', 90))
        args = [
            str(updater_exe),
            '--updater',
            '--old-pid', str(os.getpid()),
            '--old-exe', str(exe),
            '--staged', str(staged),
            '--work-dir', str(self._work_dir()),
            '--health-confirm-sec', str(health_sec),
            '--from-version', str(current),
            '--to-version', str(version),
        ]
        try:
            creationflags = 0
            if os.name == 'nt':
                creationflags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
                creationflags |= 0x08000000  # CREATE_NO_WINDOW
            subprocess.Popen(
                args,
                cwd=str(self._work_dir()),
                creationflags=creationflags,
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        except OSError as e:
            # откат создания _update.exe
            try:
                updater_exe.unlink(missing_ok=True)
            except OSError:
                pass
            return {'ok': False, 'error': f'не удалось запустить _update.exe: {e}'}

        self.log.warning(
            f'APPLY: {current} → {version} via _update.exe pid={os.getpid()} health={health_sec}с')

        async def _exit_later():
            await asyncio.sleep(EXIT_DELAY_SEC)
            os._exit(0)

        asyncio.create_task(_exit_later())
        return {'ok': True, 'note':
                f'устанавливается {version} через _update.exe: узел перезапустится автоматически'}

    async def _do_rollback(self, st: dict):
        """Вернуть .old на место и перезапуститься на прежней версии."""
        exe = Path(st.get('exe') or '')
        old = Path(st.get('old_exe') or '')
        if not old.is_file():
            self.log.error('rollback невозможен: .old отсутствует')
            st['rolled_back'] = True
            self._save_state(st)
            return
        failed = exe.with_name(exe.name + '.failed')
        try:
            os.replace(exe, failed)
        except OSError as e:
            self.log.error(f'rollback: не переименовать текущий exe: {e}')
            return
        shutil.copyfile(old, exe)
        st['rolled_back'] = True
        st['rolled_back_at'] = time.time()
        self._save_state(st)
        self.locked_add(st['to'])

        # rollback — legacy detached starter c CREATE_NO_WINDOW
        creationflags_rb = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        if os.name == 'nt':
            creationflags_rb |= 0x08000000
        subprocess.Popen(
            ['cmd', '/c',
             f'timeout /t {RESTART_DELAY_SEC} /nobreak >nul & '
             f'start "" "{exe}"'],
            cwd=str(self._work_dir()),
            creationflags=creationflags_rb,
            close_fds=True,
        )
        self.log.critical(f'ROLLBACK применён: возврат на {st.get("from")}')

        async def _exit_later():
            await asyncio.sleep(EXIT_DELAY_SEC)
            os._exit(0)

        asyncio.create_task(_exit_later())

    # --- список заблокированных версий хранится прямо в state ---

    def locked_add(self, version: str):
        st = self._load_state()
        locked = st.setdefault('locked_versions', [])
        if version not in locked:
            locked.append(version)
            self._save_state(st)

    @rpc
    def clear_state(self, data: dict = None) -> dict:
        """Сбросить состояние (после успешного обновления/разбора полётов)."""
        self._state_path().unlink(missing_ok=True)
        self.log.info('update state очищен')
        return {'ok': True}

    @rpc
    def build(self, data: dict = None) -> dict:
        """Упаковать текущую версию как пакет обновления.

        Создаёт dist/<version>/ с exe и manifest.json.
        data: {notes?: str, min_compatible?: str}
        """
        if not self._is_frozen():
            return {'ok': False, 'error':
                    'упаковка доступна только в frozen-сборке'}

        version = read_version()
        if not version or version == '0.0.0-dev':
            return {'ok': False, 'error': 'не удалось определить версию'}

        dist = self._dist_dir()
        ver_dir = dist / version
        ver_dir.mkdir(parents=True, exist_ok=True)

        exe = self._exe_path()
        exe_name = exe.name
        dest_exe = ver_dir / exe_name

        if not exe.is_file():
            return {'ok': False, 'error': f'exe не найден: {exe}'}

        shutil.copyfile(exe, dest_exe)

        sha = _sha256(dest_exe)
        size = dest_exe.stat().st_size

        d = data or {}
        manifest = {
            'version': version,
            'exe_name': exe_name,
            'exe_sha256': sha,
            'size': size,
            'notes': d.get('notes', ''),
            'min_compatible': d.get('min_compatible', ''),
        }
        (ver_dir / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding='utf-8')

        self.log.info(f'Build packaged: {version} -> {ver_dir}')
        return {'ok': True, 'version': version, 'path': str(ver_dir),
                'manifest': manifest}

    # ------------------------------------------------------------------ #
    #  RPC: массовый rollout по mesh (каркас; реализация — Task 6-8)
    # ------------------------------------------------------------------ #

    @rpc
    async def rollout_start(self, data: dict = None) -> dict:
        import time, uuid
        data = data or {}
        version = (data.get('version') or '').strip()
        if not version:
            return {'ok': False, 'error': 'укажите version'}
        if self._rollout and not self._done(self._rollout.get('task')):
            return {'ok': False, 'error': 'rollout уже идёт'}
        include_self = bool(data.get('include_self', False))
        self._rollout = {
            'plan_id': uuid.uuid4().hex[:8],
            'version': version,
            'include_self': include_self,
            'cancelled': False,
            'task': None,
            'state': {'version': version, 'started_at': time.time(), 'nodes': {}},
        }
        t = asyncio.create_task(self._rollout_run(self._rollout))
        self._rollout['task'] = t
        self._tasks.append(t)  # stop() гасит и фоновый rollout (cancel done-задачи — no-op)
        return {'ok': True, 'plan_id': self._rollout['plan_id']}

    @rpc
    async def rollout_status(self, data: dict = None) -> dict:
        if not self._rollout:
            return {'ok': True, 'state': None}
        return {'ok': True, 'state': self._rollout['state']}

    @rpc
    async def rollout_cancel(self, data: dict = None) -> dict:
        if not self._rollout:
            return {'ok': False, 'error': 'rollout не запущен'}
        self._rollout['cancelled'] = True
        return {'ok': True}

    @staticmethod
    def _done(task) -> bool:
        return task is None or task.done()

    async def _rollout_run(self, ro: dict) -> None:
        """Гард верхнего уровня: смерть задачи всегда фиксируется в журнале."""
        from src.internal_modules.rollout import RolloutJournal
        st = ro['state']
        version = ro.get('version') or st.get('version', '')
        journal = RolloutJournal(self._work_dir(), version)
        try:
            await self._rollout_run_inner(ro)
        except Exception as e:
            st['error'] = f'rollout failed: {e}'
        finally:
            try:
                journal.write(st)
            except Exception:
                pass

    async def _rollout_run_inner(self, ro: dict) -> None:
        """Склейка фаз 0→1→A→B с журналом и проверкой cancelled между шагами."""
        from src.internal_modules.rollout import RolloutJournal
        st = ro['state']
        version = ro.get('version') or st.get('version', '')
        journal = RolloutJournal(self._work_dir(), version)
        # повторный старт с тем же version: готовые ноды не трогаем
        prev = journal.read()
        prev_nodes = {}
        if isinstance(prev, dict) and prev.get('version') == version:
            pn = prev.get('nodes')
            if isinstance(pn, dict):
                prev_nodes = pn
        resume = {n: e for n, e in prev_nodes.items()
                  if isinstance(e, dict)
                  and e.get('phase') in ('updated', 'seeded')}

        def _restore():
            for n, e in resume.items():
                cur = (st.get('nodes') or {}).get(n)
                if cur is None:
                    continue
                cur['phase'] = e.get('phase', cur.get('phase'))
                for k in ('seed_mirror', 'detail', 'version_before'):
                    if k in e and k not in cur:
                        cur[k] = e[k]

        await self._rollout_phase0(ro)
        if st.get('error'):
            journal.write(st)
            return
        if resume:
            _restore()
            journal.write(st)
        if ro.get('cancelled'):
            st['cancelled'] = True
            journal.write(st)
            return

        cfg = self.ctx.config
        admin = self.ctx.NODE
        share = 'releases'
        try:
            sources = list(
                getattr(getattr(cfg, 'update', None), 'sources', []) or [])
            if sources:
                share = getattr(sources[0], 'share', 'releases') or 'releases'
        except Exception:
            pass
        try:
            download_dir = str(getattr(cfg.files, 'download_dir',
                                       'downloads') or 'downloads')
        except Exception:
            download_dir = 'downloads'

        # ensure перезаписывает фазы — готовые восстановим после
        await self._rollout_ensure_all(
            ro, admin=admin, share=share, download_dir=download_dir)
        if resume:
            for n, e in resume.items():
                cur = (st.get('nodes') or {}).get(n)
                if cur is not None and cur.get('phase') in (
                        'pending', 'ensure_failed', 'unreachable'):
                    cur['phase'] = e.get('phase', cur.get('phase'))
                    for k in ('seed_mirror', 'detail', 'version_before'):
                        if k in e and k not in cur:
                            cur[k] = e[k]
            journal.write(st)
        if ro.get('cancelled'):
            st['cancelled'] = True
            journal.write(st)
            return

        waves = st.get('waves_seed') or []
        prev_wave: list[str] = []
        for w in waves:
            if ro.get('cancelled'):
                st['cancelled'] = True
                journal.write(st)
                return
            todo = [n for n in w
                    if (((st.get('nodes') or {}).get(n) or {})
                        .get('phase') not in ('seeded', 'updated'))]
            if todo:
                await self._rollout_seed_wave(ro, todo, prev_wave)
            else:
                journal.write(st)
            prev_wave = w
            if ro.get('cancelled'):
                st['cancelled'] = True
                journal.write(st)
                return
        await self._rollout_apply(st, admin)
        journal.write(st)

    async def _rollout_poll_once(self, dst: str,
                                 version: str) -> tuple[str | None, str]:
        """Один опрос updater.status ноды.

        Возвращает (терминальная фаза | None, detail). None — не терминально
        (нода ещё в pending_boot_confirm или опрос пропущен), ждать дальше.
        """
        try:
            res = await self.ctx.network.call(
                dst=dst, service='updater', method='status',
                data={}, timeout=15)
        except Exception as e:
            return None, str(e)
        if not isinstance(res, dict) or not res.get('ok'):
            err = (res or {}).get('error', 'updater.status failed') \
                if isinstance(res, dict) else 'updater.status failed'
            return None, err
        state = res.get('state')
        if isinstance(state, dict) and state:
            if state.get('rolled_back'):
                to = state.get('to')
                return ('rolled_back',
                        f'нода откатилась{(" на " + str(to)) if to else ""}')
            if state.get('boot_ok'):
                return 'updated', ''
            return None, ''
        if res.get('current') == version:
            return 'updated', ''
        return None, ''

    async def _rollout_poll_status(self, dst: str, version: str,
                                   retries: int) -> tuple[str | None, str]:
        """До retries опросов статуса; между кругами пауза ROLLOUT_POLL_SEC."""
        last = ''
        for i in range(max(1, retries)):
            phase, detail = await self._rollout_poll_once(dst, version)
            if phase in ('updated', 'rolled_back'):
                return phase, detail
            if detail:
                last = detail
            if i < max(1, retries) - 1:
                await asyncio.sleep(ROLLOUT_POLL_SEC)
        return None, last or 'не подтвердил обновление'

    async def _rollout_apply(self, state: dict, admin: str) -> None:
        """Фаза B: канарейка, веер apply, сбор статусов. Мутирует state."""
        version = state.get('version', '')
        nodes = state.get('nodes') or {}
        cfg = getattr(self.ctx.config, 'update', None)
        health_sec = max(0, int(getattr(cfg, 'health_confirm_sec', 90) or 0))
        retries = max(1, int(getattr(cfg, 'health_confirm_retries', 3) or 1))

        # канарейка — первая seeded из waves_seed[0] (hop=1), иначе из волн
        waves = state.get('waves_seed') or []
        canary = None
        first_wave = (waves[0] if waves else []) or []
        for n in first_wave:
            if (nodes.get(n) or {}).get('phase') == 'seeded':
                canary = n
                break
        if canary is None:
            for w in waves:
                for n in w or []:
                    if (nodes.get(n) or {}).get('phase') == 'seeded':
                        canary = n
                        break
                if canary is not None:
                    break
        if canary is None:
            for n, e in nodes.items():
                if (e or {}).get('phase') == 'seeded':
                    canary = n
                    break
        if canary is None:
            state['error'] = 'нечего обновлять'
            return

        async def _call_apply(name: str):
            try:
                return await self.ctx.network.call(
                    dst=name, service='updater', method='apply',
                    data={'version': version}, timeout=120)
            except Exception as e:
                return {'ok': False, 'error': str(e)}

        def _apply_err(res) -> str:
            if isinstance(res, dict):
                return res.get('error', 'updater.apply failed')
            return 'updater.apply failed'

        # --- канарейка ---
        entry = nodes.get(canary) or {}
        entry['phase'] = 'applying'
        nodes[canary] = entry
        res = await _call_apply(canary)
        if not isinstance(res, dict) or not res.get('ok'):
            entry['phase'] = 'apply_failed'
            entry['detail'] = _apply_err(res)
            state['aborted_canary'] = True
            return
        await asyncio.sleep(health_sec)
        final, detail = await self._rollout_poll_status(
            canary, version, retries)
        if final == 'updated':
            entry['phase'] = 'updated'
        elif final == 'rolled_back':
            entry['phase'] = 'rolled_back'
            if detail:
                entry['detail'] = detail
            state['aborted_canary'] = True
            return
        else:
            entry['phase'] = 'apply_failed'
            entry['detail'] = detail
            state['aborted_canary'] = True
            return

        # --- веер: остальные seeded, дальние→ближние, батчи по 4 ---
        rest = [n for n, e in nodes.items()
                if n != canary and (e or {}).get('phase') == 'seeded']
        rest.sort(key=lambda n: -((nodes.get(n) or {}).get('hop') or 0))
        sem = asyncio.Semaphore(4)

        async def _apply_one(name: str):
            async with sem:
                e = nodes.get(name) or {}
                e['phase'] = 'applying'
                nodes[name] = e
                r = await _call_apply(name)
                if not isinstance(r, dict) or not r.get('ok'):
                    e['phase'] = 'apply_failed'
                    e['detail'] = _apply_err(r)

        await asyncio.gather(*[_apply_one(n) for n in rest])
        pending = [n for n in rest
                   if (nodes.get(n) or {}).get('phase') == 'applying']
        if not pending:
            return
        await asyncio.sleep(health_sec)
        last_err: dict[str, str] = {}
        for i in range(retries):
            if i > 0:
                await asyncio.sleep(ROLLOUT_POLL_SEC)

            async def _poll_one(name: str):
                async with sem:
                    ph, det = await self._rollout_poll_once(name, version)
                    return name, ph, det

            for name, ph, det in await asyncio.gather(
                    *[_poll_one(n) for n in pending]):
                if ph == 'updated':
                    nodes[name]['phase'] = 'updated'
                elif ph == 'rolled_back':
                    nodes[name]['phase'] = 'rolled_back'
                    if det:
                        nodes[name]['detail'] = det
                elif det:
                    last_err[name] = det
            pending = [n for n in pending
                       if (nodes.get(n) or {}).get('phase') == 'applying']
            if not pending:
                break
        for n in pending:
            nodes[n]['phase'] = 'apply_failed'
            nodes[n]['detail'] = (last_err.get(n)
                                  or 'не подтвердил обновление')

    @staticmethod
    def _remote_download_dir(text: str, fallback: str) -> str:
        try:
            import yaml as _yaml
            _parsed = _yaml.safe_load(text) or {}
            return ((_parsed.get('files') or {}).get('download_dir')) or fallback
        except Exception:
            return fallback

    async def _rollout_ensure_node(self, dst: str, admin: str, share: str, download_dir: str) -> dict:
        from src.internal_modules.rollout import ensure_cdn_share, ensure_update_source
        try:
            got = await self.ctx.network.call(
                dst=dst, service='config', method='get', data={}, timeout=15)
        except Exception as e:
            return {'phase': 'unreachable', 'detail': str(e)}
        if not isinstance(got, dict) or not got.get('ok'):
            return {'phase': 'unreachable',
                    'detail': (got or {}).get('error', 'config.get failed')
                    if isinstance(got, dict) else 'config.get failed'}
        text = got.get('text', '')
        base_mtime = got.get('mtime')
        # download_dir удаленной ноды — из ЕЕ конфига
        _dd = self._remote_download_dir(text, download_dir)
        new_text = text
        patched = ensure_update_source(new_text, admin, share)
        if patched is not None:
            new_text = patched
        patched = ensure_cdn_share(new_text, str(_dd))
        if patched is not None:
            new_text = patched
        if new_text == text:
            return {'phase': 'pending'}
        for attempt in range(2):
            try:
                saved = await self.ctx.network.call(
                    dst=dst, service='config', method='save',
                    data={'text': new_text, 'base_mtime': base_mtime, 'restart': False},
                    timeout=15)
            except Exception as e:
                return {'phase': 'unreachable', 'detail': str(e)}
            if isinstance(saved, dict) and saved.get('ok'):
                return {'phase': 'pending'}
            if isinstance(saved, dict) and saved.get('conflict') and attempt == 0:
                try:
                    got = await self.ctx.network.call(
                        dst=dst, service='config', method='get', data={}, timeout=15)
                except Exception as e:
                    return {'phase': 'unreachable', 'detail': str(e)}
                if not isinstance(got, dict) or not got.get('ok'):
                    return {'phase': 'unreachable', 'detail': 'config.get failed'}
                text = got.get('text', '')
                base_mtime = got.get('mtime')
                _dd = self._remote_download_dir(text, download_dir)
                new_text = text
                p1 = ensure_update_source(new_text, admin, share)
                if p1 is not None:
                    new_text = p1
                p2 = ensure_cdn_share(new_text, str(_dd))
                if p2 is not None:
                    new_text = p2
                if new_text == text:
                    return {'phase': 'pending'}
                continue
            err = (saved or {}).get('error', 'config.save failed') \
                if isinstance(saved, dict) else 'config.save failed'
            return {'phase': 'ensure_failed', 'detail': err}
        return {'phase': 'ensure_failed', 'detail': 'conflict: config изменён'}

    async def _rollout_phase0(self, ro: dict) -> None:
        from src.internal_modules.rollout import RolloutJournal, plan_waves
        st = ro['state']
        try:
            topo = await self.ctx.network.call(
                dst=self.ctx.NODE, service='netinfo', method='topology',
                data={'ttl': 4}, timeout=30)
        except Exception as e:
            st['error'] = f'topology failed: {e}'
            return
        if not isinstance(topo, dict) or not topo.get('ok'):
            st['error'] = 'topology failed'
            return
        seed, apply = plan_waves(topo, self.ctx.NODE)
        if not ro.get('include_self'):
            seed = [[n for n in w if n != self.ctx.NODE] for w in seed]
            apply = [[n for n in w if n != self.ctx.NODE] for w in apply]
            seed = [w for w in seed if w]
            apply = [w for w in apply if w]
        st['waves_seed'] = seed
        st['waves_apply'] = apply
        nodes: dict[str, dict] = {}
        for i, wave in enumerate(seed):
            for n in wave:
                nodes[n] = {'hop': i + 1, 'phase': 'pending'}
        if nodes:
            async def _one_status(name: str):
                try:
                    res = await self.ctx.network.call(
                        dst=name, service='updater', method='status',
                        data={}, timeout=10)
                except Exception as e:
                    return name, None, str(e)
                return name, res, None

            for name, res, err in await asyncio.gather(
                    *[_one_status(n) for n in nodes]):
                entry = nodes.get(name) or {}
                if err is not None:
                    entry['phase'] = 'unreachable'
                    entry['detail'] = err
                elif isinstance(res, dict) and res.get('ok'):
                    if res.get('current') is not None:
                        entry['version_before'] = res.get('current')
                else:
                    entry['phase'] = 'unreachable'
                    entry['detail'] = (res or {}).get(
                        'error', 'updater.status failed') \
                        if isinstance(res, dict) else 'updater.status failed'
                nodes[name] = entry
        st['nodes'] = nodes
        journal = RolloutJournal(self._work_dir(), ro['version'])
        journal.write(st)

    async def _rollout_ensure_all(self, ro: dict, admin: str, share: str, download_dir: str) -> None:
        from src.internal_modules.rollout import RolloutJournal
        st = ro['state']
        nodes = st.get('nodes') or {}
        if not nodes:
            return

        async def _one(name: str):
            try:
                res = await self._rollout_ensure_node(
                    name, admin=admin, share=share, download_dir=download_dir)
            except Exception as e:
                res = {'phase': 'unreachable', 'detail': str(e)}
            return name, res

        results = await asyncio.gather(*[_one(n) for n in nodes])
        for name, res in results:
            entry = nodes.get(name) or {}
            entry.update(res)
            entry['phase'] = res.get('phase', 'ensure_failed')
            nodes[name] = entry
        st['nodes'] = nodes
        journal = RolloutJournal(self._work_dir(), ro['version'])
        journal.write(st)

    async def _rollout_seed_node(self, dst: str, mirror: str | None,
                                 staged: str, expect_sha: str,
                                 version: str) -> dict:
        """Засидировать одну ноду staged-файлом версии.

        mirror is None (wave0) — нода сама тянет с админа через
        `updater.download` (он ходит в `update.sources`). Иначе ноде
        приказывается `files.download` с явного зеркала (mirror).
        `files.download` отвечает сразу `{ok, label}`, файл докачивается
        фоном — ждём done через `files.downloads`, затем сверяем sha
        через `files.stat` (`manifest.id == expect_sha`).
        """
        try:
            if mirror is None:
                dl = await self.ctx.network.call(
                    dst=dst, service='updater', method='download',
                    data={'version': version}, timeout=120)
                if not isinstance(dl, dict) or not dl.get('ok'):
                    err = (dl or {}).get('error', 'download failed') \
                        if isinstance(dl, dict) else 'download failed'
                    return {'phase': 'seed_failed', 'detail': err}
            else:
                dl = await self.ctx.network.call(
                    dst=dst, service='files', method='download',
                    data={'dst': mirror, 'share': 'cdn_update',
                          'path': staged, 'save_as': staged},
                    timeout=60)
                if not isinstance(dl, dict) or not dl.get('ok'):
                    err = (dl or {}).get('error', 'download failed') \
                        if isinstance(dl, dict) else 'download failed'
                    return {'phase': 'seed_failed', 'detail': err}
                label = dl.get('label')
                if not dl.get('done') and label:
                    done = False
                    for _ in range(120):
                        lst = await self.ctx.network.call(
                            dst=dst, service='files', method='downloads',
                            data={}, timeout=10)
                        if isinstance(lst, dict) and lst.get('ok'):
                            entries = lst.get('downloads') or []
                            match = next(
                                (e for e in entries
                                 if isinstance(e, dict)
                                 and e.get('label') == label),
                                None)
                            if match is not None:
                                if match.get('status') == 'done':
                                    done = True
                                    break
                                if match.get('status') == 'error':
                                    return {
                                        'phase': 'seed_failed',
                                        'detail': match.get('error')
                                        or 'files.download failed',
                                    }
                        await asyncio.sleep(0.5)
                    if not done:
                        return {'phase': 'seed_failed',
                                'detail': 'таймаут ожидания files.download'}
            st = await self.ctx.network.call(
                dst=dst, service='files', method='stat',
                data={'share': 'cdn_update', 'path': staged}, timeout=30)
            if not isinstance(st, dict) or not st.get('ok'):
                err = (st or {}).get('error', 'files.stat failed') \
                    if isinstance(st, dict) else 'files.stat failed'
                return {'phase': 'seed_failed', 'detail': err}
            got = ((st.get('manifest') or {}).get('id')
                   if isinstance(st.get('manifest'), dict) else None)
            if got != expect_sha:
                return {'phase': 'seed_failed',
                        'detail': f'sha mismatch: ожидался {expect_sha}, '
                                  f'на ноде {got}'}
            return {'phase': 'seeded'}
        except Exception as e:
            return {'phase': 'seed_failed', 'detail': str(e)}

    async def _rollout_seed_wave(self, ro: dict, wave: list[str],
                                 mirrors: list[str]) -> None:
        """Засидировать одну волну: батчи по 4, цепочка зеркал на ноду.

        Для каждой ноды перебираются зеркала (только `seeded` из
        предыдущей волны по порядку), затем деградация в A —
        `mirror=None` (нода тянет с админа напрямую). Первая удача
        фиксируется; битое зеркало (seed_failed) зеркалом не становится.
        """
        from src.internal_modules.rollout import RolloutJournal
        st = ro['state']
        if ro.get('cancelled'):
            return
        version = ro.get('version') or st.get('version', '')
        staged = f"{version}_{self.ctx.config.local.exe_name}"
        try:
            man = json.loads(
                (self._dist_dir() / version / 'manifest.json')
                .read_text(encoding='utf-8'))
            expect_sha = man.get('exe_sha256') or man.get('id')
        except (OSError, ValueError):
            expect_sha = None
        if not expect_sha:
            for n in wave:
                entry = (st.get('nodes') or {}).get(n) or {}
                entry['phase'] = 'seed_failed'
                entry['detail'] = \
                    f'нет локального dist/{version}/manifest.json'
                st['nodes'][n] = entry
            RolloutJournal(self._work_dir(), version).write(st)
            return
        seeded = [m for m in (mirrors or [])
                  if ((st.get('nodes') or {}).get(m) or {}).get('phase')
                  == 'seeded']
        sem = asyncio.Semaphore(4)

        async def _one(name: str):
            async with sem:
                try:
                    entry = (st.get('nodes') or {}).get(name) or {}
                    entry['phase'] = 'seeding'
                    st['nodes'][name] = entry
                    last: dict = {'phase': 'seed_failed',
                                  'detail': 'нет доступных зеркал'}
                    for m in seeded + [None]:
                        res = await self._rollout_seed_node(
                            name, mirror=m, staged=staged,
                            expect_sha=expect_sha, version=version)
                        if res.get('phase') == 'seeded':
                            last = dict(res)
                            last['seed_mirror'] = m
                            break
                        last = res
                    entry.update(last)
                    entry['phase'] = last.get('phase', 'seed_failed')
                    st['nodes'][name] = entry
                except Exception as e:
                    entry = (st.get('nodes') or {}).get(name) or {}
                    entry['phase'] = 'seed_failed'
                    entry['detail'] = str(e)
                    st['nodes'][name] = entry

        await asyncio.gather(*[_one(n) for n in wave])
        RolloutJournal(self._work_dir(), version).write(st)
