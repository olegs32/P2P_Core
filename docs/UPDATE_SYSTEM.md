# P2P Update System Documentation

Система безопасных обновлений для P2P кластера с цифровыми подписями.

## Архитектура

```
┌─────────────────────────────────────────────┐
│           COORDINATOR                       │
│  ┌───────────────────────────────────────┐  │
│  │       Update Server                   │  │
│  │  - Stores update packages             │  │
│  │  - Generates RSA signatures (4096bit) │  │
│  │  - Provides download API              │  │
│  │  - Manages versions                   │  │
│  └───────────────────────────────────────┘  │
│  ┌───────────────────────────────────────┐  │
│  │       Update Manager                  │  │
│  │  - Checks for updates                 │  │
│  │  - Downloads & verifies packages      │  │
│  │  - Installs updates                   │  │
│  │  - Manages backups & rollback         │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
                    │
                    │ HTTPS + RPC
                    │
┌─────────────────────────────────────────────┐
│            WORKERS                          │
│  ┌───────────────────────────────────────┐  │
│  │       Update Manager                  │  │
│  │  - Checks for updates                 │  │
│  │  - Downloads & verifies packages      │  │
│  │  - Installs updates                   │  │
│  │  - Manages backups & rollback         │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

## Безопасность

### 1. Цифровые подписи (RSA 4096-bit)
- Каждый пакет обновления подписывается приватным ключом координатора
- Воркеры проверяют подпись публичным ключом
- Используется RSA-PSS с SHA-256

### 2. Проверка целостности
- SHA-256 хеш каждого пакета
- Проверка хеша перед установкой

### 3. Транспортная безопасность
- Все коммуникации через HTTPS (если включен)
- Использование существующей P2P RPC аутентификации

### 4. Backup и Rollback
- Автоматический backup перед установкой
- Возможность отката к предыдущей версии
- Хранение нескольких backup версий

## Использование

### Подготовка пакета обновления

1. **Создайте структуру обновления:**
```bash
mkdir -p update_package/dist/services
mkdir -p update_package/layers
mkdir -p update_package/methods

# Скопируйте обновленные файлы
cp -r dist/services/* update_package/dist/services/
cp -r layers/* update_package/layers/
cp -r methods/* update_package/methods/
```

2. **Создайте tar.gz архив:**
```bash
cd update_package
tar -czf ../update-1.1.0.tar.gz .
cd ..
```

### Загрузка обновления на координатор

#### Через RPC API:
```python
import asyncio
from pathlib import Path

async def upload_update():
    # Read package file
    package_file = Path("update-1.1.0.tar.gz")
    with open(package_file, 'rb') as f:
        package_data = f.read()

    # Upload через proxy
    result = await proxy.update_server.upload_update(
        version="1.1.0",
        package_data=package_data,
        description="Bug fixes and performance improvements",
        target_nodes="all"  # "all", "workers", "coordinator"
    )

    print(result)
```

#### Через HTTP API:
```bash
# Загрузить пакет
curl -X POST https://coordinator:8002/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "update_server/upload_update",
    "params": {
      "version": "1.1.0",
      "package_data": "...",  # hex-encoded package
      "description": "Bug fixes",
      "target_nodes": "all"
    },
    "id": 1
  }'
```

### Проверка доступных обновлений

#### На воркере:
```python
# Проверить обновления
result = await proxy.update_manager.check_updates()

print(f"Current version: {result['current_version']}")
print(f"Has updates: {result['has_updates']}")

for update in result['available_updates']:
    print(f"  - Version {update['version']}: {update['description']}")
```

#### Список всех обновлений на координаторе:
```python
result = await proxy.update_server.coordinator.list_updates()

for update in result['updates']:
    print(f"{update['version']} - {update['description']}")
```

### Установка обновления

#### На воркере:
```python
# Установить обновление
result = await proxy.update_manager.install_update(
    version="1.1.0",
    auto_restart=True  # Автоматический перезапуск
)

if result['success']:
    print(f"Update installed: {result['version']}")
    print(f"Backup created: {result['backup_dir']}")
else:
    print(f"Update failed: {result['error']}")
```

#### Через Dashboard (будущее):
В веб-интерфейсе координатора можно будет:
1. Просматривать доступные обновления
2. Выбрать узлы для обновления
3. Запустить обновление одной кнопкой
4. Отслеживать прогресс

### Управление backup'ами

#### Список backup'ов:
```python
result = await proxy.update_manager.list_backups()

for backup in result['backups']:
    print(f"{backup['name']} - Version {backup['version']}")
    print(f"  Created: {backup['created']}")
```

#### Откат к backup:
```python
result = await proxy.update_manager.manual_rollback(
    backup_name="backup_1.0.0_20250110_143022"
)

if result['success']:
    print("Rollback successful")
```

## API Reference

### Update Server (Coordinator)

#### `upload_update(version, package_data, description, target_nodes)`
Загрузить пакет обновления на координатор.

**Parameters:**
- `version` (str): Версия обновления (e.g., "1.1.0")
- `package_data` (bytes): Бинарные данные tar.gz пакета
- `description` (str): Описание обновления
- `target_nodes` (str): Целевые узлы ("all", "workers", "coordinator")

**Returns:**
```python
{
    "success": True,
    "version": "1.1.0",
    "hash": "sha256_hash",
    "size": 1234567
}
```

#### `list_updates(target_node_type=None)`
Список доступных обновлений.

**Returns:**
```python
{
    "success": True,
    "updates": [
        {
            "version": "1.1.0",
            "size": 1234567,
            "hash": "sha256_hash",
            "description": "Bug fixes",
            "target_nodes": "all",
            "uploaded_at": "2025-01-10T14:30:00",
            "status": "available"
        }
    ],
    "count": 1
}
```

#### `download_update(version)`
Скачать пакет обновления.

**Returns:**
```python
{
    "success": True,
    "version": "1.1.0",
    "package_data": "hex_encoded_data",
    "signature": "hex_encoded_signature",
    "hash": "sha256_hash",
    "size": 1234567,
    "description": "Bug fixes"
}
```

#### `get_public_key()`
Получить публичный ключ для проверки подписей.

**Returns:**
```python
{
    "success": True,
    "public_key": "-----BEGIN PUBLIC KEY-----\n..."
}
```

#### `delete_update(version)`
Удалить версию обновления.

### Update Manager (All Nodes)

#### `check_updates()`
Проверить доступные обновления.

**Returns:**
```python
{
    "success": True,
    "current_version": "1.0.0",
    "available_updates": [...],
    "has_updates": True,
    "last_check": "2025-01-10T14:30:00"
}
```

#### `install_update(version, auto_restart=False)`
Установить обновление.

**Parameters:**
- `version` (str): Версия для установки
- `auto_restart` (bool): Автоматический перезапуск после установки

**Returns:**
```python
{
    "success": True,
    "version": "1.1.0",
    "backup_dir": "/path/to/backup",
    "message": "Update installed successfully",
    "restarting": True  # если auto_restart=True
}
```

#### `get_status()`
Получить статус update manager.

**Returns:**
```python
{
    "success": True,
    "current_version": "1.0.0",
    "update_in_progress": False,
    "last_check_time": "2025-01-10T14:30:00",
    "has_public_key": True,
    "backups_count": 3
}
```

#### `list_backups()`
Список backup версий.

#### `manual_rollback(backup_name)`
Откатиться к backup версии.

## Процесс обновления

1. **Подготовка:**
   - Создать пакет обновления (tar.gz)
   - Загрузить на координатор через `upload_update()`
   - Координатор подписывает пакет RSA ключом

2. **Проверка:**
   - Воркеры периодически вызывают `check_updates()`
   - Координатор возвращает список новых версий

3. **Установка:**
   - Воркер вызывает `install_update(version)`
   - Скачивание пакета с координатора
   - Проверка SHA-256 хеша
   - Проверка RSA подписи
   - Создание backup текущей версии
   - Распаковка и установка файлов
   - Обновление версии в state

4. **Перезапуск (опционально):**
   - Если `auto_restart=True`, узел перезапускается
   - Graceful shutdown с таймаутом

5. **Rollback (при ошибках):**
   - Автоматический rollback при ошибке установки
   - Ручной rollback через `manual_rollback()`

## Безопасные практики

1. **Тестирование:**
   - Сначала обновите тестовый воркер
   - Проверьте работоспособность
   - Только потом обновляйте production

2. **Поэтапное развертывание:**
   - Обновляйте воркеры по одному
   - Проверяйте после каждого обновления
   - Координатор обновляйте последним

3. **Backup:**
   - Backup создается автоматически
   - Храните несколько версий backup
   - Регулярно проверяйте возможность rollback

4. **Мониторинг:**
   - Следите за логами во время обновления
   - Проверяйте метрики после обновления
   - Используйте дашборд для контроля состояния

5. **Ключи подписи:**
   - Храните приватный ключ координатора в безопасности
   - Регулярно делайте backup ключей
   - Контролируйте доступ к update_server

## Troubleshooting

### Проблема: "Signature verification failed"
**Причина:** Пакет был изменен или публичный ключ не совпадает

**Решение:**
1. Проверьте, что пакет не был изменен
2. Убедитесь, что воркер получил правильный публичный ключ
3. Перезагрузите публичный ключ: restart update_manager

### Проблема: "Package integrity check failed"
**Причина:** SHA-256 хеш не совпадает

**Решение:**
1. Пакет был поврежден при передаче
2. Повторите скачивание
3. Проверьте сетевое соединение

### Проблема: Update зависает
**Причина:** Ошибка при установке файлов

**Решение:**
1. Проверьте логи update_manager
2. Выполните manual_rollback к предыдущей версии
3. Проверьте структуру пакета обновления

### Проблема: Rollback не работает
**Причина:** Backup поврежден или удален

**Решение:**
1. Проверьте наличие backup: `list_backups()`
2. Если backup нет - переустановите систему вручную
3. Регулярно делайте внешние backup'ы

## Примеры сценариев

### Сценарий 1: Обновление одного воркера

```python
# На координаторе - загрузить обновление
with open("update-1.1.0.tar.gz", 'rb') as f:
    package = f.read()

await proxy.update_server.upload_update(
    version="1.1.0",
    package_data=package,
    description="Security fix",
    target_nodes="workers"
)

# На воркере - проверить и установить
updates = await proxy.update_manager.check_updates()
if updates['has_updates']:
    await proxy.update_manager.install_update(
        version="1.1.0",
        auto_restart=True
    )
```

### Сценарий 2: Массовое обновление всех воркеров

```python
# На координаторе через RPC к каждому воркеру
workers = ["worker-1", "worker-2", "worker-3"]

for worker_id in workers:
    try:
        result = await proxy.update_manager[worker_id].install_update(
            version="1.1.0",
            auto_restart=True
        )
        print(f"{worker_id}: {result['message']}")
        await asyncio.sleep(30)  # Пауза между воркерами
    except Exception as e:
        print(f"{worker_id}: Failed - {e}")
```

### Сценарий 3: Откат после неудачного обновления

```python
# Список backup'ов
backups = await proxy.update_manager.list_backups()
print("Available backups:", backups['backups'])

# Откатиться к последнему backup
latest_backup = backups['backups'][0]['name']
result = await proxy.update_manager.manual_rollback(latest_backup)

if result['success']:
    print("Rollback completed, restarting...")
    # Restart node
```

## Расширение системы обновлений

### Добавление веб-интерфейса
Можно добавить UI в metrics_dashboard для:
- Просмотра доступных обновлений
- Загрузки новых пакетов
- Управления обновлениями воркеров
- Мониторинга процесса обновления

### Автоматические обновления
Добавить планировщик в update_manager:
```python
async def auto_update_loop(self):
    while True:
        await asyncio.sleep(3600)  # Проверка каждый час

        updates = await self.check_updates()
        if updates['has_updates']:
            # Auto-install if auto_update enabled
            if self.config.auto_update:
                await self.install_update(
                    version=updates['available_updates'][0]['version'],
                    auto_restart=True
                )
```

### Дополнительная безопасность
- Двухфакторная подтверждение обновлений
- Белый список доверенных версий
- Автоматическое сканирование пакетов на вирусы
- Журнал аудита всех обновлений
