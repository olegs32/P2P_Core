# P2P Remote RPC Architecture

## Обзор

Система поддерживает как локальные, так и удаленные RPC вызовы через единый интерфейс proxy. Удаленные вызовы используют существующую P2P инфраструктуру с node_registry, connection_manager и gossip протоколом.

## Архитектура

### Компоненты

```
┌─────────────────────────────────────────────────────────────┐
│                     Service Proxy                           │
│  proxy.service_name.target_node.method_name(**kwargs)       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│               SimpleLocalProxy                               │
│  - Entry point for all service calls                        │
│  - Creates ServiceMethodProxy for each service              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│            ServiceMethodProxy                                │
│  - Handles service.target_node chaining                     │
│  - Resolves roles (coordinator, worker) to node_ids         │
│  - Creates MethodCaller with target info                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│               MethodCaller                                   │
│  - Executes local or remote call based on target_node       │
│  - Uses node_registry for node resolution                   │
│  - Uses connection_manager for HTTP client pooling          │
└─────────────────────────────────────────────────────────────┘
         │                                │
         │ Local Call                     │ Remote Call
         ▼                                ▼
┌──────────────────┐          ┌──────────────────────────┐
│ Method Registry  │          │   Network Layer          │
│ (Direct invoke)  │          │   - node_registry        │
└──────────────────┘          │   - connection_manager   │
                              │   - HTTP/RPC client      │
                              └──────────────────────────┘
                                         │
                                         ▼
                              ┌──────────────────────────┐
                              │   Remote Node            │
                              │   /rpc endpoint          │
                              └──────────────────────────┘
```

## Типы вызовов

### 1. Локальный вызов
```python
# Вызов локального сервиса
result = await proxy.system.get_system_metrics()

# Flow:
# proxy.system -> ServiceMethodProxy(service='system')
# .get_system_metrics -> MethodCaller(target_node=None)
# __call__() -> _local_call() -> method_registry['system/get_system_metrics']()
```

### 2. Удаленный вызов по node_id
```python
# Вызов сервиса на конкретном узле
result = await proxy.system.worker_node_123.get_system_metrics()

# Flow:
# proxy.system -> ServiceMethodProxy(service='system')
# .worker_node_123 -> ServiceMethodProxy(service='system', target_node='worker_node_123')
# .get_system_metrics -> MethodCaller(target_node='worker_node_123')
# __call__() -> _remote_call() -> HTTP POST to worker_node_123
```

### 3. Удаленный вызов по роли
```python
# Вызов сервиса на координаторе (по роли)
result = await proxy.metrics_dashboard.coordinator.report_metrics(...)

# Flow:
# proxy.metrics_dashboard -> ServiceMethodProxy(service='metrics_dashboard')
# .coordinator -> resolve role='coordinator' in node_registry -> node_id
# .report_metrics -> MethodCaller(target_node=resolved_node_id)
# __call__() -> _remote_call() -> HTTP POST to coordinator
```

## Детали реализации

### ServiceMethodProxy.__getattr__()

Определяет является ли атрибут:
1. **Именем метода** - создает MethodCaller
2. **Ролью узла** (coordinator/worker) - резолвит через node_registry
3. **Node ID** - создает таргетированный прокси

```python
def __getattr__(self, attr_name: str):
    network = self.context.get_shared('network')
    known_targets = network.gossip.node_registry

    # Проверка роли
    if attr_name in ['coordinator', 'worker']:
        for node_id, node_info in known_targets.items():
            if node_info.role == attr_name:
                return ServiceMethodProxy(
                    service_name=self.service_name,
                    target_node=node_id,  # Резолвленный node_id
                    ...
                )

    # Проверка node_id
    if attr_name in known_targets:
        return ServiceMethodProxy(
            service_name=self.service_name,
            target_node=attr_name,
            ...
        )

    # Иначе это метод
    return MethodCaller(...)
```

### MethodCaller._remote_call()

Выполняет удаленный RPC через P2P архитектуру:

```python
async def _remote_call(self, method_path: str, **kwargs):
    # 1. Получить network layer
    network = self.context.get_shared('network')

    # 2. Получить NodeInfo из registry
    node_info = network.gossip.node_registry[self.target_node]

    # 3. Получить URL узла
    node_url = node_info.get_url(https=https_enabled)

    # 4. Подготовить JSON-RPC запрос
    rpc_request = {
        "jsonrpc": "2.0",
        "method": method_path,
        "params": kwargs,
        "id": str(uuid.uuid4())
    }

    # 5. Выполнить через connection_manager (с пулингом)
    client = await network.connection_manager.get_client(node_url)
    response = await client.post("/rpc", json=rpc_request)

    # 6. Обработать ответ
    result = response.json()
    if "error" in result:
        raise RuntimeError(f"RPC error: {result['error']}")

    return result["result"]
```

## Преимущества архитектуры

### 1. Использование существующей инфраструктуры
- **node_registry** - автоматическое обнаружение узлов через gossip
- **connection_manager** - пулинг HTTP соединений, SSL support
- **Единый транспорт** - все RPC через /rpc endpoint

### 2. Прозрачность для разработчика
```python
# Один и тот же интерфейс для локальных и удаленных вызовов
local_result = await proxy.system.get_metrics()
remote_result = await proxy.system.coordinator.get_metrics()
```

### 3. Автоматическая SSL/TLS
```python
# SSL конфигурация из context.config
https_enabled = self.context.config.https_enabled
ssl_verify = self.context.config.ssl_verify
ca_cert_file = self.context.config.ssl_ca_cert_file

# connection_manager автоматически использует правильный SSL контекст
```

### 4. Роль-based targeting
```python
# Не нужно знать конкретный node_id
await proxy.service.coordinator.method()  # Находит coordinator автоматически
await proxy.service.worker.method()       # Находит любой worker
```

### 5. Обработка ошибок
```python
try:
    await proxy.service.remote_node.method()
except RuntimeError as e:
    # Детальная информация об ошибке:
    # - HTTP errors
    # - RPC errors
    # - Network errors
    # - Node not found
    pass
```

## Примеры использования

### Dashboard Metrics Reporting

**Worker → Coordinator:**
```python
# На воркере (metrics_reporter service)
result = await self.proxy.metrics_dashboard.coordinator.report_metrics(
    worker_id=self.worker_id,
    metrics=metrics_dict,
    services=services_dict
)

# Система автоматически:
# 1. Находит coordinator в node_registry (role='coordinator')
# 2. Получает его URL (https://coordinator:8001)
# 3. Делает POST /rpc с методом 'metrics_dashboard/report_metrics'
# 4. Использует connection_manager для пулинга соединений
# 5. Возвращает result['result']
```

### Service Management

**Dashboard → Worker:**
```python
# На координаторе (metrics_dashboard service)
result = await self.proxy.orchestrator.worker_node_123.control_service(
    service_name="test_service",
    action="restart"
)

# Система автоматически:
# 1. Находит worker_node_123 в node_registry
# 2. Получает его URL
# 3. Вызывает orchestrator/control_service на удаленном узле
```

### Cross-Service Communication

**Service A → Service B на другом узле:**
```python
# В любом сервисе
data = await self.proxy.other_service.remote_node.get_data()
result = await self.proxy.other_service.remote_node.process_data(data=data)
```

## Конфигурация

### Node Registry

Автоматически заполняется через gossip протокол:

```python
NodeInfo(
    node_id="worker-001",
    address="192.168.1.100",
    port=8002,
    role="worker",  # coordinator or worker
    capabilities=["compute", "storage"],
    services={
        "system": {...},
        "metrics_reporter": {...}
    },
    status="alive"
)
```

### Connection Manager

```python
ConnectionManager(
    max_connections=100,      # Макс соединений
    max_keepalive=20,         # Keep-alive соединения
    ssl_verify=True,          # SSL верификация
    ca_cert_file="ca.crt",    # CA сертификат
    context=app_context       # Доступ к storage_manager
)
```

## Отладка

### Логирование

```python
# MethodCaller логирует все вызовы
self.logger.debug(f"Local call: {method_path}")
self.logger.debug(f"Remote call to {self.target_node}: {method_path}")
self.logger.debug(f"Remote call successful: {method_path} -> {self.target_node}")
self.logger.error(f"Remote call failed to {self.target_node}: {e}")
```

### Проверка node_registry

```python
# В любом сервисе с context
network = self.context.get_shared('network')
nodes = network.gossip.node_registry

for node_id, node_info in nodes.items():
    print(f"{node_id}: {node_info.role} at {node_info.get_url()}")
```

### Тестирование удаленных вызовов

```python
# Проверка доступности координатора
try:
    result = await proxy.system.coordinator.get_system_info()
    print(f"Coordinator found: {result['hostname']}")
except RuntimeError as e:
    print(f"Coordinator not available: {e}")
```

## Ошибки и их решение

### "Target node not found in node registry"

**Причина:** Узел не зарегистрирован в gossip протоколе

**Решение:**
1. Проверьте что узел запущен
2. Проверьте gossip соединение
3. Подождите несколько секунд для gossip синхронизации

### "Network layer not available"

**Причина:** Context не содержит network layer

**Решение:**
1. Убедитесь что сервис получает context при инициализации
2. Проверьте что NetworkComponent инициализирован в application_context

### "Remote RPC call failed: HTTP error 404"

**Причина:** Метод не зарегистрирован на удаленном узле

**Решение:**
1. Проверьте что сервис загружен на целевом узле
2. Проверьте что метод помечен как @service_method(public=True)
3. Проверьте имя метода и параметры

## Безопасность

### SSL/TLS
- Все удаленные вызовы используют HTTPS (если enabled)
- Автоматическая верификация через CA сертификат
- Взаимная TLS аутентификация между узлами

### Аутентификация
- JWT токены для RPC запросов (если настроено)
- Проверка прав доступа к методам
- Rate limiting на endpoint уровне

## Производительность

### Connection Pooling
- Переиспользование HTTP соединений
- Keep-alive для снижения latency
- HTTP/2 поддержка для мультиплексинга

### Кэширование
- node_registry кэшируется в памяти
- Connection manager хранит активные клиенты
- Резолв ролей происходит один раз за вызов

## Будущие улучшения

1. **Load Balancing**: При вызове по роли выбирать наименее загруженный узел
2. **Failover**: Автоматический retry на другой узел той же роли
3. **Circuit Breaker**: Временное отключение недоступных узлов
4. **Метрики**: Сбор статистики по удаленным вызовам
5. **Async batching**: Группировка множественных вызовов в один HTTP запрос
