# P2P Cluster Dashboard

Веб-интерфейс для мониторинга и управления кластером P2P координатора и воркеров.

## Возможности

### Мониторинг
- **Реал-тайм метрики**: CPU, память, диск для координатора и всех воркеров
- **Исторические графики**: Визуализация изменений метрик во времени (последние 100 точек)
- **Статистика кластера**: Активные воркеры, количество сервисов, uptime
- **Адаптивные обновления**: Автоматическое обновление каждые 5 секунд

### Управление
- **Управление сервисами**: Запуск, остановка, перезапуск сервисов на воркерах
- **Централизованное управление**: Все команды идут через координатор
- **Статус сервисов**: Отображение состояния всех сервисов на всех узлах

## Архитектура

### Компоненты

1. **metrics_dashboard** (координатор)
   - Веб-интерфейс на `/dashboard`
   - API endpoints для получения метрик
   - Хранение метрик от воркеров
   - Управление сервисами на воркерах

2. **metrics_reporter** (воркеры)
   - Сбор локальных метрик
   - Периодическая отправка на координатор
   - Адаптивный интервал отправки (30-300 секунд)

### Поток данных

```
Worker                          Coordinator
  │                                 │
  ├─► Собирает метрики             │
  │   (CPU, память, диск)          │
  │                                 │
  ├─► Собирает статусы сервисов    │
  │                                 │
  ├─► Отправляет на координатор ──►│
  │   (каждые 30-300 сек)          │
  │                                 │
  │                             Хранит метрики
  │                             (последние 100 точек)
  │                                 │
  │                             Отображает в веб-UI
  │                                 │
  │◄── Команды управления ──────────┤
  │    сервисами                    │
```

## Использование

### Доступ к dashboard

1. Запустите координатор
2. Откройте браузер: `http://coordinator_ip:port/dashboard`
3. Или с HTTPS: `https://coordinator_ip:port/dashboard`

### API Endpoints

#### Получение метрик
```bash
# Все метрики кластера
GET /api/dashboard/metrics

# История метрик для узла
GET /api/dashboard/history/{node_id}

# Статистика dashboard
GET /api/dashboard/stats
```

#### Управление сервисами
```bash
POST /api/dashboard/control-service
Content-Type: application/json

{
  "worker_id": "worker-001",
  "service_name": "my_service",
  "action": "restart"  # start, stop, restart
}
```

### RPC методы

#### Dashboard service (координатор)

```python
# Получить все метрики кластера
await proxy.metrics_dashboard.get_cluster_metrics()

# Получить историю для узла
await proxy.metrics_dashboard.get_metrics_history(
    node_id="worker-001",
    limit=50
)

# Получить статистику
await proxy.metrics_dashboard.get_dashboard_stats()

# Управление сервисом на воркере
await proxy.metrics_dashboard.control_service(
    worker_id="worker-001",
    service_name="my_service",
    action="restart"
)

# Очистить историю метрик
await proxy.metrics_dashboard.clear_metrics_history(node_id="worker-001")
```

#### Reporter service (воркер)

```python
# Получить статистику reporter'а
await proxy.metrics_reporter.get_stats()

# Управление reporter'ом
await proxy.metrics_reporter.control_reporter(action="stop")  # stop, start, status, report_now

# Установить интервал отправки
await proxy.metrics_reporter.set_interval(interval=120)  # 30-300 секунд
```

## Конфигурация

### Адаптивный интервал отправки

Reporter автоматически адаптирует интервал отправки метрик на основе:
- **Стабильности метрик**: Если метрики стабильны (низкая дисперсия) → интервал увеличивается до 300с
- **Изменчивости метрик**: Если метрики меняются (высокая дисперсия) → интервал уменьшается до 30с
- **Ответа координатора**: Координатор может предложить оптимальный интервал

### Очистка устаревших данных

Dashboard автоматически удаляет воркеров, которые не отправляли метрики более 10 минут.

## Структура файлов

```
dist/services/
├── metrics_dashboard/          # Сервис dashboard (координатор)
│   ├── __init__.py
│   ├── main.py                # Основная логика
│   └── templates/
│       └── dashboard.html     # Веб-интерфейс
│
└── metrics_reporter/           # Сервис reporter (воркеры)
    ├── __init__.py
    └── main.py                # Основная логика
```

## Особенности реализации

### Автоматический запуск

- **Dashboard**: Автоматически инициализируется на координаторе
- **Reporter**: Автоматически запускается только на воркерах (не на координаторе)

### Метрики

Собираемые метрики:
- **CPU**: Процент использования
- **Memory**: Процент использования + детали (total, available, used)
- **Disk**: Процент использования + детали
- **Services**: Статусы всех сервисов (running, stopped, error)

### История

- Хранится последние **100 точек** для каждого узла
- Автоматически обновляется при получении новых метрик
- Используется для построения графиков

### Веб-интерфейс

Технологии:
- **Bootstrap 5**: Современный адаптивный дизайн
- **Chart.js**: Интерактивные графики метрик
- **Vanilla JS**: Без тяжелых фреймворков
- **Темная тема**: Удобный dark mode для длительной работы

## Примеры использования

### Мониторинг воркера

```python
# На координаторе
stats = await proxy.metrics_dashboard.get_dashboard_stats()
print(f"Active workers: {stats['active_workers']}")
print(f"Total services: {stats['total_services']}")

# Получить метрики конкретного воркера
history = await proxy.metrics_dashboard.get_metrics_history("worker-001", limit=10)
for entry in history['history']:
    print(f"{entry['timestamp']}: CPU {entry['cpu_percent']}%, Memory {entry['memory_percent']}%")
```

### Управление сервисом

```python
# Перезапустить сервис на воркере
result = await proxy.metrics_dashboard.control_service(
    worker_id="worker-001",
    service_name="test_service",
    action="restart"
)

if result['success']:
    print("Service restarted successfully")
else:
    print(f"Error: {result['error']}")
```

### Настройка reporter'а на воркере

```python
# На воркере
# Получить текущую статистику
stats = await proxy.metrics_reporter.get_stats()
print(f"Total reports: {stats['statistics']['total_reports']}")
print(f"Success rate: {stats['statistics']['success_rate_percent']}%")
print(f"Current interval: {stats['current_interval']}s")

# Установить фиксированный интервал
await proxy.metrics_reporter.set_interval(interval=90)

# Отправить метрики немедленно
await proxy.metrics_reporter.control_reporter(action="report_now")
```

## Troubleshooting

### Dashboard не загружается

1. Проверьте что координатор запущен
2. Убедитесь что сервис `metrics_dashboard` загружен:
   ```python
   services = await proxy.system.list_services()
   print('metrics_dashboard' in services)
   ```
3. Проверьте логи координатора

### Воркер не отображается в dashboard

1. Проверьте что `metrics_reporter` запущен на воркере:
   ```python
   stats = await proxy.metrics_reporter.get_stats()
   print(stats['reporter_active'])
   ```
2. Проверьте соединение воркера с координатором
3. Убедитесь что `worker_id` установлен корректно

### Метрики не обновляются

1. Проверьте интервал отправки:
   ```python
   stats = await proxy.metrics_reporter.get_stats()
   print(f"Interval: {stats['current_interval']}s")
   ```
2. Принудительно отправьте метрики:
   ```python
   await proxy.metrics_reporter.control_reporter(action="report_now")
   ```
3. Проверьте ошибки в статистике:
   ```python
   stats = await proxy.metrics_reporter.get_stats()
   print(f"Failed reports: {stats['statistics']['failed_reports']}")
   print(f"Last status: {stats['last_report_status']}")
   ```

## Разработка

### Добавление новых метрик

1. Обновите `_collect_system_metrics()` в `metrics_reporter/main.py`
2. Обновите `report_metrics()` в `metrics_dashboard/main.py`
3. Обновите HTML для отображения новых метрик

### Добавление новых действий управления

1. Добавьте action в `control_service()` в `metrics_dashboard/main.py`
2. Обновите UI в `dashboard.html`

## Безопасность

- Все запросы идут через P2P RPC с JWT аутентификацией
- Управление сервисами требует аутентификации
- Прямых HTTP запросов от UI к воркерам нет
- Все команды проходят через координатор

## Производительность

- **Минимальное влияние**: Метрики собираются асинхронно
- **Адаптивный интервал**: Снижает нагрузку при стабильной системе
- **Ограниченная история**: Только последние 100 точек хранятся в памяти
- **Автоочистка**: Устаревшие данные удаляются автоматически
