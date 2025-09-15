# Service Orchestrator для P2P Core

Оркестратор сервисов - это ключевой компонент P2P Core, который управляет жизненным циклом сервисов и их распространением между узлами сети.

## Основные возможности

### 1. Управление жизненным циклом сервисов
- Установка сервисов из .tar.gz архивов
- Запуск, остановка и перезапуск сервисов
- Удаление сервисов
- Мониторинг состояния сервисов

### 2. Распространение сервисов
- Экспорт сервисов в .tar.gz формат
- Распространение сервисов на удаленные узлы
- Валидация архивов перед установкой

### 3. Мониторинг и диагностика
- Получение статуса всех сервисов
- Детальная информация о каждом сервисе
- Статистика работы оркестратора

## API методы

### install_service(archive_data, force_reinstall=False)
Установка сервиса из .tar.gz архива.

**Параметры:**
- `archive_data` (bytes) - данные архива
- `force_reinstall` (bool) - принудительная переустановка

**Возвращает:**
```json
{
  "success": true,
  "service_name": "example_service",
  "installed_at": "2025-09-15T10:30:00",
  "archive_hash": "sha256_hash",
  "auto_started": true
}
```

**Пример использования:**
```python
# Через RPC
result = await rpc.call("orchestrator/install_service", {
    "archive_data": archive_bytes,
    "force_reinstall": False
})

# Через proxy (P2P)
result = await proxy.call_method("orchestrator/install_service", {
    "archive_data": archive_bytes
})
```

### uninstall_service(service_name)
Удаление установленного сервиса.

**Параметры:**
- `service_name` (str) - имя сервиса для удаления

### start_service(service_name)
Запуск установленного сервиса.

### stop_service(service_name)
Остановка запущенного сервиса.

### restart_service(service_name)
Перезапуск сервиса.

### list_services()
Получение списка всех установленных сервисов.

**Возвращает:**
```json
{
  "total_installed": 5,
  "total_running": 3,
  "services": {
    "example_service": {
      "installed": true,
      "running": true,
      "installed_at": "2025-09-15T10:30:00",
      "archive_hash": "sha256_hash",
      "manifest": {...},
      "files_count": 15,
      "directory_exists": true
    }
  }
}
```

### get_service_info(service_name)
Получение детальной информации о сервисе.

### export_service(service_name)
Создание .tar.gz архива сервиса для распространения.

**Возвращает:**
- `bytes` - данные архива

### distribute_service(service_name, target_nodes)
Распространение сервиса на удаленные узлы.

**Параметры:**
- `service_name` (str) - имя сервиса
- `target_nodes` (list) - список ID целевых узлов

**Возвращает:**
```json
{
  "service_name": "example_service",
  "total_nodes": 3,
  "successful_distributions": 2,
  "failed_distributions": 1,
  "results": {
    "node_1": {"success": true, "result": {...}},
    "node_2": {"success": true, "result": {...}},
    "node_3": {"success": false, "error": "Connection failed"}
  }
}
```

### get_orchestrator_status()
Получение статуса оркестратора и статистики.

## Структура архива сервиса

Архив сервиса должен иметь следующую структуру:

```
service_name.tar.gz
└── service_name/
    ├── main.py              # Обязательно: точка входа
    ├── manifest.json        # Опционально: метаданные
    ├── requirements.txt     # Опционально: зависимости
    ├── config/             # Опционально: конфигурация
    │   └── default.json
    └── assets/             # Опционально: ресурсы
        └── ...
```

### Требования к main.py

Файл `main.py` должен содержать класс `Run`, наследующийся от `BaseService`:

```python
from service_framework import BaseService, service_method

class Run(BaseService):
    """Ваш сервис"""
    
    SERVICE_NAME = "your_service"
    
    async def initialize(self):
        """Инициализация сервиса"""
        pass
    
    async def cleanup(self):
        """Очистка ресурсов"""
        pass
    
    @service_method(description="Example method", public=True)
    async def example_method(self):
        """Пример публичного метода"""
        return {"message": "Hello from service!"}
```

### Пример manifest.json

```json
{
  "name": "example_service",
  "version": "1.0.0",
  "description": "Example service description",
  "author": "Service Author",
  "dependencies": ["some_dependency"],
  "exposed_methods": ["example_method"],
  "configuration": {
    "setting1": "value1",
    "setting2": 42
  }
}
```

## Установка и использование

### 1. Создание архива сервиса

```bash
# Создание архива из директории сервиса
tar -czf my_service.tar.gz my_service/
```

### 2. Установка через Python

```python
import asyncio
from pathlib import Path

async def install_service():
    # Загружаем архив
    with open("my_service.tar.gz", "rb") as f:
        archive_data = f.read()
    
    # Устанавливаем через RPC
    result = await rpc.call("orchestrator/install_service", {
        "archive_data": archive_data,
        "force_reinstall": False
    })
    
    print(f"Service installed: {result}")

asyncio.run(install_service())
```

### 3. Распространение на другие узлы

```python
async def distribute_to_network():
    result = await rpc.call("orchestrator/distribute_service", {
        "service_name": "my_service",
        "target_nodes": ["node_1", "node_2", "node_3"]
    })
    
    print(f"Distribution result: {result}")
```

## Мониторинг и диагностика

### Проверка статуса всех сервисов

```python
services = await rpc.call("orchestrator/list_services")
print(f"Total services: {services['total_installed']}")
print(f"Running services: {services['total_running']}")

for name, info in services['services'].items():
    status = "Running" if info['running'] else "Stopped"
    print(f"- {name}: {status}")
```

### Получение детальной информации

```python
service_info = await rpc.call("orchestrator/get_service_info", {
    "service_name": "my_service"
})

print(f"Service: {service_info['name']}")
print(f"Status: {'Running' if service_info['running'] else 'Stopped'}")
print(f"Installed: {service_info['installed_at']}")
```

### Статус оркестратора

```python
status = await rpc.call("orchestrator/get_orchestrator_status")
print(f"Orchestrator status: {status['orchestrator_status']}")
print(f"Services directory: {status['services_directory']}")
print(f"Statistics: {status['statistics']}")
```

## Обработка ошибок

Оркестратор использует специализированные исключения:

- `ServiceInstallationError` - ошибки установки
- `ServiceManagementError` - ошибки управления
- `ServiceDistributionError` - ошибки распространения

```python
try:
    result = await rpc.call("orchestrator/install_service", params)
except Exception as e:
    if "ServiceInstallationError" in str(e):
        print("Installation failed:", e)
    elif "ServiceManagementError" in str(e):
        print("Management operation failed:", e)
    else:
        print("Unexpected error:", e)
```

## Безопасность

### Валидация архивов
- Проверка формата .tar.gz
- Валидация структуры архива
- Проверка наличия обязательных файлов
- Вычисление хеша для целостности

### Ограничения
- Размер архива (настраивается)
- Проверка имен файлов на безопасность
- Изоляция временных файлов

## Логирование

Оркестратор ведет подробные логи всех операций:

```
INFO - Installing service: example_service
INFO - Service example_service installed successfully  
INFO - Starting service: example_service
INFO - Service example_service loaded and started
INFO - Distributing service example_service to 3 nodes
INFO - Successfully distributed service example_service to node node_1
```

## Интеграция с P2P Core

Оркестратор полностью интегрирован с P2P Core через:

- **Proxy client** - для межузлового взаимодействия
- **RPC система** - для локальных вызовов
- **Service framework** - для управления сервисами
- **Событийная система** - для уведомлений

## Производительность

- Асинхронная обработка всех операций
- Оптимизированная работа с архивами
- Кеширование метаданных сервисов
- Параллельное распространение на узлы

## Расширение функциональности

Оркестратор можно расширить следующими возможностями:

1. **Планировщик задач** - автоматическое обновление сервисов
2. **Версионирование** - управление версиями сервисов
3. **Rollback** - откат к предыдущим версиям
4. **Health monitoring** - мониторинг здоровья сервисов
5. **Auto-scaling** - автоматическое масштабирование
6. **Service dependencies** - управление зависимостями между сервисами

---

**Версия документации:** 1.0.0  
**Дата обновления:** 15 сентября 2025  
**Совместимость:** P2P Core v1.0+