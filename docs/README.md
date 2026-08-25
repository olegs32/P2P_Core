# P2P Core — Mesh Network P2P System

WebSocket-based P2P mesh network with RPC service discovery, multi-hop routing, distributed data streaming with backpressure, CryptoPro certificate management, and web control panel.

## Содержание

- [Обзор](#обзор)
- [Архитектура](#архитектура)
- [Ключевые возможности](#ключевые-возможности)
- [Быстрый старт](#быстрый-старт)
- [Конфигурация](#конфигурация)
- [Сетевой протокол](#сетевой-протокол)
- [Маршрутизация](#маршрутизация)
- [Обнаружение сервисов](#обнаружение-сервисов)
- [Mesh-стриминг с backpressure](#mesh-стриминг-с-backpressure)
- [RPC система](#rpc-система)
- [Веб-панель управления](#веб-панель-управления)
- [Сертификаты КриптоПро](#сертификаты-криптопро)
- [Сервисы](#сервисы)
- [Создание нового сервиса](#создание-нового-сервиса)
- [Сборка дистрибутива](#сборка-дистрибутива)
- [Тестирование](#тестирование)
- [Структура проекта](#структура-проекта)
- [Зависимости](#зависимости)

---

## Обзор

Система позволяет узлам (nodes) соединяться друг с другом через WebSocket, формировать mesh-топологию через gossip-протокол, обнаруживать сервисы across the network и маршрутизировать RPC и streaming вызовы через промежуточные узлы.

### Основные компоненты

| Компонент | Описание |
|-----------|----------|
| **Node** | Экземпляр приложения с уникальным ID, FastAPI сервером и набором сервисов |
| **Mesh Network** | Децентрализованная сеть с gossip-based discovery и multi-hop routing |
| **RPC** | Удалённый вызов методов между узлами с автоматической маршрутизацией |
| **Mesh Streaming** | Потоковая передача данных через mesh с route caching и backpressure |
| **Service Loader** | Динамическая загрузка и hot-reload сервисов из директории `services/` |
| **Web Panel** | Streamlit-панель управления с доступом к любому узлу сети |
| **CertsTool** | Управление КриптоПро сертификатами с сетевым деплоем между узлами |

---

## Архитектура

```
┌─────────────────────────────────────────────────────────────┐
│                        Node (FastAPI)                        │
├─────────────────────────────────────────────────────────────┤
│  NetworkModule (WebSocket endpoint /ws/{node_id})           │
│  ├── Router (message dispatch, TTL, path-based routing,     │
│  │         stream route cache, send_stream_ack)             │
│  ├── NodesManager (peer state, gossip, announce)            │
│  └── NodeConnector (outgoing peer connections)              │
├─────────────────────────────────────────────────────────────┤
│  MemoryModule (streaming infrastructure)                    │
│  ├── Pipe (async queue with backpressure)                   │
│  ├── Dispatcher (distribute to multiple pipes)              │
│  └── PipeTransport (via Router: STREAM_CHUNK + ACK)         │
├─────────────────────────────────────────────────────────────┤
│  ServiceLoader + ServiceManager                             │
│  ├── Dynamic import from services/                          │
│  ├── Hot-reload via watchdog                               │
│  └── RPC method registration + ctx.register()              │
├─────────────────────────────────────────────────────────────┤
│  Spawner (distributed compute jobs)                         │
├─────────────────────────────────────────────────────────────┤
│  CertsIndex (network certificate metadata)                  │
├─────────────────────────────────────────────────────────────┤
│  WebPanel (Streamlit subprocess on port 8501)               │
│  ├── NodeRPC (sync WS RPC client, reconnect-aware)          │
│  └── RPCProxy (injects dst for remote node targeting)       │
├─────────────────────────────────────────────────────────────┤
│  AppContext (module registry, lifespan management)          │
└─────────────────────────────────────────────────────────────┘
```

---

## Ключевые возможности

### 1. Mesh Routing
- Multi-hop маршрутизация через промежуточные узлы
- TTL-based предотвращение бесконечных циклов (TTL=16)
- Path tracking: каждый узел добавляет себя в `pack.path`
- Обратная маршрутизация: path хранится как `[origin,…,текущий узел]`, каждый хоп
  выталкивает себя с хвоста (`_route_back`) — ответы НЕ разворачиваются
- Loop detection: TTL=0 или loop → packet dropped

### 2. Mesh Streaming
- STREAM_OPEN маршрутизируется через mesh, маршрут кэшируется на всех узлах пути
- StreamRoute: forward_path + backward_path для быстрого форвардинга
- PipeTransport отправляет через Router вместо прямого WS
- Consumer отправляет ACK через `Router.send_stream_ack()` по backward_path
- `_MeshStreamIterator` — публичный async iterator API

### Service Discovery
- **GOSSIP** (каждые 30s): обмен топологией сети
- **ANNOUNCE** (каждые 60s): рассылка списка сервисов
- `NeighborTable` хранит статус каждого узла: `CONNECTED`, `KNOWN`, `UNREACHABLE`
- Поиск сервисов по имени across the network
- `LocalIPResolver` (`src/internal_modules/local_ip.py`): узел сообщает соседям реальный IP своего сетевого интерфейса (приоритет: живые WS-подключения → UDP-trick к пиру из конфига → psutil fallback), кэш на `network.ip_ttl_sec`

### 4. Streaming с Backpressure
- `Pipe`: async queue с `buff_len` и `low_watermark`; ошибка producer → исключение
  у консьюмера (`pipe.fail()`), а не «успешный» конец потока
- `Dispatcher`: распределяет данные генератора по множеству pipes; поток-продюсер
  работает через потокобезопасную очередь (без кросс-поточного планирования на item)
- `PipeTransport`: отправка батчами через Router + кумулятивный ACK protocol
- Автоматическая пауза при заполнении буфера

### 5. Connection Reconnect
- При дубликате node_id — закрыть старое подключение, принять новое (reconnect pattern)
- NodeRPC: `_reconnecting` flag предотвращает Streamlit от создания нового экземпляра

### 6. Hot-Reload Сервисов
- `watchdog` мониторит директорию `services/`
- Динамический re-import без перезапуска узла
- Отмена pending RPC при reload

### 7. Web Panel
- Streamlit на отдельном порту (8501), подключается к узлу как WS-клиент
- Навигация по сервисам с группировкой и иконками
- Управление любой нодой сети через mesh-маршрутизацию RPC
- Динамический рендеринг `web_ui.py` каждого сервиса

### 8. Сетевой деплой сертификатов
- CERT_SYNC: периодическая рассылка digest сертификатов в mesh
- On-connect обмен при подключении нового узла
- Сетевая установка с одноразовыми паролями и конфликтом по subject_cn

---

## Быстрый старт

### Запуск Node0 (основной узел)

```bash
python main.py
```

- Загружает `config.yaml`
- Стартует FastAPI сервер на порту 9000
- Запускает Streamlit-панель на порту 8501
- Автоматически загружает все сервисы из `services/`
- Подключается к настроенным пирам

### Запуск Node1 (вторичный узел)

Вторичный узел запускается тем же `main.py`, но со своим конфигом: скопируйте `config.yaml`, задайте уникальный `node` и добавьте в `local.peers` адрес основного узла — узел подключится к нему при старте. Подключиться к работающему узлу можно и через веб-панель (вкладка «Система» → «Подключение»).

### Веб-панель

Откройте `http://localhost:8501` после запуска узла.

### Тестовый клиент (LEGACY)

```bash
python debug_client.py
```

> ⚠️ `debug_client.py` — **LEGACY, не сопровождается** (времена JSON-протокола).
> Основной UI — веб-панель. При необходимости использования привести к актуальному
> msgpack wire-формату.

---

## Конфигурация

### Файлы конфигурации

| Файл | Описание |
|------|----------|
| `config.yaml` | Единственный файл конфигурации; при отсутствии создаётся автоматически с дефолтами (`node` = hostname) |

### Система конфигурации

Один файл — `config.yaml`; настройки узла и деплоя живут в его секции `local`. Любая модификация через ConfigManager автосохраняется в файл.

```yaml
node: Node0

network:
  host: "0.0.0.0"
  port: 9000
  ip_ttl_sec: 60          # TTL кэша LocalIPResolver

memory:
  default_buff: 10

logging:
  level: "INFO"

logs:                       # буфер логов для веб-панели (сервис logs)
  buffer_size: 2000         # ёмкость кольцевого буфера
  max_msg_len: 4000         # обрезка одного сообщения
  max_traceback_len: 2000   # обрезка traceback (берётся хвост)

services:
  path: "services/"

local:                      # LocalConfig — деплой и автозапуск
  name: Core                # имя задачи планировщика / ключа реестра
  exe_name: Node_P2P_Core.exe
  work_dir: "C:\\Core"
  full_path: "C:\\Core\\Node_P2P_Core.exe"
  excluded_autoload_services: [webpanel]
  peers: []                 # [{node_id, uri}] — автоподключение при старте
```

### ConfigManager API (`src/internal_modules/config.py`)

```python
from src.internal_modules.config import ConfigManager

config = ConfigManager()
config.update(network__port=9002)             # Обновить с автосохранением (вложенность через '__')
config.add_peer('Node1', 'ws://host:9000/ws/')# Добавить пира (local.peers)
config.remove_peer('Node1')                   # Удалить пира
config.list_peers()                           # Список пиров
```

---

## Сетевой протокол

### Wire-формат

**1 binary WS frame = 1 msgpack-дикт** (`encode_pack`/`decode_pack` в `protocol.py`).
Text-кадры (legacy JSON) отклоняются: узел отвечает `HELLO_REJECT` с причиной
`upgrade required`. Неизвестный `type` — пакет дропается, соединение живёт
(forward-compat).

### Типы сообщений (`PackType`)

| Тип | Направление | Описание |
|-----|-------------|----------|
| `HELLO` | → | Запрос подключения |
| `HELLO_ACK` | ← | Подтверждение подключения + таблица соседей |
| `HELLO_REJECT` | ← | Отклонение подключения |
| `REQUEST` | → | RPC вызов |
| `RESPONSE` | ← | RPC ответ |
| `FORWARDED` | ↔ | Пересылаемое сообщение (routing) |
| `STREAM_OPEN` | → | Открытие mesh-стрима (path tracking) |
| `STREAM_READY` | ← | Подтверждение стрима (route cached) |
| `STREAM_CHUNK` | → | Блок данных стрима (via cached route) |
| `STREAM_ACK` | ← | Подтверждение получения (via backward_path) |
| `STREAM_EOF` | → | Конец стрима |
| `ERROR` | ← | Ошибка транспорта/системы (нет метода, нет маршрута, исключение сервиса, упал producer) |
| `PING` / `PONG` | ↔ | Keepalive |
| `GOSSIP` | ↔ | Обмен топологией |
| `ANNOUNCE` | ↔ | Объявление сервисов |
| `CERT_SYNC` | ↔ | Рассылка digest сертификатов |

### Структура сообщения (`MsgPack`)

```python
MsgPack(
    type=PackType.REQUEST,
    source="Node0",
    dst="Node2",
    service="certstool",
    method="list_certificates",
    data={},
    label="uuid-...",         # Идентификатор сессии
    path=["Node0", "Node1"],  # История маршрута
    ttl=16,                   # Time-to-live
    error=None
)
```

---

## Маршрутизация

### Multi-hop routing

```
Node0 → Node1 → Node2
```

1. Node0 отправляет `REQUEST` с `path=["Node0"]`, `ttl=16`
2. Node1 принимает, decrement TTL, добавляет себя в `path`, пересылает `FORWARDED`
3. Node2 выполняет вызов локально через `LocalExecutor`
4. Ответ несёт тот же path `[Node0, Node1, Node2]`: каждый хоп выталкивает себя
   с хвоста — Node2 → Node1 → Node0 (пакеты не разворачиваются)

### WS-клиенты (webpanel)

RPC от WS-клиентов к удалённым узлам: Router сохраняет WS-transport в `_ws_pending[label]`, форвардит запрос. Ответ возвращается через `_ws_pending` напрямую в WS, минуя `_route_back`.

### Loop detection

При TTL=0 или обнаружении loop (node уже в path) — пакет дропается (return), дальнейший форвардинг не происходит.

---

## Обнаружение сервисов

### Gossip протокол (каждые 30s)

Каждый узел рассылает свою таблицу соседей. При получении gossip:

```python
neighbor_table.merge_gossip(received_gossip)
```

### Announce (каждые 60s)

Рассылка списка локальных сервисов всем known-узлам.

### NeighborTable

```python
class NeighborInfo:
    node_id: str
    status: NeighborStatus  # CONNECTED / KNOWN / UNREACHABLE
    via: str                # Next-hop для маршрутизации
    last_ts: float          # Timestamp последнего трафика
    services: list[str]     # Сервисы на этом узле
```

---

## Mesh-стриминг с backpressure

### Архитектура

```
Generator Node                   Intermediate Node(s)          Consumer Node
  Generator ─→ Dispatcher          Route cache (StreamRoute)     StreamRegistry
    ─→ Pipe ─→ PipeTransport ─→  Router._forward() ─→          Router.handle()
                (via Router)       _forward_stream_data()         ─→ feed to Pipe
                                   (fast path via cached route)   ─→ Consumer reads
                                   _route_back() for ACK          ─→ send_stream_ack()
```

### StreamRoute — кэшированный маршрут

При открытии стрима маршрут кэшируется на всех узлах пути:

```python
@dataclass
class StreamRoute:
    label: str
    source: str                     # узел-генератор
    dst: str                        # узел-consumer
    forward_path: list[str]         # source → dst
    backward_path: list[str]        # dst → source
    established_at: float           # TTL=300с
```

- Consumer кэширует при получении `STREAM_OPEN`
- Generator кэширует при получении `STREAM_READY`
- Intermediate кэширует при транзите `STREAM_OPEN`

### PipeTransport — через Router

PipeTransport отправляет пакеты через Router, а не напрямую через WebSocket:

```python
# Новая сигнатура
PipeTransport(pipe, router, pack_template, timeout=30)

# attach_transport
ctx.memory.attach_transport(pipe, pack_template, router)
```

- `_handshake_and_pump()`: отправляет `STREAM_OPEN` через `router._forward()`
- `_pump()`: отправляет `STREAM_CHUNK` / `STREAM_EOF` через `router._send_pack()`
- Ждёт ACK через `router.sessions`

### Consumer ACK через Router

Consumer отправляет ACK генератору через mesh:

```python
router = self.ctx.network.router
await router.send_stream_ack(label, buff)
```

ACK маршрутизируется по `backward_path` из кэша StreamRoute.

### Публичный API стриминга

```python
# Открыть mesh-стрим и читать чанки
async for chunk in await ctx.network.stream(
    dst="Node2", service="compute_full", method="compute_ranges",
    data={"count": 100}, timeout=30
):
    process(chunk)
```

Возвращает `_MeshStreamIterator` — async iterator с кумулятивным ACK: одно
подтверждение на `buff_len` потреблённых чанков (окно совпадает с батчем producer'а).

### Компоненты

| Компонент | Роль |
|-----------|------|
| **Pipe** | Async queue с `buff_len`, `low_watermark`; `fail(error)` — аварийный конец с исключением у консьюмера |
| **Dispatcher** | Распределяет данные генератора по множеству pipes (поток-продюсер → thread-safe queue → async-раздача) |
| **PipeTransport** | Отправка через Router батчами + кумулятивный ACK; при упавшем producer шлёт ERROR вместо EOF |
| **StreamRoute** | Кэшированный маршрут: forward_path + backward_path |
| **MemoryModule** | Фабрика: `create_pipe()`, `create_dispatcher()`, `attach_transport()` |
| **StreamRegistry** | Реестр inbound-стримов: label → Pipe (+ `fail()` по ERROR от producer) |

### Spawner — распределённые вычисления

Берёт генератор с локального сервиса, создаёт N Pipe + Dispatcher, подключает каждый Pipe к удалённому worker-узлу через PipeTransport (mesh-маршрутизация).

---

## RPC система

### Декораторы

```python
from services.rpc import rpc, generator, stream_wrapper, stream_consumer

class MyService(ModuleGeneric):
    @rpc
    async def echo(self, data: dict) -> dict:
        return {"echo": data}

    @generator
    def compute_ranges(self, data: dict):
        for i in range(data.get('count', 20)):
            yield [i * 100, (i + 1) * 100]

    @stream_wrapper("my_stream")
    async def open_stream(self, data: dict):
        return {"multiplier": data.get("multiplier", 1), "results": []}

    @stream_consumer("my_stream")
    async def run_range(self, pipe, ctx):
        multiplier = ctx['multiplier']
        async for chunk in pipe:
            result = chunk[0] * multiplier
            ctx['results'].append(result)
            if ctx.get('label'):
                await self.ctx.network.router.send_stream_ack(ctx['label'], pipe.buff_len)
```

### Вызов RPC

```python
# Из async-кода (модули)
result = await ctx.network.call(
    dst="Node2",
    service="certstool",
    method="list_certificates",
    data={},
    timeout=10
)

# Локальный shortcut (dst = self)
result = await ctx.network.call(dst=ctx.NODE, ...)

# Mesh-стрим (async iterator)
async for chunk in await ctx.network.stream(
    dst="Node2", service="compute_full", method="compute_ranges", data={"count": 100}
):
    process(chunk)

# Из Streamlit (синхронный)
rpc.call('certstool', 'list_certificates', data={})
rpc.call('certstool', 'network_certs', data={}, dst='Node1')
```

### Контракты выполнения и ошибок

**Выполнение (D6):** async @rpc — только await-able API внутри; sync @rpc автоматически
выполняются через `asyncio.to_thread` (не блокируют event loop). CPU-тяжёлый код —
в `ProcessPoolExecutor` вручную. Важно: `asyncio.create_task(sync_fn)` не помогает —
task исполняется в том же loop-потоке.

**Ошибки (D9) — два уровня:**

| Вид | Механизм | Caller видит |
|-----|----------|--------------|
| Транспорт/система (нет метода/маршрута, исключение метода, упал producer) | ERROR-пакет | Exception из `call()` |
| Бизнес-отказ сервиса (валидация, «узел не найден», политика) | RESPONSE с `'error'` в data | Обычный результат — проверять data |

---

## Веб-панель управления

### Архитектура

```
WebPanel (service.py)          — запускает Streamlit subprocess
  └── _streamlit_app.py        — entry point
       ├── rpc_client.py       — NodeRPC: синхронный WS RPC, reconnect-aware
       ├── RPCProxy            — подставляет dst из session_state['selected_node']
       ├── views/home.py       — главная: метрики + таблица соседей + сервисы
       └── views/service_view.py — динамический import web_ui.py → render(rpc)
```

### Контракт web_ui.py

Каждый сервис с веб-интерфейсом должен содержать `services/<name>/web_ui.py` с функцией:

```python
def render(rpc):
    """rpc — RPCProxy, поддерживает rpc.call(service, method, data, dst, timeout)"""
    ...
```

### Sidebar навигация

- Кнопки с иконками и группировкой по категориям
- Селектор целевого узла (локальный / connected / known)
- Активная кнопка подсвечивается `type="primary"`

### Реестр сервисов

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

### NodeRPC — reconnect

При потере WS соединения NodeRPC ставит `_reconnecting=True`. Свойство `connected` возвращает True во время реконнекта, предотвращая Streamlit от создания нового экземпляра. `_recv_task` отменяется перед повторным подключением.

---

## Сертификаты КриптоПро

### CertsTool — управление сертификатами

Сервис `certstool` предоставляет 16 RPC-методов:

| Метод | Описание |
|-------|----------|
| `list_certificates` | Список установленных сертификатов |
| `find_certificate_by_subject` | Поиск по Subject |
| `find_certificates_by_subject` | Поиск всех по Subject |
| `deploy_certificate` | Развертывание из файловой пары PFX + CER (автоконтейнер, смена пароля) |
| `export_certificate_pfx` | Экспорт закрытого ключа в PFX (base64) |
| `export_certificate_cer` | Экспорт открытого ключа в CER (base64) |
| `export_certificate_by_subject` | Экспорт первого найденного по Subject (PFX + CER) |
| `delete_certificate` | Удаление по thumbprint |
| `install_pfx_from_base64` | Установка PFX из base64 |
| `batch_install_pfx_from_bytes` | Пакетная установка со сменой пароля |
| `get_dashboard_data` | Данные для веб-панели |
| `get_certificate_info` | Информация по контейнеру или thumbprint |
| `network_certs` | Сертификаты из сети, не установленные локально |
| `install_from_node` | Сетевая установка с удалённого узла (`source_node`) |
| `get_cert_sync_digest` | Digest для CERT_SYNC |
| `get_install_history` | История сетевых установок |

При отсутствии КриптоПро на узле (ошибка «Тип поставщика не определен») сервис
однократно логирует причину и саморазрегистрируется — спам в логах и бесполезные
запуски certmgr прекращаются.

### CERT_SYNC — сетевая синхронизация

1. **Периодическая рассылка** (каждые 60с): CertsTool обновляет CertsIndex, рассылает CERT_SYNC соседям
2. **On-connect**: при HELLO от узла с `certstool` — немедленный обмен digest
3. **Router.handle(CERT_SYNC)**: вызывает `CertsIndex.merge_cert_sync()`

### Сетевая установка

```
NodeA (источник)                      NodeB (целевой)
  1. get_certificate_info(thumbprint) →  ← RPC через mesh
  2. export_certificate_pfx(container,  ← одноразовый пароль
     one_time_password)
  3. PFX + password →                  →  install_pfx_from_base64(pfx, otp)
                                            сменить пароль контейнера
                                            обновить CertsIndex
```

### Веб-интерфейс CertsTool

5 вкладок:
1. **Сертификаты** — построчный рендер с PFX/CER/Delete, 🟢🟡🔴 expiry badges
2. **Установка** — PFX из файла, пакетная установка
3. **🌐 Сетевая установка** — сертификаты из сети, группировка по subject_cn, конфликт-детекция, пакетная очередь
4. **Экспорт** — по Subject или контейнеру
5. **Поиск** — по паттерну Subject

---

## Сервисы

### Встроенные сервисы

| Сервис | Файл | Описание |
|--------|------|----------|
| **netinfo** | `services/netinfo/` | Диагностика сети: соседи, узлы, сервисы, поиск |
| **certstool** | `services/certstool/` | Управление КриптоПро сертификатами с сетевым деплоем |
| **system** | `services/system/` | Подключение к узлам, диагностика узла, автозапуск Windows (планировщик/реестр) |
| **webpanel** | `services/webpanel/` | Веб-панель на Streamlit |
| **compute_full** | `services/compute_full/` | Полный compute pipeline (генератор + консьюмер) |
| **generator** | `services/generator/` | Простой генератор диапазонов |
| **test** | `services/test/` | Тестовый echo-сервис |
| **logs** | `services/logs/` | 📜 Логи консоли узла в панели: кольцевой буфер + фильтры severity/поиск/regex/период, live-режим, экспорт |
| **demo** | `services/demo/` | 🎓 Эталонный сервис с пояснениями — образец для разработки |
| **spawner** | `src/internal_modules/spawner.py` | Распределённые вычисления |

### Сервис logs

`RingBufferHandler` цепляется к root logger и копит записи в памяти (кольцевой буфер; параметры — `config.yaml` → секция `logs`: `buffer_size`, `max_msg_len`, `max_traceback_len`). RPC: `get_logs` — инкрементальный поллинг (`since_id`) с серверными фильтрами (levels, search, regex, loggers, период, limit), `get_loggers`, `clear_buffer`. Веб-интерфейс: live-лента (фрагмент с автообновлением 2 сек), цветные уровни, экспорт CSV/TXT. Ограничение: только записи, доходящие до root logger.

### Сервис system

RPC-методы: `connect_to_node` (исходящее подключение + сохранение пира в config.yaml → local.peers), `list_connectors`, `node_detail`, `config_peers`, `ctx_map` (интроспекция AppContext: типы, назначения, сигнатуры методов — подсказка разработчику).

Веб-интерфейс: «Управление узлами» (метрики, таблицы соседей, RPC-консоль), «Подключение» (форма подключения к удалённому узлу) и «🧭 Контекст» (карта `self.ctx`: атрибуты, их методы и реестр сервисов).

Вспомогательные методы автозапуска Windows: задача планировщика (`schtasks /SC ONLOGON`) и ключ реестра `HKCU\...\Run`; имя и путь берутся из `LocalConfig`.

---

## Создание нового сервиса

> 🎓 Живой пример со всеми возможностями и подробными комментариями: `services/demo/` — начинайте с него.

### 1. Структура директории

```
services/myservice/
├── __init__.py          # пустой
├── service.py           # реализация сервиса
└── web_ui.py            # (опционально) веб-интерфейс
```

### 2. Реализация сервиса

```python
# services/myservice/service.py
from src.internal_modules.base import ModuleGeneric
from services.rpc import rpc


class MyService(ModuleGeneric):
    def __init__(self, name: str, context):
        super().__init__(name, context)

    async def start(self):
        self.log.info('MyService started')

    async def stop(self):
        self.log.info('MyService stopped')

    @rpc
    async def my_method(self, data: dict) -> dict:
        return {"result": "ok"}
```

### 3. Веб-интерфейс (опционально)

```python
# services/myservice/web_ui.py
import streamlit as st


def render(rpc):
    st.header("My Service")
    result = rpc.call('myservice', 'my_method', data={})
    st.json(result)
```

### 4. Добавить в реестр

В `services/webpanel/service_meta.py`:

```python
SERVICE_META = {
    ...
    'myservice': ('📦', 'Категория', 'Описание сервиса'),
}
```

### 5. ServiceLoader

Сервис будет автоматически обнаружен и зарегистрирован через `ctx.register(instance)`. Файлы с `_`-префиксом игнорируются.

---

## Тестирование

### Тесты (pytest-совместимые, в `tests/`)

| Файл | Покрывает |
|------|-----------|
| `test_integration_msgpack.py` | Wire-протокол: HELLO, RPC с bytes, 10k-чанковый стрим с backpressure, GOSSIP/CERT_SYNC, robustness |
| `test_multihop_routing.py` | B1/B3: multi-hop RPC/ERROR через промежуточный узел, живучесть соединения |
| `test_b2_ack_race.py` | B2: ACK-future регистрируется до батча |
| `test_b4_producer_error.py` | B4: ошибка producer = исключение у консьюмера / ERROR вместо EOF |
| `test_d5_hotreload.py` | D5: hot-reload — stop старого → start нового |

Запуск: `$env:PYTHONPATH='.'; python tests/<name>.py`

### Debug Client (LEGACY)

`debug_client.py` — standalone WebSocket тест клиент:

```bash
python debug_client.py
```

**Тесты:**

| # | Тест | Описание |
|---|------|----------|
| 1 | Ping/Pong | Проверка keepalive |
| 2 | Neighbor Table | Запрос таблицы соседей |
| 3 | Active Nodes | Список активных узлов |
| 4 | Service Listing | Список зарегистрированных сервисов |
| 5 | Service Lookup | Поиск узла по сервису |
| 6 | Duplicate Rejection | Проверка отклонения дублирующего соединения |
| 7 | Local Stream | Стриминг с backpressure на локальном узле |
| 8 | Node-to-Node | Стриминг между узлами через mesh |

---

## Структура проекта

```
P2P_Core/
├── main.py                 # Точка входа (все узлы)
├── compile.py              # PyInstaller-сборка двух exe + автоподпись
├── debug_client.py         # Тестовый клиент
├── config.yaml             # Единственный конфиг (создаётся автоматически при первом запуске; local.* — настройки узла)
├── glm.md                  # База знаний для AI-ассистента
├── roadmap.md              # TODO / планы развития
├── requirements.txt        # Зависимости
│
├── sign/                   # Подпись exe (osslsigncode; CA-ключи в .gitignore)
│   └── signer.py
│
├── src/
│   ├── internal_modules/
│   │   ├── base.py         # ModuleGeneric — базовый класс
│   │   ├── certs_index.py  # CertsIndex — индекс сертификатов сети
│   │   ├── config.py       # Config, ConfigManager — система конфигурации
│   │   ├── context.py      # AppContext, app_lifespan — контекст приложения
│   │   ├── exceptions.py   # Кастомные исключения
│   │   ├── executor.py     # LocalExecutor — локальное выполнение RPC
│   │   ├── local_ip.py     # LocalIPResolver — IP интерфейса mesh с TTL-кэшем
│   │   ├── memory.py       # Pipe, Dispatcher, PipeTransport, MemoryModule
│   │   ├── setup_logging.py # Настройка логирования
│   │   └── spawner.py      # Spawner — распределённые вычисления
│   │
│   └── networking/
│       ├── protocol.py     # PackType, MsgPack — сетевой протокол
│       ├── transport.py    # WebSocketTransport — транспорт
│       ├── network.py      # NetworkModule, NodesManager
│       ├── router.py       # Router, StreamRoute, _route_back, _MeshStreamIterator
│       ├── sessions.py     # SessionTable — tracking RPC futures
│       ├── stream_registry.py # StreamRegistry — registry inbound стримов
│       ├── neighbor_table.py  # NeighborTable — топология сети
│       └── node_connector.py  # NodeConnector — исходящие соединения
│
├── services/
│   ├── loader.py           # ServiceLoader — динамическая загрузка + ctx.register()
│   ├── manager.py          # ServiceManager — реестр сервисов
│   ├── rpc.py              # Декораторы @rpc, @generator, @stream_wrapper, @stream_consumer
│   │
│   ├── certstool/          # 🔐 КриптоПро сертификаты
│   │   ├── service.py      #   16 RPC-методов
│   │   └── web_ui.py       #   5 вкладок: сертификаты, установка, сетевая, экспорт, поиск
│   │
│   ├── netinfo/            # 🌐 Диагностика сети
│   │   ├── service.py      #   4 RPC-метода
│   │   └── web_ui.py       #   3 вкладки: соседи, узлы, поиск
│   │
│   ├── system/             # ⚙️ Управление узлами и подключения
│   │   ├── service.py      #   connect_to_node, list_connectors, node_detail, config_peers + автозапуск
│   │   └── web_ui.py       #   Управление узлами + RPC-консоль + подключение
│   │
│   ├── webpanel/           # Веб-панель управления
│   │   ├── service.py      #   Запуск Streamlit subprocess
│   │   ├── service_meta.py #   SERVICE_META — реестр иконок/групп сервисов
│   │   ├── _streamlit_app.py #  Entry point: sidebar + роутинг
│   │   ├── rpc_client.py   #   NodeRPC — синхронный WS RPC клиент
│   │   └── views/
│   │       ├── home.py     #     Главная: метрики + сеть + сервисы
│   │       └── service_view.py #  Динамический рендер web_ui.py
│   │
│   ├── compute_full/       # ⚡ Полный compute pipeline
│   ├── generator/          # 📤 Генератор стримов
│   ├── test/               # 🧪 Тестовый echo-сервис
│   └── demo/               # 🎓 Эталонный сервис (учебный пример)
│
└── docs/                   # Документация
```

---

## Сборка дистрибутива

```bash
python compile.py
```

Собирает два PyInstaller onefile-бинаря и подписывает их через osslsigncode:

| Бинарь | UI | Назначение |
|--------|----|-----------|
| `dist/WebUI_P2P_Core.exe` | Streamlit | Узел с веб-панелью |
| `dist/Node_P2P_Core.exe` | нет | Headless-узел (webpanel/streamlit исключены) |

Для подписи нужны `sign/ca_cert.pem` + `sign/ca_key.pem`. В frozen-режиме встроенные сервисы загружаются из `sys._MEIPASS/services`, локальные — из `./services`.

---

## Зависимости

| Пакет | Назначение |
|-------|-----------|
| `fastapi`, `uvicorn`, `starlette` | Web framework и сервер |
| `websockets` | WebSocket клиент для исходящих соединений |
| `msgpack` | Binary wire-формат (1 WS-кадр = 1 msgpack-дикт) |
| `pydantic` | Валидация данных (Config, MsgPack) |
| `pyyaml` | YAML конфигурация |
| `watchdog` | Hot-reload сервисов |
| `colorama` | ANSI-цвета логов в Windows-консоли |
| `psutil` | LocalIPResolver: TCP-таблица + сетевые интерфейсы |
| `cryptography` | Подпись/генерация сертификатов (`sign/signer.py`) |
| `streamlit`, `pandas`, `streamlit-agraph` | Веб-панель и карта сети |
| `pyinstaller` | Сборка дистрибутива |

Устаревшие зависимости (`lz4`, `aiohttp`, `requests`, `httpx`, `PyJWT`,
`cachetools`, `python-dotenv`, `urllib3`, `pydantic-settings`) удалены —
нигде не импортировались.
