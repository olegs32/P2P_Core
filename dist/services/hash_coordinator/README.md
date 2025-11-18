# Hash Coordinator Service

Координатор для распределенных вычислений хешей в P2P кластере.

## Описание

Этот сервис управляет распределением задач вычисления хешей между воркерами кластера. Он использует gossip протокол для координации без необходимости постоянных RPC вызовов.

## Функции

### Основные возможности

- **Динамическая генерация chunk batches**: Создает batches "на лету" с lookahead на N шагов вперед
- **Адаптивная нагрузка**: Автоматически подстраивает размер чанков под производительность воркеров
- **Восстановление orphaned chunks**: Обнаруживает зависшие чанки и перераспределяет их
- **Версионирование batches**: Предотвращает конфликты при обновлении данных в gossip
- **Мониторинг прогресса**: Отслеживает скорость выполнения и оценивает время завершения

### Архитектура

```
Coordinator
    │
    ├─► DynamicChunkGenerator
    │   ├─► Генерация batches (lookahead = 3)
    │   ├─► Адаптивный chunk_size
    │   └─► Восстановление orphaned
    │
    ├─► PerformanceAnalyzer
    │   ├─► Отслеживание скорости воркеров
    │   └─► Вычисление оптимального chunk_size
    │
    └─► Gossip Protocol
        ├─► Job metadata
        ├─► Batch announcements
        └─► Cleanup completed batches
```

## RPC методы

### create_job

Создает новую задачу вычисления хешей.

**Параметры:**
- `job_id` (str): Уникальный ID задачи
- `charset` (str): Набор символов для перебора
- `length` (int): Длина комбинации символов
- `hash_algo` (str): Алгоритм хеширования (sha256, md5, sha1, sha512)
- `target_hash` (str, optional): Целевой хеш для поиска
- `base_chunk_size` (int): Базовый размер чанка (default: 1,000,000)

**Пример:**
```python
result = await proxy.hash_coordinator.coordinator.create_job(
    job_id="test-job-1",
    charset="abcdefghijklmnopqrstuvwxyz0123456789",
    length=4,
    hash_algo="sha256",
    base_chunk_size=1000000
)
```

**Возвращает:**
```python
{
    "success": True,
    "job_id": "test-job-1",
    "total_combinations": 1679616,
    "initial_batches": 3
}
```

### get_job_status

Получает статус выполнения задачи.

**Параметры:**
- `job_id` (str): ID задачи

**Возвращает:**
```python
{
    "success": True,
    "job_id": "test-job-1",
    "progress": {
        "total_combinations": 1679616,
        "processed": 456000,
        "in_progress": 100000,
        "pending": 1123616,
        "progress_percentage": 27.15,
        "eta_seconds": 1245,
        "current_version": 5,
        "completed_batches": 2,
        "active_batches": 3
    },
    "cluster_stats": {
        "avg_speed": 4532.5,
        "total_speed": 9065.0,
        "min_speed": 3200.0,
        "max_speed": 5865.0
    },
    "worker_speeds": {
        "worker-001": 5865.0,
        "worker-002": 3200.0
    }
}
```

### get_all_jobs

Возвращает список всех активных задач.

**Возвращает:**
```python
{
    "success": True,
    "jobs": [
        {
            "job_id": "test-job-1",
            "progress_percentage": 27.15,
            "processed": 456000,
            "total": 1679616,
            "eta_seconds": 1245
        }
    ]
}
```

## Gossip структура данных

### Job Metadata

Публикуется в ключе `hash_job_{job_id}`:

```python
{
    "job_id": "test-job-1",
    "charset": "abc...xyz012...789",
    "length": 4,
    "hash_algo": "sha256",
    "target_hash": None,  # or "5e88489..."
    "started_at": 1700000000.123
}
```

### Batch Announcements

Публикуется в ключе `hash_batches_{job_id}`:

```python
{
    "ver": 5,  # Версия батча
    "chunks": {
        5000: {  # chunk_id
            "assigned_worker": "worker-001",
            "start_index": 5000000,
            "end_index": 5001500,
            "chunk_size": 1500000,  # Адаптивный размер
            "status": "assigned",
            "priority": 1
        },
        5001: {
            "assigned_worker": "worker-002",
            "start_index": 5001500,
            "end_index": 5002000,
            "chunk_size": 500000,  # Меньше для медленного воркера
            "status": "assigned",
            "priority": 1
        }
    },
    "created_at": 1700000010.456,
    "is_recovery": False
}
```

## Адаптивная производительность

Координатор автоматически подстраивает размер чанков под скорость воркеров:

**Формула:** `chunk_size = base_chunk_size × (worker_speed / avg_speed)`

**Ограничения:** 0.5x - 2.0x от базового размера

**Пример:**
- Base chunk size: 1,000,000
- Worker-1 speed: 10,000 h/s (в 2 раза быстрее среднего)
  - Chunk size: 2,000,000 (макс 2x)
- Worker-2 speed: 2,500 h/s (в 2 раза медленнее)
  - Chunk size: 500,000 (мин 0.5x)

## Orphaned Chunks Detection

Координатор обнаруживает "застрявшие" чанки:

**Критерии:**
1. Чанк в статусе "working" более 5 минут
2. У того же воркера есть более новые решенные чанки

**Действия:**
1. Пометить чанк как orphaned
2. Создать recovery batch с высоким приоритетом
3. Назначить другому воркеру

## Завершение задачи

Задача считается завершенной когда:
1. `current_global_index >= total_combinations`
2. Все сгенерированные чанки в статусе "solved"
3. Нет активных батчей

Результаты сохраняются в файл `hash_results_{job_id}.json`.

## Конфигурация

```python
# В P2PConfig (не требуется, используются значения по умолчанию)
base_chunk_size: int = 1_000_000  # Базовый размер чанка
lookahead_batches: int = 3  # Батчей вперед
orphaned_timeout: int = 300  # 5 минут
```

## Производительность

**Overhead на координацию:** < 0.1%
- Gossip sync каждые 10-30 секунд
- Асинхронный анализ orphaned чанков
- Lock только на генерацию batch

**Потери при сбоях:** < 5 минут работы воркера
- Orphaned chunks обнаруживаются за 5 минут
- Восстановление с последнего отчетного progress

## Требования

- Запускается только на координаторе (`coordinator_mode: true`)
- Требует наличия сервиса `hash_worker` на воркерах
- Использует gossip протокол для координации

## Интеграция с Dashboard

Веб-интерфейс доступен в metrics dashboard:
- Вкладка "Hash Jobs"
- Создание задач через UI
- Мониторинг прогресса в реальном времени
- Просмотр скорости воркеров
