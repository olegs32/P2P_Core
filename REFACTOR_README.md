# P2P Core Refactoring - New Features

Это руководство описывает новые возможности после рефакторинга P2P системы.

## Основные улучшения

### 1. Централизованная конфигурация (YAML)

Конфигурация теперь загружается из YAML файлов, что упрощает настройку для различных окружений.

**Файлы конфигурации:**
- `config/coordinator.yaml` - конфигурация координатора
- `config/worker.yaml` - конфигурация worker узла

**Пример использования:**
```python
from layers.application_context import P2PConfig

# Загрузка из YAML
config = P2PConfig.from_yaml('config/coordinator.yaml')
```

**Основные параметры:**

#### Gossip оптимизация
- `gossip_interval_min`: 5 сек (минимальный интервал при низкой нагрузке)
- `gossip_interval_max`: 30 сек (максимальный интервал при высокой нагрузке)
- `gossip_compression_enabled`: true (LZ4 компрессия)
- `gossip_compression_threshold`: 1024 байт

#### Rate Limiting
- `rate_limit_rpc_requests`: 100 req/min (строже для RPC)
- `rate_limit_health_requests`: 300 req/min (послабее для health)
- `rate_limit_rpc_burst`: 20
- `rate_limit_health_burst`: 50

#### HTTPS/SSL
- `https_enabled`: true
- `ssl_cert_file`: "node_cert.pem"
- `ssl_key_file`: "node_key.pem"

#### Persistence
- `jwt_blacklist_file`: "jwt_blacklist.json"
- `gossip_state_file`: "gossip_state.json"
- `service_state_file`: "service_state.json"
- `state_directory`: "data"

---

### 2. Rate Limiting

Поддержка разных лимитов для разных эндпоинтов.

**Эндпоинты:**
- `/rpc` - 100 req/min (строже)
- `/health` - 300 req/min (послабее)
- `/metrics` - 300 req/min
- Остальные - 200 req/min (по умолчанию)

**Алгоритм:** Token Bucket с поддержкой burst запросов

**При превышении лимита:**
- HTTP 429 Too Many Requests
- Заголовок `Retry-After` с временем ожидания

---

### 3. Adaptive Gossip Interval

Автоматическая регулировка интервала gossip на основе нагрузки.

**Логика:**
- Низкая нагрузка (<1 msg/s) → 5 сек
- Средняя нагрузка (1-5 msg/s) → 5-30 сек (интерполяция)
- Высокая нагрузка (>5 msg/s) → 30 сек

**Адаптация:** каждые 60 секунд, плавное изменение (не более ±20% за раз)

---

### 4. LZ4 Compression для Gossip

Автоматическое сжатие больших gossip сообщений.

**Параметры:**
- Порог сжатия: 1024 байт (по умолчанию)
- Сжимаются только сообщения больше порога
- Автоматическая декомпрессия при получении

**Эффективность:**
- Типичное сжатие: 40-60%
- Снижение трафика между узлами

---

### 5. Единый Method Registry

Исправлена проблема циклических зависимостей.

**Единый источник истины:**
```python
context._method_registry  # ← Единственное место хранения
```

**Функция доступа:**
```python
from layers.service import get_method_registry

registry = get_method_registry()  # Всегда возвращает context._method_registry
```

---

### 6. Persistence для состояний

Автоматическое сохранение и восстановление состояния.

#### JWT Blacklist
```python
from layers.persistence import JWTBlacklistPersistence

persistence = JWTBlacklistPersistence(Path("data/jwt_blacklist.json"))
persistence.load()
persistence.save()
```

#### Gossip State
```python
from layers.persistence import GossipStatePersistence

persistence = GossipStatePersistence(Path("data/gossip_state.json"))
persistence.save_nodes(node_registry)
nodes = persistence.load_nodes()
```

#### Service State
```python
from layers.persistence import ServiceStatePersistence

persistence = ServiceStatePersistence(Path("data/service_state.json"))
persistence.save_services(services)
services = persistence.load_services()
```

**Автосохранение:** каждые 30-60 секунд

---

### 7. HTTPS с самоподписанными сертификатами

#### Генерация сертификатов
```bash
# Автоматическая генерация для coordinator и worker
./scripts/generate_certs.sh
```

**Создаваемые файлы:**
- `coordinator_cert.pem`, `coordinator_key.pem`
- `worker_cert.pem`, `worker_key.pem`

**Срок действия:** 10 лет (3650 дней)

#### Программная генерация
```python
from layers.ssl_helper import generate_self_signed_cert

generate_self_signed_cert(
    "node_cert.pem",
    "node_key.pem",
    common_name="coordinator-1"
)
```

#### Автоматическое создание при запуске
Если сертификаты отсутствуют, они автоматически создаются при старте сервера.

---

## Установка зависимостей

```bash
pip install -r requirements.txt
```

**Новые зависимости:**
- `pyyaml~=6.0.2` - YAML конфигурация
- `lz4~=4.3.3` - LZ4 компрессия
- `cryptography~=44.0.0` - SSL сертификаты

---

## Запуск

### Coordinator
```bash
python p2p.py --config config/coordinator.yaml
```

### Worker
```bash
python p2p.py --config config/worker.yaml
```

---

## Пример полной конфигурации

```yaml
# coordinator.yaml
node_id: "coordinator-1"
port: 8001
bind_address: "0.0.0.0"
coordinator_mode: true

# Adaptive Gossip
gossip_interval_min: 5
gossip_interval_max: 30
gossip_interval_current: 10
gossip_compression_enabled: true
gossip_compression_threshold: 1024

# Rate Limiting
rate_limit_enabled: true
rate_limit_rpc_requests: 200
rate_limit_rpc_burst: 40
rate_limit_health_requests: 600
rate_limit_health_burst: 100

# HTTPS
https_enabled: true
ssl_cert_file: "coordinator_cert.pem"
ssl_key_file: "coordinator_key.pem"

# Persistence
state_directory: "data/coordinator"
jwt_blacklist_file: "jwt_blacklist.json"
gossip_state_file: "gossip_state.json"
service_state_file: "service_state.json"
```

---

## Мониторинг

### Проверка состояния
```bash
curl https://localhost:8001/health
```

### Метрики
```bash
curl https://localhost:8001/metrics
```

### Список узлов кластера
```bash
curl https://localhost:8001/cluster/nodes
```

---

## Производительность

**До рефакторинга:**
- Gossip: фиксированный интервал 10-15 сек
- Трафик: без компрессии
- Rate limiting: отсутствует

**После рефакторинга:**
- Gossip: адаптивный 5-30 сек
- Трафик: сжатие 40-60% (LZ4)
- Rate limiting: защита от перегрузки
- Persistence: сохранение состояния при перезапуске

**Экономия трафика:** до 50% при активном gossip

---

## Безопасность

### JWT Blacklist
- Автоматическая очистка просроченных токенов
- Persistence между перезапусками
- Автосохранение каждые 30 сек

### Rate Limiting
- Защита от DDoS атак
- Разные лимиты для разных типов запросов
- Автоматический Retry-After

### HTTPS
- Самоподписанные сертификаты
- TLS 1.2+ only
- Безопасные cipher suites

---

## Troubleshooting

### Проблема: Сертификаты не генерируются
**Решение:**
```bash
pip install cryptography
./scripts/generate_certs.sh
```

### Проблема: Rate limiting слишком строгий
**Решение:** Увеличьте лимиты в config YAML:
```yaml
rate_limit_rpc_requests: 500  # было 100
rate_limit_rpc_burst: 100      # было 20
```

### Проблема: Gossip слишком частый/редкий
**Решение:** Настройте диапазон:
```yaml
gossip_interval_min: 3   # уменьшить минимум
gossip_interval_max: 60  # увеличить максимум
```

---

## Changelog

### Version 2.0.0 - Refactoring

**Added:**
- YAML конфигурация
- Adaptive Gossip Interval (5-30 сек)
- LZ4 compression для gossip
- Rate Limiting по эндпоинтам
- Persistence для всех состояний
- HTTPS с самоподписанными сертификатами

**Fixed:**
- Циклические зависимости в method_registry
- Дублирование реестра методов

**Improved:**
- Централизованная конфигурация
- Производительность gossip протокола
- Безопасность JWT аутентификации

---

## Контакты

По вопросам и предложениям создавайте issues в репозитории.
