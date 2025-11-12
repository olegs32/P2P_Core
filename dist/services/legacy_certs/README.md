# Legacy Certs Service

Сервис управления legacy сертификатами CSP (Cryptographic Service Provider) для P2P Core.

## Описание

Legacy Certs Service предоставляет функциональность для работы с сертификатами CSP:

- **Развертывание сертификатов** из PFX и CER файлов
- **Экспорт сертификатов** в форматы PFX и CER
- **Листинг и поиск** установленных сертификатов
- **Управление контейнерами** ключей

## Требования

### Платформа
- Windows OS
- CSP инструменты: `certmgr.exe`, `csptest.exe`

### Зависимости
- `service_framework` - базовый фреймворк для сервисов P2P Core
- Python 3.8+
- asyncio support

## Конфигурация

Сервис можно настроить через следующие параметры:

```json
{
  "csp_path": "./",           // Путь к CSP утилитам
  "default_pin": "00000000",  // PIN по умолчанию
  "encoding": "cp1251"        // Кодировка для вывода команд
}
```

## API методы

### deploy_certificate

Развертывает сертификат и ключ из PFX и CER файлов.

**Параметры:**
- `pfx_path` (str, required) - Путь к PFX файлу
- `cer_path` (str, required) - Путь к CER файлу
- `pin` (str, optional) - PIN-код для PFX файла (по умолчанию "00000000")

**Возвращает:**
```json
{
  "success": true,
  "pfx_error": "0x00000000",
  "cer_error": "0x00000000",
  "password_error": "0x00000000",
  "container": "HDIMAGE\\container_name"
}
```

**Пример использования:**
```python
result = await proxy.legacy_certs.deploy_certificate(
    pfx_path="/path/to/cert.pfx",
    cer_path="/path/to/cert.cer",
    pin="12345678"
)
```

### list_certificates

Возвращает список всех установленных сертификатов.

**Параметры:** нет

**Возвращает:** Dict с информацией о сертификатах

**Пример:**
```python
certificates = await proxy.legacy_certs.list_certificates()
for cert_id, cert_info in certificates.items():
    print(f"Certificate: {cert_info['Subject']}")
    print(f"Container: {cert_info['Container']}")
```

### find_certificate_by_subject

Находит первый сертификат по паттерну в поле Subject.

**Параметры:**
- `subject_pattern` (str, required) - Паттерн для поиска в Subject

**Возвращает:** Dict с информацией о сертификате или None

**Пример:**
```python
cert = await proxy.legacy_certs.find_certificate_by_subject("Иванов")
if cert:
    print(f"Found: {cert['Subject']}")
```

### find_certificates_by_subject

Находит все сертификаты по паттерну в поле Subject.

**Параметры:**
- `subject_pattern` (str, required) - Паттерн для поиска в Subject

**Возвращает:** List[Dict] с информацией о сертификатах

**Пример:**
```python
certs = await proxy.legacy_certs.find_certificates_by_subject("ООО")
print(f"Found {len(certs)} certificates")
```

### export_certificate_pfx

Экспортирует сертификат с закрытым ключом в PFX файл.

**Параметры:**
- `container_name` (str, required) - Имя контейнера
- `output_path` (str, required) - Путь для сохранения PFX файла
- `password` (str, optional) - Пароль для PFX файла

**Возвращает:** bytes данные PFX файла или False при ошибке

**Пример:**
```python
pfx_data = await proxy.legacy_certs.export_certificate_pfx(
    container_name="HDIMAGE\\container",
    output_path="/path/to/export.pfx",
    password="87654321"
)
if pfx_data:
    print(f"Exported {len(pfx_data)} bytes")
```

### export_certificate_cer

Экспортирует открытую часть сертификата в CER файл.

**Параметры:**
- `container_name` (str, optional) - Имя контейнера
- `thumbprint` (str, optional) - Отпечаток сертификата (альтернатива container_name)
- `output_path` (str, required) - Путь для сохранения CER файла

**Возвращает:** bytes данные CER файла или False при ошибке

**Пример:**
```python
cer_data = await proxy.legacy_certs.export_certificate_cer(
    container_name="HDIMAGE\\container",
    output_path="/path/to/export.cer"
)
```

### export_certificate_by_subject

Находит и экспортирует сертификат по паттерну Subject.

**Параметры:**
- `subject_pattern` (str, required) - Паттерн для поиска в Subject
- `output_pfx` (str, optional) - Путь для PFX файла
- `output_cer` (str, optional) - Путь для CER файла
- `password` (str, optional) - Пароль для PFX файла

**Возвращает:**
```json
{
  "pfx": true,
  "cer": true
}
```

**Пример:**
```python
results = await proxy.legacy_certs.export_certificate_by_subject(
    subject_pattern="Петров",
    output_pfx="/path/petrov.pfx",
    output_cer="/path/petrov.cer",
    password="00000000"
)
print(f"PFX: {results['pfx']}, CER: {results['cer']}")
```

### export_certificates_by_subject

Находит и экспортирует все сертификаты по паттерну Subject.

**Параметры:**
- `subject_pattern` (str, required) - Паттерн для поиска в Subject
- `password` (str, optional) - Пароль для PFX файлов

**Возвращает:** List[Dict] с результатами экспорта для каждого сертификата

**Пример:**
```python
results = await proxy.legacy_certs.export_certificates_by_subject(
    subject_pattern="ООО",
    password="12345678"
)
for result in results:
    print(f"PFX: {result['pfx']}, CER: {result['cer']}")
```

### get_certificate_info

Получает подробную информацию о сертификате по имени контейнера.

**Параметры:**
- `container_name` (str, required) - Имя контейнера

**Возвращает:** Dict с информацией о сертификате или None

**Пример:**
```python
info = await proxy.legacy_certs.get_certificate_info("HDIMAGE\\container")
if info:
    print(f"Subject: {info['Subject']}")
    print(f"Issuer: {info['Issuer']}")
    print(f"Valid from: {info['NotBefore']} to {info['NotAfter']}")
```

## Структура сертификата

Информация о сертификате включает следующие поля:

- `Subject` - DN субъекта сертификата
- `Subject_CN` - Common Name из Subject
- `Subject_O` - Organization из Subject
- `Issuer` - DN издателя сертификата
- `Container` - Имя контейнера ключа
- `Thumbprint` - SHA1 отпечаток сертификата
- `NotBefore` - Дата начала действия
- `NotAfter` - Дата окончания действия
- `KeyUsage` - Назначение ключа
- `ExtKeyUsage` - Расширенное назначение ключа

## Обработка ошибок

Сервис использует кастомные исключения:

- `LegacyCertsServiceError` - базовое исключение
- `CertificateDeploymentError` - ошибка при развертывании сертификата
- `CertificateExportError` - ошибка при экспорте сертификата

**Пример обработки:**
```python
try:
    result = await proxy.legacy_certs.deploy_certificate(
        pfx_path="/path/to/cert.pfx",
        cer_path="/path/to/cert.cer"
    )
except CertificateDeploymentError as e:
    print(f"Deployment failed: {e}")
```

## Коды ошибок CSP

Сервис возвращает коды ошибок от CSP утилит:

- `0x00000000` - Успешное выполнение
- `0x80090016` - Неверный PIN-код
- `0x8009000D` - Некорректные данные
- `0x80090020` - Внутренняя ошибка CSP

## Логирование

Сервис использует стандартную систему логирования P2P Core:

```python
# Уровни логирования:
# - DEBUG: Детальная информация о выполнении команд
# - INFO: Основные операции (deploy, export, list)
# - WARNING: Предупреждения (отсутствие инструментов CSP)
# - ERROR: Ошибки выполнения операций
```

## Безопасность

**Важные замечания:**

1. PIN-коды передаются в открытом виде через параметры команд
2. Сервис требует прав на запись в системное хранилище сертификатов
3. Экспортируемые PFX файлы содержат закрытые ключи
4. Рекомендуется использовать в доверенной среде

## Производительность

- Операции развертывания: ~1-3 секунды на сертификат
- Листинг сертификатов: ~0.5-2 секунды (зависит от количества)
- Экспорт в PFX/CER: ~0.5-1 секунда на файл

## Совместимость

- Протестировано с КриптоПро CSP 5.0
- Совместимо с ГОСТ Р 34.10-2012 и ГОСТ Р 34.11-2012
- Поддержка контейнеров на различных носителях (HDIMAGE, реестр, USB-токены)

## Примеры использования

### Полный цикл работы с сертификатом

```python
# 1. Развертывание сертификата
deploy_result = await proxy.legacy_certs.deploy_certificate(
    pfx_path="C:\\certs\\user.pfx",
    cer_path="C:\\certs\\user.cer",
    pin="12345678"
)

if deploy_result['success']:
    print(f"Deployed to container: {deploy_result['container']}")

    # 2. Поиск установленного сертификата
    cert = await proxy.legacy_certs.find_certificate_by_subject("user")

    if cert:
        # 3. Получение детальной информации
        info = await proxy.legacy_certs.get_certificate_info(
            cert['Container']
        )
        print(f"Certificate valid until: {info['NotAfter']}")

        # 4. Экспорт сертификата
        results = await proxy.legacy_certs.export_certificate_by_subject(
            subject_pattern="user",
            output_pfx="C:\\export\\user.pfx",
            output_cer="C:\\export\\user.cer",
            password="87654321"
        )
        print(f"Export results: {results}")
```

### Массовый экспорт сертификатов

```python
# Экспорт всех сертификатов организации
results = await proxy.legacy_certs.export_certificates_by_subject(
    subject_pattern="ООО \"МояКомпания\"",
    password="00000000"
)

for i, result in enumerate(results):
    if result['pfx'] and result['cer']:
        print(f"Certificate {i+1} exported successfully")
    else:
        print(f"Certificate {i+1} export failed")
```

## Разработка и тестирование

Для тестирования сервиса локально:

```bash
# Запуск в режиме разработки
python -m dist.services.legacy_certs.main

# Запуск тестов
pytest tests/services/test_legacy_certs.py
```

## Известные ограничения

1. Работает только на Windows платформе
2. Требует наличия установленного CSP
3. Не поддерживает работу с сертификатами в формате DER напрямую
4. Операции синхронны на уровне взаимодействия с CSP утилитами

## Версионирование

- **1.0.0** - Первоначальный релиз с базовой функциональностью

## Лицензия

Внутренний сервис P2P Core Team

## Контакты

Для вопросов и предложений обращайтесь к команде P2P Core Team.
