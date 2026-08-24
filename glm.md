# P2P_Core — База знаний для AI-ассистента

> Быстрый справочник по архитектуре, конвенциям и ключевым паттернам проекта.
> Обновлено: 2026-08-23

---

## 1. Суть проекта

P2P mesh-сеть на WebSocket + MsgPack. Узлы соединяются, формируют топологию через gossip, маршрутизируют RPC и стримы через промежуточные хопы. Сервисы загружаются динамически из `services/`. Веб-панель на Streamlit подключается к узлу как WS-клиент. Проект собирается в самодостаточные exe (PyInstaller onefile): `WebUI_P2P_Core.exe` (с панелью) и `Node_P2P_Core.exe` (без UI), подписываются код-сайнингом через `sign/`.

## 2. Стек

| Слой | Технология |
|------|-----------|
| Transport | WebSocket (FastAPI server + websockets client) |
| Protocol | MsgPack (Pydantic-модель), сериализация JSON |
| RPC | Встроенный: `@rpc` декоратор, `LocalExecutor`, `Router` |
| Streaming | Mesh: StreamRoute cache, PipeTransport через Router, ACK через backward_path |
| Web UI | Streamlit subprocess на порту 8501, подключается как WS-клиент |
| Config | YAML (pydantic модели, один файл `config.yaml`) |
| Hot-reload | watchdog мониторинг `services/` (только dev-режим) |
| Build | PyInstaller onefile (`compile.py`) + подпись osslsigncode (`sign/signer.py`) |

## 3. Точка входа

`main.py`:
1. **Streamlit-перехват**: если в argv есть `-m streamlit` — аргументы вырезаются и вызывается `sys.exit(stcli.main())`. Нужно для запуска панели из PyInstaller-бинарника (subprocess вызывает `sys.executable -m streamlit run ...`, где `sys.executable` — сам exe).
2. `SERVICES_DIR`: `sys._MEIPASS/services` (frozen) либо `./services`.
3. `load_config(Path('config.yaml'))` → `cfg_manager.cfg` → `setup_logging(cfg.logging)` → `AppContext(cfg)`; `ctx.config_manager = cfg_manager`.
4. Регистрация модулей в порядке startup: `memory`, `network`, `spawn` (Spawner не в `services/`, регистрируется вручную + `register_service`/`register_method('spawn'|'list_generators')`). Обратный порядок для `stop()`.
5. `ctx.network.app.state.ctx = ctx` — проброс ctx в роуты FastAPI.
6. Два ServiceLoader'а: локальный `./services` (`scan()` + `watch()` — hot reload) и frozen `SERVICES_DIR` (`scan()` без watch).
7. Для каждого пира из `cfg.local.peers` — `ctx.register(NodeConnector(...))`.
8. `app_lifespan(ctx)` → `asyncio.Event().wait()`.

ServiceLoader.scan() вызывает `ctx.register(instance)` — ручная регистрация сервисов из `services/` не нужна.

## 4. Ключевые классы

### AppContext (`src/internal_modules/context.py`)
Центральный объект. Поля: `config`, `config_manager` (ConfigManager, присваивается в main.py), `NODE`, `peers`, `_modules`, `services` (ServiceManager), `network` (NetworkModule), `memory` (MemoryModule), `spawn` (Spawner), `certs_index` (CertsIndex). Методы: `register(module)` (возвращает module, порядок вызовов = порядок start), `startup()`, `shutdown()` (обратный порядок).

### ModuleGeneric (`src/internal_modules/base.py`)
Базовый класс всех модулей. Поля: `name`, `ctx` (=AppContext), `log`. Методы: `async start()`, `async stop()`.

### MsgPack + PackType (`src/networking/protocol.py`)
Единый формат пакета. PackType — enum: `HELLO`, `HELLO_ACK`, `HELLO_REJECT`, `REQUEST`, `RESPONSE`, `FORWARDED`, `STREAM_OPEN/READY/CHUNK/ACK/EOF`, `ERROR`, `PING/PONG`, `GOSSIP`, `ANNOUNCE`, `CERT_SYNC`.
MsgPack: `type`, `source`, `dst`, `service`, `method`, `data`, `label` (UUID), `error`, `path: list[str]`, `ttl: int=16`.

### Router (`src/networking/router.py`)
Центральный маршрутизатор. `handle(pack, transport)` — диспетчер по PackType.
- `_on_request` — локальный RPC через `LocalExecutor`
- `_on_remote_request` — сохраняет WS-transport, форвардит через mesh
- `_forward` — прямой WS / через via из NeighborTable / NoRouteToHost; перед добавлением self.NODE в `pack.path` — проверка на дубликат (`if not pack.path or pack.path[-1] != NODE`)
- `_route_back` — обратная маршрутизация по `pack.path`
- `call(dst, service, method, data, timeout)` — публичный API: локальный shortcut или mesh-вызов
- `stream(dst, service, method, data, timeout)` — публичный API: открыть mesh-стрим, вернуть `_MeshStreamIterator`
- `send_stream_ack(label, buff)` — отправить ACK генератору через mesh по cached backward_path
- `cleanup_ws_pending(websocket)` — удалить все `_ws_pending` записи, ссылающиеся на закрывшийся WS (вызывается NetworkModule при disconnect WS-клиента)
- `_ws_pending: dict[str, WebSocketTransport]` — для ответов WS-клиентам (webpanel)
- `_client_ws: dict[str, Any]` — client-side WS маппинг (от NodeConnector)
- `_stream_routes: dict[str, StreamRoute]` — кэш маршрутов стримов (TTL=300с)

#### StreamRoute (dataclass)
Кэшированный маршрут стрима: `label`, `source` (генератор), `dst` (consumer), `forward_path` (source→dst), `backward_path` (dst→source), `established_at`. Свойство `expired` — TTL=300с.

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
FastAPI + uvicorn. WS endpoint `/ws/{node_id}`. HELLO-handshake → NeighborTable.register_connected → HELLO_ACK.
- **Reconnect при дубликате node_id**: новое WS сначала регистрируется в `nodes_manager` (нет окна, когда узел отсутствует и ответы теряются), затем старое закрывается.
- **Disconnect**: удаление из nodes_manager/mark_unreachable — только если отвалившееся WS является текущим активным (не заменённое при reconnect); в любом случае `router.cleanup_ws_pending(websocket)`.
- Периодические: gossip (30с), announce (60с). On-connect CERT_SYNC если у узла есть `certstool`.
- `call(dst, ...)` / `stream(dst, ...)` — thin wrappers вокруг Router.
- `connect_to(node_id, target_uri)` — динамическое исходящее подключение (создаёт и стартует NodeConnector, регистрирует его в ctx). Используется сервисом `system`.

`ConnectionManager` — DEAD CODE (broadcast() не используется, рассылка через neighbor_table + Router).

### NeighborTable (`src/networking/neighbor_table.py`)
Статусы: `CONNECTED` (прямое WS), `KNOWN` (через gossip), `UNREACHABLE`. Хранит `via` (next-hop). `merge_gossip()` — слияние таблиц от других узлов. `find_by_service()` — поиск узлов с нужным сервисом.

### NodeConnector (`src/networking/node_connector.py`)
Исходящее подключение. **Лексикографическое правило удалено**: коннектор пропускает старт только если `_already_connected()` — пир уже CONNECTED по NeighborTable (защита от взаимоподключения теперь через статус, а не сравнение node_id). HELLO-handshake, receive-loop → Router, keepalive ping. При connect — `router.register_client_ws()`, при disconnect — `router.unregister_client_ws()`. OSError winerror 1225 (peer reset) в reconnect-цикле логируется как pass.

### CertsIndex (`src/internal_modules/certs_index.py`)
Индекс сертификатов сети: `thumbprint → CertEntry`. `CertEntry`: subject_cn, valid_to, available_on[], installed_locally, stale (TTL=180с). `last_updated` = `field(default_factory=time.monotonic)`. Методы: `merge_cert_sync()`, `update_local()` (только для `installed_locally=True`), `get_network_available()`, `get_digest_for_sync()`.

### ConfigManager (`src/internal_modules/config.py`) — см. раздел 9

### ServiceManager (`services/manager.py`)
Реестр: `services: dict[str, Any]`, методы в `services[name]` dict. Авторегистрация `@generator` при `register_service`. Генераторы с префиксом `__gen__`.

### ServiceLoader (`services/loader.py`)
Сканирует `services/`: директории без `_`-префикса, импортирует .py, находит подклассы `ModuleGeneric`, регистрирует `@rpc` методы. Вызывает `ctx.register(instance)` для lifecycle management. Hot-reload через watchdog: при изменении — reimport + `cancel_by_service`. В frozen-режиме второй loader сканирует `sys._MEIPASS/services` без watchdog.

### LocalExecutor (`src/internal_modules/executor.py`)
`execute(pack)` — резолвит `service.method` из ServiceManager, вызывает, возвращает RESPONSE MsgPack. `open_stream(pack)` — регистрирует inbound-стрим в StreamRegistry, передаёт `label` в consumer ctx для ACK через Router.

### SessionTable (`src/networking/sessions.py`)
`resolve(label, data)` — `pop(label, None)` из `_table`, ставит результат в Future. `cancel(label)` — drain Queue + sentinel. `register_single()` — создаёт Future.

### Spawner (`src/internal_modules/spawner.py`)
RPC-сервис `spawner`: `spawn`, `list_generators` (регистрируются вручную в main.py).

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

## 6. Веб-панель

### Архитектура
```
WebPanel (service.py) — запускает subprocess.Popen (sync):
  [sys.executable, -m, streamlit, run, <streamlit_app.py>, --server.port 8501, headless, no filewatcher]
  Frozen: путь к app берётся из sys._MEIPASS/services/webpanel/streamlit_app.py;
  запуск `-m streamlit` возможен благодаря argv-перехвату в main.py (PyInstaller onefile)
  Env: P2P_NODE_ID, P2P_WS_PORT, P2P_WS_HOST, P2P_PANEL_PORT, P2P_PROJECT_ROOT
  stop(): terminate → ожидание до ~2с → kill
  └── streamlit_app.py (ранее _streamlit_app.py) — entry point Streamlit
       ├── rpc_client.py — NodeRPC (синхронный WS RPC в отдельном потоке)
       ├── RPCProxy — подставляет dst из session_state['selected_node']
       ├── Sidebar: selectbox выбора узла — [local] + connected + known;
       │   при смене узла session_state чистится (кроме rpc/current_page/select-ключей)
       ├── views/home.py — метрики + таблица соседей + сервисы
       └── views/service_view.py — динамический import services/<name>/web_ui.py → render(rpc)
```

### Контракт web_ui.py
Каждый сервис с UI: `services/<name>/web_ui.py` с функцией `render(rpc)`, где `rpc` — `RPCProxy`. Для совместимости с Node-сборкой (без streamlit): `try: import streamlit as st / except ImportError: st = None` + guard в `render()`.

### Реестр сервисов (иконки и группы)
Определён в `services/webpanel/service_meta.py` (единственный источник):
```python
SERVICE_META = {
    'certstool':    ('🔐', 'Сертификаты',  'Управление КриптоПро сертификатами'),
    'netinfo':      ('🌐', 'Сеть',         'Состояние сети и маршрутизация'),
    'system':       ('⚙️', 'Система',      'Управление узлами и подключениями'),
    'compute_full': ('⚡', 'Вычисления',   'Генератор + консьюмер'),
    'generator':    ('📤', 'Вычисления',   'Генератор стримов'),
    'test':         ('🧪', 'Диагностика',  'Тестовый echo-сервис'),
}
GROUP_ORDER = ['Система', 'Сеть', 'Сертификаты', 'Вычисления', 'Диагностика']
```
Импортируется в `streamlit_app.py` и `service_view.py` из `service_meta.py`.

### NodeRPC (`services/webpanel/rpc_client.py`)
Синхронная обёртка для Streamlit. Фоновый asyncio loop в отдельном потоке. HELLO-handshake, receive-loop. `call()` блокируется через `threading.Event`. Свойства: `connected`, `node` (= target_node), `reconnecting`.

**Reconnect:** при потере WS — `_reconnecting=True`, `connected` возвращает True во время реконнекта (предотвращает Streamlit от создания нового NodeRPC). `_recv_task` отменяется при реконнекте.

### Sidebar навигация
Кнопки вместо radio, группировка по GROUP_ORDER + «Другие» для сервисов без meta, `st.session_state.current_page` для роутинга. Активная кнопка `type="primary"`. Для удалённого узла список UI-сервисов берётся через `netinfo.services` (dst=selected_node).

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

### PipeTransport — сигнатура
```python
PipeTransport(pipe, router, pack_template, timeout=30)
# отправка через Router вместо прямого WS
```

### attach_transport — сигнатура
```python
ctx.memory.attach_transport(pipe, pack_template, router)
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

## 8a. Сервис system (`services/system/`)

Управление подключениями и диагностика узла (`service.py`, класс `System`):
- `connect_to_node {host, port, node_id}` — динамический outbound через `NetworkModule.connect_to()`; отклоняется, если узел уже CONNECTED; после подтверждения подключения (poll NeighborTable до 5с) пир сохраняется в `config.yaml` через `config_manager.add_peer()` — автопереподключение после рестарта
- `list_connectors` — активные `Connector_*` модули из `ctx._modules`
- `node_detail` — own / connected / known / ws_connections / services
- `config_peers` — пиры из config.yaml (`config_manager.list_peers()`)

`web_ui.py`: форма подключения к узлу + таблицы соединений; справочник `KNOWN_METHODS` (все RPC-методы всех сервисов); guard `st = None` для Node-сборки.

## 9. Конфигурация

**Один файл**: `config.yaml` (двухфайловая система config.local.yaml удалена; файл остался только в .gitignore — кодом не читается). Автосоздание с дефолтом при отсутствии. Pydantic-модели: `Config` → `NetworkConfig`, `MemoryConfig`, `LoggingConfig`, `ServicesConfig`, `LocalConfig` (alias, secret, peers: list[PeerConfig]).

`node` по умолчанию — **локальный IP**, автоопределение через UDP-socket к 8.8.8.8:80 (не hostname, не 'Node0').

### ConfigManager (`src/internal_modules/config.py`)
```python
mgr = load_config(Path('config.yaml'))   # → ConfigManager
mgr.cfg                                  # текущий Config (pydantic)
mgr.reload()                             # перечитать с диска
mgr.update(network__port=9001, logging__level='INFO')  # вложенность через '__', автосохранение
mgr.get_local('alias') / mgr.set_local('alias', 'x')   # секция local
mgr.add_peer(node_id, uri)               # дедупликация, автосохранение
mgr.remove_peer(node_id)
mgr.list_peers()                         # → list[PeerConfig]
```
Любая модификация автосохраняется на диск и пересобирает `mgr.cfg`.

```yaml
node: 192.168.1.10        # дефолт = локальный IP
network:
  host: 0.0.0.0
  port: 9000
memory:
  default_buff: 10
logging:
  level: INFO
  uvicorn_level: WARNING
  websockets_level: WARNING
services:
  path: services/
local:
  alias: HOSTNAME         # дефолт = socket.gethostname()
  secret: null
  peers: []
  # - node_id: Node1
  #   uri: ws://192.168.1.10:9000/ws/Node1
```

## 10. Конвенции

### Создание нового сервиса
1. `services/<name>/` — директория
2. `services/<name>/__init__.py` — пустой
3. `services/<name>/service.py` — класс `<Name>(ModuleGeneric)` с `@rpc` методами
4. ServiceLoader автоматически найдёт, зарегистрирует и вызовет `ctx.register(instance)`
5. Если сервис должен попадать в frozen-сборку — он уже соберётся автоматически (`compile.py` собирает все `services/*/`, кроме webpanel для Node-версии)

### Для добавления UI к сервису
6. `services/<name>/web_ui.py` — функция `render(rpc)` (+ guard `st = None` при отсутствии streamlit)
7. Добавить запись в `SERVICE_META` (в `services/webpanel/service_meta.py`)

### Файловые конвенции
- Файлы/директории с `_`-префиксом игнорируются ServiceLoader и discover_ui_services
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

## 12. Сборка и подпись

### compile.py
Две сборки PyInstaller `--onefile` из `main.py`, выход в `dist/`, после каждой — подпись:
1. **WebUI_P2P_Core.exe** (`ui=True`): `--collect-all services` + streamlit (`--collect-binaries/datas`, `--recursive-copy-metadata`), hidden-imports: `streamlit.web.cli`, `streamlit.web.bootstrap`, `streamlit.runtime.scriptrunner.magic_funcs`.
2. **Node_P2P_Core.exe** (`ui=False`): `--exclude-module services.webpanel` + streamlit целиком; остальные `services/*` собираются по одному через `--collect-all`.
Общие hidden-imports — `BASE_HIDDEN_IMPORTS` (src.* networking/context/router и т.д.). Иконка `src/icon.ico`. `signed_<name>.exe` переименовывается обратно в `<name>.exe`.

### sign/signer.py
- Требует локально в `sign/`: `ca_cert.pem` + `ca_key.pem` (см. `sign/readme`, в git не входят).
- `cert_generate_from_ca()` — сертификат код-сайнинга (CN=Trusted_Software, O=Dev_Python, EKU=CodeSigning, 365 дней) из CA → `temp_dev_bundle.pfx` (пароль `00000000`), генерируется лениво при первом `sign_exe`.
- `sign_exe(file, out_dir)` — `osslsigncode.exe sign -pkcs12 temp_dev_bundle.pfx -h sha256` (обязательно `cwd=sign/`), результат `out_dir/signed_<filename>`.

### Frozen runtime
В собранном exe: `SERVICES_DIR = sys._MEIPASS/services`; второй ServiceLoader сканирует его без watchdog (hot-reload только в dev). WebPanel ищет `streamlit_app.py` в `_MEIPASS/services/webpanel/`.

### Инструменты
- `debug_client.py` — TUI-клиент (Windows console) для ручного тестирования узла: прямой WS, HELLO-handshake, RPC-консоль, просмотр соседей, стримы.

## 13. Известные проблемы / TODO

- `_PathAwareTransport` — composition (не наследует WebSocketTransport), используется для path-aware ответов на FORWARDED-пакеты
- Удалённые сервисы в webpanel: web_ui.py проверяется локально, при отсутствии — fallback-сообщение
- CERT_SYNC on-connect — проверка services в HELLO предотвращает timeout
- StreamRoute cache TTL=300с — при изменении топологии маршруты могут устареть до истечения TTL
- Loop detection + TTL=0 в `_on_forwarded` — пакет дропается (return), не форвардится дальше
- `layers/` и `methods/` содержат только устаревший `__pycache__` (legacy-код удалён) — можно почистить
- `config.local.yaml` числится в .gitignore, но кодом больше не используется
- `services/services_metadata.json` создан, но пока нигде не читается