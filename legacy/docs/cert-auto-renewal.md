# Автоматическая генерация и обновление сертификатов

## Обзор

Система поддерживает автоматическую генерацию SSL/TLS сертификатов для воркеров через координатор, используя ACME-подобный протокол валидации (аналогично Let's Encrypt).

## Процесс работы

### 1. Проверка необходимости обновления

При запуске воркера система автоматически проверяет:
- **Существование сертификата** - если сертификат отсутствует
- **Изменение IP адресов** - если IP адреса машины изменились
- **Изменение hostname** - если DNS имя машины изменилось
- **Срок действия** - если до истечения срока осталось менее 30 дней

### 2. Запрос сертификата (Worker → Coordinator)

Если обновление необходимо, воркер:

1. Генерирует уникальный challenge (64-символьная hex-строка)
2. Сохраняет challenge в контексте приложения
3. Получает свои текущие IP адреса и hostname
4. Отправляет POST запрос на координатор:

```json
POST http://coordinator:8001/internal/cert-request
{
  "node_id": "worker-1",
  "challenge": "abc123...",
  "ip_addresses": ["192.168.1.100", "127.0.0.1"],
  "dns_names": ["worker-node"],
  "old_cert_fingerprint": "def456..."  // опционально
}
```

### 3. Валидация challenge (Coordinator → Worker)

Координатор проверяет подлинность запроса:

1. Извлекает IP адрес воркера из запроса
2. Отправляет GET запрос на воркер:

```
GET http://worker-ip:8002/internal/cert-challenge/abc123...
```

3. Воркер отвечает сохраненным challenge:

```json
{
  "challenge": "abc123...",
  "node_id": "worker-1",
  "timestamp": 1234567890
}
```

4. Координатор проверяет:
   - Challenge совпадает
   - Node ID совпадает

### 4. Генерация сертификата (Coordinator)

После успешной валидации координатор:

1. Генерирует новую пару ключей (RSA 2048)
2. Создает сертификат со следующими параметрами:
   - **Common Name**: node_id воркера
   - **SAN (Subject Alternative Name)**:
     - IP addresses: все указанные IP адреса
     - DNS names: все указанные DNS имена
   - **Подпись**: CA координатора
   - **Срок действия**: 365 дней
   - **Extensions**:
     - SubjectKeyIdentifier
     - AuthorityKeyIdentifier
     - BasicConstraints (CA=false)
     - KeyUsage (digitalSignature, keyEncipherment)
     - ExtendedKeyUsage (serverAuth, clientAuth)

3. Возвращает сертификат и ключ в PEM формате:

```json
{
  "certificate": "-----BEGIN CERTIFICATE-----\n...",
  "private_key": "-----BEGIN RSA PRIVATE KEY-----\n...",
  "node_id": "worker-1",
  "valid_days": 365
}
```

### 5. Сохранение сертификата (Worker)

Воркер:
1. Получает сертификат и ключ
2. Сохраняет их в файлы (например, `certs/node_cert.cer` и `certs/node_key.key`)
3. Запускает HTTPS сервер с новым сертификатом

## API Эндпоинты

### Worker: GET /internal/cert-challenge/{challenge}

**Описание**: Эндпоинт для валидации challenge при запросе сертификата

**Параметры**:
- `challenge` (path) - строка challenge для валидации

**Ответ** (200 OK):
```json
{
  "challenge": "abc123...",
  "node_id": "worker-1",
  "timestamp": 1234567890
}
```

**Ошибки**:
- `404 Not Found` - challenge не найден (не было запроса на сертификат)
- `403 Forbidden` - неверный challenge

### Coordinator: POST /internal/cert-request

**Описание**: Эндпоинт для запроса генерации нового сертификата

**Тело запроса**:
```json
{
  "node_id": "worker-1",
  "challenge": "abc123...",
  "ip_addresses": ["192.168.1.100", "127.0.0.1"],
  "dns_names": ["worker-node", "localhost"],
  "old_cert_fingerprint": "def456..."  // опционально
}
```

**Ответ** (200 OK):
```json
{
  "certificate": "-----BEGIN CERTIFICATE-----\n...",
  "private_key": "-----BEGIN RSA PRIVATE KEY-----\n...",
  "node_id": "worker-1",
  "valid_days": 365
}
```

**Ошибки**:
- `400 Bad Request` - отсутствуют обязательные параметры
- `403 Forbidden` - эндпоинт доступен только на координаторе / невалидный challenge
- `503 Service Unavailable` - не удалось подключиться к воркеру для валидации
- `500 Internal Server Error` - ошибка генерации сертификата

## Утилиты в ssl_helper.py

### get_certificate_fingerprint(cert_file: str) -> Optional[str]
Получить SHA256 отпечаток сертификата

### get_certificate_san(cert_file: str) -> Tuple[list, list]
Получить IP адреса и DNS имена из SubjectAlternativeName

### get_current_network_info() -> Tuple[list, str]
Получить текущие IP адреса и hostname машины

### needs_certificate_renewal(cert_file: str, ca_cert_file: str = None) -> Tuple[bool, str]
Проверить нужно ли обновление сертификата

**Возвращает**: `(True/False, причина)`

**Причины обновления**:
- `certificate_not_found` - сертификат не существует
- `expiring_soon_N_days` - срок действия истекает через N дней
- `ip_address_changed` - IP адрес машины изменился
- `hostname_changed` - hostname машины изменился
- `check_error_...` - ошибка при проверке

### generate_challenge() -> str
Генерация уникального 64-символьного challenge

### request_certificate_from_coordinator(...) -> Tuple[Optional[str], Optional[str]]
Асинхронный запрос сертификата от координатора

**Параметры**:
- `node_id` - идентификатор узла
- `coordinator_url` - URL координатора
- `challenge` - уникальный challenge
- `ip_addresses` - список IP адресов для сертификата
- `dns_names` - список DNS имен для сертификата
- `old_cert_fingerprint` - опционально, для обновления
- `ca_cert_file` - путь к CA сертификату

**Возвращает**: `(certificate_pem, private_key_pem)` или `(None, None)` при ошибке

### save_certificate_and_key(cert_pem: str, key_pem: str, cert_file: str, key_file: str) -> bool
Сохранить сертификат и ключ в файлы

## Конфигурация

### Координатор (config/coordinator.yaml)

```yaml
node_id: "coordinator-1"
coordinator_mode: true

# CA должен существовать на координаторе
ssl_ca_cert_file: "certs/ca_cert.cer"
ssl_ca_key_file: "certs/ca_key.key"

ssl_cert_file: "certs/coordinator_cert.cer"
ssl_key_file: "certs/coordinator_key.key"
ssl_verify: true
https_enabled: true
```

### Воркер (config/worker.yaml)

```yaml
node_id: "worker-1"
coordinator_mode: false

# Адреса координаторов для автоматического запроса сертификата
coordinator_addresses:
  - "192.168.1.1:8001"

ssl_ca_cert_file: "certs/ca_cert.cer"
ssl_ca_key_file: ""  # Воркеру не нужен CA ключ

ssl_cert_file: "certs/worker_cert.cer"
ssl_key_file: "certs/worker_key.key"
ssl_verify: true
https_enabled: true
```

## Безопасность

### Challenge Validation
- Challenge генерируется криптографически безопасным генератором (`secrets.token_hex`)
- Каждый challenge уникален и одноразовый
- Валидация происходит по HTTP для воркера (чтобы можно было получить первый сертификат)
- Координатор проверяет что воркер действительно контролирует указанный IP

### Certificate Security
- Сертификаты подписываются собственным CA
- Размер ключа: 2048 бит (RSA)
- Хэш-алгоритм: SHA256
- Срок действия: 365 дней
- Автоматическое обновление за 30 дней до истечения

### Network Security
- Запрос сертификата использует HTTP (воркер еще не имеет валидного сертификата)
- После получения сертификата вся коммуникация по HTTPS с CA верификацией
- Challenge валидация предотвращает MITM атаки

## Логирование

### Воркер
```
WARNING | Certificate renewal needed: ip_address_changed
INFO    | Requesting new certificate from coordinator: http://192.168.1.1:8001
INFO    | Successfully received certificate from coordinator
INFO    | Certificate and key saved: certs/worker_cert.cer, certs/worker_key.key
INFO    | Certificate successfully updated from coordinator
```

### Координатор
```
INFO    | Generated certificate for node worker-1
INFO    |   IPs: ['192.168.1.100', '127.0.0.1']
INFO    |   DNS names: ['worker-node', 'localhost']
INFO    |   Replaced cert with fingerprint: def456...
```

## Примеры использования

### Первый запуск воркера (нет сертификата)
```bash
python p2p.py --config config/worker.yaml
```

Воркер автоматически:
1. Обнаружит отсутствие сертификата
2. Запросит сертификат у координатора
3. Получит и сохранит сертификат
4. Запустится с HTTPS

### Изменение IP адреса
При изменении IP адреса машины воркер при следующем запуске:
1. Обнаружит несоответствие IP в сертификате
2. Автоматически запросит новый сертификат с обновленными IP
3. Сохранит новый сертификат

### Ручной запрос обновления
Для принудительного обновления сертификата:
```bash
# Удалить существующий сертификат
rm certs/worker_cert.cer certs/worker_key.key

# Запустить воркер - он автоматически получит новый сертификат
python p2p.py --config config/worker.yaml
```

## Troubleshooting

### Воркер не может получить сертификат

**Проблема**: `Failed to validate challenge`

**Решение**:
1. Проверить что координатор доступен
2. Проверить что воркер доступен по указанному IP
3. Проверить firewall rules (порт должен быть открыт)

### Certificate validation failed

**Проблема**: `Challenge validation failed: 404`

**Решение**:
1. Убедиться что воркер запущен и принимает запросы
2. Проверить что challenge endpoint доступен
3. Проверить логи воркера на наличие ошибок

### No coordinator address configured

**Проблема**: Воркер не может запросить сертификат

**Решение**:
Добавить `coordinator_addresses` в конфигурацию воркера:
```yaml
coordinator_addresses:
  - "192.168.1.1:8001"
```

### IP address mismatch

**Проблема**: SSL ошибка при подключении

**Решение**:
1. Обновить сертификат (удалить старый и перезапустить)
2. Убедиться что все IP адреса машины включены в сертификат
3. Проверить что используется правильный IP для подключения

## Зависимости

Требуется:
- `cryptography` - для работы с сертификатами
- `httpx` - для HTTP запросов
- Опционально: `netifaces` - для получения всех сетевых интерфейсов

Установка:
```bash
pip install cryptography httpx
# Опционально для лучшего определения IP
pip install netifaces
```
