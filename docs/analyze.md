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

### B1. ✅ РЕШЁН — Обратная маршрутизация ломается на маршрутах из ≥3 узлов

> **Исправлено (2026-08-25):** все ответные пакеты строятся без разворота —
> `path = list(pack.path)` (RESPONSE/ERROR/STREAM_CHUNK/EOF в `_on_request`,
> PONG, `_send_back`, `_PathAwareTransport.send`). Зафиксирована конвенция
> `_route_back` в docstring: path = [origin,…,текущий узел], каждый хоп
> выталкивает себя с хвоста. `_cache_stream_route_on_open` приведён к той же
> конвенции (forward_path = генератор→consumer, backward_path = consumer→генератор,
> без дублей узла). Бонус: ERROR, вернувшийся по chain, резолвится на origin
> как Exception (`Router._resolve_payload`) — раньше текст ошибки терялся.
> Регрессионный тест: `tests/test_multihop_routing.py` (цепочка A←B←C,
> RPC + ERROR через промежуточный хоп).

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

### B2. ✅ РЕШЁН — Гонка ACK в PipeTransport: future регистрируется после отправки батча

> **Исправлено (2026-08-25):** `_pump` (`memory.py`) регистрирует ack-future
> **до** отправки батча и перерегистрирует сразу после получения ACK — быстрый
> консьюмер больше не может ответить в незарегистрированную сессию. Дополнительно:
> перед EOF незакрытая ack-сессия отменяется через `sessions.cancel()` (раньше
> после timeout запись навсегда оставалась в SessionTable — утечка). Регрессионный
> тест: `tests/test_b2_ack_race.py` (fake-router отвечает ACK на первый чанк
> батча; старый код детерминированно умирал после 1-го батча, новый проходит).

`src/internal_modules/memory.py:103-128`: `_pump` отправляет все `buff_size` чанков и
только потом (`:121`) делает `register_single(ack_label)`. Консьюмер
(`_MeshStreamIterator.__anext__`, `router.py:636`) шлёт ACK после **каждого** чанка.
Быстрый консьюмер отвечает раньше, чем регистрируется future → `sessions.resolve`
(`router.py:161`) не находит сессию и **молча роняет ACK** → `_pump` ждёт полный
`timeout=30с` → стрим умирает после первого батча.

Сейчас спасает только то, что демо-консьюмер медленный
(`services/compute_full/service.py:114` — `sleep(0.1)`).
Лечится регистрацией future **до** отправки батча.

### B3. ✅ РЕШЁН — Необработанное исключение в @rpc убивает WS-соединение целиком

> **Исправлено (2026-08-25):** три слоя защиты.
> 1. `Router._on_request` / `_on_stream_open` ловят **любое** исключение сервиса:
>    лог с traceback + ERROR-пакет caller'у (`type(e).__name__: e`) вместо падения;
> 2. приёмные циклы `network.py` и `node_connector.py` обёрнуты per-packet
>    try/except вокруг `router.handle()` — сбой обработки одного пакета
>    не рвёт соединение и оставляет узел живым;
> 3. ERROR, вернувшийся по chain, резолвит сессию исключением
>    (`Router._resolve_payload`, сделано в рамках B1).
> Тест: `tests/test_multihop_routing.py` — сценарий `boom`: исключение доходит
> до origin как исключение, последующий RPC по той же цепочке проходит.

- `executor.execute` (`executor.py:24-49`) не ловит ничего;
- `_on_request` ловит только `MethodNotFound` (`router.py:295`);
- исключение сервиса летит через `handle()` → в `websocket_endpoint`
  (`network.py:161-182`) **нет общего `except Exception`** → корутина умирает без
  `WebSocketDisconnect`;
- следствия: `nodes_manager` не чистится (запись-«призрак» до keepalive DEAD_TIMEOUT
  90с), `_ws_pending` не чистится; `MsgPack(**data)` с невалидным payload
  (`network.py:163`) убивает соединение так же.

### B4. ✅ РЕШЁН — Ошибка producer неотличима от нормального завершения стрима

> **Исправлено (2026-08-25):** `Pipe.fail(error)` — аварийный конец с сохранением
> причины; `Pipe.__anext__` бросает исходное исключение вместо StopAsyncIteration.
> `Dispatcher` при падении генератора вызывает `pipe.fail(исходное исключение)`
> для всех pipes. `PipeTransport._pump` при упавшем pipe шлёт ERROR-пакет вместо
> «успешного» STREAM_EOF; `Router.handle(ERROR)` для известного стрима вызывает
> `StreamRegistry.fail()` — удалённый консьюмер получает исключение. Комментарий
> «прервать цепочку» теперь соответствует поведению. Тесты:
> `tests/test_b4_producer_error.py` (локальный консьюмер + wire-уровень).

`memory.py:213-217`: при ошибке генератора Dispatcher делает `pipe.close()` **без
sentinel** — но `Pipe.__anext__` (`memory.py:51-57`) бросает `StopAsyncIteration` когда
`closed and empty` → `_pump` выходит из цикла как обычно и шлёт **STREAM_EOF**
(`memory.py:130`). Консьюмер получает «успешное» завершение при оборванных данных.
Комментарий «прервать цепочку» и описание в glm.md не соответствуют реальному поведению.

### B5. ✅ РЕШЁН — RPC-консоль в system/web_ui игнорирует выбранный узел

> **Исправлено:** вызов передаёт `dst=target` (system/web_ui.py:386-390,
> `target = dst_node if dst_node != own else None`). Удалённые вызовы уходят
> по назначению.

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

### B8. ✅ РЕШЁН — Broadcast'ы не доходят до client-side соседей

> **Исправлено (2026-08-25):** `_gossip_loop`/`_announce_loop` (network.py) уже
> были переведены на `Router.get_transport_to()`; добиты остальные места —
> `_cert_sync_loop` certstool, проверка цели в `compute_full.start_stream` и
> `demo.start_stream` теперь тоже работают с client-side соседями через
> `get_transport_to()`. Сервис spawner удалён из проекта — пункт к нему
> неприменим.

`_gossip_loop` / `_announce_loop` (`network.py:224-231, 243-250`) и `_cert_sync_loop`
(`certstool/service.py:81-88`) берут `neighbor_table.connected()`, но отправляют только
через `nodes_manager.get(...)` — **server-side**. Узел, к которому мы подключились
исходящим коннектором (он есть только в `_client_ws`), не получает gossip / announce /
cert_sync вообще. То же в `spawner.py:36` и `compute_full/service.py:40` — client-side
таргеты считаются «not found», хотя `Router.get_transport_to` умеет в оба направления.

### B9. ✅ РЕШЁН — Подсказки-кнопки в system/web_ui пишут в session_state виджетов после их создания

> **Исправлено (2026-08-25):** присвоения `st.session_state.connect_*` перенесены
> в `on_click`-колбэк кнопки подстановки — колбэки выполняются до рендера
> виджетов в следующем ране, StreamlitAPIException невозможен; лишний `st.rerun()`
> убран.

`system/web_ui.py:292-296`: кнопки «подстановки» ниже по коду, чем виджеты
`connect_host/port/node_id` (`:271-280`) — присвоение `st.session_state.connect_host=...`
после инстанцирования виджета в свежих Streamlit бросает `StreamlitAPIException`.

---

## 2. Гонки и снижение надёжности (средний приоритет)

| # | Место | Суть |
|---|-------|------|
| R1 | ✅ `node_connector.py` | **НЕАКТУАЛЬНО (2026-08-25):** лексикографическое правило восстановлено — dial инициирует только лексикографически больший узел (`start()`: `if self.ctx.NODE > self.peer_node_id`), mutual-dial исключён; `_already_connected()` дедуплицирует по обоим каналам |
| R2 | ✅ `router.py` | **РЕШЁН (2026-08-25):** `Router.stream()` регистрирует pipe в StreamRegistry **до** отправки OPEN/ожидания READY (с чисткой при NoRouteToHost/timeout) — ранние CHUNK буферизуются вместо молчаливого дропа |
| R3 | ✅ `router.py` | **РЕШЁН (2026-08-25):** `_ws_pending` хранит `(transport, created_ts)`; `sweep_ws_pending(TTL=180s)` вызывается из `_gossip_loop` каждые 30с — записи по неотвеченным запросам больше не текут |
| R4 | ✅ `sessions.py` | **РЕШЁН (2026-08-25):** `resolve()` для Future-сессий удаляет и `_meta[label]`; queue-сессии сохраняют meta до `cancel()` (осознанно) |
| R5 | ✅ `neighbor_table.py` | **РЕШЁН (2026-08-25):** `merge_gossip` обновляет non-CONNECTED записи из свежего gossip (`via`/host/port/services/last_ts), реанимирует UNREACHABLE→KNOWN; CONNECTED не трогается. TTL-удаление записей не введено — осознанное решение (таблица маленькая) |
| R6 | ✅ `node_connector.py:122` | **РЕШЁН (2026-08-24):** HELLO теперь шлёт `local_ip()` вместо `cfg.network.host`; то же в HELLO_ACK (`network.py:158`) — коммит 5fc1062. Реальный хост в таблице соседей и UI. Было: advertised `host = cfg.network.host` (обычно `0.0.0.0`) → мусорный хост |
| R7 | ✅ `node_connector.py` | **РЕШЁН (2026-08-25):** при DEAD_TIMEOUT вместе с `mark_unreachable` вызывается `_close_local_ws()` — полумёртвый client-side сокет закрывается, connect_loop пересоединяется без дублей |
| R8 | ✅ `node_connector.py` | **РЕШЁН (2026-08-25):** повторные отказы handshake идут с экспоненциальным backoff 5→300с (cap); после успеха backoff сбрасывается |
| R9 | ✅ `rpc_client.py` | **РЕШЁН (2026-08-25):** `NodeRPC.close()` уже существовал — подключён в `streamlit_app.get_rpc()`: заменяемый экземпляр закрывает recv-task, WS, loop и поток |
| R10 | ✅ `memory.py` | **РЕШЁН (2026-08-25):** классический test-and-wait — `_resume.clear()` перенесён **до** проверки свободных pipe'ов; lost-wakeup окно устранено |
| R11 | ✅ `webpanel/streamlit_app.py` | **НЕАКТУАЛЬНО:** `get_rpc()` читает `P2P_WS_HOST`/`P2P_WS_PORT`/`P2P_NODE_ID` из env — захардкоженного host нет |

---

## 3. Мёртвый код

### Целые конструкции

- ✅ `ConnectionManager` — **удалён ранее** (запись устарела: класса и бесполезных
  вызовов `conn_manager.*` в коде больше нет);
- ✅ `_PathAwareTransport` + `_make_transport_back` (`router.py`) — **УДАЛЕНЫ
  (2026-08-25)**, `_on_request` освобождён от игнорируемого параметра `transport`;
- ✅ `exceptions.NodeNotFound`, `exceptions.NoRouteToHost` — **УДАЛЕНЫ (2026-08-25)**
  (дубликаты локальных классов router.py);
- ✅ `layers/`, `methods/` (только `__pycache__` legacy) и пустой
  `services/services_metadata.json` — **УДАЛЕНЫ (2026-08-25)**.

### Отдельные методы (callers отсутствуют)

- ✅ `NeighborTable`: `mark_connected`, `has`, `remove` — **УДАЛЕНЫ (2026-08-25)**
  (`register_known` оставлен как публичный API с поддержкой role);
- ✅ `SessionTable`: `register_stream`, `close_stream`, `has` — **УДАЛЕНЫ (2026-08-25)**
  (вытеснены StreamRegistry);
- ✅ `MemoryModule`: `pipe_from_stream`, `feed_chunk`, `close_stream`, `create_pipes`
  — **УДАЛЕНЫ (2026-08-25)** (Router ходит напрямую в StreamRegistry);
- ✅ `ServiceManager`: `remove_method`, `register_generator` — **УДАЛЕНЫ (2026-08-25)**;
  `remove_service` теперь ЖИВОЙ — используется certstool при отсутствии КриптоПро;
  добавлен `replace_service()` для hot-reload (D5);
- ✅ `CertsIndex`: `get_all`, `get_by_subject_cn` — **УДАЛЕНЫ (2026-08-25)**;
- ✅ `ConfigManager`: `_deep_merge`, `_save`, `get_local`, `set_local` — **УДАЛЕНЫ
  (2026-08-25)**;
- ✅ `loader.stop_watch` — **ПОДКЛЮЧЁН (2026-08-25)**: вызывается в `finally` main.py,
  watchdog останавливается при shutdown;
- certstool RPC без вызывающих: ✅ `export_certificates_by_subject` **УДАЛЁН
  (2026-08-25)** — агрегатор над find_certificates_by_subject + export_*; 
  `deploy_certificate` **ОСТАВЛЕН** — уникальный сценарий (файловая пара PFX+CER,
  автоконтейнер, смена пароля), не покрывается другими методами;
- ✅ импорты: `asyncio` в rpc.py, `Any` в certstool/service.py, неиспользуемый
  `psutil` в webpanel/service.py, локальные `import secrets as _s` ×3 — **УДАЛЕНЫ
  (2026-08-25)**;
- `NeighborInfo.uri` — уже честно закомментирован как unused.

### Мёртвые зависимости

✅ **ОЧИЩЕНО (2026-08-25):** из `requirements.txt` удалены `lz4`, `aiohttp`,
`requests`, `httpx`, `PyJWT`, `cachetools`, `python-dotenv`, `urllib3`,
`pydantic-settings` (не импортируются нигде; stale hidden-import убран и из
compile.py). Запись про `msgpack` устарела — msgpack теперь **основной wire-формат**.
Добавлен отсутствовавший, но используемый `colorama`. Подтверждено живыми:
`psutil` (local_ip.py), `cryptography` (sign/signer.py), `watchdog`, `pyyaml`.

### Неверные подсказки UI

✅ **ИСПРАВЛЕНО (2026-08-25):** `_get_arg_hint` обновлён — `spawner.spawn` описывает
реальные параметры (`generator_service/generator/service/method/workers_count`),
`install_from_node` — `source_node` вместо `node_id`.

---

## 4. Отклонения от архитектуры / несоответствия

- ✅ **D1. УСТАРЕЛО** — протокол теперь **настоящий msgpack** (binary WS frames,
  `encode_pack`/`decode_pack` в protocol.py); название класса MsgPack соответствует.
- ✅ **D2. УСТАРЕЛО** — `transport.py` сериализует один раз (`encode_pack` →
  `send_bytes`/`send`), двойной работы нет.
- ✅ **D3. РЕШЁН (2026-08-25):** единый источник версии — `certs_index.sync_version`
  (инкрементируется при каждом `update_local`); certstool и `_request_cert_sync`
  шлют её вместо счётчика/хардкода `0`; merge по строгому `>` теперь осмыслен.
- ✅ **D4. РЕШЁН (2026-08-25):** touch остался только в `router.handle()`; дубли
  в приёмных циклах network.py/node_connector.py убраны.
- ✅ **D5. РЕШЁН (2026-08-25):** hot-reload выполняет полный swap — `stop()`
  старого инстанса → замена в `ctx._modules` → `replace_service()` + перерегистрация
  методов → `start()` нового. Планирование через `ctx.loop` (run_coroutine_threadsafe
  из потока watchdog) или create_task. Тест: `tests/test_d5_hotreload.py`.
- ✅ **D6. РЕШЁН (2026-08-25):** контракт зафиксирован (glm.md «Контракт выполнения»,
  docs/README.md). Sync @rpc выполняются через `asyncio.to_thread` — loop не блокируется
  (`create_task` НЕ помог бы: task крутится в том же потоке; для CPU-heavy —
  ProcessPoolExecutor вручную, to_thread не обходит GIL).
- ✅ **D7. РЕШЁН (2026-08-25):** `memory.default_buff` из конфига используется в
  `Router.stream()` и прокидывается в `executor.open_stream()`.
- ✅ **D8. РЕШЁН (2026-08-25):** путь исключения web_ui исправлен
  (`services/<name>/web_ui.py`), exclude получает имя модуля `services.<name>.web_ui`.
- ✅ **D9. РЕШЁН (2026-08-25):** соглашение зафиксировано (glm.md + docs/README.md) —
  это разные виды отказов: ERROR-пакет = транспорт/система (нет метода/маршрута,
  исключение метода, упал producer → caller получает исключение); `'error'` в RESPONSE
  data = бизнес-отказ при исправном транспорте (caller проверяет data). Существующее
  разделение кода соответствует конвенции — унификация не требуется.
- ✅ **D10. УСТАРЕЛО** — `ColorFormatter.format` восстанавливает record.* в finally,
  второй handler безопасен.
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

- ✅ `Dispatcher._produce`: **РЕШЁН (2026-08-25)** — поток-продюсер кладёт в
  потокобезопасную `queue.Queue` (блокирующий put с проверкой `_running`), async-сторона
  вычитывает через `run_in_executor(get, timeout=0.25)`. Кросс-поточный
  `run_coroutine_threadsafe(...).result()` на каждый item и `_resume`/lost-wakeup
  механика удалены полностью.
- ✅ ACK-шторм: **РЕШЁН (2026-08-25)** — кумулятивный ACK раз на `buff_len`
  потреблённых чанков (`_MeshStreamIterator`, окно = батчу producer'а); встроенные
  консьюмеры (compute_full, тестовый сервис) переведены на тот же паттерн.
- `_MeshStreamIterator` не обрабатывает ранний выход (`break`): pipe остаётся в
  StreamRegistry, генератор виснет до ACK-таймаута. Нужен `aclose()` → отмена/EOF-сигнал.
  **Отложено.**

### Сеть

- ✅ `Router.get_transport_to`: **РЕШЁН (2026-08-25)** — транспорты кэшируются по
  node_id; инвалидация при register/unregister client-side WS и при смене/удалении
  server-side сокета в `websocket_endpoint`.
- `_gossip_loop`/`_announce_loop` шлют соседям **последовательно** (`await` в цикле,
  `network.py:229,248`) — медленный пир задерживает рассылку всем.
  ✅ **РЕШЁНО (2026-08-25):** `asyncio.gather(..., return_exceptions=True)` — в
  `_gossip_loop`, `_announce_loop` и `_cert_sync_loop` certstool.
- ✅ `WebSocketTransport.send` — **УСТАРЕЛО** (двойной сериализации больше нет, см. D2).
- `certstool._cert_sync_loop`: `list_certificates` (spawn `certmgr.exe`) каждые 60с на
  узел — можно кэшировать digest и инвалировать по событию установки/удаления (сами
  методы уже знают точки изменения). **Отложено.**

### Прочее

- ✅ Refill-callback спам: **РЕШЁН (2026-08-25)** — механизм callback'ов удалён
  полностью; Dispatcher опрашивает свободные pipe'ы с sleep(5мс) только когда ВСЕ полные,
  producer блокируется на thread-safe queue. R10-lost-wakeup исчез вместе с механизмом.
- `_already_connected()` (`node_connector.py:63-64`) — двойной lookup таблицы. **Отложено** (косметика).
- `ServiceLoader` на каждое изменение файла перезагружает **весь** файл и создаёт новый
  instance — ок для dev, но watchdog триггерится и на `views/`, и дважды
  (created+modified) — фильтр по `service.py` и debounce. **Отложено.**

---

## 6. Мелочи

- ✅ `loader.py:104`: **РЕШЁН (2026-08-25)** — лог печатает сервис и число методов,
  утечка переменной цикла исчезла.
- ✅ `config.py:104`: **РЕШЁН (2026-08-24)** — `_ensure_config` переписан на
  `model_dump(mode='json')` + `yaml.dump` (config.py:74-92): no-op `.format(hostname=...)`
  исчез, дефолты унифицированы (шаблон генерируется из самой модели `Config`).
  Было: `.format(hostname=...)` по строке без плейсхолдеров — no-op;
  `NetworkConfig.host` дефолт `_HOSTNAME`, а шаблон писал `0.0.0.0` — рассинхрон дефолтов.
- ✅ `netinfo.nodes`: **РЕШЁН (2026-08-25)** — возвращает host/port/version/services из
  таблицы соседей вместо `{id: {'node_id': id}}`.
- ✅ `compute_full/service.py:111`: **РЕШЁН (2026-08-25)** — мёртвое условие
  `ctx.get('eof')` и его присвоение в executor удалены.
- `spawner.spawn` выбирает «первые N узлов» без учёта нагрузки; `buff` по умолчанию 3 при
  `default_buff=10` в конфиге (**отложено** — изменение API-дефолтов).
- `main.py:49-50`: `os.makedirs(SERVICES_DIR)` создаст `./services` в dev, если его нет —
  раньше маскировал B7 (теперь B7 ✅ решён, строка безвредна).
- `debug_client.py` и `docs/README.md` не проверены построчно (TUI-клиент и docs), но по
  grep проблем в протокольной части не видно.

---

## 7. Итог: топ-5 по важности

1. ~~**B1**~~ (✅ решён 2026-08-25) — разворот path в ответах: multi-hop RPC/стримы не работали на 3+ узлах
2. ~~**B2**~~ (✅ решён 2026-08-25) — ACK-гонка PipeTransport: стрим умирал после первого батча при быстром консьюмере.
3. ~~**B3**~~ (✅ решён 2026-08-25) — исключения сервисов больше не роняют WS-соединение и не оставляют «призраков».
4. ~~B7~~ (✅ решён) + ~~**B8**~~ (✅ решён 2026-08-25) — client-side соседи получают gossip/announce/cert_sync.
5. ~~Оптимизация~~ — msgpack внедрён ✅; **батчевые ACK ✅, кэш транспортов ✅,
   queue-мост в Dispatcher ✅, refill-callback спам ✅** (2026-08-25). Осталось
   отложенным: `aclose()` для раннего выхода консьюмера, кэш digest certstool,
   watchdog debounce, TTL записей соседей, API-дефолты spawner.
