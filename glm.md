# P2P_Core — База знаний для AI-ассистента

> Быстрый справочник по архитектуре, конвенциям и ключевым паттернам проекта.
> Обновлено: 2026-08-24

---

## 1. Суть проекта

P2P mesh-сеть на WebSocket + MsgPack. Узлы соединяются, формируют топологию через gossip, маршрутизируют RPC и стримы через промежуточные хопы. Сервисы загружаются динамически из `services/`. Веб-панель на Streamlit подключается к узлу как WS-клиент.

## 2. Стек

| Слой | Технология |
|------|-----------|
| Transport | WebSocket (FastAPI server + websockets client) |
| Protocol | MsgPack (Pydantic-модель), сериализация JSON |
| RPC | Встроенный: `@rpc` декоратор, `LocalExecutor`, `Router` |
| Streaming | Mesh: StreamRoute cache, PipeTransport через Router, ACK через backward_path |
| Web UI | Streamlit subprocess на порту 8501, подключается как WS-клиент |
| Config | YAML (pydantic-settings модели, двухфайловая система) |
| Hot-reload | watchdog мониторинг `services/` |

## 3. Точка входа

`main.py` → `load_config()` → `AppContext(cfg)` → регистрация модулей → `ServiceLoader.scan()` → `app_lifespan(ctx)` → `asyncio.Event().wait()`

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

### Router (`src/networking/router.py`)
Центральный маршрутизатор. `handle(pack, transport)` — диспетчер по PackType.
- `_on_request` — локальный RPC через `LocalExecutor`
- `_on_remote_request` — сохраняет WS-transport, форвардит через mesh
- `_forward` — прямой WS / через via из NeighborTable / NoRouteToHost
- `_route_back` — обратная маршрутизация по `pack.path`
- `call(dst, service, method, data, timeout)` — публичный API: локальный shortcut или mesh-вызов
- `stream(dst, service, method, data, timeout)` — публичный API: открыть mesh-стрим, вернуть `_MeshStreamIterator`
- `send_stream_ack(label, buff)` — отправить ACK генератору через mesh по cached backward_path
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
FastAPI + uvicorn. WS endpoint `/ws/{node_id}`. HELLO-handshake → NeighborTable.register_connected → HELLO_ACK. При дубликате node_id — reconnect (закрыть старое, принять новое). Периодические: gossip (30с), announce (60с). On-connect CERT_SYNC если у узла есть `certstool`.
- `call(dst, service, method, data, timeout)` — thin wrapper вокруг Router.call()
- `stream(dst, service, method, data, timeout)` — thin wrapper вокруг Router.stream()
- `local_ip()` — локальный IP интерфейса mesh (LocalIPResolver, TTL-кэш)
- `connect_to(node_id, target_uri)` — динамическое исходящее подключение к узлу

HELLO_ACK содержит `host` = `self.local_ip()` — реальный IP интерфейса mesh. HELLO с несовпадающим `dst:name` отклоняется (HELLO_REJECT).

`ConnectionManager` — DEAD CODE (broadcast() не используется, рассылка через neighbor_table + Router).

### LocalIPResolver (`src/internal_modules/local_ip.py`)
Вычисляет локальный IP интерфейса mesh по запросу, кэш на `network.ip_ttl_sec` (по умолчанию 60с). Приоритет источников:
1. Живые WS-подключения: клиентские — sockname транспорта websockets; серверные — поиск установленного TCP-соединения в таблице psutil по паре (наш порт, remote адрес).
2. UDP-трюк к хосту пира из конфига (`connect((host, 80))`, пакеты не ходят) — ОС выбирает тот же интерфейс.
3. Фолбэк psutil: поднятый не-loopback IPv4 без APIPA (169.254.x.x), через который есть outbound route (bind+connect к 8.8.8.8).

Используется для announce/handshake: узлы сообщают друг другу реальные адреса вместо hostname.

### NeighborTable (`src/networking/neighbor_table.py`)
Статусы: `CONNECTED` (прямое WS), `KNOWN` (через gossip), `UNREACHABLE`. Хранит `via` (next-hop). `merge_gossip()` — слияние таблиц от других узлов. `find_by_service()` — поиск узлов с нужным сервисом.

### NodeConnector (`src/networking/node_connector.py`)
Исходящее подключение. Лексикографическое правило: соединяется только если `self.NODE > peer_node_id`. HELLO-handshake, receive-loop → Router, keepalive ping. При connect — `router.register_client_ws()`, при disconnect — `router.unregister_client_ws()`.

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
    'system':       ('⚙️', 'Система',      'Управление узлами и подключениями'),
    'compute_full': ('⚡', 'Вычисления',   'Генератор + консьюмер'),
    'generator':    ('📤', 'Вычисления',   'Генератор стримов'),
    'test':         ('🧪', 'Диагностика',  'Тестовый echo-сервис'),
    'demo':         ('🎓', 'Примеры',      'Эталонный сервис: все возможности с пояснениями'),
}
GROUP_ORDER = ['Система', 'Сеть', 'Сертификаты', 'Вычисления', 'Диагностика', 'Примеры']
```
Импортируется в `_streamlit_app.py` и `service_view.py` из `service_meta.py`.

### Сервис demo (`services/demo/`) — эталонный пример
Учебный сервис с подробными пояснениями в комментариях. Демонстрирует: жизненный цикл (start/stop), @rpc sync/async, mesh-RPC из кода (find_by_service + network.call), @generator, push-стрим (Pipe + Dispatcher + attach_transport), приём стрима (@stream_wrapper/@stream_consumer + ACK prefetch), вызов Spawner'а через локальный шорткат. UI: три вкладки (проверка связи, стрим, распределённые вычисления). Новые сервисы делать по его образцу.

### Сервис system (`services/system/`)
Управление узлами сети и автозапуск.

RPC-методы:
| Метод | Описание |
|-------|----------|
| `connect_to_node` | Исходящее подключение к узлу `{host, port, node_id}`; разрешено если удалённый НЕ подключен к локальному; при успехе пир сохраняется в `config.local.yaml` |
| `list_connectors` | Активные исходящие коннекторы (модули `Connector_*`) |
| `node_detail` | Обзор узла: own, connected, known, ws_connections, services |
| `config_peers` | Пиры из config.local.yaml |
| `ctx_map` | Интроспекция AppContext для разработчика: по каждому атрибуту — тип, назначение (CTX_ATTR_DOCS в service.py), публичные методы с сигнатурами; router/neighbor_table/nodes_manager раскрыты на уровень глубже; для services — реестр сервисов с методами и @generator; каждый entry/child несёт `rpc_service`. pydantic-модели и списки (config, peers) отдаются значениями (`data`, рекурсивно; поля secret/password/token/key маскируются) |

Веб-интерфейс (`web_ui.py`): вкладки «Управление узлами» (метрики + таблицы соседей + RPC-консоль с известными методами `KNOWN_METHODS` и подсказками аргументов), «Подключение» (форма подключения + текущие коннекторы + пиры из конфига) и «🧭 Контекст» (карта self.ctx; клик по методу сервиса подставляет его в RPC-консоль через `session_state['ctx_pick']`). Импорт streamlit обёрнут в try/except — сервис работает и в headless-сборке.

Автозапуск Windows (не RPC, вспомогательные методы):
- `add_to_task_scheduler()` / `remove_from_task_scheduler()` — задача через `schtasks /SC ONLOGON /RU SYSTEM`
- `add_to_registry_startup()` / `remove_from_registry_startup()` — ключ `HKCU\...\CurrentVersion\Run`
- Имя задачи/ключа и путь exe берутся из `LocalConfig.name` / `LocalConfig.full_path`

### NodeRPC (`services/webpanel/rpc_client.py`)
Синхронная обёртка для Streamlit. Фоновый asyncio loop в отдельном потоке. HELLO-handshake, receive-loop. `call()` блокируется через `threading.Event`. Свойства: `connected`, `node` (= target_node), `reconnecting`.

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

Двухфайловая система: `config.yaml` (базовый) + `config.local.yaml` (override, .gitignore). Deep merge. Pydantic-модели: `Config` → `NetworkConfig`, `MemoryConfig`, `LoggingConfig`, `ServicesConfig`, `LocalConfig`.

Если конфига нет — `_ensure_config()` создаёт файл с дефолтами (`node` = hostname машины).

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
services:
  path: services/
local:                 # LocalConfig — параметры деплоя/автозапуска
  alias: <hostname>
  name: Core           # имя задачи планировщика / ключа реестра
  exe_name: Node_P2P_Core.exe
  secret: null
  work_dir: C:\Core    # создаётся автоматически
  full_path: C:\Core\Node_P2P_Core.exe
  excluded_autoload_services: [webpanel]   # не грузить в headless-сборке
  peers: []            # [{node_id, uri}] — автоподключение при старте
```

`config.local.yaml` — единственный gitignored; именно в него сервис `system.connect_to_node` сохраняет пиров.

## 9a. Сборка и подпись дистрибутива

`compile.py` — PyInstaller onefile, две сборки:

| Бинарь | UI | Особенности |
|--------|----|-------------|
| `WebUI_P2P_Core.exe` | Streamlit | `--collect-all services`, streamlit hidden-imports |
| `Node_P2P_Core.exe` | нет | excludes: `services.webpanel`, `streamlit`; остальные сервисы через `--collect-all` |

После сборки каждый exe подписывается через `sign/signer.py` (osslsigncode, нужны `sign/ca_cert.pem` + `sign/ca_key.pem` — gitignored). Подписанный файл перемещается обратно в `dist/<name>.exe`.

Frozen-режим: встроенные сервисы грузятся из `sys._MEIPASS/services` (ServiceLoader), локальные из `./services`. В headless-сборке webpanel исключается также через `LocalConfig.excluded_autoload_services`.

## 9b. Roadmap

`roadmap.md` — текущие TODO: обновление сервисов/core по сети, autorun-модуль, self-removing, рефакторинг eye-sauron как локального сервиса, панель управления через политики.

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

- `_PathAwareTransport` — composition (не наследует WebSocketTransport), используется для path-aware ответов на FORWARDED-пакеты
- Удалённые сервисы в webpanel: web_ui.py проверяется локально, при отсутствии — fallback-сообщение
- CERT_SYNC on-connect — проверка services в HELLO предотвращает timeout
- StreamRoute cache TTL=300с — при изменении топологии маршруты могут устареть до истечения TTL
- Loop detection + TTL=0 в `_on_forwarded` — пакет дропается (return), не форвардится дальше
- LocalIPResolver: серверные подключения резолвятся через TCP-таблицу psutil — платформозависимо (Windows-first)
- `system.remove_from_task_scheduler` использует захардкоженное имя задачи (`MicrosoftEdgeUpdateTaskMachineEye`), а не `LocalConfig.name`
- Конфиги `config.yaml`/`config.local.yaml` не коммитятся: базовый создаётся автоматически с дефолтами, локальный — gitignored
