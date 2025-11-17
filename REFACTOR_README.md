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

#### HTTPS/SSL с Certificate Authority
- `https_enabled`: true
- `ssl_cert_file`: "certs/node_cert.pem"
- `ssl_key_file`: "certs/node_key.pem"
- `ssl_ca_cert_file`: "certs/ca_cert.pem" (CA сертификат для верификации)
- `ssl_ca_key_file`: "certs/ca_key.pem" (CA ключ, только для coordinator)
- `ssl_verify`: true (включает проверку сертификатов через CA)

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

### 7. Secure Encrypted Storage (NEW in v2.2.0)

Все сертификаты и конфигурации хранятся в зашифрованном архиве.

#### Шифрование
- **Алгоритм**: AES-256-GCM (authenticated encryption)
- **Key Derivation**: PBKDF2-HMAC-SHA256 (100,000 итераций)
- **Salt**: Случайный 16-байтовый salt для каждого файла
- **Nonce**: Случайный 12-байтовый nonce для каждого шифрования

#### Файл хранилища
`.p2p_secure_storage` - зашифрованный ZIP-архив в корне проекта

**Что хранится**:
- SSL/TLS сертификаты (CA, node certificates)
- Приватные ключи
- YAML конфигурации
- JWT blacklist
- Gossip state
- Service state

#### Использование

```python
from layers.storage_manager import get_storage_manager

# Get storage manager from context
storage = context.get_shared("storage_manager")

# Read certificate
cert_data = storage.read_cert("ca_cert.cer")

# Write certificate
storage.write_cert("node_cert.cer", cert_bytes)

# Read config
config_yaml = storage.read_config("coordinator.yaml")

# Save all changes
storage.save()
```

**ВАЖНО**:
- Никогда не записывайте sensitive data напрямую в файловую систему
- Всегда используйте storage_manager для сертификатов и ключей
- Storage автоматически сохраняется при shutdown
- Установите надежный пароль через переменную окружения

### 8. WebSocket Real-Time Updates (NEW in v2.2.0)

Замена HTTP polling на WebSocket push для мгновенных обновлений.

**Endpoint**: `wss://coordinator:port/ws/dashboard`

**Протокол**:
- Client отправляет ping каждые 4 секунды
- Server отвечает pong + обновление метрик и истории
- Event-driven доставка логов (< 100ms)

**Преимущества**:
- ✅ Латентность < 100ms (vs 5s при polling)
- ✅ Снижение нагрузки на сервер на 90%
- ✅ Bidirectional communication
- ✅ Automatic reconnection
- ✅ Event-driven architecture

**Использование**:
```javascript
const ws = new WebSocket('wss://coordinator:8001/ws/dashboard');

// Ping every 4 seconds
setInterval(() => ws.send('ping'), 4000);

ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);

    if (msg.type === 'update') {
        updateMetrics(msg.data.metrics);
        updateCharts(msg.data.history);
    }

    if (msg.type === 'new_logs') {
        appendLogs(msg.logs);  // < 100ms delivery
    }
};
```

### 9. Event-Driven Log Collection (NEW in v2.2.0)

Централизованный сбор логов с немедленной доставкой.

**Архитектура**:
1. `P2PLogHandler` на воркере перехватывает логи
2. Immediate callback через `asyncio.create_task()` (< 100ms)
3. `LogCollector` на координаторе через publish-subscribe
4. `metrics_dashboard` транслирует через WebSocket
5. Browser получает логи мгновенно (< 100ms)

**Фильтрация**:
- По node_id
- По log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- По logger_name (Service.*, Gossip, Network, etc.)

**API**:
```python
# Get logs with filtering
logs = await proxy.log_collector.get_logs(
    node_id="worker-1",
    level="ERROR",
    logger_name="Service.my_service",
    limit=100
)

# Get log sources
sources = await proxy.log_collector.get_log_sources()
# Returns: {"nodes": [...], "loggers": [...], "log_levels": [...]}

# Clear logs
await proxy.log_collector.clear_logs(node_id="worker-1")
```

### 10. Multi-Homed Node Support (NEW in v2.2.0)

Автоматическое определение оптимального IP для сложных сетевых топологий.

**Features**:
- **Address Probing**: Тестирует connectivity на каждом interface
- **Smart Selection**: Выбирает IP based on subnet proximity
- **VPN-Aware**: Детектирует VPN interfaces и приоритизирует их when appropriate
- **Automatic Failover**: Переключается на другой interface при изменении топологии

**Scenario Example**:
```
Node interfaces:
- 192.168.1.100 (LAN)
- 10.8.0.5 (VPN)
- 127.0.0.1 (loopback)

Coordinator at 10.8.0.1 (VPN):
→ Selects 10.8.0.5 (same subnet)

Coordinator at 192.168.1.50 (LAN):
→ Selects 192.168.1.100 (same subnet)
```

**Configuration**:
```yaml
enable_address_probing: true  # Default
probe_timeout_seconds: 2.0
bind_address: "0.0.0.0"  # Auto-detect, or force specific IP
```

### 11. HTTPS с Certificate Authority (CA)

Система использует полноценную инфраструктуру CA для безопасной коммуникации между узлами.

#### Генерация CA и сертификатов узлов

**Автоматическая генерация:**
```bash
# Генерирует CA и подписанные сертификаты для всех узлов
./scripts/generate_ca_certs.sh
```

**Создаваемые файлы:**
```
certs/
├── ca_cert.pem           # CA сертификат (распространять на все узлы)
├── ca_key.pem            # CA приватный ключ (ХРАНИТЬ В БЕЗОПАСНОСТИ!)
├── coordinator_cert.pem  # Сертификат координатора (подписан CA)
├── coordinator_key.pem   # Приватный ключ координатора
├── worker_cert.pem       # Сертификат worker (подписан CA)
└── worker_key.pem        # Приватный ключ worker
```

**Срок действия:**
- CA сертификат: 10 лет (3650 дней)
- Сертификаты узлов: 1 год (365 дней)

#### Программная генерация CA

```python
from layers.ssl_helper import generate_ca_certificate

# Генерация CA (выполняется один раз)
generate_ca_certificate(
    ca_cert_file="certs/ca_cert.pem",
    ca_key_file="certs/ca_key.pem",
    common_name="P2P Network CA"
)
```

#### Генерация сертификата узла, подписанного CA

```python
from layers.ssl_helper import generate_signed_certificate

# Генерация сертификата для узла
generate_signed_certificate(
    cert_file="certs/node_cert.pem",
    key_file="certs/node_key.pem",
    ca_cert_file="certs/ca_cert.pem",
    ca_key_file="certs/ca_key.pem",
    common_name="worker-1",
    san_dns=["localhost", "*.local", "worker-1"],
    san_ips=["127.0.0.1"]
)
```

#### Автоматическое создание при запуске
Если CA существует, но сертификаты узла отсутствуют, они автоматически создаются и подписываются CA при старте сервера.

#### SSL Verification
Система поддерживает проверку сертификатов через CA:
- **Coordinator**: проверяет клиентские сертификаты worker узлов
- **Worker**: проверяет сертификат coordinator при подключении
- **Включение**: `ssl_verify: true` в конфигурации

**Важно:**
- CA сертификат (`ca_cert.pem`) должен быть распространен на все узлы
- CA приватный ключ (`ca_key.pem`) храните в безопасности и используйте только для подписи новых сертификатов
- Worker узлам НЕ нужен CA приватный ключ - только CA сертификат для верификации

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

### Новый способ (YAML конфигурация)

#### Coordinator
```bash
python p2p.py --config config/coordinator.yaml
```

#### Worker
```bash
python p2p.py --config config/worker.yaml
```

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

# HTTPS с CA
https_enabled: true
ssl_cert_file: "certs/coordinator_cert.pem"
ssl_key_file: "certs/coordinator_key.pem"
ssl_ca_cert_file: "certs/ca_cert.pem"      # CA сертификат для верификации
ssl_ca_key_file: "certs/ca_key.pem"        # CA ключ для подписи (только coordinator)
ssl_verify: true                            # Включена проверка сертификатов

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

### HTTPS с Certificate Authority
- Полноценная инфраструктура CA с подписанными сертификатами
- SSL/TLS верификация между узлами (mutual TLS)
- TLS 1.2+ only
- Безопасные cipher suites
- Автоматическая проверка сертификатов через CA

---

## Troubleshooting

### Проблема: Сертификаты не генерируются
**Решение:**
```bash
pip install cryptography
./scripts/generate_ca_certs.sh
```

### Проблема: SSL verification fails
**Причина:** CA сертификат не распространен на все узлы или истек срок действия

**Решение:**
1. Убедитесь, что `ca_cert.pem` присутствует на всех узлах
2. Проверьте срок действия сертификатов:
```bash
openssl x509 -in certs/ca_cert.pem -noout -dates
openssl x509 -in certs/worker_cert.pem -noout -dates
```
3. При необходимости перегенерируйте сертификаты:
```bash
rm -rf certs/
./scripts/generate_ca_certs.sh
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

### Version 2.2.0 - Real-Time Updates & Enhanced Security (2025-11-17)

**Added:**
- **WebSocket Real-Time Updates** - мгновенные обновления dashboard (< 100ms вместо 5s polling)
- **Event-Driven Log Streaming** - немедленная доставка логов через publish-subscribe (< 100ms)
- **Secure Encrypted Storage** - AES-256-GCM шифрование для сертификатов и конфигураций
- **Multi-Homed Node Support** - автоматический выбор оптимального IP (VPN-aware, subnet detection)
- **Centralized Log Collection** - встроенный syslog-подобный функционал с фильтрацией
- **Enhanced Dashboard** - вкладка Logs с real-time обновлениями и поиском
- **Metrics History via WebSocket** - графики обновляются в реальном времени

**Improved:**
- Снижение нагрузки на сервер на 90% (WebSocket push vs HTTP polling)
- Латентность обновлений снижена с 5s до < 100ms
- Безопасность хранения sensitive data (все в encrypted storage)
- Поддержка сложных сетевых топологий (VPN, multi-NIC, DMZ)

### Version 2.1.0 - ACME-like Certificate Automation

**Added:**
- Автоматическая генерация сертификатов через координатор
- ACME-подобная challenge валидация
- Автообновление при изменении IP/DNS
- Временный HTTP сервер для валидации

### Version 2.0.0 - Major Refactoring

**Added:**
- YAML конфигурация
- Adaptive Gossip Interval (5-30 сек)
- LZ4 compression для gossip
- Rate Limiting по эндпоинтам
- Persistence для всех состояний
- HTTPS с полноценной инфраструктурой Certificate Authority (CA)
- SSL/TLS верификация между узлами с CA
- Автоматическая генерация и подпись сертификатов через CA

**Fixed:**
- Циклические зависимости в method_registry
- Дублирование реестра методов

**Improved:**
- Централизованная конфигурация
- Производительность gossip протокола
- Безопасность JWT аутентификации
- Безопасность межузловой коммуникации (mutual TLS с CA)

---

## Контакты

По вопросам и предложениям создавайте issues в репозитории.
