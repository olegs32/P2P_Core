# EyeSauron — анализ проекта и план интеграции в P2P_Core

> Источник: `C:\Users\oleg\PycharmProjects\EyeSauron` (проанализирован 2026-08-25,
> код не менялся). Документ — база знаний для дальнейшей интеграции, чтобы не
> пересканировать проект.

> **СТАТУС ИНТЕГРАЦИИ (2026-08-26):** Фаза 1+2 реализованы сервисом
> `services/eyesauron/` — коллектор (raw PNG вместо ChunkStore: тысячи мелких
> чанков убивают NAS) + агент с WTS-хелпером и spool-очередью вместо HTTP.
> Конфиг: `eyesauron.*`, по умолчанию выключен. Vendor-снимок ChunkStore:
> `services/eyesauron/_vendor_chunk_store.py`.
> Спека пакованного дедуп-хранилища (тома 10 ГБ, seal → заливка, chunker
> v1 = grid256 + телеметрия скролла перед CDC): **`docs/eyesauron_storage.md`**.

---

## 1. Назначение

Система периодического мониторинга экранов рабочих станций Windows:

```
[ПК пользователя]                      [Сервер :8000]                [Хранение]
Agent (скриншот раз в сек,             eye_server.py (FastAPI)       NAS \\nas\dir
 дедуп по хешу, HTTP upload)  ──HTTP──►  /upload → raw PNG          ├── ...\screens\<host>\<date>\
                                        /version,/deploy            └── ...\store\   (ChunkStore)
Launcher (SYSTEM, WTS-инъекция     ◄──апдейты── /download,/upload_eye
 агента в активные сессии,
 автообновление раз в 10 мин)
```

Три уровня: **агент в сессии пользователя** → **launcher как SYSTEM** → **сервер сбора**.
Плюс отдельные инструменты: дедуп-хранилище ChunkStore, веб-вьюер к нему, Streamlit-галерея (старая), удалённый деплой через admin-шары+psexec.

Маскировочные имена: агент = `W32TimeHelper.exe`, launcher = `W64TimeHelper.exe`,
каталог `%PROGRAMDATA%\WindowsTimeHelper`, задача планировщика
`MicrosoftEdgeUpdateTaskMachineEye`, реестр `MicrosoftEdgeUpdateEye`.

---

## 2. Компоненты

### 2.1 `eye_agent.py` (≈900 строк) — агент захвата

- **Захват экрана**: 4 метода каскадом с фолбэком — `mss` → `PIL.ImageGrab` → win32gui/win32ui BitBlt → чистый ctypes GDI. Первый валидный побеждает.
- **Валидация кадра**: PNG ≥ 10 000 байт (`MIN_SCREENSHOT_SIZE`), детект чёрного экрана (средняя яркость grayscale < 5 из 255).
- **Дедупликация на клиенте**: `imagehash.average_hash`; отправка только при отличии (`HASH_THRESHOLD = 0` — любое изменение). `INTERVAL = 1` сек между попытками.
- **Метаданные**: заголовок активного окна (`win32gui.GetForegroundWindow`, санитизация `[<>:"/\|?*]`, ≤60 симв.), hostname, timestamp `%Y-%m-%d_%H-%M-%S`.
- **Имя файла**: `{timestamp}__{title}.png`; upload — HTTP POST multipart на `/upload` (поля `hostname`, `timestamp`, `title` + file).
- **Офлайн-кэш**: `%PROGRAMDATA%\WindowsTimeHelper\cache\` — файл без расширения (имя = md5 от `timestamp__title__hostname`) + `.meta` рядом. При восстановлении связи кэш дожимается на сервер (поток в отдельном daemon-thread), после каждой успешной отправки пауза `delay` из ответа сервера (по умолчанию 0.5с).
- **Ретраи**: до 3 попыток с экспоненциальной задержкой (base 2с).
- **Выбор сервера — захардкожен по подсети** (module-level, при импорте):
  - IP `192.168.20.x` → `http://192.168.20.9:8000/upload`
  - иначе → `http://192.168.53.41:8000/upload`
  - частный случай `192.168.53.53` → сам на себя.
- **CLI**: `enable | disable | status | check | test | send-cache | cache-info | clean-cache | logs | help`. Без аргументов — ставит автозапуск и возвращается (рабочие потоки стартуют дальше по `__main__`).
- **Автозапуск**: при КАЖДОМ запуске (`__main__`) вызывает `add_to_task_scheduler()` — задача `MicrosoftEdgeUpdateTaskMachineEye`, `ONLOGON /RU SYSTEM /DELAY 0000:30 /rl highest` + ключ HKCU Run `MicrosoftEdgeUpdateEye`. Плюс self-heal: команда `check` перезапускает задачу, если та не Running, и создаёт заново, если отсутствует.
- **Логи**: `agent.log` (ошибки+traceback) и `debug.log` — пишутся ТОЛЬКО если существует каталог `...\WindowsTimeHelper\Logs` (флаг `LOGS`). Ротация при 10 МБ → остаются последние 1000 строк.
- ⚠️ Импорт `from pandas.tseries.holiday import next_workday` — мусорный (не используется), но тянет pandas в сборку агента.

### 2.2 `launcher.py` → `W64TimeHelper.exe` — сессионный монитор

Запускается задачей планировщика от SYSTEM; собственный mutex `SessionMonitorMutex` (защита от дублей).

- **Инъекция в сессии**: `WTSEnumerateSessionsW` → активные сессии (`WTSActive`, id≠0) → `WTSQueryUserToken` + `DuplicateTokenEx(SCREAM...0xF01FF)` + `CreateEnvironmentBlock` + `CreateProcessAsUserW` → запуск агента **внутри интерактивной сессии** (без этого скриншоты рабочего стола невозможны из SYSTEM).
- **Учёт процессов**: `running: {session_id: (pid, handle)}`; сессия исчезла → TerminateProcess; процесс умер сам → перезапуск; poll каждые 2с.
- **Анти-размножение**: если `tasklist` показывает >2 копий `W32TimeHelper.exe` → `taskkill /f /im`.
- **Автообновление** (раз в 600с): `GET /version` → сравнение с локальной версией из `C:\ProgramData\WindowsTimeHelper\version.txt` (целое число) → `GET /download` во временный файл → kill всех агентов → `os.replace`. Откат не предусмотрен (просто следующая проверка).
- Обновляется ТОЛЬКО агент (`W32TimeHelper.exe` через `/download`); сам launcher версий не имеет и не обновляется.

### 2.3 `eye_server.py` — сервер сбора (FastAPI, :8000)

| Эндпоинт | Что делает |
|----------|-----------|
| `POST /upload` | Скриншот от агента: сохраняет **сырой PNG** на NAS `\\192.168.53.21\photo\screens\{hostname}\{YYYY-MM-DD}\{ts}__{title}.png`; в ответе `{'status', 'path', 'version'}` |
| `GET /version` | Текущая версия сборки (builds/version.txt, целое) |
| `GET /download` | Отдаёт `builds/W32TimeHelper.exe` (для launcher-апдейта) |
| `GET /deploy` (Header `x-token`) | Zip `DEPLOY_PACK` [W32, W64] для Deployer |
| `POST /upload_eye` (Header `x-token`) | Деплой новой сборки: zip → extract в builds/, версия++ |
| `/chunks/*` | Смонтирован роутер ChunkStore-вьюера (см. ниже) |

Константы: `SAVE_ROOT = \\192.168.53.21\photo\screens`, `CHUNK_ROOT = \\192.168.53.21\photo\store`,
`UPLOAD_TOKEN = "secret-token-Naujhirtcbltkrjhjkm"`, учётка NAS `admin / Yfujhirtcbltkrjhjkm`.

⚠️ **Код сломан в текущем виде**: строка 151 — `@app.post("/upload", dependencies=[Depends(limiter)])`, а `limiter` закомментирован (строка 35) → `NameError` при импорте модуля. Перед любым переиспользованием починить.

### 2.4 `tools/chunk_store.py` (1053 строки) — дедуп-хранилище изображений

Самая ценная для переиспользования часть. Самодостаточный модуль (зависимости: numpy + Pillow).

**Формат хранилища** (content-addressed, чанки 256×256):
```
store/
  chunks/ab/abcdef….png      # уникальный чанк, имя = hash массива пикселей
  maps/<host>/<date>/<name>.json   # карта изображения = grid хешей чанков
  hashes.json                # персистентный set известных хешей
  index.json                 # {map_key → {rel_path, width, height}}
```

**API класса `ChunkStore(store_path, chunk_size=256)`**:
- запись: `store_image_bytes(data: bytes, rel_path: str)` (синхронный — из FastAPI через `run_in_executor`; rel_path задаёт виртуальный путь `host/date/name.png`), `store_image(path)`, `stream_folder(...)` — ленивый генератор прогресса, параллелизм через ProcessPoolExecutor, `flush_every` (рассчитан на 10⁷+ файлов, RAM константна);
- чтение: `restore_image(map_name, out)`, **`assemble_to_bytes(map_name, fmt='PNG') -> bytes`**, `stream_restore_folder(...)`;
- навигация (только индекс, без чтения карт): `list_arg1()` → hostnames, `list_arg2(arg1)` → даты, `list_images(arg1, arg2, filter)` → `[{map_name, rel_path, w, h}]`;
- сервис: `stats()` (уникальные чанки/карты/объёмы), `verify_image(map_name)`, `rebuild_index()`;
- производительность: `lru_cache(8192)` на чтение байтов чанка (`_read_chunk_bytes`) — при высокой дедупликации повторные чтения с NAS бьются в RAM.

Дедуп измеряется в `FileResult.dedup_pct` (переиспользованные/все чанки). Для скриншотов одного рабочего места типично очень высокая степень повторного использования.

### 2.5 `tools/web_server.py` — веб-вьюер хранилища

`make_router(store, username, password) -> APIRouter` — монтируется в любой FastAPI (`prefix="/chunks"`):
- страницы: `/` (SPA-вьюер на чистом HTML/JS, тёмная тема), `/login`, `/logout`;
- auth: cookie-сессия (`cs_session`, токен `secrets.token_hex(32)`, срок 7 дней, `compare_digest`);
- API: `GET /api/arg1`, `/api/arg2?arg1=`, `/api/images?arg1=&arg2=&filter=`, **`GET /api/image/{map_name}`** — отдаёт собранный PNG из чанков;
- есть standalone-запуск (`python web_server.py --store ... --user ... --password ...`).

### 2.6 `gallery/back.py` + `gallery/front.py` — старая галерея (не актуальная ветка)

FastAPI + SQLAlchemy(SQLite) + JWT (python-jose/passlib): пользователи с ролями, «важные» скриншоты, лог активности; источник картинок — SMB `\\nas\store\CoreEye\screens[_important]`. Front — Streamlit-клиент. Дублирует функциональность /chunks более тяжёлым стеком.

### 2.7 `Deployer.py` → `deployer.exe` — удалённая установка

Через админские шары: копирует себя на `\\host\c$\ProgramData\WindowsTimeHelper\`, запуск через **psexec**; режим `run`: скачивает `/deploy` (zip), распаковывает, создаёт задачу планировщика (ONLOGON SYSTEM), стартует её. `uninstall`: psexec taskkill обоих exe + удаление файлов. `deploy <hostname>` — дистанционная установка с текущей машины.

### 2.8 `compile.py` — сборка и публикация

PyInstaller onefile: `eye_agent.py → dist\...\W32TimeHelper.exe`, `launcher.py → W64TimeHelper.exe`, `eye_server.exe`, `deployer.exe`; подпись через `tools/sign` (osslsigncode — **тот же signer, что в P2P_Core**); упаковка `deploy.zip` → `POST /upload_eye`. Иконка `shell32_154.ico` (замаскирована под системную).

### 2.9 `legacy/` — история

Ранние итерации: запись напрямую на SMB-шару (`eye_sheduler_boot.py`), локальный планировщик, обработка картинок, `MO_eye.py` и т.п. Не актуальны, но показывают эволюцию: SMB-запись → HTTP-сервер → ChunkStore.

### 2.10 `requirements.txt`

Замусорен двойными секциями (fastapi/uvicorn/pillow/streamlit дважды, разные версии). Реально нужны агенту: `mss, pillow, imagehash, requests, pywin32`; серверу: `fastapi, uvicorn, python-multipart, numpy, pillow`; OCR-утилитам: `opencv-python, pytesseract` (в core-компоненты не входят).

---

## 3. Захардкоженные значения (важно для интеграции)

| Что | Значение | Где |
|-----|----------|-----|
| Сервер (сеть 20.x) | `http://192.168.20.9:8000` | eye_agent.py:57, launcher.py:28 |
| Сервер (остальное) | `http://192.168.53.41:8000` | eye_agent.py:59, launcher.py:30 |
| Частный случай | IP `.53.53` → сервер сам на себя | eye_agent.py:60, launcher.py:31 |
| Токен деплоя | `secret-token-Naujhirtcbltkrjhjkm` (Header `x-token`) | eye_server.py:23, Deployer.py:14, compile.py:12 |
| NAS (raw PNG) | `\\192.168.53.21\photo\screens` | eye_server.py:25 |
| NAS (chunk store) | `\\192.168.53.21\photo\store` | eye_server.py:26 |
| Учётка NAS | `admin / Yfujhirtcbltkrjhjkm` | eye_server.py:27-28 |
| Каталог агента | `%PROGRAMDATA%\WindowsTimeHelper` | eye_agent.py:64 и др. |
| Задача планировщика | `MicrosoftEdgeUpdateTaskMachineEye` | eye_agent.py:634, Deployer.py:13 |
| Ключ реестра | `HKCU\...\Run\MicrosoftEdgeUpdateEye` | eye_agent.py:666 |
| Mutex launcher'а | `SessionMonitorMutex` | launcher.py:75 |

---

## 4. Замеченные проблемы (актуально при переносе кода)

1. **eye_server.py не запускается как есть** — `NameError: limiter` (стр. 151, декоратор ссылается на закомментированный объект).
2. Все адреса/токены/пароли в исходниках; транспорт — голый HTTP без TLS.
3. `requirements.txt` противоречив (двойные пакеты с разными версиями).
4. Агент тянет **pandas** целиком ради неиспользуемого импорта `next_workday`.
5. Апдейт-механизм launcher'а без отката; обновляет только агента, сам launcher неизменяем.
6. `check_and_start_if_needed`/автоустановка задачи при каждом старте агента — «самовосстановление», затрудняет штатное удаление (актуально: purge-подход P2P_Core должен знать про эти артефакты).
7. Raw-PNG копятся без лимитов (нет ротации по возрасту/квоте).

---

## 5. Интеграция в P2P_Core — возможности

Общая идея: EyeSauron распадается на три независимых роли, и каждая интегрируется по-своему.

### Вариант A — коллектор как mesh-сервис (`services/eyesight/` или `services/surveillance/`)

ChunkStore почти готов к переносу: зависит только от numpy+Pillow, файловая структура самописная (JSON + PNG-чанки).

- Узел-коллектор получает сервис `eyesight` с RPC:
  - `ingest({hostname, timestamp, title}, data: bytes(png))` → `store.store_image_bytes` через `asyncio.to_thread` (как в files-сервисе — тяжёлое IO не блокирует loop);
  - `browse_hosts() / browse_dates(host) / list_images(host, date, filter)` — прокси над `list_arg1/arg2/list_images`;
  - `image_bytes(map_name)` → bytes собранного PNG (**bytes в data допустимы** протоколом; скриншот 0.1–1 МБ << MAX_FRAME_SIZE 32 МБ, стрим не обязателен; для батчей — mesh-стрим);
  - `stats()`.
- UI в webpanel: вкладка сервиса — дерево host/date + список кадров + просмотр (data URI/base64 `<img>` через st.image(bytes)); фильтр по title уже есть в `list_images`.
- Веб-вьюер `make_router` можно НЕ портировать — его роль закрывает webpanel; либо смонтировать как sub-app FastAPI узла, но тогда он вне mesh-архитектуры.

### Вариант B — агент под управлением mesh

Захват вынести в сервис `services/eyecapture/` (логика grab_screen + imagehash + кэш переносима; выбросить pandas-импорт):

- вместо HTTP — `network.call(dst=<коллектор>, 'eyesight', 'ingest', data={'meta':…, 'png': bytes})` либо push-стрим (PipeTransport) для очередей кадров;
- офлайн-кэш остаётся как есть (локальный каталог), дожим при восстановлении связи;
- конфиг в config.yaml (`eyecapture: {interval, threshold, collector: <node_id>, enabled}`) вместо хардкода IP;
- **главная тонкость — сессия**: захват требует интерактивного рабочего стола. Узел P2P_Core живёт в session 0 (ONSTART/SYSTEM) → нужен механизм launcher'а: WTS-перечисление сессий + `CreateProcessAsUserW` для запуска лёгкого хелпера в сессии пользователя. Это отдельный модуль ядра (`session_spawner`), полезный и за пределами EyeSauron. Альтернатива MVP — держать второй инстанс узла в пользовательской сессии (реестр Run), но это ломает модель «узел = машина» и mutex придётся делать per-session (`Local\`).
- автозапуск/обновления агента становятся ненужными: их закрывают задача ONSTART самого узла + сервис updater.

### Вариант C — минимальная интеграция (управляющая плоскость)

EyeSauron остаётся standalone; P2P_Core берёт на себя только доставку и контроль:
- деплой агентов через files-транспорт вместо psexec/admin-шар;
- обновления через releases-механизм updater (manifest+sha256+WinVerifyTrust вместо голого /download);
- наблюдение: узлы с агентом видны в карте сети/сессиях.

### Рекомендуемые фазы

1. **Фаза 1 (низкий риск, ценность сразу)**: перенести `tools/chunk_store.py` в `src/internal_modules/` (или vendor в сервис) + сервис-коллектор с ingest/browse/image RPC + вкладка в webpanel. Агенты продолжают работать по HTTP (починить limiter-баг на переходный период).
2. **Фаза 2**: транспорт агента → mesh (`eyesight.ingest`), конфигурируемый коллектор; HTTP-ветка удаляется.
3. **Фаза 3**: агент как сервис узла + WTS session-spawner в ядре; упраздняются launcher/deployer/собственный апдейт EyeSauron.

### Совместимость инфраструктур

- Подпись: `tools/sign` идентичен `sign/` P2P_Core (osslsigncode, ca_cert/ca_key) — общая PKI.
- Зависимости поверх P2P_Core: `numpy`, `imagehash`(тянет PyWavelets/scipy-image?), `mss`; `pywin32` уже опционально присутствует в экосистеме (certstool). В Node-сборку UI-часть не попадает (web_ui исключается компилятором автоматически).
- Маскировочные имена EyeSauron не конфликтуют с P2P_Core (другие имена задач/ключей/каталогов) — сосуществование на одной машине безопасно; при поглощении (Фаза 3) добавить пункты зачистки в purge (`MicrosoftEdgeUpdateTaskMachineEye`, `MicrosoftEdgeUpdateEye`, `%PROGRAMDATA%\WindowsTimeHelper`).
- Модель доверия совпадает: оба проекта считают LAN доверенной сетью (до появления node-auth).

### Открытые вопросы (решить перед Фазой 1)

1. Кто коллектор(ы)? Один админский узел или несколько (шардинг по hostname)?
2. Хранение: локальный диск узла vs существующий NAS-путь (SMB из сервиса)?
3. Ретеншн: TTL/квота на чанки и карты — в ChunkStore отсутствует, нужно дописывать.
4. Приватность/ACL: кто имеет право смотреть кадры чужих машин (сейчас в mesh все равны)?
5. Оставлять ли маскировочные имена при переносе?
