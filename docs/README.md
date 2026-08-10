# P2P Core — Mesh Network P2P System

WebSocket-based P2P mesh network with RPC service discovery, multi-hop routing, and distributed data streaming with backpressure.

## Содержание

- [Обзор](#обзор)
- [Архитектура](#архитектура)
- [Ключевые возможности](#ключевые-возможности)
- [Быстрый старт](#быстрый-старт)
- [Конфигурация](#конфигурация)
- [Сетевой протокол](#сетевой-протокол)
- [Маршрутизация](#маршрутизация)
- [Обнаружение сервисов](#обнаружение-сервисов)
- [Стриминг с backpressure](#стриминг-с-backpressure)
- [RPC система](#rpc-система)
- [Сервисы](#сервисы)
- [Тестирование](#тестирование)
- [Структура проекта](#структура-проекта)

---

## Обзор

Система позволяет узлам (nodes) соединяться друг с другом через WebSocket, формировать mesh-топологию через gossip-протокол, обнаруживать сервисы across the network и маршрутизировать RPC и streaming вызовы через промежуточные узлы.

### Основные компоненты

| Компонент | Описание |
|-----------|----------|
| **Node** | Экземпляр приложения с уникальным ID, FastAPI сервером и набором сервисов |
| **Mesh Network** | Децентрализованная сеть с gossip-based discovery и multi-hop routing |
| **RPC** | Удалённый вызов методов между узлами с автоматической маршрутизацией |
| **Streaming** | Потоковая передача данных с backpressure между producer и consumer узлами |
| **Service Loader** | Динамическая загрузка и hot-reload сервисов из директории `services/` |

---

## Архитектура

```
┌─────────────────────────────────────────────────────────────┐
│                        Node (FastAPI)                        │
├─────────────────────────────────────────────────────────────┤
│  NetworkModule (WebSocket endpoint /ws/{node_id})            │
│  ├── Router (message dispatch, TTL, path-based routing)      │
│  ├── ConnectionManager (incoming connections)                │
│  ├── NodesManager (peer state, gossip, announce)             │
│  └── NodeConnector (outgoing peer connections)               │
├─────────────────────────────────────────────────────────────┤
│  MemoryModule (streaming infrastructure)                     │
│  ├── Pipe (async queue with backpressure)                    │
│  ├── Dispatcher (distribute to multiple pipes)               │
│  └── PipeTransport (network chunk transfer + ACK)            │
├─────────────────────────────────────────────────────────────┤
│  ServiceLoader + ServiceManager                              │
│  ├── Dynamic import from services/                           │
│  ├── Hot-reload via watchdog                                 │
│  └── RPC method registration                                 │
├─────────────────────────────────────────────────────────────┤
│  Spawner (distributed compute jobs)                          │
├─────────────────────────────────────────────────────────────┤
│  AppContext (module registry, lifespan management)            │
└─────────────────────────────────────────────────────────────┘
```

---

## Ключевые возможности

### 1. Mesh Routing
- Multi-hop маршрутизация через промежуточные узлы
- TTL-based предотвращение бесконечных циклов (TTL=16)
- Path tracking: каждый узел добавляет себя в `pack.path`
- Обратная маршрутизация по reversed path для ответов
- Loop detection (warning-only)

### 2. Service Discovery
- **GOSSIP** (каждые 30s): обмен топологией сети
- **ANNOUNCE** (каждые 60s): рассылка списка сервисов
- `NeighborTable` хранит статус каждого узла: `CONNECTED`, `KNOWN`, `UNREACHABLE`
- Поиск сервисов по имени across the network

### 3. Streaming с Backpressure
- `Pipe`: async queue с `buff_len` и `low_watermark`
- `Dispatcher`: распределяет данные по множеству pipes
- `PipeTransport`: отправка батчами + ACK protocol
- Автоматическая пауза при заполнении буфера

### 4. Connection Deduplication
- Lexicographic rule: `self.NODE_ID < peer.NODE_ID` предотвращает дублирующие соединения
- HELLO handshake отклоняет дубликаты, неправильный тип или destination

### 5. Hot-Reload Сервисов
- `watchdog` мониторит директорию `services/`
- Динамический re-import без перезапуска узла
- Отмена pending RPC при reload

---

## Быстрый старт

### Запуск Node0 (основной узел)

```bash
python main.py
```

- Загружает `config.yaml`
- Стартует FastAPI сервер
- Автоматически загружает все сервисы из `services/`
- Подключается к настроенным пирам

### Запуск Node1 (вторичный узел)

```bash
python main_node1.py
```

- Загружает `config1.yaml` + `config1.local.yaml`
- Подключается к Node0 как outgoing peer
- Запускает Compute сервис (consumer-only)

### Тестовый клиент

```bash
python debug_client.py
```

Выполняет 7 тестов:
1. Ping/Pong
2. Таблица соседей
3. Активные узлы
4. Список сервисов
5. Поиск сервиса
6. Отклонение дублирующих соединений
7. Локальный стриминг с backpressure
8. Node-to-node streaming

---

## Конфигурация

### Файлы конфигурации

| Файл | Описание |
|------|----------|
| `config.yaml` | Базовая конфигурация (Node0) |
| `config1.yaml` | Базовая конфигурация для Node1 |
| `config1.local.yaml` | Локальные настройки Node1 (alias, peers) |

### Система конфигурации

Двухфайловая система с deep merge:
- `config.yaml` — shared настройки
- `config.local.yaml` — локальные override

```yaml
node:
  name: "Node0"

network:
  host: "0.0.0.0"
  port: 9000

memory:
  buffer_size: 1024

logging:
  level: "INFO"

services:
  path: "services"

peers:
  - host: "localhost"
    port: 9001
    node_id: "Node1"
```

### ConfigManager API

```python
from src.internal_modules.config import ConfigManager

config = ConfigManager()
config.get("network.port")       # Получить значение
config.update({"network.port": 9002})  # Обновить с автосохранением
config.add_peer(...)             # Добавить пира
config.remove_peer("Node1")      # Удалить пира
config.list_peers()              # Список пиров
```

---

## Сетевой протокол

### Типы сообщений (`PackType`)

| Тип | Направление | Описание |
|-----|-------------|----------|
| `HELLO` | → | Запрос подключения |
| `HELLO_ACK` | ← | Подтверждение подключения |
| `HELLO_REJECT` | ← | Отклонение подключения |
| `REQUEST` | → | RPC вызов |
| `RESPONSE` | ← | RPC ответ |
| `FORWARDED` | ↔ | Пересылаемое сообщение (routing) |
| `STREAM_OPEN` | → | Открытие стрима |
| `STREAM_READY` | ← | Подтверждение стрима |
| `STREAM_CHUNK` | → | Блок данных стрима |
| `STREAM_ACK` | ← | Подтверждение получения блока |
| `STREAM_EOF` | → | Конец стрима |
| `ERROR` | ← | Ошибка |
| `PING` / `PONG` | ↔ | Keepalive |
| `GOSSIP` | ↔ | Обмен топологией |
| `ANNOUNCE` | ↔ | Объявление сервисов |

### Структура сообщения (`MsgPack`)

```python
MsgPack(
    type=PackType.REQUEST,
    source="Node0",
    dst="Node2",
    service="compute",
    method="start_stream",
    data={"ranges": [...]},
    label="uuid-...",       # Идентификатор сессии/стрима
    path=["Node0", "Node1"], # История маршрута
    ttl=16,                  # Time-to-live
    error=None
)
```

### WebSocket Transport

`WebSocketTransport` работает с обоими типами соединений:
- FastAPI WebSocket (server-side, `send_json`)
- `websockets` client connections

Автоматически определяет тип и использует соответствующий метод отправки.

---

## Маршрутизация

### Multi-hop routing

```
Node0 → Node1 → Node2
```

1. Node0 отправляет `REQUEST` с `path=["Node0"]`, `ttl=16`
2. Node1 принимает, decrement TTL до 15, добавляет себя в `path`
3. Node1 пересылает `FORWARDED` пакет в Node2
4. Node2 выполняет вызов локально через `LocalExecutor`
5. Ответ идёт обратно по reversed `path`: Node2 → Node1 → Node0

### Loop detection

Если узел обнаруживает себя в `pack.path`, он логирует warning. Полная блокировка циклов — TODO.

### Session tracking

`SessionTable` хранит:
- `register_single(label, Future)` — для one-shot RPC
- `register_stream(label, Queue)` — для streaming
- `cancel_by_service(service)` — отмена при hot-reload

---

## Обнаружение сервисов

### Gossip протокол (каждые 30s)

Каждый узел рассылает свою таблицу соседей. При получении gossip:

```python
neighbor_table.merge_gossip(received_gossip)
# Обновляет статусы, via (next-hop), last_activity, services
```

### Announce (каждые 60s)

Рассылка списка локальных сервисов всем известным узлам.

### NeighborTable

```python
class NeighborInfo:
    node_id: str
    status: NeighborStatus  # CONNECTED / KNOWN / UNREACHABLE
    via: str                # Next-hop для маршрутизации
    last_activity: float
    services: list[str]
```

### Поиск сервисов

```python
# Найти узлы, предоставляющие сервис "compute"
nodes = neighbor_table.find_by_service("compute")
```

---

## Стриминг с Backpressure

### Компоненты

| Компонент | Роль |
|-----------|------|
| **Pipe** | Async queue с `buff_len`, `low_watermark`, refill callback |
| **Dispatcher** | Распределяет данные генератора по множеству pipes |
| **PipeTransport** | Отправляет чанки по сети, ждёт ACK перед следующей партией |

### Поток данных

```
Generator → Dispatcher → Pipe → PipeTransport → Network → Stream Chunk
                                                        ↓
                                                  ACK ← Remote
```

### Backpressure механизм

1. `Pipe` имеет `buff_len` (размер буфера)
2. Когда буфер заполняется, `Dispatcher` ставит producer на паузу
3. `PipeTransport` отправляет батч и ждёт `STREAM_ACK`
4. После получения ACK буфер освобождается, producer возобновляет работу

### Spawner — распределённые вычисления

```python
# RPC метод spawn
# 1. Берёт генератор с локального сервиса
# 2. Создаёт pipes для каждого worker node
# 3. Dispatcher распределяет данные
# 4. PipeTransport отправляет батчи worker'ам
# 5. Workers обрабатывают и возвращают результаты
```

---

## RPC система

### Декораторы

```python
from services.rpc import rpc, generator, stream_wrapper, stream_consumer

class MyService:
    @rpc
    def echo(self, data: dict) -> dict:
        """Обычный RPC вызов"""
        return {"echo": data}

    @generator
    def compute_ranges(self, start: int, end: int):
        """Генератор для streaming"""
        for i in range(start, end):
            yield i

    @stream_wrapper
    def open_stream(self, context: dict):
        """Подготовка контекста стрима"""
        pass

    @stream_consumer
    def run_range(self, pipe, context: dict):
        """Обработка чанков из pipe"""
        async for chunk in pipe:
            process(chunk)
```

### Автоматическая регистрация

- `@rpc` методы регистрируются напрямую
- `@generator` методы автоматически регистрируются с префиксом `__gen__`
- `ServiceLoader` сканирует `services/` и импортирует все Python файлы

### Вызов RPC

```python
# Локальный вызов
result = await network_module.call(service="test", method="echo", data={"msg": "hi"})

# Remote вызов (автоматическая маршрутизация)
result = await network_module.call(
    service="compute",
    method="start_stream",
    data={"start": 0, "end": 100},
    dst="Node2"  # Если не указан, маршрутизируется автоматически
)
```

---

## Сервисы

### Встроенные сервисы

| Сервис | Файл | RPC методы | Описание |
|--------|------|------------|----------|
| **netinfo** | `services/netinfo/service.py` | `neighbors`, `nodes`, `services`, `find_service` | Диагностика сети |
| **compute_full** | `services/compute_full/service.py` | `start_stream`, `compute_ranges`, `compute_squares`, `run_range` | Полный compute pipeline |
| **compute** | `services/compute/service.py` | — | Consumer-only compute (Node1) |
| **generator** | `services/generator/service.py` | `start_stream` | Простой генератор диапазонов |
| **test** | `services/test/service.py` | `echo`, `echo_stream` | Тестовый сервис |

### NetInfo сервис

```python
# Получить таблицу соседей
neighbors = await call("netinfo", "neighbors")

# Список активных узлов
nodes = await call("netinfo", "nodes")

# Все зарегистрированные сервисы
services = await call("netinfo", "services")

# Найти узлы с конкретным сервисом
found = await call("netinfo", "find_service", {"service": "compute"})
```

### Compute Full Pipeline

```
Node0 (producer)                          Node1 (consumer)
┌──────────────────┐                      ┌──────────────────┐
│ compute_ranges() │─── stream ──────────>│ run_range()      │
│   → yield ranges │      (backpressure)  │   → process      │
│ compute_squares()│<── results ──────────│   → log results  │
│   → yield sq     │                      │                  │
└──────────────────┘                      └──────────────────┘
```

---

## Тестирование

### Debug Client

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
| 8 | Node-to-Node | Стриминг между узлами |

---

## Структура проекта

```
P2P_Core/
├── main.py                    # Точка входа Node0
├── main_node1.py              # Точка входа Node1
├── debug_client.py            # Тестовый клиент
├── config.yaml                # Базовая конфигурация
├── config1.yaml               # Конфигурация Node1
├── config1.local.yaml         # Локальные настройки Node1
├── requirements.txt           # Зависимости
│
├── src/
│   ├── internal_modules/
│   │   ├── base.py            # ModuleGeneric — базовый класс модулей
│   │   ├── config.py          # Config, ConfigManager — система конфигурации
│   │   ├── context.py         # AppContext, app_lifespan — контекст приложения
│   │   ├── exceptions.py      # Custom exceptions
│   │   ├── executor.py        # LocalExecutor — локальное выполнение RPC
│   │   ├── memory.py          # Pipe, Dispatcher, PipeTransport, MemoryModule
│   │   ├── setup_logging.py   # Colored logging
│   │   └── spawner.py         # Spawner — распределённые вычисления
│   │
│   └── networking/
│       ├── protocol.py        # PackType, MsgPack — сетевой протокол
│       ├── transport.py       # WebSocketTransport — транспорт
│       ├── network.py         # NetworkModule, Node, ConnectionManager
│       ├── router.py          # Router — маршрутизация сообщений
│       ├── sessions.py        # SessionTable — tracking RPC futures
│       ├── stream_registry.py # StreamRegistry — registry inbound стримов
│       ├── neighbor_table.py  # NeighborTable — топология сети
│       └── node_connector.py  # NodeConnector — outgoing peer connections
│
├── services/
│   ├── loader.py              # ServiceLoader — динамическая загрузка
│   ├── manager.py             # ServiceManager — реестр сервисов
│   ├── rpc.py                 # Декораторы @rpc, @generator, etc.
│   │
│   ├── netinfo/               # Диагностика сети
│   ├── compute_full/          # Полный compute pipeline
│   ├── compute/               # Consumer-only compute
│   ├── generator/             # Генератор диапазонов
│   └── test/                  # Тестовый сервис
│
├── docs/                      # Документация (эта директория)
└── venv/                      # Виртуальное окружение
```

---

## Зависимости

| Пакет | Назначение |
|-------|------------|
| `fastapi`, `uvicorn`, `starlette` | Web framework и сервер |
| `websockets` | WebSocket клиент для outgoing соединений |
| `httpx`, `aiohttp`, `requests` | HTTP клиенты |
| `pydantic`, `pydantic-settings` | Валидация данных и настройки |
| `pyyaml` | YAML конфигурация |
| `lz4` | Сжатие gossip сообщений |
| `cryptography` | Генерация сертификатов |
| `watchdog` | Hot-reload сервисов |
| `pyjwt` | Аутентификация |
| `psutil` | Системная информация |
| `python-dotenv` | Загрузка .env файлов |
| `cachetools` | Кеширование |

---

## Паттерны и практики

### Module Lifecycle

Все модули наследуют `ModuleGeneric`:

```python
class ModuleGeneric:
    name: str
    context: AppContext
    
    async def start(self): ...
    async def stop(self): ...
```

`AppContext` запускает модули в порядке регистрации, останавливает в обратном.

### Connection Management

```python
# Lexicographic rule предотвращает дублирующие соединения
if self.ctx.NODE < peer_node_id:
    # Мы соединяемся
    await self.connect(peer)
else:
    # Они соединятся к нам
    pass
```

### Stream Pattern

```python
# Producer side
pipe = Pipe(buff_len=100)
dispatcher = Dispatcher(generator, pipes=[pipe])
transport = PipeTransport(pipe, network_transport, label)

# Consumer side
async def consumer(pipe: Pipe):
    async for chunk in pipe:
        process(chunk)
```
