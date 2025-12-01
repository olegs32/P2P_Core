# Hash Worker Service

Воркер для выполнения распределенных вычислений хешей в P2P кластере.

## Описание

Этот сервис выполняет высокопроизводительное вычисление хешей для chunk batches, получаемых от координатора через gossip протокол. Воркер автономно выбирает доступные чанки и отчитывается о прогрессе.

## Функции

### Основные возможности

- **Автономное получение чанков**: Читает batches из gossip без RPC к координатору
- **Оптимизированные вычисления**: Максимальная скорость хеширования (99.99% времени на вычисления)
- **Автоматическая отчетность**: Публикует прогресс в gossip каждые 10 секунд
- **Обнаружение решений**: Находит комбинации, соответствующие целевому хешу
- **Метрики производительности**: Отслеживает скорость вычислений (hashes/sec)

### Архитектура

```
Worker
    │
    ├─► Gossip Listener
    │   ├─► Читает job metadata
    │   ├─► Читает batch announcements
    │   └─► Находит свои чанки
    │
    ├─► HashComputer
    │   ├─► index_to_combination (O(length))
    │   ├─► Батчи по 10k итераций
    │   └─► Оптимизированный цикл (без проверок)
    │
    └─► Progress Reporter
        ├─► Обновление gossip каждые 10 сек
        └─► Немедленная публикация результатов
```

## Основной цикл

```python
while running:
    # 1. Получить активные задачи из gossip
    jobs = get_active_jobs()

    # 2. Для каждой задачи
    for job_id in jobs:
        # 3. Найти доступный чанк для меня
        chunk = get_available_chunk(job_id)

        if chunk:
            # 4. Обработать чанк
            process_chunk(job_id, chunk)
```

## Оптимизированное вычисление

### Batching (Никаких проверок в основном цикле)

```python
BATCH_SIZE = 10_000  # Проверяем только после батча

while current_idx < end_idx:
    batch_end = min(current_idx + BATCH_SIZE, end_idx)

    # Основной цикл БЕЗ ПРОВЕРОК
    for idx in range(current_idx, batch_end):
        combination = index_to_combination(idx)
        hash_val = sha256(combination)
        # Только вычисления, никаких if, никаких вызовов

    current_idx = batch_end

    # Проверки ТОЛЬКО ПОСЛЕ батча
    if should_update_gossip():
        update_gossip_state(current_idx)
```

### Предрасчет констант

```python
# ❌ ПЛОХО: вычисления на каждой итерации
charset_str = "abc...XYZ"
char = charset_str[idx % len(charset_str)]

# ✅ ХОРОШО: предрасчет ДО цикла
charset_list = list("abc...XYZ")
base = len(charset_list)

for idx in range(start, end):
    char = charset_list[idx % base]  # Быстрый доступ
```

### Оптимизированный index_to_combination

```python
def index_to_combination(idx: int) -> str:
    """
    Минимум операций, максимум скорость
    O(length) - около 1 микросекунды
    """
    result = [None] * length  # Предаллокация

    for pos in range(length - 1, -1, -1):
        result[pos] = charset_list[idx % base]
        idx //= base

    return ''.join(result)
```

### Hash алгоритмы

```python
# ❌ МЕДЛЕННО: создание объекта каждый раз
hash_val = hashlib.sha256(combination.encode()).hexdigest()

# ✅ БЫСТРЕЕ: digest() вместо hexdigest()
hash_bytes = hashlib.sha256(combination.encode()).digest()

# ✅✅ ЕЩЕ БЫСТРЕЕ: сравнение с target_hash
target_hash_bytes = bytes.fromhex(target_hash)

for idx in range(start, end):
    combination = index_to_combination(idx)
    hash_bytes = hashlib.sha256(combination.encode()).digest()

    if hash_bytes == target_hash_bytes:
        # НАЙДЕНО!
        return combination
```

## Gossip интеграция

### Чтение задач

Воркер читает метаданные из gossip:

```python
# Ключ: hash_job_{job_id}
job_metadata = coordinator.metadata.get("hash_job_test-1")
# {
#     "job_id": "test-1",
#     "charset": "abc...xyz",
#     "length": 4,
#     "hash_algo": "sha256",
#     "target_hash": None
# }
```

### Получение чанков

```python
# Ключ: hash_batches_{job_id}
batches = coordinator.metadata.get("hash_batches_test-1")
# {
#     "ver": 5,
#     "chunks": {
#         5000: {
#             "assigned_worker": "worker-001",  # Это я!
#             "start_index": 5000000,
#             "end_index": 5001000,
#             ...
#         }
#     }
# }

# Воркер берет ТОЛЬКО свои чанки
my_worker_id = "worker-001"
for chunk_id, chunk_data in batches["chunks"].items():
    if chunk_data["assigned_worker"] == my_worker_id:
        if chunk_data["status"] in ("assigned", "recovery"):
            # Это мой чанк!
            process_chunk(chunk_id, chunk_data)
```

### Публикация прогресса

```python
# Ключ: hash_worker_status
network.gossip.self_info.metadata.update({
    "hash_worker_status": {
        "job_id": "test-1",
        "chunk_id": 5000,
        "status": "working",  # или "solved"
        "progress": 5000456,  # Текущий индекс
        "timestamp": time.time(),
        "total_hashes": 123456789,
        "completed_chunks": 45
    }
})
```

### Публикация результата

```python
# Когда чанк завершен
network.gossip.self_info.metadata.update({
    "hash_worker_status": {
        "job_id": "test-1",
        "chunk_id": 5000,
        "status": "solved",
        "hash_count": 1000000,
        "time_taken": 234.5,  # Секунды
        "solutions": [  # Найденные решения
            {
                "combination": "password",
                "hash": "5e88489...",
                "index": 5000234
            }
        ],
        "timestamp": time.time()
    }
})
```

## RPC методы

### get_worker_status

Возвращает текущий статус воркера.

**Возвращает:**
```python
{
    "success": True,
    "current_job": "test-job-1",
    "current_chunk": 5000,
    "total_hashes_computed": 123456789,
    "completed_chunks": 45,
    "running": True
}
```

## Производительность

### Overhead на координацию

**Gossip updates:** раз в 10 секунд
- Чтение batches: < 1ms
- Публикация прогресса: < 5ms

**Итого overhead:** < 0.01% времени

### Скорость вычислений

**Зависит от:**
- CPU частота и количество ядер
- Алгоритм хеширования (SHA-256 медленнее MD5)
- Длина комбинации

**Типичные значения:**
- SHA-256, 4 символа: 50,000 - 100,000 h/s
- MD5, 4 символа: 100,000 - 200,000 h/s
- SHA-512, 8 символов: 10,000 - 30,000 h/s

### Метрики

```python
{
    "hash_count": 1_000_000,       # Обработано хешей
    "time_taken": 23.4,            # Секунд
    "hash_rate": 42735.0,          # Хешей/сек
    "solutions": [],               # Найденные решения
    "start_index": 5000000,
    "end_index": 5001000
}
```

## Конфигурация

```python
# В P2PConfig (не требуется, используются значения по умолчанию)
hash_worker_enabled: bool = True
progress_interval: int = 10  # Секунд между gossip updates
batch_size: int = 10000  # Итераций перед проверкой
```

## Требования

- Запускается только на воркерах (`coordinator_mode: false`)
- Требует наличия `hash_coordinator` на координаторе
- Использует gossip протокол для получения задач

## Примеры

### Простой перебор

```python
# Координатор создает задачу
await proxy.hash_coordinator.coordinator.create_job(
    job_id="brute-force-1",
    charset="0123456789",
    length=4,
    hash_algo="sha256"
)

# Воркер автоматически:
# 1. Видит задачу в gossip
# 2. Находит свой чанк
# 3. Вычисляет хеши (0000 - 9999)
# 4. Публикует результат
```

### Поиск пароля

```python
# Создать задачу с целевым хешом
await proxy.hash_coordinator.coordinator.create_job(
    job_id="password-crack",
    charset="abcdefghijklmnopqrstuvwxyz",
    length=5,
    hash_algo="sha256",
    target_hash="5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8"
)

# Воркеры ищут комбинацию
# Когда находят - публикуют в gossip
# {
#     "solutions": [{
#         "combination": "hello",
#         "hash": "5e88489...",
#         "index": 12345
#     }]
# }
```

## Ограничения

- **Максимальная длина:** 12 символов (из-за размера комбинаций)
- **Целевой хеш:** Только один на задачу
- **Память:** ~100 MB на воркер (для буферизации)

## Оптимизация для разных сценариев

### Короткие ключи (≤ 6 символов)

```python
base_chunk_size = 100_000  # Меньшие чанки
```

### Длинные ключи (> 8 символов)

```python
base_chunk_size = 10_000_000  # Большие чанки
progress_interval = 30  # Реже обновления
```

### Быстрые алгоритмы (MD5)

```python
base_chunk_size = 5_000_000  # Больше работы
```

### Медленные алгоритмы (SHA-512)

```python
base_chunk_size = 500_000  # Меньше работы
```
