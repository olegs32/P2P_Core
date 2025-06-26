# P2P Admin System

Асинхронная P2P система для администрирования локальных сервисов, построенная на Python с использованием FastAPI и Streamlit.

## 🌟 Особенности

- **P2P архитектура** - Полностью децентрализованная система без единой точки отказа
- **Самописная DHT** - Distributed Hash Table на основе Kademlia для маршрутизации
- **Асинхронное взаимодействие** - Построено на asyncio для высокой производительности
- **Прозрачные RPC вызовы** - Синтаксис `await service.node.domain.method()`
- **Веб-интерфейс** - Streamlit dashboard для мониторинга и управления
- **Безопасность** - JWT токены и PKI инфраструктура
- **Масштабируемость** - Горизонтальное масштабирование узлов

## 📋 Требования

- Python 3.9+
- Redis (опционально)
- Docker и Docker Compose (для контейнерного запуска)

## 🚀 Быстрый старт

### Локальная установка

1. **Клонирование репозитория:**
```bash
git clone <repository>
cd p2p_admin_system
```

2. **Создание виртуального окружения:**
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# или
venv\Scripts\activate  # Windows
```

3. **Установка зависимостей:**
```bash
pip install -r requirements.txt
```

4. **Настройка конфигурации:**
```bash
cp .env.example .env
# Отредактируйте .env файл
```

5. **Запуск первого узла (Bootstrap):**
```bash
python run.py --host 127.0.0.1 --port 8000 --dht-port 5678
```

6. **Запуск дополнительных узлов:**
```bash
# Узел 2
python run.py --host 127.0.0.1 --port 8001 --dht-port 5679 --bootstrap 127.0.0.1:5678

# Узел 3
python run.py --host 127.0.0.1 --port 8002 --dht-port 5680 --bootstrap 127.0.0.1:5678
```

7. **Запуск веб-интерфейса:**
```bash
streamlit run admin/app.py
```

### Docker Compose

1. **Запуск всей системы:**
```bash
docker-compose up -d
```

2. **Просмотр логов:**
```bash
docker-compose logs -f
```

3. **Остановка системы:**
```bash
docker-compose down
```

## 🏗️ Архитектура

### Компоненты системы

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   P2P Node 1    │────▶│   P2P Node 2    │────▶│   P2P Node 3    │
│                 │     │                 │     │                 │
│  ┌───────────┐  │     │  ┌───────────┐  │     │  ┌───────────┐  │
│  │    DHT    │  │     │  │    DHT    │  │     │  │    DHT    │  │
│  └───────────┘  │     │  └───────────┘  │     │  └───────────┘  │
│  ┌───────────┐  │     │  ┌───────────┐  │     │  ┌───────────┐  │
│  │ Services  │  │     │  │ Services  │  │     │  │ Services  │  │
│  └───────────┘  │     │  └───────────┘  │     │  └───────────┘  │
│  ┌───────────┐  │     │  ┌───────────┐  │     │  ┌───────────┐  │
│  │ FastAPI   │  │     │  │ FastAPI   │  │     │  │ FastAPI   │  │
│  └───────────┘  │     │  └───────────┘  │     │  └───────────┘  │
└─────────────────┘     └─────────────────┘     └─────────────────┘
         ▲                       ▲                       ▲
         │                       │                       │
         └───────────────────────┴───────────────────────┘
                                 │
                         ┌───────────────┐
                         │   Streamlit   │
                         │  Admin Panel  │
                         └───────────────┘
```

### Основные модули

- **core/dht.py** - Реализация Distributed Hash Table
- **core/p2p_node.py** - Основной P2P узел
- **core/rpc_proxy.py** - Прокси для RPC вызовов
- **core/auth.py** - Авторизация и безопасность
- **api/main.py** - FastAPI приложение
- **admin/app.py** - Streamlit интерфейс
- **services/** - Системные сервисы

## 📡 API

### REST API Endpoints

- `GET /health` - Проверка здоровья узла
- `GET /api/info` - Информация об узле
- `GET /api/stats` - Статистика узла
- `POST /p2p/message` - Прием P2P сообщений
- `POST /rpc` - RPC вызовы

### WebSocket

- `WS /ws` - WebSocket соединение для real-time обновлений

### Примеры использования

#### Python клиент
```python
from core import create_service_proxy

# Подключение к узлу
proxy = await create_service_proxy("node1", "192.168.1.100", 8000, auth_token)

# Вызов удаленного сервиса
processes = await proxy.system.process.list_processes()
system_info = await proxy.system.monitor.get_status()

# Управление процессами
await proxy.system.process.start_process("my_service", "/usr/bin/service")
await proxy.system.process.stop_process("my_service")
```

#### CURL
```bash
# Получение статуса
curl http://localhost:8000/api/stats

# Выполнение команды
curl -X POST http://localhost:8000/api/v1/execute \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"command": "ls -la", "timeout": 30}'
```

## 🛠️ Конфигурация

### Основные параметры (.env)

```bash
# Узел
NODE_HOST=0.0.0.0
NODE_PORT=8000
DHT_PORT=5678

# Безопасность
AUTH_SECRET=your-secret-key
TOKEN_EXPIRE_MINUTES=60

# Сеть
BOOTSTRAP_NODES=192.168.1.100:5678
MAX_PEERS=100
PEER_TIMEOUT=120

# Мониторинг
METRICS_ENABLED=true
LOG_LEVEL=INFO
```

### Сервисы

Система включает следующие сервисы:

1. **Process Manager** - Управление процессами
   - Запуск/остановка процессов
   - Мониторинг состояния
   - Автоматический перезапуск

2. **File Manager** - Работа с файлами
   - Чтение/запись файлов
   - Навигация по файловой системе
   - Поиск файлов

3. **Network Manager** - Сетевые операции
   - Сканирование портов
   - Мониторинг интерфейсов
   - Трассировка маршрутов

4. **System Monitor** - Мониторинг системы
   - CPU, память, диск
   - Сетевая статистика
   - Алерты и уведомления

## 🔒 Безопасность

### Аутентификация

- JWT токены для API
- Срок жизни токенов настраивается
- Поддержка refresh токенов

### Авторизация

- PKI инфраструктура для узлов
- Цифровые подписи сообщений
- Шифрование чувствительных данных

### Сетевая безопасность

- TLS для защищенных соединений
- Проверка доверенных узлов
- Ограничение доступа к критичным операциям

## 📊 Мониторинг

### Встроенные метрики

- Системные ресурсы (CPU, память, диск)
- Сетевая активность
- Статус узлов и соединений
- Очередь задач

### Интеграции

- Prometheus endpoint для экспорта метрик
- Grafana dashboards
- Алерты через webhooks

## 🧪 Тестирование

```bash
# Запуск тестов
pytest

# С покрытием
pytest --cov=core --cov=api --cov=services

# Только unit тесты
pytest tests/unit

# Только интеграционные тесты
pytest tests/integration
```

## 🚧 Разработка

### Структура проекта

```
p2p_admin_system/
├── core/               # Основные P2P компоненты
├── api/                # FastAPI приложение
├── admin/              # Streamlit интерфейс
├── services/           # Системные сервисы
├── config/             # Конфигурация
├── tests/              # Тесты
├── docs/               # Документация
├── monitoring/         # Конфигурации мониторинга
├── nginx/              # Nginx конфигурация
├── requirements.txt    # Python зависимости
├── docker-compose.yml  # Docker оркестрация
└── run.py              # Точка входа
```

### Добавление нового сервиса

1. Создайте класс сервиса в `services/`
2. Зарегистрируйте в `run.py`
3. Добавьте маршруты в `api/routes.py`
4. Обновите документацию

## 📝 Лицензия

MIT License

## 🤝 Вклад в проект

1. Fork репозитория
2. Создайте feature branch
3. Commit изменения
4. Push в branch
5. Создайте Pull Request

## 📞 Поддержка

- GitHub Issues
- Email: support@p2p-admin.example
- Документация: https://docs.p2p-admin.example