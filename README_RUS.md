# P2P Core - Распределенная система управления сервисами

Enterprise-решение для peer-to-peer администрирования и оркестрации сервисов в распределенной среде.

## Описание

P2P Core - это production-ready распределенная асинхронная система для управления и оркестрации сервисов на множественных узлах. Построена на Python и FastAPI, предоставляет надежную основу для создания масштабируемых микросервисных архитектур со встроенным service discovery, балансировкой нагрузки и отказоустойчивостью.

## Ключевые возможности

- **Распределенная архитектура**: Топология координатор-воркер с автоматическим failover
- **Service Discovery**: Gossip-протокол для динамического обнаружения узлов
- **Балансировка нагрузки**: Интеллектуальная маршрутизация с учетом состояния узлов
- **Многоуровневое кеширование**: Redis + in-memory кеширование с автоинвалидацией
- **Service Framework**: Плагинная архитектура сервисов с управлением жизненным циклом
- **Administrative API**: RESTful API для управления кластером и мониторинга
- **Graceful Shutdown**: Корректное управление жизненным циклом компонентов
- **Мониторинг здоровья**: Встроенные health checks и сбор метрик

## Архитектура

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Координатор   │    │     Воркер      │    │     Воркер      │
│    (Узел 1)     │    │    (Узел 2)     │    │    (Узел 3)     │
├─────────────────┤    ├─────────────────┤    ├─────────────────┤
│ Сервисный слой  │    │ Сервисный слой  │    │ Сервисный слой  │
│ Сетевой слой    │◄──►│ Сетевой слой    │◄──►│ Сетевой слой    │
│Транспортный слой│    │Транспортный слой│    │Транспортный слой│
│ Слой кеширования│    │ Слой кеширования│    │ Слой кеширования│
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

### Стек компонентов

1. **Application Context** - Централизованное управление жизненным циклом и зависимостями
2. **Service Layer** - FastAPI endpoints и RPC обработчики
3. **Network Layer** - Gossip протокол и управление кластером
4. **Transport Layer** - Оптимизированные HTTP/2 коммуникации
5. **Cache Layer** - Многоуровневое кеширование с Redis fallback

## Быстрый старт

### Предварительные требования

```bash
# Python 3.7+
python --version

# Необходимые пакеты
pip install fastapi uvicorn httpx psutil cachetools pydantic PyJWT aioredis
```

### Базовая настройка

1. **Запуск координатора:**
```bash
python p2p.py coordinator --port 8001 --verbose
```

2. **Запуск воркеров:**
```bash
python p2p.py worker --port 8002 --coord 127.0.0.1:8001 --verbose
python p2p.py worker --port 8003 --coord 127.0.0.1:8001 --verbose
```

3. **Проверка статуса кластера:**
```bash
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8001/cluster/status
```

### Документация API

После запуска доступно:
- **API Docs**: http://127.0.0.1:8001/docs
- **Health Check**: http://127.0.0.1:8001/health
- **Статус кластера**: http://127.0.0.1:8001/cluster/status

## Конфигурация

### Параметры командной строки

```bash
python p2p.py [coordinator|worker] [опции]

Опции:
  --node-id TEXT        Идентификатор узла (автогенерация если не указан)
  --port INTEGER        Порт HTTP сервера (8001 для координатора, 8002+ для воркеров)
  --address TEXT        Адрес привязки (по умолчанию: 127.0.0.1)
  --coord TEXT          Адрес координатора для воркеров (по умолчанию: 127.0.0.1:8001)
  --redis-url TEXT      URL Redis для кеширования (по умолчанию: redis://localhost:6379)
  --verbose, -v         Включить отладочное логирование
```

### Переменные окружения

```bash
export P2P_REDIS_URL="redis://localhost:6379"
export P2P_JWT_SECRET="ваш-production-секретный-ключ"
export P2P_LOG_LEVEL="INFO"
```

## Разработка сервисов

### Создание нового сервиса

1. **Создание директории сервиса:**
```bash
mkdir services/my_service
```

2. **Реализация класса сервиса:**
```python
# services/my_service/main.py
from layers.service_framework import BaseService, service_method

class Run(BaseService):
    SERVICE_NAME = "my_service"
    
    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.info.version = "1.0.0"
        self.info.description = "Мой пользовательский сервис"
    
    async def initialize(self):
        # Асинхронная логика инициализации
        self.logger.info("Сервис инициализирован")
    
    async def cleanup(self):
        # Очистка ресурсов
        self.logger.info("Сервис очищен")
    
    @service_method(description="Hello world endpoint", public=True)
    async def hello(self, name: str = "Мир") -> dict:
        return {
            "message": f"Привет, {name}!",
            "service": self.service_name,
            "timestamp": datetime.now().isoformat()
        }
```

3. **Автоматическое обнаружение сервисов:**
Сервисы автоматически обнаруживаются и загружаются из директории `services/`.

### Межсервисное взаимодействие

```python
@service_method(description="Вызов другого сервиса", public=True)
async def call_other_service(self) -> dict:
    if self.proxy:
        # Вызов метода другого сервиса
        result = await self.proxy.other_service.some_method(param="значение")
        return {"result": result}
    return {"error": "Прокси недоступен"}
```

## Справочник API

### Аутентификация

Все endpoints требуют JWT аутентификации:

```bash
# Получение токена
curl -X POST http://127.0.0.1:8001/auth/token \
     -H "Content-Type: application/json" \
     -d '{"node_id": "client-1"}'

# Использование токена
curl -H "Authorization: Bearer ВАШ_ТОКЕН" \
     http://127.0.0.1:8001/cluster/nodes
```

### Основные endpoints

| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/health` | GET | Проверка здоровья узла |
| `/cluster/status` | GET | Детальный статус кластера |
| `/cluster/nodes` | GET | Список всех узлов кластера |
| `/local/services` | GET | Список локальных сервисов |
| `/rpc/{service}/{method}` | POST | Вызов метода сервиса |
| `/admin/broadcast` | POST | Широковещательный RPC ко всем узлам |

### Пример RPC вызова

```bash
curl -X POST http://127.0.0.1:8001/rpc/system/get_system_info \
     -H "Authorization: Bearer ВАШ_ТОКЕН" \
     -H "Content-Type: application/json" \
     -d '{
       "method": "get_system_info",
       "params": {},
       "id": "req-123"
     }'
```

## Мониторинг и наблюдаемость

### Встроенные метрики

- **Здоровье кластера**: Статус узлов, gossip метрики
- **Статистика запросов**: Процент успешных, задержки, количество ошибок
- **Производительность кеша**: Hit rates, события инвалидации
- **Метрики сервисов**: Количество вызовов методов, время ответа

### Health Checks

```bash
# Здоровье системы
curl http://127.0.0.1:8001/health

# Детальный статус
curl http://127.0.0.1:8001/cluster/status

# Health check конкретного сервиса
curl -X POST http://127.0.0.1:8001/rpc/system/health_check \
     -H "Authorization: Bearer ТОКЕН" \
     -d '{"method": "health_check", "params": {}, "id": "1"}'
```

## Production развертывание

### Соображения безопасности

1. **Смена JWT секрета:**
```python
# В production
JWT_SECRET_KEY = os.environ.get("P2P_JWT_SECRET", "ваш-безопасный-случайный-ключ")
```

2. **Сетевая безопасность:**
- Использовать TLS для межузлового общения
- Правильно настроить firewall для портов координатора
- Реализовать правильную сегментацию сети

3. **Аутентификация:**
- Реализовать правильную аутентификацию узлов
- Использовать сильные JWT секреты
- Рассмотреть certificate-based auth для production

### Настройка высокой доступности

```bash
# Несколько координаторов для HA
python p2p.py coordinator --port 8001 --node-id coord-1
python p2p.py coordinator --port 8002 --node-id coord-2

# Воркеры подключаются к нескольким координаторам
python p2p.py worker --coord 127.0.0.1:8001,127.0.0.1:8002
```

### Redis кластеризация

```python
# Настройка Redis кластера
CACHE_CONFIG = {
    "redis_url": "redis://redis-cluster:6379",
    "redis_enabled": True,
    "cluster_mode": True
}
```

## Решение проблем

### Частые проблемы

**1. Connection Refused**
```bash
# Проверить работает ли координатор
curl http://127.0.0.1:8001/health

# Проверить сетевое подключение
telnet 127.0.0.1 8001
```

**2. Проблемы Service Discovery**
```bash
# Проверить статус gossip
curl http://127.0.0.1:8001/cluster/nodes

# Проверить адреса координаторов
python p2p.py worker --coord 127.0.0.1:8001 --verbose
```

**3. Проблемы с кешем**
```bash
# Тест подключения к Redis
redis-cli ping

# Проверить статус кеша в логах
python p2p.py coordinator --verbose
```

### Режим отладки

```bash
# Включить подробное логирование
python p2p.py coordinator --verbose

# Проверить статус компонентов
curl http://127.0.0.1:8001/debug/registry
```

### Анализ логов

```bash
# Мониторинг логов
tail -f logs/p2p.log

# Фильтр по ошибкам
grep ERROR logs/p2p.log

# Проверка регистрации сервисов
grep "registered" logs/p2p.log
```

## Разработка

### Структура проекта

```
P2P_Core/
├── p2p.py                        # Основная точка входа
├── layers/
│   ├── application_context.py    # Управление жизненным циклом приложения
│   ├── transport.py              # HTTP транспортный слой
│   ├── network.py                # Gossip протокол и сети
│   ├── service.py                # Сервисный слой и RPC обработка
│   ├── service_framework.py      # Фреймворк разработки сервисов
│   ├── local_service_bridge.py   # Интеграция локальных сервисов
│   └── cache.py                  # Многоуровневое кеширование
├── methods/
│   └── system.py                 # Встроенные системные методы
├── services/                     # Пользовательские сервисы (автообнаружение)
│   └── example_service/
│       └── main.py
└── docs/                         # Документация
```


### Вклад в разработку

1. Сделайте fork репозитория
2. Создайте feature ветку (`git checkout -b feature/amazing-feature`)
3. Напишите тесты для ваших изменений
4. Убедитесь что все тесты проходят (`python -m pytest`)
5. Закоммитьте изменения (`git commit -m 'Добавить удивительную фичу'`)
6. Запушьте в ветку (`git push origin feature/amazing-feature`)
7. Откройте Pull Request

## Производительность

### Бенчмарки

- **Пропускная способность**: 1000+ RPC вызовов/секунду на узел
- **Задержка**: <50мс для вызовов локальных сервисов
- **Масштабирование**: Протестировано до 100 узлов в production
- **Память**: ~50МБ базовое потребление памяти на узел


## Дорожная карта

- [ ] в gossip добавить метрики
- [ ] оркестрация сервисов
- [ ] crypto-persistent storage
- [ ] Web UI дашборд
- [ ] mqtt управление (интеграция с hassio)

---

**Построено с любовью на Python 3.7+ • FastAPI • Redis • asyncio**