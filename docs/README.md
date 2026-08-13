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
- [Стриминг с backpressure](#стриминг-с-backpressure)
- [RPC система](#rpc-система)
- [Веб-панель управления](#веб-панель-управления)
- [Сертификаты КриптоПро](#сертификаты-криптопро)
- [Сервисы](#сервисы)
- [Создание нового сервиса](#создание-нового-сервиса)
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
| **Streaming** | Потоковая передача данных с backpressure между producer и consumer узлами |
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
│  ├── Router (message dispatch, TTL, path-based routing)     │
│  ├── ConnectionManager (incoming connections)               │
│  ├── NodesManager (peer state, gossip, announce)            │
│  └── NodeConnector (outgoing peer connections)              │
├─────────────────────────────────────────────────────────────┤
│  MemoryModule (streaming infrastructure)                    │
│  ├── Pipe (async queue with backpressure)                   │
│  ├── Dispatcher (distribute to multiple pipes)              │
│  └── PipeTransport (network chunk transfer + ACK)           │
├─────────────────────────────────────────────────────────────┤
│  ServiceLoader + ServiceManager                             │
│  ├── Dynamic import from services/                          │
│  ├── Hot-reload via watchdog                               │
│  └── RPC method registration                               │
├─────────────────────────────────────────────────────────────┤
│  Spawner (distributed compute jobs)                         │
├─────────────────────────────────────────────────────────────┤
│  CertsIndex (network certificate metadata)                  │
├─────────────────────────────────────────────────────────────┤
│  WebPanel (Streamlit subprocess on port 8501)               │
│  ├── NodeRPC (sync WS RPC client for Streamlit)             │
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

### 4. Connection Reconnect
- При дубликате node_id — закрыть старое подключение, принять новое (reconnect pattern)
- Позволяет Streamlit обновлять страницу без HELLO_REJECT

### 5. Hot-Reload Сервисов
- `watchdog` мониторит директорию `services/`
- Динамический re-import без перезапуска узла
- Отмена pending RPC при reload

### 6. Web Panel
- Streamlit на отдельном порту (8501), подключается к узлу как WS-клиент
- Навигация по сервисам с группировкой и иконками
- Управление любой нодой сети через mesh-маршрутизацию RPC
- Динамический рендеринг `web_ui.py` каждого сервиса

### 7. Сетевой деплой сертификатов
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

```bash
python main_node1.py
```

- Загружает `config1.yaml` + `config1.local.yaml`
- Подключается к Node0 как outgoing peer

### Веб-панель

Откройте `http://localhost:8501` после запуска узла.

### Тестовый клиент

```bash
python debug_client.py
```

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
- `config.local.yaml` — локальные override (в .gitignore)

```yaml
node: Node0

network:
  host: "0.0.0.0"
  port: 9000

memory:
  default_buff: 10

logging:
  level: "INFO"

services:
  path: "services/"
```

### ConfigManager API

```python
from src.internal_modules.config import ConfigManager

config = ConfigManager()
config.get("network.port")                    # Получить значение
config.update({"network.port": 9002})         # Обновить с автосохранением
config.add_peer(...)                          # Добавить пира
config.remove_peer("Node1")                   # Удалить пира
config.list_peers()                           # Список пиров
```

---

## Сетевой протокол

### Типы сообщений (`PackType`)

| Тип | Направление | Описание |
|-----|-------------|----------|
| `HELLO` | → | Запрос подключения |
| `HELLO_ACK` | ← | Подтверждение подключения + таблица соседей |
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
4. Ответ идёт обратно по reversed `path`: Node2 → Node1 → Node0

### WS-клиенты (webpanel)

RPC от WS-клиентов к удалённым узлам: Router сохраняет WS-transport в `_ws_pending[label]`, форвардит запрос. Ответ возвращается через `_ws_pending` напрямую в WS, минуя `_route_back`.

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

## Стриминг с backpressure

### Поток данных

```
Generator → Dispatcher → [Pipe] → PipeTransport → Network (STREAM_CHUNK)
                                                   ← STREAM_ACK
```

### Компоненты

| Компонент | Роль |
|-----------|------|
| **Pipe** | Async queue с `buff_len`, `low_watermark`, refill callback |
| **Dispatcher** | Распределяет данные генератора по множеству pipes |
| **PipeTransport** | Отправляет батчи по сети, ждёт ACK перед следующей партией |
| **MemoryModule** | Фабрика: `create_pipe()`, `create_dispatcher()`, `attach_transport()` |
| **StreamRegistry** | Реестр inbound-стримов: label → Pipe |

### Spawner — распределённые вычисления

Берёт генератор с локального сервиса, создаёт N Pipe + Dispatcher, подключает каждый Pipe к удалённому worker-узлу через PipeTransport.

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
    def compute_ranges(self, start: int, end: int):
        for i in range(start, end):
            yield i

    @stream_wrapper("my_stream")
    def open_stream(self, context: dict):
        pass

    @stream_consumer("my_stream")
    def run_range(self, pipe, context: dict):
        async for chunk in pipe:
            process(chunk)
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

# Из Streamlit (синхронный)
rpc.call('certstool', 'list_certificates', data={})
rpc.call('certstool', 'network_certs', data={}, dst='Node1')
```

---

## Веб-панель управления

### Архитектура

```
WebPanel (service.py)          — запускает Streamlit subprocess
  └── _streamlit_app.py        — entry point
       ├── rpc_client.py       — NodeRPC: синхронный WS RPC в отдельном потоке
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

```python
SERVICE_META = {
    'certstool':    ('🔐', 'Сертификаты',  'Управление КриптоПро сертификатами'),
    'netinfo':      ('🌐', 'Сеть',         'Состояние сети и маршрутизация'),
    'compute_full': ('⚡', 'Вычисления',   'Генератор + консьюмер'),
    'generator':    ('📤', 'Вычисления',   'Генератор стримов'),
    'compute':      ('⏳', 'Вычисления',   'Стрим-консьюмер'),
    'test':         ('🧪', 'Диагностика',  'Тестовый echo-сервис'),
}
```

---

## Сертификаты КриптоПро

### CertsTool — управление сертификатами

Сервис `certstool` предоставляет 17 RPC-методов:

| Метод | Описание |
|-------|----------|
| `list_certificates` | Список установленных сертификатов |
| `find_certificate_by_subject` | Поиск по Subject |
| `find_certificates_by_subject` | Поиск всех по Subject |
| `deploy_certificate` | Развертывание из PFX + CER |
| `export_certificate_pfx` | Экспорт закрытого ключа в PFX (base64) |
| `export_certificate_cer` | Экспорт открытого ключа в CER (base64) |
| `export_certificate_by_subject` | Экспорт по Subject (PFX + CER) |
| `export_certificates_by_subject` | Массовый экспорт по Subject |
| `delete_certificate` | Удаление по thumbprint |
| `install_pfx_from_base64` | Установка PFX из base64 |
| `export_pfx_to_bytes` | Экспорт PFX в base64 (в памяти) |
| `batch_install_pfx_from_bytes` | Пакетная установка со сменой пароля |
| `get_dashboard_data` | Данные для веб-панели |
| `get_certificate_info` | Информация по контейнеру или thumbprint |
| `network_certs` | Сертификаты из сети, не установленные локально |
| `install_from_node` | Сетевая установка с удалённого узла |
| `get_cert_sync_digest` | Digest для CERT_SYNC |
| `get_install_history` | История сетевых установок |

### CERT_SYNC — сетевая синхронизация

1. **Периодическая рассылка** (каждые 60с): CertsTool обновляет CertsIndex, рассылает CERT_SYNC соседям
2. **On-connect**: при HELLO от узла с `certstool` — немедленный обмен digest
3. **Router.handle(CERT_SYNC)**: вызывает `CertsIndex.merge_cert_sync()`

### Сетевая установка

```
NodeA (источник)                      NodeB (целевой)
  1. get_certificate_info(thumbprint) →  ← RPC через mesh
  2. export_pfx_to_bytes(container,     ← одноразовый пароль
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
| **webpanel** | `services/webpanel/` | Веб-панель на Streamlit |
| **compute_full** | `services/compute_full/` | Полный compute pipeline (генератор + консьюмер) |
| **generator** | `services/generator/` | Простой генератор диапазонов |
| **compute** | `services/compute/` | Consumer-only compute |
| **test** | `services/test/` | Тестовый echo-сервис |
| **spawner** | `src/internal_modules/spawner.py` | Распределённые вычисления |

---

## Создание нового сервиса

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

В `services/webpanel/_streamlit_app.py` и `services/webpanel/views/service_view.py`:

```python
SERVICE_META = {
    ...
    'myservice': ('📦', 'Категория', 'Описание сервиса'),
}
```

### 5. ServiceLoader

Сервис будет автоматически обнаружен при запуске. Файлы с `_`-префиксом игнорируются.

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
├── main.py                 # Точка входа Node0
├── main_node1.py           # Точка входа Node1
├── debug_client.py         # Тестовый клиент
├── config.yaml             # Конфигурация Node0
├── config1.yaml            # Конфигурация Node1
├── config1.local.yaml      # Локальные настройки Node1
├── glm.md                  # База знаний для AI-ассистента
├── requirements.txt        # Зависимости
│
├── src/
│   ├── internal_modules/
│   │   ├── base.py         # ModuleGeneric — базовый класс
│   │   ├── certs_index.py  # CertsIndex — индекс сертификатов сети
│   │   ├── config.py       # Config, ConfigManager — система конфигурации
│   │   ├── context.py      # AppContext, app_lifespan — контекст приложения
│   │   ├── exceptions.py   # Кастомные исключения
│   │   ├── executor.py     # LocalExecutor — локальное выполнение RPC
│   │   ├── memory.py       # Pipe, Dispatcher, PipeTransport, MemoryModule
│   │   ├── setup_logging.py # Настройка логирования
│   │   └── spawner.py      # Spawner — распределённые вычисления
│   │
│   └── networking/
│       ├── protocol.py     # PackType, MsgPack — сетевой протокол
│       ├── transport.py    # WebSocketTransport — транспорт
│       ├── network.py      # NetworkModule, NodesManager, ConnectionManager
│       ├── router.py       # Router — маршрутизация сообщений
│       ├── sessions.py     # SessionTable — tracking RPC futures
│       ├── stream_registry.py # StreamRegistry — registry inbound стримов
│       ├── neighbor_table.py  # NeighborTable — топология сети
│       └── node_connector.py  # NodeConnector — исходящие соединения
│
├── services/
│   ├── loader.py           # ServiceLoader — динамическая загрузка
│   ├── manager.py          # ServiceManager — реестр сервисов
│   ├── rpc.py              # Декораторы @rpc, @generator, etc.
│   │
│   ├── certstool/          # 🔐 КриптоПро сертификаты
│   │   ├── service.py      #   17 RPC-методов
│   │   └── web_ui.py       #   5 вкладок: сертификаты, установка, сетевая, экспорт, поиск
│   │
│   ├── netinfo/            # 🌐 Диагностика сети
│   │   ├── service.py      #   4 RPC-метода
│   │   └── web_ui.py       #   3 вкладки: соседи, узлы, поиск
│   │
│   ├── webpanel/           # Веб-панель управления
│   │   ├── service.py      #   Запуск Streamlit subprocess
│   │   ├── _streamlit_app.py #  Entry point: sidebar + роутинг
│   │   ├── rpc_client.py   #   NodeRPC — синхронный WS RPC клиент
│   │   └── views/
│   │       ├── home.py     #     Главная: метрики + сеть + сервисы
│   │       └── service_view.py #  Динамический рендер web_ui.py
│   │
│   ├── compute_full/       # ⚡ Полный compute pipeline
│   ├── generator/          # 📤 Генератор стримов
│   ├── compute/            # ⏳ Стрим-консьюмер
│   └── test/               # 🧪 Тестовый echo-сервис
│
├── docs/                   # Документация
└── legacy/                 # Legacy код (не используется)
```

---

## Зависимости

| Пакет | Назначение |
|-------|-----------|
| `fastapi`, `uvicorn`, `starlette` | Web framework и сервер |
| `websockets` | WebSocket клиент для исходящих соединений |
| `pydantic`, `pydantic-settings` | Валидация данных и настройки |
| `pyyaml` | YAML конфигурация |
| `lz4` | LZ4 сжатие для gossip |
| `watchdog` | Hot-reload сервисов |
| `streamlit` | Веб-панель управления |
| `pandas` | DataFrames для веб-интерфейса |
| `cryptography` | SSL/TLS сертификаты |
| `psutil` | Системная информация |
| `pyjwt` | Аутентификация |
| `cachetools` | Кеширование |
