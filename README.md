# P2P Core

Enterprise распределенная система для управления сервисами с автоматической оркестрацией, service discovery и встроенной Certificate Authority.

## Ключевые возможности

- **Распределенная архитектура** - топология координатор-воркер с автоматическим failover
- **Gossip протокол** - адаптивный интервал (5-30 сек), LZ4 компрессия для снижения трафика на 40-60%
- **Service Discovery** - автоматическое обнаружение узлов и сервисов
- **ACME-подобная CA инфраструктура** - автоматическая генерация и обновление SSL сертификатов
- **Mutual TLS** - безопасная коммуникация с CA верификацией между узлами
- **Secure Storage** - шифрованное хранилище (AES-256-GCM) для сертификатов и конфигураций
- **WebSocket Real-Time Updates** - мгновенные обновления метрик и логов (< 100ms)
- **Event-Driven Log Collection** - централизованный сбор логов с немедленной доставкой
- **Multi-Homed Node Support** - автоматическое определение лучшего IP для сложных сетевых топологий (VPN, multi-NIC)
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


### Запуск

#### Координатор
```bash
python p2p.py --config config/coordinator.yaml --password some_password
```

#### Воркер
```bash
python p2p.py --config config/coordinator.yaml --password some_password --coordinator IP_COORD_OR_127.0.0.1_if_local
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



### Web Dashboard с real-time updates
```
https://coordinator:8001/dashboard
```

**Возможности:**
- Real-time метрики координатора и воркеров (WebSocket push, < 100ms)
- Графики с историей (последние 100 точек)
- Централизованный просмотр логов с фильтрацией
- Event-driven логи - обновления мгновенно
- Управление сервисами (start/stop/restart)


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

### v2.2.0 - Real-Time Updates & Enhanced Security (2025-11-17)
- ✅ **WebSocket Real-Time Updates** - мгновенные обновления dashboard (< 100ms вместо 5s polling)
- ✅ **Event-Driven Log Streaming** - немедленная доставка логов через publish-subscribe
- ✅ **Secure Encrypted Storage** - AES-256-GCM для сертификатов и конфигураций
- ✅ **Multi-Homed Node Support** - автоматический выбор оптимального IP (VPN-aware, subnet detection)
- ✅ **Centralized Log Collection** - встроенный syslog-подобный функционал с фильтрацией
- ✅ **Enhanced Dashboard** - вкладка Logs с real-time обновлениями и поиском
- ✅ **Metrics History via WebSocket** - графики обновляются в реальном времени

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

**Built with _Love_ Python 3.7+ • FastAPI • Redis • asyncio • cryptography**

Документация: `docs/` | Вопросы: GitHub Issues
