# services/certs_tool/service.py — управление КриптоПро сертификатами
# Переработано из legacy/dist/services/certs_tool под текущую архитектуру:
#   BaseService → ModuleGeneric, @service_method → @rpc, proxy_client → ctx

import asyncio
import base64
import re
import secrets
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from src.internal_modules.base import ModuleGeneric
from src.networking.protocol import MsgPack, PackType
from src.se.storage import SecureStorage
try:
    from src.se.wts import WTS_SESSION, get_sessions, run_in_session, run_in_session_output
except ImportError:
    WTS_SESSION = None
    get_sessions = None
    run_in_session = None
    run_in_session_output = None
from services.rpc import rpc


class CertsTool(ModuleGeneric):
    """
    Сервис управления КриптоПро сертификатами (CSP).

    Обеспечивает:
    - Развертывание сертификатов из PFX/CER
    - Экспорт сертификатов в PFX/CER (base64)
    - Поиск и листинг установленных сертификатов
    - Пакетная установка со сменой пароля
    - CERT_SYNC: периодическая рассылка digest в mesh
    - Сетевая установка: экспорт с удалённого узла → импорт локально
    """

    _CARRIER = '\\\\.\\HDIMAGE'

    # Признаки отсутствия КриптоПро в выводе certmgr/csptest
    _CSP_MISSING_MARKERS = (
        'Не удалось получить контекст',
        'Тип поставщика не определен',
    )

    # Каталоги binaries для разных версий CSP
    _CSP_VERSION_DIRS = {'5': 'v5', '4': 'v4'}

    def __init__(self, name: str, context):
        super().__init__(name, context)
        # Определяем версию установленного в системе КриптоПро CSP
        self.csp_version, self.csp_version_full = self._detect_csp_version()
        self._cert_sync_task: asyncio.Task | None = None
        self._install_history: list[dict] = []
        self._terminated: bool = False
        # Теперь определяем путь к бинарникам на основе версии
        self.csp_path = self._detect_csp_path(self.csp_version)
        self.log.info(f'CertsTool started (csp_path={self.csp_path}, csp_version={self.csp_version}, full={self.csp_version_full})')

    def _parse_csp_output(self, out: str) -> tuple[str | None, str | None]:
        """Парсит вывод csptest -version -> (major, full). Поддерживает CryptoPro / Crypto-Pro, RU/EN."""
        if not out:
            return None, None
        if re.search(r'КРИПТО[-\s]*ПРО', out, re.IGNORECASE):
            return '5', None
        if re.search(r'Crypto[-\s]*Pro', out, re.IGNORECASE):
            return '4', None
        return None, None

    def _detect_csp_version(self) -> tuple[str | None, str | None]:
        """Detect installed system Cryptopro CSP version. Returns (major, full)."""
        # Расширяем where — находим реальный путь csptest/certmgr в PATH, чтобы не вызвать виндовый certmgr
        def _where_candidates(name: str) -> list[str]:
            try:
                r = subprocess.run(f'where {name}', shell=True, capture_output=True, text=True, encoding='cp1251', errors='ignore', timeout=3)
                out = (r.stdout or '').strip()
                cand = []
                for line in out.splitlines():
                    line=line.strip().strip('"')
                    if line and 'Crypto' in line:
                        cand.append(line)
                    elif line and name.lower() in line.lower() and 'Windows' not in line:
                        cand.append(line)
                return cand
            except Exception:
                return []

        where_csptest = _where_candidates('csptest')
        candidates = list(dict.fromkeys(where_csptest + [
            'csptest',
            r'C:\Program Files\Crypto Pro\CSP\csptest.exe',
            r'C:\Program Files\CryptoPro\CSP\csptest.exe',
            r'C:\Program Files (x86)\Crypto Pro\CSP\csptest.exe',
            r'C:\Program Files (x86)\CryptoPro\CSP\csptest.exe',
            r'C:\Program Files\Common Files\Crypto Pro\CSP\csptest.exe',
            r'C:\Program Files\Common Files\CryptoPro\CSP\csptest.exe',
        ]))
        for exe in candidates:
            try:
                # кавычки нужны только если в пути пробелы
                exe_q = f'"{exe}"' if ' ' in exe and not exe.startswith('"') else exe
                full_command = f'chcp 1251 >nul && {exe_q} -version'
                result = subprocess.run(
                    full_command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding='cp1251',
                    errors='ignore',
                    timeout=5,
                )
                out = (result.stdout or '') + '\n' + (result.stderr or '')
                major, full = self._parse_csp_output(out)
                if major:
                    self.log.info(f'CSP version detected via {exe}: major={major}, full={full}')
                    return major, full
                else:
                    self.log.debug(f'csptest -version via {exe} no match, out={out[:200]!r}')
            except Exception as e:
                self.log.debug(f'CSP version probe {exe} failed: {e}')
                continue

        # Fallback 2: certmgr -help — различие по копирайту/году
        # v5: 'Certmgr (c) "КРИПТО-ПРО", 2007-2025.'  v4: 'Certmgr 1.1 (c) "Crypto-Pro", 2007-2018.'
        # Год может меняться, но v4 не перевалит за 2018, v5 уже >=2025 — эвристика по году.
        def _parse_help(out: str) -> tuple[str | None, str | None]:
            if not out:
                return None, None
            m = re.search(r'2007-(\d{4})', out)
            if m:
                year = int(m.group(1))
                # 2018 и раньше — v4, 2020+ — v5
                if year >= 2020:
                    return '5', f'5.x (help {year})'
                else:
                    return '4', f'4.x (help {year})'
            if 'КРИПТО-ПРО' in out:
                return '5', None
            if 'Crypto-Pro' in out and 'Certmgr 1.1' in out:
                return '4', None
            return None, None

        # 'certmgr' без пути — виндовый, пропускаем. Ищем только КриптоПро.
        where_certmgr = [p for p in _where_candidates('certmgr') if 'Crypto' in p]
        help_candidates = list(dict.fromkeys(where_certmgr + [
            r'C:\Program Files\Crypto Pro\CSP\certmgr.exe',
            r'C:\Program Files\CryptoPro\CSP\certmgr.exe',
            r'C:\Program Files (x86)\Crypto Pro\CSP\certmgr.exe',
            r'C:\Program Files (x86)\CryptoPro\CSP\certmgr.exe',
            r'C:\Program Files\Common Files\Crypto Pro\CSP\certmgr.exe',
            str(Path(__file__).parent / 'v5' / 'certmgr.exe'),
            str(Path(__file__).parent / 'v4' / 'certmgr.exe'),
        ]))
        for exe in help_candidates:
            try:
                exe_q = f'"{exe}"' if ' ' in exe and not exe.startswith('"') else exe
                full_command = f'chcp 1251 >nul && {exe_q} -help'
                result = subprocess.run(
                    full_command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding='cp1251',
                    errors='ignore',
                    timeout=5,
                )
                out = (result.stdout or '') + '\n' + (result.stderr or '')
                major, full = _parse_help(out)
                if major:
                    self.log.info(f'CSP version via certmgr -help {exe}: major={major}, help_year={full}')
                    return major, full
            except Exception as e:
                self.log.debug(f'certmgr help probe {exe} failed: {e}')
                continue

        # Fallback 3: реестр Windows (если csptest/certmgr не в PATH)
        try:
            import winreg
            for hive, sub in [
                (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Crypto Pro\Cryptography\CurrentVersion'),
                (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Crypto Pro\Cryptography\CurrentVersion'),
            ]:
                try:
                    with winreg.OpenKey(hive, sub) as k:
                        for val in ('Version', 'ProductVersion', 'DisplayVersion'):
                            try:
                                v, _ = winreg.QueryValueEx(k, val)
                                major, full = self._parse_csp_output(str(v))
                                if major:
                                    self.log.info(f'CSP version from registry {sub}\\{val}: {v}')
                                    return major, full
                            except FileNotFoundError:
                                continue
                except FileNotFoundError:
                    continue
        except Exception as e:
            self.log.debug(f'Registry CSP probe failed: {e}')

        self.log.warning('CSP version not detected — fallback to unknown')
        return None, None

    def _detect_csp_path(self, detected_version: str | None = None) -> Path:
        """Detect CSP version and return path to appropriate binaries directory.
        Приоритет — системный CSP (C:\\Program Files\\Crypto Pro\\CSP), т.к. bundled v4/v5
        может не видеть контейнеры системного CSP 5.x.
        """
        # 1. Попытка найти системный certmgr (предпочтение — он видит реальные хранилища)
        try:
            # reuse _where logic: ищем в PATH и фиксированных путях
            import subprocess as _sp
            def _where_sys(name: str) -> list[str]:
                try:
                    r = _sp.run(f'where {name}', shell=True, capture_output=True, text=True, encoding='cp1251', errors='ignore', timeout=2)
                    out = (r.stdout or '').strip()
                    res = []
                    for line in out.splitlines():
                        line=line.strip().strip('"')
                        if line and 'Crypto' in line and Path(line).exists():
                            res.append(line)
                    return res
                except Exception:
                    return []
            sys_candidates = _where_sys('certmgr')
            sys_dirs = []
            for exe in sys_candidates:
                d = Path(exe).parent
                if (d / 'certmgr.exe').exists():
                    sys_dirs.append(d)
            for d in [r'C:\Program Files\Crypto Pro\CSP', r'C:\Program Files\CryptoPro\CSP',
                      r'C:\Program Files (x86)\Crypto Pro\CSP', r'C:\Program Files (x86)\CryptoPro\CSP',
                      r'C:\Program Files\Common Files\Crypto Pro\CSP']:
                pd = Path(d)
                if (pd / 'certmgr.exe').exists() and pd not in sys_dirs:
                    sys_dirs.append(pd)
            if sys_dirs:
                # выбираем первый, логируем все
                chosen = sys_dirs[0]
                self.log.info(f'Using SYSTEM CSP directory: {chosen} (candidates={sys_dirs})')
                return chosen
        except Exception as e:
            self.log.debug(f'system CSP probe failed: {e}')

        # 2. Bundled по версии
        if detected_version and detected_version in self._CSP_VERSION_DIRS:
            version_dir = self._CSP_VERSION_DIRS[detected_version]
            candidate = Path(__file__).parent / version_dir
            if candidate.exists() and (candidate / 'certmgr.exe').exists():
                self.log.info(f'Using CSP version directory: {candidate}')
                return candidate
            self.log.warning(f'CSP version {detected_version} directory not found or incomplete: {candidate}')

        # Фоллбек: используем v4 (старшая совместимость)
        fallback = Path(__file__).parent / 'v4'
        if fallback.exists() and (fallback / 'certmgr.exe').exists():
            self.log.info(f'Using fallback CSP directory: {fallback}')
            return fallback

        # Последний фоллбек: корневой каталог certstool
        root = Path(__file__).parent
        self.log.warning(f'Using root CSP directory (no version detection): {root}')
        return root

    async def start(self):
        self._validate_csp_path()
        self._cert_sync_task = asyncio.create_task(self._cert_sync_loop())
        self.log.info(f'CertsTool started (csp_path={self.csp_path})')

    async def stop(self):
        if self._cert_sync_task:
            self._cert_sync_task.cancel()
        self.log.info('CertsTool stopped')

    async def _terminate_no_csp(self):
        """КриптоПро отсутствует на ПК — завершить certstool
        (установка ГОСТ сертификатов не требуется)."""
        if self._terminated:
            return
        self._terminated = True
        self.log.error(
            'CryptoPro CSP not available (provider context error) — '
            'terminating certstool: GOST certificate management is not required'
        )
        if self._cert_sync_task and not self._cert_sync_task.done():
            self._cert_sync_task.cancel()
        try:
            self.ctx.services.remove_service(self)
        except Exception as e:
            self.log.warning(f'Failed to unregister certstool service: {e}')

    # ------------------------------------------------------------------ #
    #  CERT_SYNC — периодическая рассылка digest сертификатов
    # ------------------------------------------------------------------ #

    async def _cert_sync_loop(self):
        """Каждые 60с: обновить CertsIndex из локальных сертификатов,
        рассылать CERT_SYNC всем connected соседям."""
        while True:
            try:
                await asyncio.sleep(60)

                # 1. Обновить локальные сертификаты в индексе
                # (update_local инкрементирует certs_index.sync_version — D3)
                certs = await self.list_certificates({})
                self.ctx.certs_index.update_local(certs)

                # 2. Подготовить digest для рассылки
                digest = self.ctx.certs_index.get_digest_for_sync()
                sync_version = self.ctx.certs_index.sync_version

                # 3. Рассылать CERT_SYNC всем connected соседям
                pack = MsgPack(
                    type=PackType.CERT_SYNC,
                    source=self.ctx.NODE,
                    data={
                        'certs': digest,
                        'sync_version': sync_version,
                    },
                )
                transports = [
                    self.ctx.network.router.get_transport_to(n.node_id)
                    for n in self.ctx.network.neighbor_table.connected()
                    # get_transport_to умеет в server-side И client-side WS
                    # (B8: через nodes_manager.get() client-side соседи
                    # вообще не получали CERT_SYNC)
                ]
                await asyncio.gather(
                    *[t.send(pack) for t in transports if t],
                    return_exceptions=True,
                )

                self.log.debug(f'CERT_SYNC broadcast: {len(digest)} certs, v{sync_version}')

            except asyncio.CancelledError:
                return
            except Exception as e:
                self.log.error(f'CERT_SYNC loop error: {e}')
                await asyncio.sleep(10)

    # ------------------------------------------------------------------ #
    #  Внутренние утилиты
    # ------------------------------------------------------------------ #

    def _validate_csp_path(self):
        if not self.csp_path.exists():
            self.log.warning(f'CSP path not found: {self.csp_path}')
            return
        missing = [t for t in ('certmgr.exe', 'csptest.exe')
                    if not (self.csp_path / t).exists()]
        if missing:
            self.log.warning(f'Missing CSP tools: {missing}')

    # Маркеры пустого хранилища — не ошибка
    _EMPTY_MARKERS = ('Список сертификатов пуст', 'No certificates', '0 certificates')

    def _extract_session(self, data: dict | None) -> int | None:
        """session_id из RPC data (int/str), None = SYSTEM/session 0."""
        if not isinstance(data, dict):
            return None
        sid = data.get('session_id', data.get('sessionId', None))
        if sid is None or sid == '' or str(sid).lower() == 'none':
            return None
        try:
            v = int(sid)
            return v if v != 0 else None
        except Exception:
            return None

    def _shared_tmp_path(self, suffix: str) -> Path:
        """Кросс-сессионный tmp (work_dir доступен и SYSTEM и user)."""
        try:
            base = Path(getattr(self.ctx.config.local, 'work_dir', tempfile.gettempdir()))
        except Exception:
            base = Path(tempfile.gettempdir())
        d = base / 'cert_tmp'
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return d / f'cert_{secrets.token_hex(4)}{suffix}'

    async def _run_async(self, command: str, session_id: int | None = None) -> str:
        if self._terminated:
            return ''
        # session-aware путь — без окна, в контексте пользователя
        if session_id is not None and run_in_session_output is not None:
            try:
                cmdline = f'cmd.exe /c chcp 1251 >nul && {command}'
                self.log.debug(f'WTS exec session={session_id}: {command[:120]}')
                out = await asyncio.to_thread(run_in_session_output, cmdline, int(session_id), None, 45)
                self.log.debug(f'WTS exec done session={session_id} out_len={len(out)}')
                if any(m in out for m in self._CSP_MISSING_MARKERS):
                    await self._terminate_no_csp()
                    return ''
                # EMPTY markers — считаем пустым только если реально 0 сертификатов, но логируем
                if any(m in out for m in self._EMPTY_MARKERS):
                    self.log.info(f'certmgr: empty store (session {session_id}) raw={out[:300]!r}')
                    return ''
                if not out.strip():
                    self.log.warning(f'WTS exec empty output session={session_id} cmd={command[:80]}')
                return out
            except Exception as e:
                self.log.error(f'Command error (session {session_id}): {e}', exc_info=True)
                return ''
        try:
            # chcp 1251 в том же shell-контексте, что и основная команда
            full_command = f'chcp 1251 && {command}'
            proc = await asyncio.create_subprocess_shell(
                full_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            def _decode(b: bytes) -> str:
                for enc in ('cp1251', 'cp866', 'utf-8'):
                    try:
                        return b.decode(enc)
                    except UnicodeDecodeError:
                        continue
                return b.decode('cp1251', errors='ignore')
            out = _decode(stdout)
            err_text = _decode(stderr) if stderr else ''
            if err_text.strip():
                if any(m in err_text for m in self._CSP_MISSING_MARKERS):
                    await self._terminate_no_csp()
                    return ''
                if any(m in err_text for m in self._EMPTY_MARKERS) or any(m in out for m in self._EMPTY_MARKERS):
                    self.log.info(f'certmgr: empty store ({err_text[:120]})')
                    return ''
                self.log.warning(f'certmgr stderr: {err_text[:500]}')
                out += '\n' + err_text
            if any(m in out for m in self._CSP_MISSING_MARKERS):
                await self._terminate_no_csp()
                return ''
            if any(m in out for m in self._EMPTY_MARKERS):
                self.log.info('certmgr: empty store')
                return ''
            return out
        except Exception as e:
            self.log.error(f'Command error: {e}')
            return ''

    @staticmethod
    def _extract_error_code(output: str) -> str:
        for line in output.split('\n'):
            if 'ErrorCode' in line or 'КодОшибки' in line or '[0x' in line:
                parts = line.split(':')
                if len(parts) >= 2:
                    code = parts[-1].strip().replace(']', '').strip()
                    if code.startswith('0x'):
                        return code
        return ''

    @staticmethod
    def _extract_container(output: str) -> str:
        for line in output.split('\n'):
            if 'Container' in line or 'Контейнер' in line:
                parts = line.split(':', 1)
                if len(parts) >= 2:
                    c = parts[1].strip().replace('[', '').replace(']', '').replace('"', '').strip()
                    if c:
                        return c
        return ''

    # Русские → английские имена полей (certmgr с chcp 1251)
    _FIELD_NAME_MAP = {
        'Издатель': 'Issuer',
        'Субъект': 'Subject',
        'Серийный номер': 'Serial',
        'SHA1 отпечаток': 'SHA1 Thumbprint',
        'Идентификатор ключа': 'SubjectKeyID',
        'Алгоритм подписи': 'Signature Algorithm',
        'Алгоритм откр. кл.': 'PublicKey Algorithm',
        'Выдан': 'Not valid before',
        'Истекает': 'Not valid after',
        'Ссылка на ключ': 'PrivateKey Link',
        'Контейнер': 'Container',
        'Имя провайдера': 'Provider Name',
        'Инфо о провайдере': 'Provider Info',
        'Тип идентификации': 'Identification Kind',
        'URL сертификата УЦ': 'CA cert URL',
        'URL списка отзыва': 'CDP',
        'Встроенная лицензия': 'Embedded License',
        'Назначение/EKU': 'Extended Key Usage',
    }

    def _parse_certificate_list(self, output: str) -> dict:
        certificates = {}
        for index, cert_block in enumerate(output.split('-------')):
            if ' : ' not in cert_block or not cert_block.strip():
                continue
            cert_info: dict[str, str] = {}
            for line in cert_block.split('\n'):
                line = re.sub(r'  +', ' ', line.strip())
                if ' : ' in line:
                    key, value = line.split(' : ', 1)
                    key = key.strip()
                    # Нормализация русских имён полей → английские
                    key = self._FIELD_NAME_MAP.get(key, key)
                    cert_info[key] = value.strip()

            # --- CSP v5: Subject может отсутствовать (корневые CA) → взять Issuer ---
            if 'Subject' not in cert_info and 'Issuer' in cert_info:
                cert_info['Subject'] = cert_info['Issuer']

            if 'Subject' in cert_info:
                for part in cert_info['Subject'].split(', '):
                    if '=' in part:
                        k, v = part.split('=', 1)
                        cert_info[f'Subject_{k.strip()}'] = v.strip()

            if 'Issuer' in cert_info:
                for part in cert_info['Issuer'].split(', '):
                    if '=' in part:
                        k, v = part.split('=', 1)
                        cert_info[f'Issuer_{k.strip()}'] = v.strip()

            # --- Нормализация Thumbprint (CSP v5: SHA1 Thumbprint) ---
            for alt in ('SHA1 Thumbprint', 'SHA1 Hash', 'SHA1', 'Hash', 'Отпечаток'):
                if alt in cert_info and 'Thumbprint' not in cert_info:
                    cert_info['Thumbprint'] = cert_info[alt]
                    break

            # --- Нормализация дат (CSP v5: Not valid before/after) ---
            if 'ValidFrom' not in cert_info and 'Not valid before' in cert_info:
                cert_info['ValidFrom'] = cert_info['Not valid before']
            if 'ValidTo' not in cert_info and 'Not valid after' in cert_info:
                cert_info['ValidTo'] = cert_info['Not valid after']

            # --- Нормализация Container (убрать REGISTRY\\, FAT12\, HDIMAGE\\ префиксы) ---
            container = cert_info.get('Container', '')
            if container:
                for prefix in ('REGISTRY\\\\', 'HDIMAGE\\\\'):
                    if container.startswith(prefix):
                        cert_info['Container'] = container[len(prefix):]
                        cert_info['ContainerType'] = prefix.rstrip('\\')
                        break
                else:
                    for prefix in ('FAT12\\',):
                        if container.startswith(prefix):
                            cert_info['ContainerType'] = prefix.rstrip('\\')
                            break

            if cert_info:
                sub_cn = cert_info.get('Subject_CN', '-')
                certificates[f'{index}_{sub_cn}'] = cert_info
        return certificates

    # ------------------------------------------------------------------ #
    #  RPC методы
    # ------------------------------------------------------------------ #

    @rpc
    async def list_certificates(self, data: dict) -> dict:
        """Список установленных сертификатов. data может содержать session_id."""
        sid = self._extract_session(data)
        # Проверяем uMy, затем mMy, затем общий -list — пользовательские и машинные хранилища
        outputs = []
        certs: dict = {}
        for store in ("uMy", "mMy", None):
            if store:
                cmd = f'"{self.csp_path / "certmgr.exe"}" -list -store {store}'
            else:
                cmd = f'"{self.csp_path / "certmgr.exe"}" -list'
            out = await self._run_async(cmd, session_id=sid)
            outputs.append((store or "all", len(out)))
            if out.strip():
                parsed = self._parse_certificate_list(out)
                # префикс чтобы не затереть одинаковые ключи index_CN
                for k, v in parsed.items():
                    nk = f'{store or "all"}_{k}' if store else k
                    if nk not in certs:
                        certs[nk] = v
                if certs:
                    break
        try:
            self.log.info(f'list_certificates session={sid} csp_path={self.csp_path} outputs={outputs} total={len(certs)}')
            if certs:
                # head первого непустого для диагностики
                for store, l in outputs:
                    if l>0:
                        break
        except Exception:
            pass
        return certs

    @rpc
    async def find_certificate_by_subject(self, data: dict) -> dict:
        """Найти первый сертификат по паттерну в Subject."""
        pattern = data.get('subject_pattern', '')
        sid = self._extract_session(data)
        certs = await self.list_certificates({'session_id': sid} if sid is not None else {})
        for info in certs.values():
            if 'Subject' in info and pattern.lower() in info['Subject'].lower():
                return info
        return {}

    @rpc
    async def find_certificates_by_subject(self, data: dict) -> list:
        """Найти все сертификаты по паттерну в Subject."""
        pattern = data.get('subject_pattern', '')
        sid = self._extract_session(data)
        certs = await self.list_certificates({'session_id': sid} if sid is not None else {})
        return [info for info in certs.values()
                if 'Subject' in info and pattern.lower() in info['Subject'].lower()]

    @rpc
    async def deploy_certificate(self, data: dict) -> dict:
        """Развернуть сертификат из PFX + CER файлов."""
        sid = self._extract_session(data)
        pfx_path = data.get('pfx_path', '')
        cer_path = data.get('cer_path', '')
        pin = data.get('pin', '00000000')

        if not Path(pfx_path).exists():
            return {'success': False, 'error': f'PFX not found: {pfx_path}'}
        if not Path(cer_path).exists():
            return {'success': False, 'error': f'CER not found: {cer_path}'}

        result = {'success': False, 'pfx_error': '0x00000000',
                  'cer_error': '0x00000000', 'password_error': '0x00000000',
                  'container': ''}

        # 1. Install PFX
        auto_container = f'{self._CARRIER}\\p2p_{secrets.token_hex(4)}'
        cmd = (f'"{self.csp_path / "certmgr.exe"}" -install -store uMy '
               f'-file "{pfx_path}" -pfx -container "{auto_container}" '
               f'-silent -keep_exportable -pin {pin}')
        output = await self._run_async(cmd, session_id=sid)
        result['pfx_error'] = self._extract_error_code(output)
        result['container'] = self._extract_container(output)
        if not result['container'] and result['pfx_error'] == '0x00000000':
            result['container'] = auto_container

        if result['pfx_error'] != '0x00000000':
            return result

        # 2. Install CER
        cmd = (f'"{self.csp_path / "certmgr.exe"}" -install -store uMy '
               f'-file "{cer_path}" -certificate -container "{result["container"]}" '
               f'-silent -inst_to_cont')
        output = await self._run_async(cmd, session_id=sid)
        result['cer_error'] = self._extract_error_code(output)

        if result['cer_error'] != '0x00000000':
            return result

        # 3. Change password
        cmd = (f'"{self.csp_path / "csptest.exe"}" -passwd '
               f'-container "{result["container"]}" -change {pin}')
        output = await self._run_async(cmd, session_id=sid)
        result['password_error'] = self._extract_error_code(output)

        result['success'] = all(
            result[k] == '0x00000000'
            for k in ('pfx_error', 'cer_error', 'password_error')
        )
        return result

    @rpc
    async def export_certificate_pfx(self, data: dict) -> dict:
        """Экспорт закрытого ключа в PFX (base64)."""
        sid = self._extract_session(data)
        container = data.get('container_name', '')
        thumbprint = data.get('thumbprint', '')
        password = data.get('password', '00000000')

        if sid is not None:
            tmp_path = str(self._shared_tmp_path('.pfx'))
            Path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
            tmp_created = False
            # touch empty file to ensure dest exists
            try:
                Path(tmp_path).write_bytes(b'')
            except Exception:
                pass
        else:
            with tempfile.NamedTemporaryFile(suffix='.pfx', delete=False) as tmp:
                tmp_path = tmp.name
            tmp_created = True

        try:
            cmd = (f'"{self.csp_path / "certmgr.exe"}" -export '
                   f'-container "{container}" -dest "{tmp_path}" '
                   f'-pfx -keep_exportable -pin {password}')
            output = await self._run_async(cmd, session_id=sid)
            error = self._extract_error_code(output)

            self.log.info(f'export_pfx: container={container}, error={error}')
            self.log.info(f'export_pfx certmgr output:\n{output}')

            if error != '0x00000000' or not Path(tmp_path).exists():
                return {'success': False, 'error': f'Export failed: {error}', 'pfx_base64': ''}

            pfx_size = Path(tmp_path).stat().st_size
            self.log.info(f'export_pfx: file size={pfx_size} bytes')

            with open(tmp_path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('utf-8')

            return {'success': True, 'pfx_base64': b64, 'error': ''}
        except Exception as e:
            return {'success': False, 'error': str(e), 'pfx_base64': ''}
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    @rpc
    async def export_certificate_cer(self, data: dict) -> dict:
        """Экспорт открытого ключа в CER (base64)."""
        sid = self._extract_session(data)
        container = data.get('container_name', '')
        thumbprint = data.get('thumbprint', '')

        if not container and not thumbprint:
            return {'success': False, 'error': 'container_name or thumbprint required', 'cer_base64': ''}

        if sid is not None:
            tmp_path = str(self._shared_tmp_path('.cer'))
            Path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
            try:
                Path(tmp_path).write_bytes(b'')
            except Exception:
                pass
        else:
            with tempfile.NamedTemporaryFile(suffix='.cer', delete=False) as tmp:
                tmp_path = tmp.name

        try:
            if container:
                cmd = (f'"{self.csp_path / "certmgr.exe"}" -export '
                       f'-container "{container}" -dest "{tmp_path}"')
            else:
                cmd = (f'"{self.csp_path / "certmgr.exe"}" -export '
                       f'-thumbprint "{thumbprint}" -dest "{tmp_path}"')

            output = await self._run_async(cmd, session_id=sid)
            error = self._extract_error_code(output)

            if error != '0x00000000' or not Path(tmp_path).exists():
                return {'success': False, 'error': f'Export failed: {error}', 'cer_base64': ''}

            with open(tmp_path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('utf-8')

            return {'success': True, 'cer_base64': b64, 'error': ''}
        except Exception as e:
            return {'success': False, 'error': str(e), 'cer_base64': ''}
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    @rpc
    async def export_certificate_by_subject(self, data: dict) -> dict:
        """Найти по Subject и экспортировать PFX + CER (base64)."""
        pattern = data.get('subject_pattern', '')
        password = data.get('password', '00000000')

        cert = await self.find_certificate_by_subject({'subject_pattern': pattern})
        if not cert:
            return {'pfx': {'success': False, 'error': 'Certificate not found', 'pfx_base64': ''},
                    'cer': {'success': False, 'error': 'Certificate not found', 'cer_base64': ''}}

        container = cert.get('Container', '')
        thumbprint = cert.get('Thumbprint', '')

        sid = self._extract_session(data)
        pfx_result = {'success': False, 'error': 'No container', 'pfx_base64': ''}
        if container:
            pfx_result = await self.export_certificate_pfx(
                {'container_name': container, 'password': password, 'session_id': sid} if sid is not None else {'container_name': container, 'password': password})

        cer_result = await self.export_certificate_cer(
            {'container_name': container, 'thumbprint': thumbprint, 'session_id': sid} if sid is not None else {'container_name': container, 'thumbprint': thumbprint})

        return {'pfx': pfx_result, 'cer': cer_result}

    @rpc
    async def delete_certificate(self, data: dict) -> dict:
        """Удалить сертификат по thumbprint."""
        sid = self._extract_session(data)
        thumbprint = data.get('thumbprint', '')
        if not thumbprint:
            return {'success': False, 'error': 'Thumbprint is required'}

        cmd = f'"{self.csp_path / "certmgr.exe"}" -delete -thumbprint "{thumbprint}"'
        output = await self._run_async(cmd, session_id=sid)
        error = self._extract_error_code(output)

        if error == '0x00000000':
            return {'success': True}
        return {'success': False, 'error': f'Delete failed: {error}', 'error_code': error}

    @rpc
    async def install_pfx_from_base64(self, data: dict) -> dict:
        """Установка PFX из base64-данных."""
        sid = self._extract_session(data)
        pfx_b64 = data.get('pfx_base64', '')
        password = data.get('password', '00000000')
        filename = data.get('filename', 'cert.pfx')

        try:
            pfx_bytes = base64.b64decode(pfx_b64)
        except Exception as e:
            return {'success': False, 'error': f'Base64 decode error: {e}'}

        if sid is not None:
            tmp_path = str(self._shared_tmp_path('.pfx'))
            Path(tmp_path).write_bytes(pfx_bytes)
        else:
            with tempfile.NamedTemporaryFile(suffix='.pfx', delete=False) as tmp:
                tmp.write(pfx_bytes)
                tmp_path = tmp.name

        try:
            # CSP v5: без явного контейнера certmgr может не привязать закрытый ключ.
            # Генерируем имя контейнера, если не задано.
            container_name = data.get('container_name', '')
            if not container_name:
                        container_name = f'{self._CARRIER}\\p2p_{secrets.token_hex(4)}'

            cmd = (f'"{self.csp_path / "certmgr.exe"}" -install -store uMy '
                   f'-file "{tmp_path}" -pfx -container "{container_name}" '
                   f'-silent -keep_exportable -pin {password}')
            output = await self._run_async(cmd, session_id=sid)
            error = self._extract_error_code(output)
            container = self._extract_container(output)

            # Если certmgr не вернул контейнер — используем заданное имя
            if not container and error == '0x00000000':
                container = container_name

            self.log.info(f'install_pfx: error={error}, container={container}, output_len={len(output)}')
            self.log.info(f'install_pfx certmgr output:\n{output[:1000]}')

            if error == '0x00000000':
                return {'success': True, 'container': container}

            return {'success': False, 'error': f'Install failed: {error}', 'error_code': error}
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    @rpc
    async def batch_install_pfx_from_bytes(self, data: dict) -> dict:
        """Пакетная установка PFX из base64 со сменой пароля."""
        sid = self._extract_session(data)
        pfx_list: list[dict[str, str]] = data.get('pfx_list', [])
        current_pwd = data.get('current_password', '00000000')
        new_pwd = data.get('new_password') or current_pwd

        results = []
        ok = 0
        fail = 0

        for idx, item in enumerate(pfx_list):
            pfx_b64 = item.get('pfx_base64', '')
            fname = item.get('filename', f'cert_{idx}.pfx')

            try:
                pfx_bytes = base64.b64decode(pfx_b64)
            except Exception as e:
                results.append({'filename': fname, 'success': False, 'error': str(e)})
                fail += 1
                continue

            if sid is not None:
                tmp_path = str(self._shared_tmp_path('.pfx'))
                Path(tmp_path).write_bytes(pfx_bytes)
            else:
                with tempfile.NamedTemporaryFile(suffix='.pfx', delete=False) as tmp:
                    tmp.write(pfx_bytes)
                    tmp_path = tmp.name

            try:
                auto_container = f'{self._CARRIER}\\p2p_{secrets.token_hex(4)}'

                cmd = (f'"{self.csp_path / "certmgr.exe"}" -install -store uMy '
                       f'-file "{tmp_path}" -pfx -container "{auto_container}" '
                       f'-silent -keep_exportable -pin {current_pwd}')
                output = await self._run_async(cmd, session_id=sid)
                error = self._extract_error_code(output)
                container = self._extract_container(output)

                if not container and error == '0x00000000':
                    container = auto_container

                if error != '0x00000000':
                    results.append({'filename': fname, 'success': False,
                                    'error': f'Install failed', 'error_code': error})
                    fail += 1
                    continue

                if new_pwd != current_pwd and container:
                    pw_cmd = (f'"{self.csp_path / "csptest.exe"}" -passwd '
                              f'-container "{container}" -change {new_pwd}')
                    await self._run_async(pw_cmd, session_id=sid)

                results.append({'filename': fname, 'success': True, 'container': container})
                ok += 1
            finally:
                Path(tmp_path).unlink(missing_ok=True)

        return {
            'success': ok > 0,
            'total': len(pfx_list),
            'success_count': ok,
            'fail_count': fail,
            'results': results,
        }

    @rpc
    async def get_dashboard_data(self, data: dict) -> dict:
        """Данные для веб-панели: список сертификатов с нормализацией полей."""
        sid = self._extract_session(data)
        try:
            certs = await self.list_certificates({'session_id': sid} if sid is not None else {})
            cert_list = []
            for cert_id, info in certs.items():
                valid_from = (info.get('ValidFrom') or info.get('Not valid before') or
                              info.get('Действителен с') or '')
                valid_to = (info.get('ValidTo') or info.get('Not valid after') or
                            info.get('Действителен до') or '')
                cert_list.append({
                    'id': cert_id,
                    'subject': info.get('Subject', 'Unknown'),
                    'subject_cn': info.get('Subject_CN', 'Unknown'),
                    'issuer': info.get('Issuer', 'Unknown'),
                    'issuer_cn': info.get('Issuer_CN', info.get('Issuer', 'Unknown')),
                    'thumbprint': info.get('Thumbprint', ''),
                    'container': info.get('Container', ''),
                    'serial': info.get('Serial', ''),
                    'valid_from': valid_from,
                    'valid_to': valid_to,
                    'raw': info,
                })
            return {
                'total_certificates': len(cert_list),
                'certificates': cert_list,
                'csp_version': self.csp_version or 'unknown',
                'csp_version_full': self.csp_version_full or '',
                'csp_bin': self.csp_path.name if self.csp_path else 'unknown',
            }
        except Exception as e:
            self.log.error(f'Dashboard data error: {e}')
            return {'total_certificates': 0, 'certificates': [], 'error': str(e)}

    # ------------------------------------------------------------------ #
    #  Сетевая установка сертификатов (CERT_SYNC)
    # ------------------------------------------------------------------ #

    @rpc
    async def network_certs(self, data: dict) -> dict:
        """Сертификаты из сети, не установленные локально, сгруппированные по subject_cn.

        Возвращает:
          groups: {subject_cn: [entry_dict, ...]}
          total: общее число недостающих сертификатов
        Каждый entry_dict: {thumbprint, subject_cn, valid_to, available_on, sync_version}
        Сортировка: сначала самые свежие (valid_to), CONNECTED узлы приоритетнее.
        """
        available = self.ctx.certs_index.get_network_available()
        connected_ids = {n.node_id for n in self.ctx.network.neighbor_table.connected()}

        groups: dict[str, list[dict]] = {}
        for entry in available:
            entry_dict = {
                'thumbprint': entry.thumbprint,
                'subject_cn': entry.subject_cn,
                'valid_to': entry.valid_to,
                'available_on': entry.available_on,
                'sync_version': entry.sync_version,
            }
            cn = entry.subject_cn or '?'
            groups.setdefault(cn, []).append(entry_dict)

        # Сортировка внутри группы: приоритет по CONNECTED узлам, затем по valid_to
        for cn, entries in groups.items():
            def _sort_key(e):
                has_connected = any(n in connected_ids for n in e['available_on'])
                return (0 if has_connected else 1, e.get('valid_to', ''))
            entries.sort(key=_sort_key)

        return {
            'groups': groups,
            'total': len(available),
        }

    @rpc
    async def install_from_node(self, data: dict) -> dict:
        """Сетевая установка сертификата с удалённого узла.

        Процесс:
        1. RPC к удалённому узлу certstool.export_pfx_to_bytes — экспорт с одноразовым паролем
        2. Локальная установка PFX с этим паролем
        3. Смена пароля контейнера на пользовательский (или дефолтный)

        Параметры:
          thumbprint: str — идентификатор сертификата
          source_node: str — узел-источник (из available_on)
          new_password: str — новый пароль контейнера (default='00000000')

        Возвращает: {success, container, error, source_node, thumbprint}
        """
        sid = self._extract_session(data)
        thumbprint = data.get('thumbprint', '')
        source_node = data.get('source_node', '')
        new_password = data.get('new_password', '') or '00000000'

        if not thumbprint:
            return {'success': False, 'error': 'thumbprint is required'}
        if not source_node:
            return {'success': False, 'error': 'source_node is required'}

        # Проверить, что сертификат не установлен локально
        entry = self.ctx.certs_index.get_by_thumbprint(thumbprint)
        if entry and entry.installed_locally:
            return {'success': False, 'error': 'Certificate already installed locally'}

        # 1. Найти контейнер по thumbprint на удалённом узле
        try:
            cert_info = await self.ctx.network.call(
                dst=source_node,
                service='certstool',
                method='get_certificate_info',
                data={'thumbprint_lookup': thumbprint},
                timeout=10,
            )
        except Exception as e:
            return {'success': False, 'error': f'Remote lookup failed: {e}'}

        if not cert_info:
            return {'success': False, 'error': 'Certificate not found on source node'}

        container = cert_info.get('Container', '')
        if not container:
            return {'success': False, 'error': 'No container on source node'}

        # 2. Сгенерировать одноразовый пароль для PFX экспорта
        one_time_password = secrets.token_hex(8)

        # 3. Запросить PFX с удалённого узла
        try:
            export_result = await self.ctx.network.call(
                dst=source_node,
                service='certstool',
                method='export_certificate_pfx',
                data={'container_name': container, 'thumbprint': thumbprint, 'password': one_time_password},
                timeout=15,
            )
        except Exception as e:
            return {'success': False, 'error': f'Remote export failed: {e}'}

        if not export_result.get('success'):
            return {'success': False, 'error': f'Remote export error: {export_result.get("error", "?")}'}

        pfx_b64 = export_result.get('pfx_base64', '')
        if not pfx_b64:
            return {'success': False, 'error': 'Empty PFX data from source node'}

        # 4. Установить PFX локально с одноразовым паролем (в выбранной сессии если указано)
        pfx_data = {
            'pfx_base64': pfx_b64,
            'password': one_time_password,
            'filename': f'{thumbprint[:8]}.pfx',
        }
        if sid is not None:
            pfx_data['session_id'] = sid
        install_result = await self.install_pfx_from_base64(pfx_data)

        if not install_result.get('success'):
            return {
                'success': False,
                'error': f'Local install failed: {install_result.get("error", "?")}',
                'source_node': source_node,
                'thumbprint': thumbprint,
            }

        local_container = install_result.get('container', '')

        # 5. Сменить пароль на пользовательский
        if new_password != one_time_password and local_container:
            pw_cmd = (f'"{self.csp_path / "csptest.exe"}" -passwd '
                      f'-container "{local_container}" -change {new_password}')
            await self._run_async(pw_cmd, session_id=sid)

        # 6. Обновить CertsIndex и историю
        self.ctx.certs_index.update_local(await self.list_certificates({'session_id': sid} if sid is not None else {}))
        self._add_install_history(thumbprint, source_node)

        self.log.info(
            f'Network install OK: {thumbprint[:8]} from {source_node} → container {local_container}'
        )
        return {
            'success': True,
            'container': local_container,
            'source_node': source_node,
            'thumbprint': thumbprint,
            'error': '',
        }

    @rpc
    async def get_cert_sync_digest(self, data: dict) -> dict:
        """Digest локальных сертификатов для CERT_SYNC (по запросу).

        Используется при подключении нового узла для немедленного обмена.
        """
        digest = self.ctx.certs_index.get_digest_for_sync()
        return {
            'certs': digest,
            'sync_version': self.ctx.certs_index.sync_version,
        }

    @rpc
    async def get_install_history(self, data: dict) -> list:
        """История сетевой установки сертификатов."""
        return self._install_history

    @rpc
    async def get_certificate_info(self, data: dict) -> dict:
        """Детальная информация о сертификате по имени контейнера или thumbprint."""
        sid = self._extract_session(data)
        container = data.get('container_name', '')
        thumbprint_lookup = data.get('thumbprint_lookup', '')
        certs = await self.list_certificates({'session_id': sid} if sid is not None else {})
        for info in certs.values():
            if container and info.get('Container', '') == container:
                return info
            if thumbprint_lookup and info.get('Thumbprint', '').lower() == thumbprint_lookup.lower():
                return info
        return {}

    @rpc
    async def fix_certificate_link(self, data: dict) -> dict:
        """Починить связку сертификата с закрытым ключом.

        Проблема: при ручной установке PFX через КриптоПро, сертификат может
        не связаться с закрытым ключом. Этот метод экспортирует PFX и устанавливает
        его заново с явным указанием контейнера — это создаёт правильную связку.

        Параметры:
          thumbprint: str — идентификатор сертификата
          password: str — текущий пароль контейнера (default='00000000')
          session_id: int — опционально выполнять в сессии пользователя

        Возвращает: {success, container, error}
        """
        sid = self._extract_session(data)
        thumbprint = data.get('thumbprint', '')
        password = data.get('password', '00000000')

        if not thumbprint:
            return {'success': False, 'error': 'Thumbprint is required'}

        # 1. Найти сертификат по thumbprint
        certs = await self.list_certificates({'session_id': sid} if sid is not None else {})
        cert_info = None
        for info in certs.values():
            if info.get('Thumbprint', '').lower() == thumbprint.lower():
                cert_info = info
                break

        if not cert_info:
            return {'success': False, 'error': 'Certificate not found'}

        container = cert_info.get('Container', '')
        if not container:
            return {'success': False, 'error': 'No container for this certificate'}

        self.log.info(f'Fixing link for {thumbprint[:8]}... (container={container})')

        # 2. Экспортировать PFX с текущим паролем
        export_result = await self.export_certificate_pfx({
            'container_name': container,
            'password': password,
            'session_id': sid,
        } if sid is not None else {
            'container_name': container,
            'password': password,
        })

        if not export_result.get('success'):
            return {
                'success': False,
                'error': f'Export failed: {export_result.get("error", "?")}',
            }

        pfx_b64 = export_result.get('pfx_base64', '')
        if not pfx_b64:
            return {'success': False, 'error': 'Empty PFX data'}

        # 3. Удалить старый сертификат
        del_result = await self.delete_certificate({'thumbprint': thumbprint, 'session_id': sid} if sid is not None else {'thumbprint': thumbprint})
        if not del_result.get('success'):
            self.log.warning(f'Delete old cert failed: {del_result.get("error", "?")}')
            # Продолжить установку — может быть дубликат

        # 4. Установить PFX заново с явным контейнером — это создаст связку
        pfx_bytes = base64.b64decode(pfx_b64)

        if sid is not None:
            tmp_path = str(self._shared_tmp_path('.pfx'))
            Path(tmp_path).write_bytes(pfx_bytes)
        else:
            with tempfile.NamedTemporaryFile(suffix='.pfx', delete=False) as tmp:
                tmp.write(pfx_bytes)
                tmp_path = tmp.name

        try:
            cmd = (f'"{self.csp_path / "certmgr.exe"}" -install -store uMy '
                   f'-file "{tmp_path}" -pfx -container "{container}" '
                   f'-silent -keep_exportable -pin {password}')
            output = await self._run_async(cmd, session_id=sid)
            error = self._extract_error_code(output)
            result_container = self._extract_container(output)

            if not result_container:
                result_container = container

            self.log.info(f'Fix link: error={error}, container={result_container}')

            if error != '0x00000000':
                return {
                    'success': False,
                    'error': f'Install failed: {error}',
                    'error_code': error,
                }

            return {
                'success': True,
                'container': result_container,
                'thumbprint': thumbprint,
            }
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    #  Сеансы и установка сертификата в сессию (WTS)
    # ------------------------------------------------------------------ #

    @rpc
    async def list_sessions(self, data: dict) -> dict:
        """Перечислить активные интерактивные сессии на локальном узле."""
        if get_sessions is None:
            return {"ok": False, "error": "WTS not available (Windows only)"}
        sessions = get_sessions()
        return {"ok": True, "sessions": [s.__dict__ for s in sessions]}

    @rpc
    async def debug_raw(self, data: dict) -> dict:
        """Отладка: сырой вывод certmgr -list -store uMy в SYSTEM и в сессии."""
        sid = self._extract_session(data)
        cmd = f'"{self.csp_path / "certmgr.exe"}" -list -store uMy'
        out_sys = await self._run_async(cmd, session_id=None)
        out_sess = ""
        if sid is not None:
            out_sess = await self._run_async(cmd, session_id=sid)
        return {"csp_path": str(self.csp_path), "csp_version": self.csp_version, "session_id": sid, "out_system_len": len(out_sys), "out_system_head": out_sys[:2000], "out_session_len": len(out_sess), "out_session_head": out_sess[:2000]}

    @rpc
    async def install_cert_to_session(self, data: dict) -> dict:
        """Установить node-сертификат в хранилище 'my' выбранной пользовательской сессии."""
        if run_in_session is None:
            return {"ok": False, "error": "WTS not available (Windows only)"}
        session_id = data.get('session_id')
        if session_id is None:
            return {"ok": False, "error": "session_id required"}
        storage = getattr(self.ctx, 'secure_storage', None)
        if storage is None:
            return {"ok": False, "error": "SecureStorage not available"}
        try:
            node_cert_pem = storage.read_bytes("/certs/node_cert.pem")
        except Exception as e:
            return {"ok": False, "error": f"node_cert read failed: {e}"}
        cert_obj = None
        try:
            from cryptography import x509
            cert_obj = x509.load_pem_x509_certificate(node_cert_pem)
        except Exception:
            pass
        if cert_obj is None:
            return {"ok": False, "error": "invalid node_cert.pem"}
        with tempfile.NamedTemporaryFile(suffix='.pem', delete=False, mode='wb') as tmp:
            tmp.write(node_cert_pem)
            tmp_path = tmp.name
        try:
            certmgr = str(self.csp_path / "certmgr.exe")
            cmd = (f'"{certmgr}" -inst -store my -file "{tmp_path}" -silent')
            pid = await asyncio.to_thread(run_in_session, cmd, int(session_id))
            self.log.info(f"certmgr installed to session {session_id} (pid={pid})")
            return {"ok": True, "session_id": session_id, "pid": pid}
        except Exception as e:
            self.log.error(f"install_cert_to_session failed: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    #  История установки
    # ------------------------------------------------------------------ #

    def _add_install_history(self, thumbprint: str, source_node: str):
        record = {
            'thumbprint': thumbprint,
            'source_node': source_node,
            'installed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        self._install_history.append(record)
        self.log.info(f'Install history: {thumbprint[:8]} from {source_node}')
