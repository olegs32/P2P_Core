# P2P_Core — База знаний для AI-ассистента

> Быстрый справочник по архитектуре, конвенциям и ключевым паттернам проекта.
> Обновлено: 2026-08-26

---
## Важно: здесь запрещено делать любые записи и упоминания о системе безопасности этого проекта. Для этого существует src/se/AGENTS.md 

## 1. Суть проекта

P2P mesh-сеть на WebSocket + бинарный MessagePack. Узлы соединяются, формируют топологию через gossip, маршрутизируют RPC и стримы через промежуточные хопы. Сервисы загружаются динамически из `services/`. Веб-панель на Streamlit подключается к узлу как WS-клиент.

## 2. Стек

| Слой | Технология |
|------|-----------|
| Transport | WebSocket (FastAPI server + websockets client) |
| Protocol | MessagePack (binary WS frames), PROTOCOL_VERSION 2.0; JSON — только legacy у необновлённых узлов |
| RPC | Встроенный: `@rpc` декоратор, `LocalExecutor`, `Router` |
| Streaming | Mesh: StreamRoute cache, PipeTransport через Router, ACK через backward_path |
| Web UI | Streamlit subprocess на порту 8501, подключается как WS-клиент |
| Config | YAML (pydantic-settings модели, двухфайловая система) |
| Hot-reload | watchdog мониторинг `services/` |

## 3. Точка входа

`main.py` → `load_config()` → **`acquire_single_instance()`** (Global-mutex `P2P_Core_<name>`: второй инстанс узла на хосте немедленно выходит; защищает от срабатывания обоих каналов автозапуска) → `AppContext(cfg)` → регистрация модулей → `ServiceLoader.scan()` → `app_lifespan(ctx)` → `asyncio.Event().wait()`

Порядок регистрации модулей в `AppContext._modules` = порядок `start()`. Обратный порядок для `stop()`.

ServiceLoader.scan() вызывает `ctx.register(instance)` — ручная регистрация сервисов из `services/` в main.py больше не нужна. Spawner остаётся ручным (не в `services/`).

## 4. Ключевые классы

### AppContext (`src/internal_modules/context.py`)
Центральный объект. Содержит: `config`, `NODE`, `services` (ServiceManager), `network` (NetworkModule), `memory` (MemoryModule), `spawn` (Spawner), `certs_index` (CertsIndex).

### ModuleGeneric (`src/internal_modules/base.py`)
Базовый класс всех модулей. Поля: `name`, `ctx` (=AppContext), `log`. Методы: `async start()`, `async stop()`.

### MsgPack + PackType (`src/networking/protocol.py`)
Единый формат пакета. PackType — enum: `HELLO`, `HELLO_ACK`, `HELLO_REJECT`, `REQUEST`, `RESPONSE`, `FORWARDED`, `STREAM_OPEN/READY/CHUNK/ACK/EOF`, `ERROR`, `PING/PONG`, `GOSSIP`, `ANNOUNCE`, `CERT_SYNC`.
MsgPack: `type`, `source`, `dst`, `service`, `method`, `data`, `label` (UUID), `path: list[str]`, `ttl: int=16`.

Сериализация — только через хелперы `encode_pack(pack) -> bytes` / `decode_pack(raw) -> bytes` (прямые `msgpack.packb/unpackb` в других модулях запрещены). `MAX_FRAME_SIZE` = 32 МБ. **Запрещён `model_dump(mode='json')`** для wire-кадров — он не представит `bytes`. В `data` допустимы msgpack-натуральные типы: dict/list/str/int/float/bool/None/**bytes**; ExtType/datetime/timestamps — нет (нужен timestamp → float epoch, как `ts`). str-enum'ы пакуются своим строковым значением, pydantic восстанавливает enum при decode.

### Wire-формат
- Фрейминг: **1 binary WS frame = 1 msgpack-словарь** `MsgPack.model_dump()`. Префиксы длины не нужны — WS message-oriented.
- Кодирование: `encode_pack` = `msgpack.packb(model_dump(), use_bin_type=True)`; декодирование = `msgpack.unpackb(raw, raw=False)` → `MsgPack(**d)`. Пара `use_bin_type/raw=False` обязательна: `bytes ↔ bin-type`, строки всегда UTF-8.
- Режим один: **msgpack-only** (`PROTOCOL_VERSION 2.0`). Сервер читает сырой `websocket.receive()`: text-кадр (легаси JSON-клиент) → binary `HELLO_REJECT` «upgrade required» + закрытие; битый кадр → лог + hexdump первых 64 байт + `close(1002)` (граница доверия). Неопознанный `type` → `UnknownPackTypeError`: пакет дропается, соединение живёт (forward-compat).
- HELLO.data несёт информационное `"enc": "msgpack"`.
- Лимиты кадров обязательны на обеих сторонах: `uvicorn.Config(ws_max_size=MAX_FRAME_SIZE)` и `websockets.connect(..., max_size=MAX_FRAME_SIZE)` (дефолт websockets 1 МБ уронит большие чанки).
- Совместимость: узлы до обновления говорят JSON и с новыми не соединяются (получают понятный HELLO_REJECT); после обновления всей сети сеть полностью msgpack.
- debug_client.py — legacy: остался на JSON, против новых узлов не работает.

### Конвенции для сервисов
Если метод кладёт `bytes` в `data`/чанки стрима — укажи это в докстринге. UI-слой сам решает вопрос отображения (base64 и т.п.) уже вне протокола.

### Router (`src/networking/router.py`)
Центральный маршрутизатор. `handle(pack, transport)` — диспетчер по PackType.
- `_on_request` — локальный RPC через `LocalExecutor`
- `_on_remote_request` — сохраняет WS-transport, форвардит через mesh
- `_forward` — прямой WS / через via из NeighborTable / разрешение по host/IP / NoRouteToHost (условия `or`→`and` в проверке path/alias)
- `_route_back` — обратная маршрутизация по `pack.path` (pop последнего элемента корректно, fallback к `pack.dst` при пустом пути)
- `_resolve_by_host` — поиск node_id в NeighborTable по host/IP
- `call(dst, service, method, data, timeout)` — публичный API: локальный shortcut или mesh-вызов
- `stream(dst, service, method, data, timeout)` — публичный API: открыть mesh-стрим, вернуть `_MeshStreamIterator`
- `send_stream_ack(label, buff)` — отправить ACK генератору через mesh по cached backward_path
- `_ws_pending: dict[str, tuple[WebSocketTransport, float]]` — для ответов WS-клиентам (webpanel); хранит `(transport, created_ts)` для TTL-чистки через `sweep_ws_pending()`
- `_client_ws: dict[str, Any]` — client-side WS маппинг (от NodeConnector)
- `_stream_routes: dict[str, StreamRoute]` — кэш маршрутов стримов (TTL=300с)

#### StreamRoute (dataclass)
Кэшированный маршрут стрима: `label`, `source` (генератор), `dst` (consumer), `forward_path` (source→dst), `backward_path` (dst→source), `established_at`. Свойство `expired` — TTL=300с, **скользящий**: `get_stream_route()` продлевает `established_at` при каждом обращении (долгая передача не теряет маршрут посреди потока). На STREAM_EOF маршрут удаляется сразу; поздние ACK хвостовых чанков после EOF логируются debug-ом (warning — только если стрим ещё жив в StreamRegistry).

Маршрут кэшируется:
- На consumer-узле при получении STREAM_OPEN (`_cache_stream_route_on_open`)
- На generator-узле при получении STREAM_READY (`_cache_stream_route_on_ready`)
- На промежуточных узлах при транзите STREAM_OPEN (`_forward_stream_open`)

#### Mesh streaming flow
```
Consumer                          Intermediate                    Generator
  |--- STREAM_OPEN (path=[]) -------->|--- (cache route) -------->|
  |<-- STREAM_READY (path=rev) ------|<-- (cache route) ---------|
  |<-- STREAM_CHUNK (via cache) -----|--- (fast forward) --------|
  |--- STREAM_ACK (backward_path) -->|--- (route_back) ---------->|
  |<-- STREAM_EOF (via cache) -------|--- (fast forward) --------|
```

#### _MeshStreamIterator
Async iterator, возвращаемый `Router.stream()`. Читает чанки из Pipe, после каждого чанка вызывает `send_stream_ack()`. При `_SENTINEL` — StopAsyncIteration.

### NetworkModule (`src/networking/network.py`)
FastAPI + uvicorn. WS endpoint `/ws/{node_id}`. HELLO-handshake → NeighborTable.register_connected → HELLO_ACK. При дубликате node_id — reconnect (закрыть старое, принять новое). Периодические: gossip (30с), announce (60с). On-connect CERT_SYNC если у узла есть `certstool`.
- `call(dst, service, method, data, timeout)` — thin wrapper вокруг Router.call()
- `stream(dst, service, method, data, timeout)` — thin wrapper вокруг Router.stream()
- `local_ip()` — локальный IP интерфейса mesh (LocalIPResolver, TTL-кэш)
- `connect_to(node_id, target_uri)` — динамическое исходящее подключение к узлу
- `local_sessions()` — снапшот сессий узла с направлением каналов (`direction`: inbound/outbound/inbound+outbound/'' и `age_sec`); единый источник для `system.sessions()` и `netinfo.topology()`

HELLO_ACK содержит `host` = `self.local_ip()` — реальный IP интерфейса mesh, `neighbors` — текущая таблица соседей (для первичного пополнения `NeighborTable` у нового узла). HELLO с несовпадающим `dst:name` отклоняется (HELLO_REJECT). HELLO.data несёт `role`: `'node'` (дефолт) или `'client'` (webpanel и др. служебные WS-клиенты) — сохраняется в NeighborInfo.role.

`ConnectionManager` — DEAD CODE (broadcast() не используется, рассылка через neighbor_table + Router).

### LocalIPResolver (`src/internal_modules/local_ip.py`)
Вычисляет локальный IP интерфейса mesh по запросу, кэш на `network.ip_ttl_sec` (по умолчанию 60с). Приоритет источников:
1. Живые WS-подключения: клиентские — sockname транспорта websockets; серверные — поиск установленного TCP-соединения в таблице psutil по паре (наш порт, remote адрес).
2. UDP-трюк к хосту пира из конфига (`connect((host, 80))`, пакеты не ходят) — ОС выбирает тот же интерфейс.
3. Фолбэк psutil: поднятый не-loopback IPv4 без APIPA (169.254.x.x), через который есть outbound route (bind+connect к 8.8.8.8).

Используется для announce/handshake: узлы сообщают друг другу реальные адреса вместо hostname.

### NeighborTable (`src/networking/neighbor_table.py`)
Статусы: `CONNECTED` (прямое WS), `KNOWN` (через gossip), `UNREACHABLE`. Хранит `via` (next-hop) и `role` ('node'/'client', из HELLO.data; клиенты в карту сети попадают серым, BFS их не опрашивает). `merge_gossip()` — слияние таблиц от других узлов (role, host, port, services, version переносятся из свежего gossip). При `incoming_hops < existing.hops` — полное обновление; при `incoming_hops == existing.hops` и `via` различается — обновляет via только если `existing.via == UNREACHABLE` (failover), иначе сохраняет для стабильности (нет флаппинга). Метаданные (host, port, services, version, role) обновляются всегда при поступлении свежего gossip. `find_by_service()` — поиск узлов с нужным сервисом.

### NodeConnector (`src/networking/node_connector.py`)
Исходящее подключение. Всегда пытается соединиться с пиром; лексикографическое правило (`self.NODE > peer_node_id`) принудительно применяется **сервером** при входящем HELLO: сервер отвечает `HELLO_REJECT lex_rule` и запускает `_lex_reverse_keep_inbound()` (reverse dial обратно к меньшему узлу). `NodeConnector` не блокируется при lex-отказе — ждёт reverse-dial или inbound. HELLO-handshake, receive-loop → Router, keepalive ping (`PING` каждые 20с, таймаут 60с без трафика → ping, 90с → `mark_unreachable`). При connect — `router.register_client_ws()`, при disconnect — `router.unregister_client_ws()`.

### CertsIndex (`src/internal_modules/certs_index.py`)
Индекс сертификатов сети: `thumbprint → CertEntry`. `CertEntry`: subject_cn, valid_to, available_on[], installed_locally, stale (TTL=180с). `last_updated` = `field(default_factory=time.monotonic)`. Методы: `merge_cert_sync()`, `update_local()` (только для `installed_locally=True`), `get_network_available()`, `get_digest_for_sync()`.

### ServiceManager (`services/manager.py`)
Реестр: `services: dict[str, Any]`, методы в `services[name]` dict. Авторегистрация `@generator` при `register_service`. Генераторы с префиксом `__gen__`.

### ServiceLoader (`services/loader.py`)
Сканирует `services/`: директории без `_`-префикса, импортирует .py, находит подклассы `ModuleGeneric`, регистрирует `@rpc` методы. Вызывает `ctx.register(instance)` для lifecycle management. Hot-reload через watchdog: при изменении — reimport + `cancel_by_service`.

### LocalExecutor (`src/internal_modules/executor.py`)
`execute(pack)` — резолвит `service.method` из ServiceManager, вызывает, возвращает RESPONSE MsgPack. `open_stream(pack)` — регистрирует inbound-стрим в StreamRegistry, передаёт `label` в consumer ctx для ACK через Router.

### SessionTable (`src/networking/sessions.py`)
`resolve(label, data)` — `pop(label, None)` из `_table`, ставит результат в Future. `cancel(label)` — drain Queue + sentinel. `register_single()` — создаёт Future.

## 5. RPC система

### Декораторы (`services/rpc.py`)
```python
@rpc           # обычный RPC: method._is_rpc = True
@generator     # генератор для стримов: method._is_generator = True
@stream_wrapper(stream_name)   # подготовка контекста стрима
@stream_consumer(stream_name)  # обработчик чанков из pipe
```

### Вызов RPC
```python
# Из async-кода (модули, сервисы)
result = await ctx.network.call(dst="Node2", service="certstool", method="list_certificates", data={}, timeout=10)

# Локальный shortcut
result = await ctx.network.call(dst=ctx.NODE, ...)  # Router → LocalExecutor

# Mesh-стрим (async iterator)
async for chunk in await ctx.network.stream(
    dst="Node2", service="compute_full", method="compute_ranges", data={"count": 100}
):
    process(chunk)

# Из Streamlit (синхронный)
rpc.call('certstool', 'list_certificates', data={})             # локальный
rpc.call('certstool', 'network_certs', data={}, dst='Node1')   # удалённый через mesh
```

### Контракт метода
```python
class MyService(ModuleGeneric):
    @rpc
    async def my_method(self, data: dict) -> dict:  # data — произвольный dict
        ...
```

### Контракт выполнения (D6) и ошибок (D9)

**Выполнение (D6):**
- async @rpc — выполняются в event loop; внутри ЗАПРЕЩЕНЫ блокирующие вызовы
  (`time.sleep`, sync-сокеты, тяжёлые файловые операции) — только await-able API.
- sync @rpc — автоматически выполняются через `asyncio.to_thread` (не блокируют loop);
  блокирующий I/O в них разрешён. CPU-тяжёлый код → `ProcessPoolExecutor` вручную
  (`to_thread` не обходит GIL). Внимание: `asyncio.create_task(sync_call)` НЕ спасает —
  task исполняется в том же loop-потоке.

**Ошибки (D9) — два уровня, это разные виды отказов:**

| Вид | Механизм | Примеры | Что видит caller |
|-----|----------|---------|------------------|
| Транспорт/система | **ERROR-пакет** → исключение на вызывающей стороне | MethodNotFound, no route, упал producer стрима, необработанное исключение метода | `await call(...)` кидает Exception |
| Бизнес-отказ сервиса | **RESPONSE с `'error'` в data** (`{'ok': False, 'error': ...}` или `{'error': ...}`) | «узел не найден», валидация параметров, отказ по политике | call() возвращается нормально; проверять data |

Правило: если транспорт исправен и сервис осознанно отвечает отказом — это RESPONSE с
`error` в данных. Если сломался маршрут/метод отсутствует/сервис упал — ERROR-пакет.

## 6. Веб-панель

### Архитектура
```
WebPanel (service.py) — запускает subprocess
  └── _streamlit_app.py — entry point Streamlit
       ├── rpc_client.py — NodeRPC (синхронный WS RPC в отдельном потоке)
       ├── RPCProxy — подставляет dst из session_state['selected_node']
       ├── views/home.py — главная: метрики + таблица соседей + сервисы
       └── views/service_view.py — динамический import services/<name>/web_ui.py → render(rpc)
```

### Контракт web_ui.py
Каждый сервис с UI: `services/<name>/web_ui.py` с функцией `render(rpc)`, где `rpc` — `RPCProxy`.

### Реестр сервисов (иконки и группы)
Определён в `services/webpanel/service_meta.py` (единственный источник):
```python
SERVICE_META = {
    'certstool':    ('🔐', 'Сертификаты',  'Управление КриптоПро сертификатами'),
    'netinfo':      ('🌐', 'Сеть',         'Состояние сети и маршрутизация'),
    'files':        ('🗂️', 'Сеть',         'Файловый транспорт между узлами'),
    'system':       ('⚙️', 'Система',      'Управление узлами и подключениями'),
    'config':       ('🛠️', 'Система',      'Удалённое редактирование config.yaml узла'),
    'updater':      ('⬆️', 'Система',      'Обновление узла по mesh'),
    'purge':        ('☢️', 'Система',      'Аварийное удаление узла с хоста'),
    'eyesauron':    ('👁', 'Система',      'Мониторинг экранов: сбор и просмотр кадров'),
    'compute_full': ('⚡', 'Вычисления',   'Генератор + консьюмер'),
    'generator':    ('📤', 'Вычисления',   'Генератор стримов'),
    'test':         ('🧪', 'Диагностика',  'Тестовый echo-сервис'),
    'demo':         ('🎓', 'Примеры',      'Эталонный сервис: все возможности с пояснениями'),
}
GROUP_ORDER = ['Система', 'Сеть', 'Сертификаты', 'Вычисления', 'Диагностика', 'Примеры']
```
Импортируется в `_streamlit_app.py` и `service_view.py` из `service_meta.py`.

### Сервис logs (`services/logs/`) — просмотр логов консоли
`RingBufferHandler` (сквозной id записей) цепляется к root logger в `start()`; параметры — из config.yaml → `logs` (buffer_size / max_msg_len / max_traceback_len, применяются при подключении). RPC: `get_logs({since_id, levels, search, regex, loggers, since_ts/until_ts, limit})` — инкрементальный поллинг по since_id + серверные фильтры, ответ несёт `last_id`/`gap` (обрыв буфера между опросами), `get_loggers`, `clear_buffer`. UI: лента в `st.fragment(run_every=2s)` с тумблером автообновления; смена фильтров меняет сигнатуру `lv_sig` и сбрасывает накопленную ленту (`session_state.lv_rows`, новые записи сверху); экспорт CSV/TXT через download_button. Ограничение: видны только записи, доходящие до root logger (уровень = logging.level из конфига); propagate=False и логи Streamlit-процесса не попадают.

### Сервис files (`services/files/`) — файловый транспорт между узлами
Передача файлов поверх mesh-стриминга **push-механизмом** (Dispatcher + PipeTransport + @stream_consumer, как в demo) — работает через промежуточные хопы, с ACK/backpressure. `router.stream()` сознательно не используется. Все тяжёлые ФС-операции (`find`/`stat`/`list_local_dirs`) — async через `asyncio.to_thread`, чтобы не блокировать event loop узла; диски определяются мгновенно через WinAPI GetLogicalDrives (Path.exists() по сетевым дискам вешает секунды).

Протокол загрузки (инициатор — получатель B, источник A):
1. B: `files.download({dst:'A', ref})` → RPC `stat` к A → манифест `{id=sha256, share, path(отн.), size, chunk_size}`
2. B регистрирует состояние приёма по `label`, RPC `serve({label, reply_to, ref, offset})` к A
3. A: pipe+dispatcher, sync-генератор читает файл чанками (`_chunk_file`), пушит STREAM_OPEN(method=`file_in`) к B
4. B: @stream_wrapper(`file_in`) находит состояние по label; @stream_consumer пишет в `<final>.part` с prefetch-ACK; на EOF — сверка размера и sha256, атомарный `os.replace(.part → final)`. **Грабли**: имена wrapper/consumer-методов не должны начинаться с `_` — `get_stream_handlers()` пропускает приватные имена, стрим не регистрируется, чанки дропаются («CHUNK for unknown stream»), а PipeTransport печатал «handshake ok» даже на ERROR-пакет (resolve() кладёт исключение как значение; теперь проверяется `isinstance(res, Exception)`).

Адресация: `ref = {share, path}` или content-addressed `{id}` (sha256 считается лениво, кэш по size+mtime_ns). Resume: докачка `.part` через `offset`; повторный download целого файла мгновенно отвечает done. Локальный шорткат dst=self → shutil.copyfile.

RPC-методы: `list_shares` (имена/объём, без локальных путей), `find({share?, pattern?, limit})`, `stat({share,path}|{id})`, `serve`, `download({dst, ref, save_as?, resume?})`, `downloads()` (статусы для UI), `cancel_download({label})`; управление шарами из UI: `list_local_dirs({path})` (браузер каталогов, абсолютные пути), `add_share({path, name?, allow?, chunk_size?})`, `remove_share({name})`.

Расшаривание из UI: `_persist_shares()` пишет через `ConfigManager.update(files__shares=…)` и **синхронизирует `ctx.config.files.shares` на месте** — update() создаёт новый объект cfg, и без этого `ctx.config` расходился бы с `config_manager.cfg`. `_cfg()` читает первично из `config_manager.cfg`.

Безопасность: path traversal закрыт `_safe_join` (только относительные пути внутри корня шары); ACL шары `allow: [node_id]` ([] = всем), проверяется по `reply_to` из запроса (до появления аутентификации узлов — защита от ошибок, не от злонамеренных узлов); наружу никогда не отдаются абсолютные пути узла.

Конфиг (config.yaml → `files`, модели `FilesConfig/ShareConfig`): `shares: [{name, path, allow[], chunk_size=256KB}]`, `download_dir` (относительный резолвится от `local.work_dir`), `max_chunk=4МБ`. UI: экспандер «Расшаривание папок» (текущие шары + удаление, браузер каталогов узла с навигацией диск→вниз/вверх, форма имя/чанк/allow), выбор источника из подключенных, каталог шары с маской, скачивание выделенного, лента загрузок с прогрессом (`st.fragment(run_every=3s)`). Ограничения MVP: обрыв передачи детектится получателем по размеру/hash (источник останавливается по таймауту ACK); потоковое воспроизведение media — следующий этап; `list_local_dirs` доступен любому подключенному узлу (до аутентификации mesh считается доверенной сетью).


### Сервис demo (`services/demo/`) — эталонный пример
Учебный сервис с подробными пояснениями в комментариях. Демонстрирует: жизненный цикл (start/stop), @rpc sync/async, mesh-RPC из кода (find_by_service + network.call), @generator, push-стрим (Pipe + Dispatcher + attach_transport), приём стрима (@stream_wrapper/@stream_consumer + ACK prefetch), вызов Spawner'а через локальный шорткат. UI: три вкладки (проверка связи, стрим, распределённые вычисления). Новые сервисы делать по его образцу.

### Сервис updater (`services/updater/`) — обновление узла по mesh
Тонкий клиент над files-транспортом + локальный applier. Версия узла: `version.txt` (frozen — из бандла, генерирует compile.py из `VERSION` + счётчика `BUILD_NUMBER`; dev — корень проекта, fallback `0.0.0-dev`), читается `src/internal_modules/app_version.py` (формат `MAJOR.MINOR.PATCH[-buildN]`).

Релиз на админской ноде = каталог в шаре (расшаривается через files UI): `<ver>/Node_P2P_Core.exe` + `<ver>/manifest.json` `{version, exe_sha256|id, exe_name?, size?, notes?, min_compatible?}`.

Упаковка текущей версии: `build({notes?, min_compatible?})` — создаёт `dist/<version>/` с exe и `manifest.json`; доступно только в frozen-сборке. Auto-сборка manifest: `compile.py` после сборки вызывает `make_manifest(version)` — переносит `dist/Node_P2P_Core.exe` в `dist/<version>/` и генерирует `manifest.json` с sha256/size.

Поток: `check()` — find(`*/manifest.json`) + `files.read` по каждому источнику → список версий; если источник = локальный узел, вызов идёт локально (`dst=self.ctx.NODE`) через `_resolve_dst` (разрешение node_id/host). `download({version})` — files.download с `save_as=<ver>_<exe>` + сверка sha256; `apply({version, force})` — только frozen, guard перехода (новее ИЛИ allow_downgrade/force), hash+WinVerifyTrust (`verify.py`, ctypes/wintrust, GENERIC_VERIFY_V2), rename-trick (running→`.old`, новый на место), state-файл `<local.work_dir>/update_state.json`, detached cmd-стартер ждёт exit и поднимает новый exe, затем `os._exit(0)`.

Boot-confirm/rollback: новая версия инкрементирует `attempts`, после `health_confirm_sec` здоровой работы ставит `boot_ok`; если процесс упал до подтверждения — при старте `attempts > MAX_ATTEMPTS(2)` ⇒ автоматический откат `.old`, версия попадает в `locked_versions`. RPC: `status/check/download/apply/build/clear_state` (+внутр. `_do_rollback`). В dev-режиме apply/build запрещены.

### Сервис system (`services/system/`)
Управление узлами сети и автозапуск.

RPC-методы:
| Метод | Описание |
|-------|----------|
| `connect_to_node` | Исходящее подключение к узлу `{host, port, node_id}`; разрешено если удалённый НЕ подключен к локальному И соблюдено лексикографическое правило (`NODE > node_id`, иначе `{ok: False, lex_rule: True}` без создания коннектора); при успехе пир сохраняется в config.yaml → local.peers |
| `list_connectors` | Активные исходящие коннекторы (модули `Connector_*`) |
| `node_detail` | Обзор узла: own, connected, known, ws_connections, services |
| `config_peers` | Пиры из config.yaml → local.peers |
| `sessions` | Все сессии узла: записи NeighborTable любого статуса (connected/known/unreachable) + session_id из HELLO-рукопожатия (тот же, что в логе «Node X accepted (session=…)»), direction (inbound по nodes_manager / outbound по Router.has_client_ws), age_sec, counts. Строки строит общий `NetworkModule.local_sessions()` |
| `ctx_map` | Интроспекция AppContext для разработчика: по каждому атрибуту — тип, назначение (CTX_ATTR_DOCS в service.py), публичные методы с сигнатурами; router/neighbor_table/nodes_manager раскрыты на уровень глубже; для services — реестр сервисов с методами и @generator; каждый entry/child несёт `rpc_service`. pydantic-модели и списки (config, peers) отдаются значениями (`data`, рекурсивно; поля secret/password/token/key маскируются) |

Веб-интерфейс (`web_ui.py`): вкладки «Управление узлами» (метрики + таблицы соседей + RPC-консоль с известными методами `KNOWN_METHODS` и подсказками аргументов), «Подключение» (форма подключения + текущие коннекторы + пиры из конфига; после попытки подключения — `st.rerun()` с перезапросом всех таблиц, результат попытки показывается после рерана из `session_state['sys_connect_result']`), «🧵 Сессии» (таблица всех сессий узла с session_id/направлением/возрастом, автообновление через st.fragment, полный JSON в expander) и «🧭 Контекст» (карта self.ctx; клик по методу сервиса подставляет его в RPC-консоль через `session_state['ctx_pick']`). Импорт streamlit обёрнут в try/except — сервис работает и в headless-сборке.

Автозапуск Windows (не RPC, вспомогательные методы):
- `add_to_task_scheduler()` / `remove_from_task_scheduler()` — задача автозапуска при **старте хоста**: `schtasks /SC ONSTART /RU SYSTEM` (до логина пользователя, имя = LocalConfig.name, bool-результат)
- `remove_from_registry_startup()` — [legacy] зачистка HKCU Run-ключей от старых версий; реестровый канал автозапуска упразднён (пользовательская сессия не нужна)
- Двойной запуск не страшен: main.py держит Global-mutex (`P2P_Core_<name>`) — второй инстанс сразу выходит; плюс NetworkModule.start() fail-fast: если WS-порт не поднялся за ~3с (занят/ошибка bind) — RuntimeError, узел завершается
- BASE_DIR: у frozen-узла = каталог exe (планировщик запускает процесс с cwd=System32 — привязка к cwd унесла бы config.yaml в системный каталог), в dev — корень репозитория, а не живёт зомби без сети

### Сервис config (`services/config/`) — удалённое редактирование config.yaml
Всегда включён (флага нет): headless-узел не имеет локального UI, конфиг правится только из панели. Семантика применения: **сохранить** (валидация → бэкап → атомарная запись → инплейс-синк живых объектов → hot apply) и **сохранить + перезапустить** (detached-стартер, механика updater; frozen — тот же exe, dev — `python main.py`, cwd = каталог config.yaml).

RPC:
| Метод | Описание |
|-------|----------|
| `get` | Текст config.yaml (+ mtime, path, backups), `local.secret` замаскирован `__MASKED_SECRET__` |
| `save({text, base_mtime?, restart?})` | Валидация `Config(**parsed)` ДО записи; конфликт по mtime если файл меняли с момента чтения (`conflict: true`); ответ несёт `applied_hot`, `restart_required_sections`, `warnings` |
| `backups` / `read_backup({name})` | Список копий / текст копии (тоже с маской секрета) |
| `restore({name})` | Восстановление ТОЛЬКО по имени из backups() — путей от сети нет |

Горячо применяются: `logging.level`, `logging.websockets_level` (setLevel на root/websockets-логгеры) и `logs.*` (панель подхватывает при следующем подключении). Остальные секции полностью вступают после рестарта. Инплейс-синк `_sync_live()` заменяет секции в `ctx.config` И `config_manager.cfg` с сохранением идентичности объектов (ссылки на `ctx.config` валидны); сервисы, кэшировавшие ссылки на сами секции, увидят новое после рестарта.

Безопасность: валидация до записи (битый конфиг не попадает на диск); бэкап перед каждой записью (`config.yaml.backups/config_<ts>.yaml`, ротация 10, дубликат последней копии не создаётся); маска секрета при save() подменяется реальным значением; неизвестные top-level секции сохраняются как есть с warning (forward-compat). UI: textarea-редактор (синхронизация по mtime через `cfg_loaded_mtime`), кнопки «Сохранить»/«Сохранить и перезапустить» (чекбокс подтверждения), экспандер бэкапов с просмотром/восстановлением; потеря связи при рестарте трактуется как ожидаемый исход.

### Сервис purge (`services/purge/`) — аварийное удаление узла с хоста
Полное снятие узла: автозапуск, конфиг, данные, образ exe, процесс. Включён по умолчанию (`purge.enabled: true`) — headless-узлу аварийное удаление нужно беспрепятственно.

RPC:
| Метод | Описание |
|-------|----------|
| `plan` | Сухой прогон: перечень целей с id/группой/путём/размером/present + `enabled`, `frozen`, `pid`. Единственный источник допустимых id |
| `purge({items, confirm})` | Исполнение по id из плана; обязателен `confirm: true`; выбор `exe`/`process` останавливает узел (~3с после RESPONSE, как в updater) |

Безопасность: пути снаружи не принимаются вовсе (только id из `plan()`); отказ на опасные цели (корень диска, Windows, Program Files, ProgramData); в dev-режиме (не frozen) образ exe не удаляется; очистка work_dir пропускает живой образ exe.

Механика self-destruct (выбор `exe`): rename-trick `exe → exe.purging` (запущенный образ можно переименовать) + detached cmd-стартер (`timeout 3s & del & rd /q` — rd без /s удаляет только ПУСТОЙ каталог), затем `os._exit(0)`. Пункты: `autorun_task` (schtasks по LocalConfig.name), `autorun_registry` (HKCU Run), `config` (config.yaml через `config_manager.config_path`), `work_dir` (весь, кроме живого exe), `update_leftovers` (.old/.failed/.purging + updates/), `exe`, `process`.

UI (`web_ui.py`): таблица целей с мультивыбором (st.dataframe on_select multi-row), кнопки «🗑 Удалить выбранное» и «☢️ Удалить ВСЁ» (все present-пункты), обязательный checkbox-подтверждение; предупреждение при выборе фатальных пунктов; потеря связи с узлом трактуется как ожидаемый исход. Результат показывается после st.rerun из `session_state['purge_result']`.

### Сервис eyesauron (`services/eyesauron/`) — мониторинг экранов EyeSauron
Порт проекта EyeSauron в mesh (анализ и план — `docs/eyeSauron.md`). Две независимые роли, включаются в config.yaml → `eyesauron`. По умолчанию ВЫКЛЮЧЕН (`eyesauron.enabled: false`, по аналогии с purge).

- **Роль collect (коллектор)**: RPC `ingest({meta:{hostname,timestamp,title}, png}, data несёт bytes)` → raw PNG в `store_path/<host>/<date>/<ts>__<title>.png` (через `asyncio.to_thread`, NAS медленный); `browse({level:'hosts'|'dates'|'images', host?, date?, filter?})`; `image({file})` → bytes PNG (только относительные пути внутри store_path, `_safe_rel`); `stats()` (полный обход, долгий).
- **Роль capture (агент)**: узел в session 0 не видит рабочий стол → сервис через WTS-инъекцию (`_wts.py`, порт launcher.py: WTSEnumerateSessions + WTSQueryUserToken + CreateProcessAsUserW, флаг CREATE_NO_WINDOW) запускает хелпер `_session_helper.py` в каждой активной сессии. Хелпер: захват (mss → PIL.ImageGrab → ctypes GDI), валидация (PNG ≥ 10KB, детект чёрного экрана), дедуп собственным average_hash на numpy (без пакета imagehash), заголовок активного окна через ctypes; кадры пишет в spool `<local.work_dir>/eyesauron/spool/<md5>` + `.meta` (формат офлайн-кэша оригинала). Сервис разбирает spool: шлёт коллектору `eyesauron.ingest` по mesh, при недоступности копит буфер (потолок `max_spool_mb`, старейшие вытесняются); пауза между отправками `send_delay_sec` (щадит NAS). Хелпер держит mutex `Local\EyeSauronCaptureMutex` (per-session namespace) и сам выходит при завершении своей сессии.
- Запуск хелпера: frozen — тот же exe с ключом `--eye-sauron-helper` (argv-хук в начале main.py до инициализации узла); dev — `python <script>` c bootstrap sys.path.
- RPC `status()` (роли, хелперы по сессиям, spool), `test_capture()` (прямой захват из процесса узла — только dev/интерактивная сессия). UI: вкладка «👁 EyeSauron» — статус агента + просмотр архива (host → date → filter → таблица кадров → st.image).
- Зависимости: `mss`, `pillow`, `numpy` (+ vendor `_vendor_chunk_store.py` — снимок ChunkStore как образец).
- **Пакованное дедуп-хранилище (РЕАЛИЗОВАНО, спека `docs/eyesauron_storage.md`)**: движок `_pack_store.py` — иммутабельные тома `.pack/.idx/.bloom` (append-only, seal → одна последовательная заливка на NAS с докачкой `.part` и sha256-верификацией), манифест `volumes.json`, карты кадров в дневных сегментах `maps/seg-*.mseg`. Включается `eyesauron.store.enabled: true` (по умолчанию выкл — ingest пишет raw PNG). Ключи: `store.volume_size_gb/local_cache_gb(100)/max_age_hours/bloom_enabled/root`. Дедуп через границы seal/рестартов (hot-кэш + per-volume idx binsearch + bloom опционально). Журнал staging — коммит на кадр; краш теряет максимум хвост журнала. Каталог host/date выводится из мет сегментов + доскан хвостов. RPC `seal_now`; в `status()` блок `store`+`telemetry`.
- **Телеметрия скролла** (`_telemetry.py`, на коллекторе): детект вертикального сдвига между соседними кадрами хоста (downscale 96×128, ±15 строк); файлы `<work_dir>/eyesauron/telemetry/<день>.jsonl` (строка на наблюдение) + `summary.json` (агрегаты по дням/хостам, атомарная перезапись раз в минуту, ротация 90 дней). Метрика решает включение CDC-томов (порог ~15% — docs/eyesauron_storage.md §7). Бенчмарк стратегий чанкинга — `_bench_cdc.py`: chunker v1 = grid256 (CDC проиграл на спокойных потоках из-за компрессии сырых чанков, выиграл только скролл; cdc_png опровергнут каскадом deflate).

### Сервис netinfo (`services/netinfo/`) — диагностика сети и карта топологии
RPC: `neighbors`, `nodes`, `services`, `find_service`, `topology`.

`topology({ttl=4, visited?})` — карта сети: рекурсивный BFS по connected-узлам (параллельный gather, timeout 6с/узел; клиенты role='client' не опрашиваются). Каждое ребро `{src → dst}` = «src держит outbound WS к dst» (канонизация из `direction` через `NetworkModule.local_sessions()`: outbound даёт ребро own→peer, inbound — peer→own; dual-канал = два встречных ребра). Ребро `verified=True` только если его сообщили ОБА конца (иначе half-open — признак зомби-сокета). Ответ: `{ok, root, nodes[], clients[], edges[], errors{}}`; ошибки отдельных узлов попадают в `errors`, не валия карту. Полный снимок (ttl=4, без visited) кэшируется на узле на 4с (`cache_age_sec`). known-only узлы приходят со status='known' + via — физических рёбер у них нет.

UI (вкладка «🗺 Карта сети» в `web_ui.py`): streamlit-agraph (force-graph, directed) — зелёные рёбра verified, красные half-open, жёлтый пунктир «gossip» от via для known-узлов, серые клиенты панели, синий корневой узел; автообновление st.fragment(5s) с тумблером + кнопка; клик по узлу — карточка JSON из снимка. Фолбэки: нет компонента → таблица рёбер. Зависимость только WebUI-сборки: `streamlit-agraph` (+ `--collect-all streamlit_agraph` в compile.py); Node-сборка не меняется (web_ui.py исключается).

### NodeRPC (`services/webpanel/rpc_client.py`)
Синхронная обёртка для Streamlit. Фоновый asyncio loop в отдельном потоке. HELLO-handshake (с `"role": "client"` в data — узел хранит панель как клиента, не mesh-узла), receive-loop. `call()` блокируется через `threading.Event`. Свойства: `connected`, `node` (= target_node), `reconnecting`.

**Reconnect:** при потере WS — `_reconnecting=True`, `connected` возвращает True во время реконнекта (предотвращает Streamlit от создания нового NodeRPC). `_recv_task` отменяется при реконнекте.

### Sidebar навигация
Кнопки вместо radio, группировка по категориям, `st.session_state.current_page` для роутинга. Активная кнопка `type="primary"`.

## 7. Mesh-стриминг с backpressure

### Архитектура
```
Generator Node                   Intermediate Node(s)          Consumer Node
  Generator ─→ Dispatcher          Route cache (StreamRoute)     StreamRegistry
    ─→ Pipe ─→ PipeTransport ─→  Router._forward() ─→          Router.handle()
                (via Router)       _forward_stream_data()         ─→ feed to Pipe
                                   (fast path via cached route)   ─→ Consumer reads
                                   _route_back() for ACK          ─→ send_stream_ack()
```

### Компоненты

| Компонент | Роль |
|-----------|------|
| **Pipe** | asyncio.Queue с buff_len, low_watermark, refill callback |
| **Dispatcher** | Распределяет элементы генератора по N Pipe; при ошибке producer — close() без sentinel |
| **PipeTransport** | Подключен к Router (не к WS напрямую). _handshake_and_pump → router._forward(STREAM_OPEN). _pump → router._send_pack(CHUNK/EOF). Ждёт ACK через router.sessions |
| **StreamRoute** | Кэшированный маршрут: forward_path + backward_path для быстрого форвардинга |
| **Router.send_stream_ack()** | Consumer вызывает для отправки ACK генератору через mesh (backward_path) |
| **_MeshStreamIterator** | Async iterator: читает из Pipe, после каждого чанка — ACK |
| **MemoryModule** | Фабрика: `create_pipe()`, `create_dispatcher()`, `attach_transport(pipe, template, router)` |
| **StreamRegistry** | Реестр inbound-стримов: label → Pipe |

### PipeTransport — новая сигнатура
```python
PipeTransport(pipe, router, pack_template, timeout=30)
# Раньше: PipeTransport(pipe, transport, pack_template, router, timeout)
# transport убран — отправка через Router вместо прямого WS
```

### attach_transport — новая сигнатура
```python
ctx.memory.attach_transport(pipe, pack_template, router)
# Раньше: attach_transport(pipe, transport, pack_template)
```

### Consumer ACK через Router
```python
# В @stream_consumer:
router = self.ctx.network.router
if label:
    await router.send_stream_ack(label, buff)
```

### Публичный API стриминга
```python
# Открыть mesh-стрим и читать чанки
async for chunk in await ctx.network.stream(
    dst="Node2", service="compute_full", method="compute_ranges",
    data={"count": 100}, timeout=30
):
    process(chunk)
```

## 8. CERT_SYNC — сетевая установка сертификатов

### Механизм
1. CertsTool._cert_sync_loop() (каждые 60с): обновляет CertsIndex из локальных серт., рассылает CERT_SYNC всем connected
2. Router.handle(CERT_SYNC): вызывает CertsIndex.merge_cert_sync()
3. NetworkModule при HELLO с `certstool` в services: запрашивает get_cert_sync_digest через RPC
4. UI: вкладка «Сетевая установка» — сертификаты из сети, группировка по subject_cn

### Сетевая установка (install_from_node)
1. RPC к удалённому узлу: get_certificate_info (по thumbprint) → получить Container
2. Сгенерировать одноразовый пароль (secrets.token_hex)
3. RPC к удалённому: export_certificate_pfx(container, one_time_password)
4. Локально: install_pfx_from_base64(pfx_base64, one_time_password)
5. Сменить пароль контейнера на пользовательский
6. Обновить CertsIndex, записать в _install_history

## 9. Конфигурация

Один файл — `config.yaml`; настройки узла живут в его секции `local`. Если файла нет — `_ensure_config()` создаёт его с дефолтами (`node` = hostname машины). Если файл есть, но не хватает секций/полей (например, после обновления кода) — `ConfigManager._load()` достраивает их дефолтами при загрузке (`_deep_fill`: добавляются только отсутствующие ключи, существующие значения не трогаются, пустая секция `key:` трактуется как отсутствующая) и перезаписывает файл с `log.info` о добавленных путях; операция идемпотентна. Pydantic-модели: `Config` → `NetworkConfig`, `MemoryConfig`, `LoggingConfig`, `ServicesConfig`, `LocalConfig`.

```yaml
node: Node0            # default: hostname
network:
  host: 0.0.0.0
  port: 9000
  ip_ttl_sec: 60       # TTL кэша LocalIPResolver
memory:
  default_buff: 10
logging:
  level: INFO
logs:                    # LogsConfig — буфер логов для веб-панели
  buffer_size: 2000      # ёмкость кольцевого буфера
  max_msg_len: 4000      # обрезка одного сообщения
  max_traceback_len: 2000  # обрезка traceback (берётся хвост)
files:                   # FilesConfig — файловый транспорт (сервис files)
  download_dir: downloads  # куда класть полученное (отн. → от local.work_dir)
  max_chunk: 4194304       # потолок chunk_size из запросов
  shares: []               # [{name, path, allow: [], chunk_size: 262144}]
update:                  # UpdateConfig — обновление узла (сервис updater)
  enabled: true
  sources: []              # [{node: AdminNode, share: releases}]
  auto_check: true
  check_interval_min: 60
  auto_apply: false        # применять без подтверждения из панели
  require_signed: true     # WinVerifyTrust перед применением
  allow_downgrade: false   # иначе только apply({force:true})
  health_confirm_sec: 90   # время до boot_ok после апдейта
purge:
  enabled: true            # аварийное удаление узла (сервис purge) — включён по умолчанию
eyesauron:                 # EyesauronConfig — мониторинг экранов (сервис eyesauron)
  enabled: false             # ВКЛЮЧАТЬ ОСОЗНАННО (аналог purge.enabled)
  collect: true              # роль коллектора: приём кадров + raw PNG в store_path
  capture: false             # роль агента: захват экранов машины (хелпер в сессии)
  store_path: \\192.168.53.21\photo\screens  # <host>/<date>/<ts>__<title>.png
  collector_node: ''         # для capture: узел-коллектор ('' = копить в spool)
  interval_sec: 5.0          # период захвата, сек
  send_delay_sec: 0.5        # пауза между отправками коллектору (щадит NAS)
  max_spool_mb: 500          # потолок офлайн-буфера агента
  store:                     # пакованное дедуп-хранилище (см. docs/eyesauron_storage.md)
    enabled: false             # вкл → ingest пишет в тома .pack вместо raw PNG
    root: \\192.168.53.21\photo\store\packs   # NAS: готовые тома + манифест
    volume_size_gb: 10         # цель seal по размеру
    local_cache_gb: 100        # кэш готовых томов локально (LRU)
    max_age_hours: 24          # seal полупустого тома
    bloom_enabled: false       # поиск по bloom (файлы пишутся всегда)
services:
  path: services/
local:                 # LocalConfig — параметры деплоя/автозапуска
  alias: <hostname>
  name: Core           # имя задачи планировщика / ключа реестра
  exe_name: Node_P2P_Core.exe
  secret: null         # маскируется в ctx_map
  work_dir: C:\Core    # создаётся автоматически
  full_path: C:\Core\Node_P2P_Core.exe
  excluded_autoload_services: [webpanel]   # не грузить в headless-сборке
  peers: []            # [{node_id, uri}] — автоподключение при старте
```

### ConfigManager (`src/internal_modules/config.py`)
Автосохранение в config.yaml при каждой модификации:
- `update(network__port=9001, ...)` — обновление любых полей, вложенность через `__`
- `get_local(key)` / `set_local(key, value)`
- `add_peer(node_id, uri)` / `remove_peer(node_id)` / `list_peers()` — пиры в local.peers; именно сюда сервис `system.connect_to_node` сохраняет пиров

## 9a. Сборка и подпись дистрибутива

`compile.py` — PyInstaller onefile, две сборки:

| Бинарь | UI | Особенности |
|--------|----|-------------|
| `WebUI_P2P_Core.exe` | Streamlit | `--collect-all services`, `--collect-all streamlit_agraph`, streamlit hidden-imports |
| `Node_P2P_Core.exe` | нет | excludes: `services.webpanel`, `streamlit`; остальные сервисы через `--collect-all` |

После сборки каждый exe подписывается через `sign/signer.py` (osslsigncode, нужны `sign/ca_cert.pem` + `sign/ca_key.pem` — gitignored). Подписанный файл перемещается обратно в `dist/<name>.exe`.

Frozen-режим: встроенные сервисы грузятся из `sys._MEIPASS/services` (ServiceLoader), локальные из `./services`. В headless-сборке webpanel исключается также через `LocalConfig.excluded_autoload_services`.

## 9b. Roadmap

`roadmap.md` — текущие TODO: обновление сервисов/core по сети, autorun-модуль, self-removing, рефакторинг eye-sauron как локального сервиса (анализ проекта и план интеграции — **`docs/eyeSauron.md`**), панель управления через политики.

## 10. Конвенции

### Создание нового сервиса
1. `services/<name>/` — директория
2. `services/<name>/__init__.py` — пустой
3. `services/<name>/service.py` — класс `<Name>(ModuleGeneric)` с `@rpc` методами
4. ServiceLoader автоматически найдёт, зарегистрирует и вызовет `ctx.register(instance)`

### Для добавления UI к сервису
5. `services/<name>/web_ui.py` — функция `render(rpc)`
6. Добавить запись в `SERVICE_META` (в `services/webpanel/service_meta.py`)

### Файловые конвенции
- Файлы с `_`-префиксом игнорируются ServiceLoader (например `_streamlit_app.py`)
- Директория `views/` (не `pages/`!) — чтобы Streamlit не создавал автоматическую навигацию
- Импорты в сервисах: `from src.internal_modules.base import ModuleGeneric`, `from services.rpc import rpc`

### Паттерн ModuleGeneric
```python
class MyService(ModuleGeneric):
    def __init__(self, name: str, context):
        super().__init__(name, context)
        # self.ctx = context (доступно через self.ctx)
        # self.log = logging.getLogger(name) (доступно через self.log)

    async def start(self):
        ...

    async def stop(self):
        ...

    @rpc
    async def my_method(self, data: dict) -> dict:
        return {"result": "ok"}
```

## 11. Порты

| Порт | Назначение |
|------|-----------|
| 9000 | WebSocket сервер узла (FastAPI) |
| 8501 | Streamlit веб-панель |

## 12. Известные проблемы / TODO

- `debug_client.py` — legacy на JSON: против msgpack-узлов не работает (перевести отдельной задачей)
- Удалённые сервисы в webpanel: sidebar теперь берёт `services` из NeighborTable (gossip, `node_status`), отдельный RPC `netinfo.services` больше не вызывается для удалённых нод; fallback на кэш `session_state` при временной недоступности узла. `web_ui.py` проверяется локально (`service_view.py`) — для рендера нужен файл на UI-ноде (обычно уже есть в codebase, т.к. UI актуальной версии); если сервис есть только на конкретной удалённой ноде и не закомичен в UI — при клике покажет fallback. Ограничение #fixed 4c68d425 on 01.09.2026
- CERT_SYNC on-connect — проверка services в HELLO предотвращает timeout
- StreamRoute cache TTL=300с скользящий (продлевается обращениями через get_stream_route) — устаревание возможно только при простое стрима дольше TTL
- Loop detection + TTL=0 в `_on_forwarded` — пакет дропается (return), не форвардится дальше
- LocalIPResolver: серверные подключения резолвятся через TCP-таблицу psutil — платформозависимо (Windows-first)
- Автозапуск: задача планировщика создаётся с `/TR "{exe_path}"` без внутреннего экранирования кавычек — путь к exe с пробелами может обрезаться (актуально при нестандартном work_dir)
- `config.yaml` не хранится в репозитории — создаётся автоматически с дефолтами при первом запуске; отдельного `config.local.yaml` больше нет, всё в config.yaml → секция local
