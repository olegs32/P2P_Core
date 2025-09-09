# P2P Administrative System - Техническая документация

## Обзор

P2P система для администрирования распределенных сервисов с автоматической регистрацией методов и локально-оптимизированными вызовами.

**Ключевые возможности:**
- Автоматическое обнаружение и регистрация сервисов
- Локальные вызовы без сетевых запросов (через method_registry)
- Удаленные вызовы через P2P сеть
- Универсальный прокси для межсервисного взаимодействия
- REST API для внешнего доступа

## Архитектура системы

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   P2P Core      │    │ Service Layer   │    │   Services      │
│  (p2p_admin)    │◄──►│ (service.py)    │◄──►│ (services/*.py) │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Network Layer  │    │ Method Registry │    │  Local Proxy    │
│   (gossip)      │    │ (RPC methods)   │    │ (direct calls)  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

---

## Цепочка вызова метода через прокси

### Детальная трассировка вызова `await self.proxy.system.get_system_metrics()`

#### 1. Точка входа - Сервис
```python
# В сервисе
metrics = await self.proxy.system.get_system_metrics()
```

#### 2. SimpleLocalProxy.__getattr__("system")
```python
# local_service_bridge.py:48
def __getattr__(self, service_name: str):
    return ServiceMethodProxy(service_name, self.method_registry)
```
**Результат:** Создается `ServiceMethodProxy(service_name="system")`

#### 3. ServiceMethodProxy.__getattr__("get_system_metrics")
```python
# local_service_bridge.py:78
def __getattr__(self, attr_name: str):
    # attr_name = "get_system_metrics"
    known_targets = ['coordinator', 'worker', ...]
    
    if attr_name in known_targets:
        # Создать таргетированный прокси
        return ServiceMethodProxy(target_node=attr_name)
    else:
        # Создать callable для метода
        return MethodCaller(
            service_name=self.service_name,    # "system"
            method_name=attr_name,             # "get_system_metrics"
            method_registry=self.method_registry,
            target_node=self.target_node       # None
        )
```
**Результат:** Создается `MethodCaller(service="system", method="get_system_metrics")`

#### 4. MethodCaller.__call__(**kwargs)
```python
# local_service_bridge.py:95
async def __call__(self, **kwargs):
    method_path = f"{self.service_name}/{self.method_name}"  # "system/get_system_metrics"
    
    # Проверка наличия в реестре
    if method_path not in self.method_registry:
        raise RuntimeError(f"Method {method_path} not found in local registry")
    
    # Логирование
    if self.target_node:
        self.logger.debug(f"Targeted call to {self.target_node}: {method_path}")
    else:
        self.logger.debug(f"Local call: {method_path}")  # ← Этот путь
    
    # ПРЯМОЙ ВЫЗОВ метода из реестра
    method = self.method_registry[method_path]  # Получаем bound method
    return await method(**kwargs)               # Выполняем локально
```

#### 5. Выполнение зарегистрированного метода
```python
# method_registry содержит:
# "system/get_system_metrics" -> SystemMethods.get_system_metrics (bound method)

# Прямой вызов bound method БЕЗ сетевых запросов:
result = await SystemMethods_instance.get_system_metrics()
```

### Альтернативный путь: Таргетированный вызов

#### Для `await self.proxy.system.coordinator.get_system_metrics()`:

1. **SimpleLocalProxy.__getattr__("system")** → ServiceMethodProxy("system")
2. **ServiceMethodProxy.__getattr__("coordinator")** → ServiceMethodProxy("system", target_node="coordinator")  
3. **ServiceMethodProxy.__getattr__("get_system_metrics")** → MethodCaller("system", "get_system_metrics", target_node="coordinator")
4. **MethodCaller.__call__()** → Логика для удаленного вызова

```python
# В MethodCaller.__call__ с target_node="coordinator"
if self.target_node:
    raise RuntimeError(
        f"Method {method_path} not found in local registry. "
        f"Remote calls to '{self.target_node}' not implemented in local bridge."
    )
```

### Производительность локальных вызовов

**Время выполнения:**
- Локальный вызов: ~0.1-0.5ms
- HTTP RPC вызов: ~10-50ms
- Удаленный P2P вызов: ~20-100ms

**Преимущества:**
- Нет HTTP overhead
- Нет JSON сериализации/десериализации  
- Прямое обращение к bound method
- Использует локальную память

---

## Компоненты системы

### P2P Core (p2p_admin.py)
- **P2PAdminSystem** - основной класс системы
- **P2PClient** - клиент для удаленных вызовов
- Управление жизненным циклом сервисов
- Gossip протокол для обнаружения узлов

### Service Layer (service.py)
- **P2PServiceLayer** - FastAPI endpoints
- **RPCMethods** - автоматическая регистрация методов
- Method registry - реестр всех RPC методов
- JWT аутентификация

### Service Framework (service_framework.py)
- **BaseService** - базовый класс для сервисов
- **@service_method** - декоратор для публичных методов
- **ServiceRegistry** - управление сервисами
- Автоматический lifecycle management

### Local Service Bridge (local_service_bridge.py)
- **SimpleLocalProxy** - точка входа для вызовов
- **ServiceMethodProxy** - прокси сервиса с таргетингом
- **MethodCaller** - исполнитель методов
- Маршрутизация локальных/удаленных вызовов

---

## Создание сервисов

### Структура сервиса
```
services/my_service/
├── main.py          # Класс Run(BaseService)
└── requirements.txt # Зависимости (опционально)
```

### Базовый шаблон
```python
from service_framework import BaseService, service_method

class Run(BaseService):
    SERVICE_NAME = "my_service"
    
    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.info.description = "My custom service"
        self.info.dependencies = ["system"]
    
    async def initialize(self):
        # Инициализация сервиса
        pass
    
    async def cleanup(self):
        # Очистка ресурсов
        pass
    
    @service_method(description="Public API method", public=True)
    async def my_method(self, param: str) -> dict:
        # Вызов другого сервиса
        system_info = await self.proxy.system.get_system_info()
        
        return {
            "param": param,
            "hostname": system_info.get("hostname"),
            "service": self.service_name
        }
```

### Автоматическая регистрация
- Система сканирует `services/` каждые 60 секунд
- Автоматически регистрирует класс `Run` из `main.py`
- Инжектирует локальный прокси в `self.proxy`
- Регистрирует `@service_method(public=True)` в RPC

---

## REST API

### Аутентификация
```bash
curl -X POST "http://127.0.0.1:8001/auth/token" \
  -H "Content-Type: application/json" \
  -d '{"node_id": "client"}'
```

### RPC вызовы
```bash
curl -X POST "http://127.0.0.1:8001/rpc/{service}/{method}" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"method": "{method}", "params": {...}, "id": "req1"}'
```

### Административные endpoints
- `GET /health` - статус узла
- `GET /cluster/status` - статус кластера  
- `GET /cluster/nodes` - список узлов
- `POST /admin/broadcast` - широковещательный вызов
- `GET /local/services` - локальные сервисы
- `GET /debug/registry` - методы в реестре

---

## Запуск системы

### Координатор
```bash
python p2p_admin.py coordinator --verbose
```

### Worker узел
```bash
python p2p_admin.py worker --coord 127.0.0.1:8001 --port 8002
```

### Клиент (демо)
```bash
python p2p_admin.py client --coord 127.0.0.1:8001
```

### Тестовый кластер
```bash
python p2p_admin.py test
```

---

## Мониторинг и отладка

### Логи вызовов
```
DEBUG | Method.system.get_system_metrics | Local call: system/get_system_metrics
INFO  | Service.test_monitoring | Coordinator metrics #25: CPU 12.5%, Memory 65.2%
```

### Диагностика реестра
```bash
curl "http://127.0.0.1:8001/debug/registry" -H "Authorization: Bearer {token}"
```

### Проверка сервисов
```bash
curl "http://127.0.0.1:8001/local/services" -H "Authorization: Bearer {token}"
```

---

## Конфигурация

### Переменные окружения
- `REDIS_URL` - URL Redis для кэша (по умолчанию: redis://localhost:6379)
- `JWT_SECRET_KEY` - секретный ключ для JWT
- `SERVICES_DIR` - директория сервисов (по умолчанию: ./services)

### Порты по умолчанию
- Coordinator: 8001
- Worker: 8002+
- Gossip: тот же что HTTP

### Производительность
- Gossip interval: 15 секунд
- Failure timeout: 60 секунд
- JWT expiration: 24 часа
- Кэш TTL: настраивается через @service_method