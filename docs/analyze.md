# Анализ проекта P2P_Core

> Полный аудит: логические ошибки, баги, мёртвый код, отклонения от архитектуры,
> места оптимизации и возможные ускорения без смены основного замысла.
> Дата: **2026-08-23**
> Актуализировано: **2026-08-24** — решённые пункты помечены ✅ с описанием исправления.
> Первоначальный аудит: код не изменялся — только анализ. Ссылки в формате `файл:строка`.

---

## Содержание

- [1. Критические логические баги](#1-критические-логические-баги)
- [2. Гонки и снижение надёжности (средний приоритет)](#2-гонки-и-снижение-надёжности-средний-приоритет)
- [3. Мёртвый код](#3-мёртвый-код)
- [4. Отклонения от архитектуры / несоответствия](#4-отклонения-от-архитектуры--несоответствия)
- [5. Возможные ускорения](#5-возможные-ускорения)
- [6. Мелочи](#6-мелочи)
- [7. Итог: топ-5 по важности](#7-итог-топ-5-по-важности)

---

## 1. Критические логические баги

### B1. Обратная маршрутизация ломается на маршрутах из ≥3 узлов

Самая серьёзная находка. Возвращаемые пакеты строятся как `path = reversed(pack.path)`:

- `src/networking/router.py:292` (`_on_request`, RESPONSE)
- `src/networking/router.py:488` (`_send_back`, STREAM_READY/PONG)
- `src/networking/router.py:209` (PING)
- `src/networking/router.py:655` (`_PathAwareTransport.send`)

При этом `_route_back` (`router.py:454`) трактует `path` как стек, где **хвост = следующий
хоп к источнику**, и выталкивает себя **с хвоста**.

Трасса цепочки C→I→J (J — исполнитель):

1. У J: `pack.path = [C,I,J]` (J добавил себя в `_on_forwarded:236`);
2. RESPONSE: `path = reversed → [J,I,C]`;
3. `_route_back` у J: хвост `C ≠ J` → не выталкивается → `next_hop = C`;
4. У J нет прямого транспорта до C → `return path broken`, пакет **молча теряется**
   → RPC по таймауту.

Правильно — **не разворачивать**: путь уже `[origin,…,responder]`, каждый хоп выталкивает
себя с хвоста (`[C,I,J]` → pop J → `[C,I]` → next_hop=I ✓). Именно так работает
`STREAM_ACK` через `backward_path` (`router.py:331`) — единственное место, где конвенция
соблюдена, что подтверждает правильность «не разворачивать».

Почему не замечено: на 1–2 узлах `next_hop = origin` совпадает с прямым линком и всё
«работает». Любая настоящая multi-hop топология (3+ узла) теряет RESPONSE / ERROR /
STREAM_READY / PONG. `STREAM_CHUNK/EOF/ACK` частично живы из-за fallback на `_forward`.

### B2. Гонка ACK в PipeTransport: future регистрируется после отправки батча

`src/internal_modules/memory.py:103-128`: `_pump` отправляет все `buff_size` чанков и
только потом (`:121`) делает `register_single(ack_label)`. Консьюмер
(`_MeshStreamIterator.__anext__`, `router.py:636`) шлёт ACK после **каждого** чанка.
Быстрый консьюмер отвечает раньше, чем регистрируется future → `sessions.resolve`
(`router.py:161`) не находит сессию и **молча роняет ACK** → `_pump` ждёт полный
`timeout=30с` → стрим умирает после первого батча.

Сейчас спасает только то, что демо-консьюмер медленный
(`services/compute_full/service.py:114` — `sleep(0.1)`).
Лечится регистрацией future **до** отправки батча.

### B3. Необработанное исключение в @rpc убивает WS-соединение целиком

- `executor.execute` (`executor.py:24-49`) не ловит ничего;
- `_on_request` ловит только `MethodNotFound` (`router.py:295`);
- исключение сервиса летит через `handle()` → в `websocket_endpoint`
  (`network.py:161-182`) **нет общего `except Exception`** → корутина умирает без
  `WebSocketDisconnect`;
- следствия: `nodes_manager` не чистится (запись-«призрак» до keepalive DEAD_TIMEOUT
  90с), `_ws_pending` не чистится; `MsgPack(**data)` с невалидным payload
  (`network.py:163`) убивает соединение так же.

### B4. Ошибка producer неотличима от нормального завершения стрима

`memory.py:213-217`: при ошибке генератора Dispatcher делает `pipe.close()` **без
sentinel** — но `Pipe.__anext__` (`memory.py:51-57`) бросает `StopAsyncIteration` когда
`closed and empty` → `_pump` выходит из цикла как обычно и шлёт **STREAM_EOF**
(`memory.py:130`). Консьюмер получает «успешное» завершение при оборванных данных.
Комментарий «прервать цепочку» и описание в glm.md не соответствуют реальному поведению.

### B5. RPC-консоль в system/web_ui игнорирует выбранный узел

`services/system/web_ui.py:200-204`: `target` вычисляется, но в вызов **не передаётся**:
`rpc.call(selected_svc, selected_method, call_data, timeout=timeout)` — аргумент
`dst=target` потерян. Все вызовы «удалённых» узлов уходят локально.

### B6. ✅ РЕШЁН — Крэш при импорте config.py без сети

> **Исправлено (2026-08-24):** UDP-connect к `8.8.8.8:80` на уровне модуля удалён
> из `config.py` полностью. Логика определения IP перенесена в
> `src/internal_modules/local_ip.py` (`LocalIPResolver._udp_source`) — с try/except,
> закрытием сокета и TTL-кэшем; офлайн-машина больше не роняет import.

Было: `src/internal_modules/config.py:14-16`: UDP-connect к `8.8.8.8:80` выполнялся
**на уровне модуля** без try/except → офлайн-машина/строгий фаервол = падение всего
процесса на import. Сокет вдобавок не был закрыт.

### B7. ✅ РЕШЁН — Двойная загрузка сервисов в dev-режиме

> **Исправлено (2026-08-24):** `main.py` реструктурирован — `frozen_loader.scan()`
> перенесён внутрь `try`-блока с обращением к `sys._MEIPASS` (`main.py:77-85`).
> В dev-режиме исключение возникает ещё до scan(), поэтому каталог `./services`
> загружается единожды. Дубликаты инстансов и второй Streamlit на 8501 исчезли.

Было: `main.py:42-47`: в dev `sys._MEIPASS` отсутствовал → `SERVICES_DIR = './services'` →
второй `frozen_loader.scan()` (`main.py:96-101`) грузил **тот же каталог повторно**:
дубликаты инстансов в `ctx._modules` (у certstool стартовали **два** `_cert_sync_loop`,
webpanel пытался поднять второй Streamlit на порту 8501).

### B8. Broadcast'ы не доходят до client-side соседей

`_gossip_loop` / `_announce_loop` (`network.py:224-231, 243-250`) и `_cert_sync_loop`
(`certstool/service.py:81-88`) берут `neighbor_table.connected()`, но отправляют только
через `nodes_manager.get(...)` — **server-side**. Узел, к которому мы подключились
исходящим коннектором (он есть только в `_client_ws`), не получает gossip / announce /
cert_sync вообще. То же в `spawner.py:36` и `compute_full/service.py:40` — client-side
таргеты считаются «not found», хотя `Router.get_transport_to` умеет в оба направления.

### B9. Подсказки-кнопки в system/web_ui пишут в session_state виджетов после их создания

`system/web_ui.py:292-296`: кнопки «подстановки» ниже по коду, чем виджеты
`connect_host/port/node_id` (`:271-280`) — присвоение `st.session_state.connect_host=...`
после инстанцирования виджета в свежих Streamlit бросает `StreamlitAPIException`.

---

## 2. Гонки и снижение надёжности (средний приоритет)

| # | Место | Суть |
|---|-------|------|
| R1 | `node_connector.py` | После удаления лексикографического правила возможен **mutual-dial**: A→B и B→A одновременно → двойные соединения (server-side + client-side) без дедупликации. Старое правило это исключало |
| R2 | `router.py:611-614` | `Router.stream()` регистрирует pipe **после** READY — ранние CHUNK дропаются в `stream_registry.feed` (малое окно) |
| R3 | `router.py:65` | `_ws_pending` без TTL — утечка записей по неотвеченным запросам WS-клиентов (чистка только на disconnect) |
| R4 | `sessions.py:36-44` | `resolve()` не удаляет `_meta[label]` → вечный рост dict (утечка на каждый RPC); `cancel()` — удаляет. Несимметрично |
| R5 | `neighbor_table.py:167-168` | `merge_gossip`: `if node_id in self._table: continue` — `via`/статусы **никогда** не обновляются из свежего gossip: устаревший маршрут через умерший узел не чинится, UNREACHABLE не реанимируется в KNOWN; записи не удаляются никогда |
| R6 | ✅ `node_connector.py:122` | **РЕШЁН (2026-08-24):** HELLO теперь шлёт `local_ip()` вместо `cfg.network.host`; то же в HELLO_ACK (`network.py:158`) — коммит 5fc1062. Реальный хост в таблице соседей и UI. Было: advertised `host = cfg.network.host` (обычно `0.0.0.0`) → мусорный хост |
| R7 | `node_connector.py:185-189` | DEAD_TIMEOUT помечает unreachable, но WS не закрывается — полумёртвый сокет; reconnect пойдёт параллельно со старым |
| R8 | `node_connector.py:85` | HELLO_REJECT → бесконечный retry каждые ~15с при перманентном отказе |
| R9 | `rpc_client.py:139-141` | После исчерпания reconnect старый NodeRPC не `close()` — утекают поток+loop+сокет; Streamlit создаст новый поверх |
| R10 | `memory.py:203-208` | Классический lost-wakeup: `_resume.clear()` между проверкой и ожиданием — теоретический стоп (лечится `asyncio.Condition`/очередью) |
| R11 | `streamlit_app.py:34` | `host='127.0.0.1'` захардкожен, `P2P_WS_HOST` из env игнорируется |

---

## 3. Мёртвый код

### Целые конструкции

- `ConnectionManager` (`network.py:26-44`) — сам класс помечен DEAD, но
  `conn_manager.connect/disconnect` всё равно вызываются впустую;
- `_PathAwareTransport` + `_make_transport_back` (`router.py:506-513, 644-656`) —
  результат `_make_transport_back` передаётся в `_on_request`, который параметр
  `transport` **игнорирует** (ответ уходит через `_send_pack`). Фактически мёртв
  (glm.md описывает его как живой — устарело);
- `exceptions.NodeNotFound`, `exceptions.NoRouteToHost` (`exceptions.py:7,17`) —
  дубликаты локальных классов `router.py:27,31`;
- `layers/`, `methods/` — только `__pycache__` от удалённого legacy;
  `services/services_metadata.json` — пустой, ссылок ноль.

### Отдельные методы (callers отсутствуют)

- `NeighborTable`: `mark_connected`, `has`, `remove` (`neighbor_table.py:104,129,118`);
- `SessionTable`: `register_stream`, `close_stream`, `has` (`sessions.py:28,46,33`) —
  вытеснены StreamRegistry;
- `MemoryModule`: `pipe_from_stream`, `feed_chunk`, `close_stream`, `create_pipes`
  (`memory.py:293-310,267`) — Router ходит напрямую в StreamRegistry;
- `ServiceManager`: `remove_service`, `remove_method`, `register_generator`
  (`manager.py:28,42,49`);
- `CertsIndex`: `get_all`, `get_by_subject_cn` (`certs_index.py:141,145`); механизм
  `sync_version` фактически не работает (см. D3);
- `ConfigManager`: `_deep_merge`, `_save`, `get_local`, `set_local`
  (`config.py:128,189,214,219`) — остались от двухфайловой эпохи;
- `loader.stop_watch` (`loader.py:45`) — не вызывается: watchdog не останавливается при
  shutdown;
- certstool: `deploy_certificate` (`service.py:276`), `export_certificates_by_subject`
  (`service.py:423`) — RPC без единого вызывающего (UI их не дёргает);
- импорты: `asyncio` в `rpc.py:3`, `Any` в `certstool/service.py:12`, `psutil` в
  `webpanel/service.py:11` (импорт без использования), троекратный локальный
  `import secrets as _s` при уже импортированном `secrets`;
- `NeighborInfo.uri` — уже честно закомментирован как unused.

### Мёртвые зависимости

В `requirements.txt`, но не импортируются нигде: `lz4`, `aiohttp`, `requests`, `httpx`,
`PyJWT`, `cachetools`, `python-dotenv`, `msgpack` (+ stale `--hidden-import msgpack` в
`compile.py:26`). Раздувает билд.

### Неверные подсказки UI

`_get_arg_hint` (`system/web_ui.py:228-236`) описывает несуществующие параметры
`spawner.spawn` (`dst/service/method` вместо `generator_service/generator/...`) и
`install_from_node` (`node_id` вместо `source_node`).

---

## 4. Отклонения от архитектуры / несоответствия

- **D1. Протокол называется MsgPack, а является JSON.** Везде
  `model_dump_json`/`receive_json` (`transport.py:22-26`, `network.py:91`,
  `node_connector.py:91`). Название класса `MsgPack` вводит в заблуждение; msgpack даже
  не в зависимостях.
- **D2. `transport.py:22`** — `model_dump_json()` вычисляется всегда, но для FastAPI-пути
  тут же выбрасывается и сериализация делается заново через `send_json(model_dump())`.
  Двойная работа на каждый пакет.
- **D3. CERT_SYNC `sync_version` расходится**: `certstool` шлёт счётчик
  (`service.py:70,757`), `network.py:283` — хардкод `0`; merge обновляет метаданные
  только при строгом `>` (`certs_index.py:79`) → push-обновления метаданных фактически
  не работают, работает только refresh `last_updated`/`available_on`.
- **D4. Дублирование `touch()`**: `router.handle:111` и приёмные циклы
  (`network.py:166`, `node_connector.py:95`) — тройное обновление `last_ts` на пакет.
- **D5. Hot-reload ломает lifecycle**: `loader._register_from_module:103` добавляет новый
  instance в `ctx._modules`, но `start()` нового инстанса **не вызывается** (startup уже
  прошёл), старый не `stop()` — сервисы с логикой в `start()` после правки работают
  иначе, чем при старте.
- **D6. `executor.execute:29`**: sync @rpc выполняются инлайн в event loop — тяжёлый
  синхронный метод заблокирует всю ноду (сейчас все «тяжёлые» в certstool асинхронные
  через `create_subprocess_shell` — но контракт нигде не зафиксирован).
- **D7. `executor.open_stream:68`**: `buff_len=10` захардкожен; `memory.default_buff` из
  конфига игнорируется (как и в `router.py:611`).
- **D8. `compile.py:101`**: путь `f"./services/{p}/web_ui.py"` даёт
  `./services/services/<name>/web_ui.py` (p уже содержит `services/`) → exclude никогда
  не срабатывает → web_ui модули попадают в Node-сборку (лишний вес + ошибки импорта в
  лог при старте Node).
- **D9. Контракт ошибок**: часть сервисов возвращает `{'error': ...}` в data
  (`spawner.py:29`, `compute_full/service.py:42`), часть — ERROR-пакетом. Два механизма
  ошибок без соглашения.
- **D10. `setup_logging.py:21-26`** мутирует `record.msg/levelname/name`, вшивая
  ANSI-коды — при добавлении второго handler'а (файл) получим двойное окрашивание и
  мусор в логах.
- **D11. `_get_arg_hint`/`KNOWN_METHODS`** — ручной реестр, дрейфующий от кода — источник
  «лживой» документации.

---

## 5. Возможные ускорения

Без смены основного замысла.

### Сериализация (самое значимое)

Заменить JSON на **настоящий msgpack** (бинарные фреймы WS): меньше CPU на
`model_dump_json`/парсинг в 2–5 раз, в 1.5–2 раза меньше трафика — критично для
стриминга чанков. Это даже *возврат* к заявленной архитектуре (имя протокола).
Требует замены `send_json/receive_json` → `send_bytes/receive` + `msgpack.packb/unpackb`
на обеих сторонах и в webpanel/debug_client.

### Стриминг

- `Dispatcher._produce` (`memory.py:182,188`): `run_coroutine_threadsafe(...).result()`
  **на каждый элемент** — кросс-потоковый переключатель на item. Замена: потоковый
  `queue.Queue` + один async-мост (или `janus`), либо батчирование. Даёт порядок
  ускорения генераторного тракта при высоких частотах чанков.
- ACK-шторм: консьюмер шлёт ACK на **каждый** чанк (`router.py:636`) — по mesh-пакету на
  чанк в обе стороны. Достаточно ACK раз на `low_watermark`/батч (как и задумано
  `buff_size`-батчами в `_pump`).
- `_MeshStreamIterator` не обрабатывает ранний выход (`break`): pipe остаётся в
  StreamRegistry, генератор виснет до ACK-таймаута. Нужен `aclose()` → отмена/EOF-сигнал.

### Сеть

- `Router.get_transport_to` создаёт новый `WebSocketTransport` на **каждую отправку**
  (`router.py:98,101,415,429`…) — кэшировать транспорт на node_id (инвалидация на
  disconnect/reconnect).
- `_gossip_loop`/`_announce_loop` шлют соседям **последовательно** (`await` в цикле,
  `network.py:229,248`) — медленный пир задерживает рассылку всем.
  `asyncio.gather(..., return_exceptions=True)`.
- `WebSocketTransport.send` — убрать двойную сериализацию (см. D2).
- `certstool._cert_sync_loop`: `list_certificates` (spawn `certmgr.exe`) каждые 60с на
  узел — можно кэшировать digest и инвалировать по событию установки/удаления (сами
  методы уже знают точки изменения).

### Прочее

- `Pipe.get` дёргает refill-callback на каждый get ниже watermark (`memory.py:31`) — спам
  `_resume.set()`; достаточно одноразового сигнала «появился слот».
- `_already_connected()` (`node_connector.py:63-64`) — двойной lookup таблицы.
- `ServiceLoader` на каждое изменение файла перезагружает **весь** файл и создаёт новый
  instance — ок для dev, но watchdog триггерится и на `views/`, и дважды
  (created+modified) — фильтр по `service.py` и debounce.

---

## 6. Мелочи

- `loader.py:104`: лог `Registered {service_name}.{method_name}` печатает только
  **последний** метод (утечка переменной цикла).
- ✅ `config.py:104`: **РЕШЁН (2026-08-24)** — `_ensure_config` переписан на
  `model_dump(mode='json')` + `yaml.dump` (config.py:74-92): no-op `.format(hostname=...)`
  исчез, дефолты унифицированы (шаблон генерируется из самой модели `Config`).
  Было: `.format(hostname=...)` по строке без плейсхолдеров — no-op;
  `NetworkConfig.host` дефолт `_HOSTNAME`, а шаблон писал `0.0.0.0` — рассинхрон дефолтов.
- `netinfo.nodes` (`service.py:26-29`) возвращает `{id: {'node_id': id}}` — бесполезная
  структура.
- `compute_full/service.py:111`: проверка `ctx.get('eof')` — флаг никем не выставляется,
  всегда False (мёртвое условие).
- `spawner.spawn` выбирает «первые N узлов» без учёта нагрузки; `buff` по умолчанию 3 при
  `default_buff=10` в конфиге.
- `main.py:49-50`: `os.makedirs(SERVICES_DIR)` создаст `./services` в dev, если его нет —
  раньше маскировал B7 (теперь B7 ✅ решён, строка безвредна).
- `debug_client.py` и `docs/README.md` не проверены построчно (TUI-клиент и docs), но по
  grep проблем в протокольной части не видно.

---

## 7. Итог: топ-5 по важности

1. **B1** — разворот path в ответах: multi-hop RPC/стримы не работают на 3+ узлах
   (сейчас маскируется малыми топологиями).
2. **B2** — ACK-гонка PipeTransport: стрим умирает после первого батча при быстром
   консьюмере.
3. **B3** — исключения сервисов роняют WS-соединение и оставляют «призраков» в
   nodes_manager.
4. ~~B7~~ (✅ решён) + **B8** — client-side соседи без gossip/announce/cert_sync.
5. **Оптимизация**: msgpack вместо JSON + батчевые ACK + кэш транспортов — самый большой
   выигрыш без изменения замысла.
