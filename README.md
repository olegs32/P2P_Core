# P2P Core

Enterprise распределенная система для управления сервисами с автоматической оркестрацией, service discovery и встроенной Certificate Authority.

## Ключевые возможности

- **Распределенная архитектура** - топология координатор-воркер с автоматическим failover
- **Gossip протокол** - адаптивный интервал (5-30 сек), LZ4 компрессия для снижения трафика на 40-60%
- **Service Discovery** - автоматическое обнаружение узлов и сервисов
- **ACME-подобная CA инфраструктура** - автоматическая генерация и обновление SSL сертификатов
- **Mutual TLS** - безопасная коммуникация с CA верификацией между узлами
- **Rate Limiting** - защита от перегрузки с Token Bucket алгоритмом
- **Многоуровневое кеширование** - Redis + in-memory с автоинвалидацией
- **Плагинная архитектура** - автоматическое обнаружение и загрузка сервисов
- **Persistence** - сохранение состояния (JWT blacklist, gossip, services)
- **YAML конфигурация** - централизованное управление настройками

## Архитектура

```
┌─────────────────────────────────────────────────────────────┐
│                    Application Context                      │
│         (Lifecycle Management & Dependencies)                │
└─────────────────────────────────────────────────────────────┘
         │                  │                  │
         ▼                  ▼                  ▼
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│  Transport  │   │   Network   │   │   Service   │
│   Layer     │◄─►│   Layer     │◄─►│   Layer     │
│  (HTTP/2)   │   │  (Gossip)   │   │ (RPC/REST)  │
└─────────────┘   └─────────────┘   └─────────────┘
         │                  │                  │
         └──────────────────┴──────────────────┘
                           │
                    ┌──────┴──────┐
                    │    Cache    │
                    │ Redis/Memory│
                    └─────────────┘
```

## Быстрый старт

### Установка

```bash
pip install -r requirements.txt
```

**Зависимости:**
- Python 3.7+
- FastAPI, uvicorn, httpx
- cryptography (для SSL/TLS)
- lz4 (для компрессии)
- pyyaml (для конфигурации)
- redis (опционально)

### Генерация CA и сертификатов

```bash
# Автоматическая генерация CA и сертификатов для всех узлов
./scripts/generate_ca_certs.sh
```

**Создаваемые файлы:**
```
certs/
├── ca_cert.cer           # CA сертификат (10 лет)
├── ca_key.key            # CA приватный ключ
├── coordinator_cert.cer  # Сертификат координатора (1 год)
├── coordinator_key.key
├── worker_cert.cer       # Сертификат воркера (1 год)
└── worker_key.key
```

### Запуск кластера

#### Координатор
```bash
python p2p.py --config config/coordinator.yaml
```

#### Воркер
```bash
python p2p.py --config config/worker.yaml
```

### Автоматическое получение сертификата

Воркеры автоматически запрашивают сертификаты от координатора при:
- Отсутствии сертификата
- Изменении IP адреса
- Изменении DNS имени
- Истечении срока (за 30 дней)

**Процесс (ACME-подобный):**
1. Воркер запускает временный HTTP сервер на порту 8802
2. Отправляет запрос с challenge на координатор (HTTPS)
3. Координатор валидирует challenge через HTTP callback
4. Генерирует CA-подписанный сертификат
5. Воркер получает и сохраняет сертификат
6. Запускает HTTPS сервер на основном порту

## Конфигурация (YAML)

### Пример: coordinator.yaml

```yaml
node_id: "coordinator-1"
port: 8001
bind_address: "0.0.0.0"
coordinator_mode: true
coordinator_addresses: []

# Adaptive Gossip
gossip_interval_min: 5
gossip_interval_max: 30
gossip_compression_enabled: true
gossip_compression_threshold: 1024

# Rate Limiting
rate_limit_enabled: true
rate_limit_rpc_requests: 100
rate_limit_rpc_burst: 20
rate_limit_health_requests: 300
rate_limit_health_burst: 50

# HTTPS с CA
https_enabled: true
ssl_cert_file: "certs/coordinator_cert.cer"
ssl_key_file: "certs/coordinator_key.key"
ssl_ca_cert_file: "certs/ca_cert.cer"
ssl_ca_key_file: "certs/ca_key.key"
ssl_verify: true

# Persistence
state_directory: "data/coordinator"
jwt_blacklist_file: "jwt_blacklist.json"
gossip_state_file: "gossip_state.json"
service_state_file: "service_state.json"

# Redis Cache
redis_enabled: false
redis_url: "redis://localhost:6379"
```

### Пример: worker.yaml

```yaml
node_id: "worker-1"
port: 8002
coordinator_mode: false
coordinator_addresses:
  - "192.168.1.100:8001"  # Адрес координатора для запроса сертификата

# SSL (воркеру НЕ нужен CA key)
https_enabled: true
ssl_cert_file: "certs/worker_cert.cer"
ssl_key_file: "certs/worker_key.key"
ssl_ca_cert_file: "certs/ca_cert.cer"
ssl_ca_key_file: ""  # Пусто - будет запрошен от координатора
ssl_verify: true

state_directory: "data/worker"
```

## API Endpoints

### Аутентификация
```bash
curl -X POST https://localhost:8001/auth/token \
  -H "Content-Type: application/json" \
  -d '{"node_id": "client-1"}'
```

### Основные endpoints

| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/health` | GET | Статус узла |
| `/cluster/status` | GET | Статус кластера |
| `/cluster/nodes` | GET | Список узлов |
| `/services` | GET | Список сервисов |
| `/rpc/{service}/{method}` | POST | RPC вызов метода |
| `/admin/broadcast` | POST | Broadcast RPC |

### RPC вызов
```bash
curl -X POST https://localhost:8001/rpc/system/get_system_info \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "method": "get_system_info",
    "params": {},
    "id": "req-1"
  }'
```

## Разработка сервисов

### Структура сервиса
```
services/my_service/
├── main.py          # Класс Run(BaseService)
└── requirements.txt # Опционально
```

### Базовый шаблон

```python
from layers.service import BaseService, service_method
from datetime import datetime

class Run(BaseService):
    SERVICE_NAME = "my_service"

    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.info.version = "1.0.0"
        self.info.description = "My custom service"

    async def initialize(self):
        self.logger.info("Service initialized")

    async def cleanup(self):
        self.logger.info("Service cleanup")

    @service_method(description="Hello world", public=True)
    async def hello(self, name: str = "World") -> dict:
        # Вызов другого сервиса через прокси
        system_info = await self.proxy.system.get_system_info()

        return {
            "message": f"Hello, {name}!",
            "service": self.service_name,
            "node": system_info.get("hostname"),
            "timestamp": datetime.now().isoformat()
        }
```

**Автообнаружение:** Сервисы автоматически загружаются из `services/` каждые 60 секунд.

## Мониторинг

### Health check
```bash
curl https://localhost:8001/health
```

### Метрики кластера
```bash
curl https://localhost:8001/cluster/status \
  -H "Authorization: Bearer TOKEN"
```

### Список узлов
```bash
curl https://localhost:8001/cluster/nodes \
  -H "Authorization: Bearer TOKEN"
```

## Production рекомендации

### Безопасность
- ✅ Используйте сильные JWT секреты (`P2P_JWT_SECRET`)
- ✅ Распространите CA сертификат на все узлы
- ✅ Храните CA приватный ключ в безопасности
- ✅ Включите `ssl_verify: true` для mutual TLS
- ✅ Настройте firewall правила

### Высокая доступность
- Несколько координаторов для failover
- Redis кластер для распределенного кеша
- Мониторинг узлов через gossip health checks

### Производительность
- **Gossip:** адаптивный интервал 5-30 сек (снижение нагрузки при high load)
- **Компрессия:** LZ4 для gossip (экономия трафика 40-60%)
- **Rate Limiting:** защита от DDoS (настраиваемые лимиты)
- **Локальные вызовы:** прямой доступ через method_registry (<1ms vs 10-50ms RPC)

## Troubleshooting

### Проблема: Воркер не получает сертификат

**Проверить:**
1. Координатор запущен и доступен по HTTPS
2. `coordinator_addresses` указан в конфигурации воркера
3. CA сертификат существует на воркере
4. Порт 8802 доступен для HTTP (временный сервер)

**Логи:**
```
INFO | Starting temporary HTTP server on port 8802 for validation...
INFO | Certificate request response: 200
INFO | Certificate successfully updated from coordinator
INFO | Temporary HTTP server stopped successfully
```

### Проблема: SSL verification failed

**Решение:**
```bash
# Проверить срок действия
openssl x509 -in certs/ca_cert.cer -noout -dates
openssl x509 -in certs/worker_cert.cer -noout -dates

# Перегенерировать если истек
rm -rf certs/
./scripts/generate_ca_certs.sh
```

### Проблема: IP address mismatch

Сертификат не содержит текущий IP - требуется обновление.
Воркер автоматически запросит новый сертификат при следующем запуске.

### Проблема: Rate limiting слишком строгий

Увеличить лимиты в YAML:
```yaml
rate_limit_rpc_requests: 500   # Было 100
rate_limit_rpc_burst: 100       # Было 20
```

## Структура проекта

```
P2P_Core/
├── p2p.py                        # Точка входа
├── layers/
│   ├── application_context.py    # Управление жизненным циклом
│   ├── transport.py              # HTTP/2 транспорт
│   ├── network.py                # Gossip протокол
│   ├── service.py                # Service layer & RPC
│   ├── cache.py                  # Multi-level cache
│   ├── ssl_helper.py             # Certificate management
│   ├── persistence.py            # State persistence
│   └── local_service_bridge.py   # Local method calls
├── methods/
│   └── system.py                 # Built-in system methods
├── services/                     # Custom services (auto-discovery)
├── config/                       # YAML configurations
├── certs/                        # SSL certificates
├── data/                         # Persistent state
└── docs/                         # Documentation
    └── cert-auto-renewal.md      # Certificate automation guide
```

## Changelog

### v2.1.0 - ACME-like Certificate Automation
- ✅ Автоматическая генерация сертификатов через координатор
- ✅ ACME-подобная challenge валидация
- ✅ Автообновление при изменении IP/DNS
- ✅ Временный HTTP сервер на порту 8802

### v2.0.0 - Major Refactoring
- ✅ YAML конфигурация
- ✅ Adaptive Gossip (5-30 сек)
- ✅ LZ4 compression для gossip
- ✅ Rate Limiting с Token Bucket
- ✅ Persistence для всех состояний
- ✅ Certificate Authority инфраструктура
- ✅ Mutual TLS с CA верификацией
- ✅ Application Context архитектура

---

**Built with Python 3.7+ • FastAPI • Redis • asyncio • cryptography**

Документация: `docs/` | Вопросы: GitHub Issues
