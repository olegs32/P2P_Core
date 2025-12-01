# P2P Core - Build Scripts

Скрипты для сборки проекта P2P Core в исполняемые файлы через PyInstaller.

## Файлы

- **build_p2p.py** - основной Python скрипт сборки с настройками PyInstaller
- **build.sh** - shell wrapper для Linux/macOS
- **build.bat** - batch wrapper для Windows

## Использование

### Linux/macOS

```bash
# Стандартная сборка (один .exe файл)
./scripts/build.sh

# Сборка с очисткой предыдущих файлов
./scripts/build.sh --clean

# Сборка в папку (быстрее, но больше файлов)
./scripts/build.sh --onedir

# Сборка с отладкой
./scripts/build.sh --debug

# Комбинация опций
./scripts/build.sh --clean --onedir
```

### Windows

```cmd
# Стандартная сборка
scripts\build.bat

# С параметрами
scripts\build.bat --clean
scripts\build.bat --onedir
scripts\build.bat --clean --onedir --debug
```

### Прямой запуск Python скрипта

```bash
python scripts/build_p2p.py --help
python scripts/build_p2p.py --clean
python scripts/build_p2p.py --onedir
```

## Параметры

- `--clean` - Удалить build/ и dist/p2p.exe перед сборкой (dist/services сохраняется!)
- `--onedir` - Создать папку с файлами вместо одного .exe (по умолчанию --onefile)
- `--debug` - Включить отладочный вывод в консоли

**ВАЖНО:** Папка `dist/` содержит `services/` для тестирования собранного exe и НЕ удаляется даже с `--clean`!

## Режимы сборки

### --onefile (по умолчанию) ✅

Создает один исполняемый файл `dist/p2p.exe`.

Сервисы из `dist/services/` включаются внутрь exe, но сама папка `dist/services/` сохраняется для тестирования собранного приложения.

**Плюсы:**
- Один файл для распространения
- Проще деплой

**Минусы:**
- Медленнее запуск (распаковка во временную папку)
- Долгая сборка
- Больший размер

**Использование:**
```bash
./scripts/build.sh
# Результат: dist/p2p.exe
```

### --onedir

Создает папку `dist/p2p/` с исполняемым файлом и зависимостями.

**Плюсы:**
- Быстрый запуск
- Быстрая сборка
- Проще отладка

**Минусы:**
- Много файлов
- Сложнее распространять

**Использование:**
```bash
./scripts/build.sh --onedir
# Результат: dist/p2p/p2p.exe и файлы рядом
```

## Что включается в сборку

### Модули Python
- Все модули из `layers/`
- Все сервисы из `dist/services/`
- Зависимости из requirements.txt

### Data файлы (внутри exe)
- `dist/services/` - все сервисы со всем содержимым включаются в exe:
  - `certs_tool/certmgr.exe`, `certs_tool/csptest.exe`
  - `metrics_dashboard/templates/dashboard.html`
  - `metrics_dashboard/README.md`
  - Все остальные файлы сервисов
- `.env` файлы (если есть)

**Важно:** Папка `dist/services/` на диске НЕ удаляется - она используется для тестирования собранного exe!

### Зависимости
- httpx
- fastapi, uvicorn, starlette
- pydantic
- PyJWT
- psutil
- cachetools
- cryptography
- lz4
- pyyaml
- python-dotenv

## Результат сборки

После успешной сборки:

```
dist/
├── p2p.exe              # Исполняемый файл (если --onefile)
├── p2p/                 # Папка с файлами (если --onedir)
│   ├── p2p.exe
│   ├── _internal/       # Библиотеки
│   └── ...
└── services/            # Сервисы (сохраняются)
    ├── certs_tool/
    ├── metrics_dashboard/
    ├── metrics_reporter/
    └── ...
```

## Запуск собранного приложения

### Координатор

```bash
# --onefile
./dist/p2p.exe coordinator --port 8001 --address 127.0.0.1 --node-id coord1

# --onedir
./dist/p2p/p2p.exe coordinator --port 8001 --address 127.0.0.1 --node-id coord1
```

### Воркер

```bash
# --onefile
./dist/p2p.exe worker --port 8100 --coord 127.0.0.1:8001 --node-id worker1

# --onedir
./dist/p2p/p2p.exe worker --port 8100 --coord 127.0.0.1:8001 --node-id worker1
```

### С конфигурацией из YAML

```bash
./dist/p2p.exe --config config/coordinator.yaml
./dist/p2p.exe --config config/worker1.yaml
```

## Требования

- Python 3.7+
- PyInstaller (устанавливается автоматически)
- Все зависимости из requirements.txt

## Установка зависимостей

```bash
pip install -r requirements.txt
```

## Troubleshooting

### Ошибка "Module not found"

Если PyInstaller не находит модуль, добавьте его в `collect_hidden_imports()` в `build_p2p.py`:

```python
hidden = [
    # ...
    'your_module_name',
]
```

### Ошибка "File not found" при запуске

Проверьте что файл включен в `collect_data_files()`:

```python
data_files.append("path/to/file;destination/path")
```

### Большой размер .exe

Используйте `--onedir` для уменьшения времени сборки, или добавьте модули в excludes:

```python
excludes = [
    'matplotlib',
    'numpy',
    # ...
]
```

### Медленный запуск --onefile

Используйте `--onedir` режим для производства, если медленный запуск критичен.

### Сервисы не найдены

Убедитесь что `dist/services/` существует и содержит сервисы. Папка `dist/services/` никогда не удаляется, даже с `--clean`.

## Размер сборки

Типичные размеры:

- **--onefile**: ~30-50 MB (один файл)
- **--onedir**: ~50-80 MB (распакованные файлы)

Размер зависит от:
- Количества зависимостей
- Включенных data файлов
- Наличия бинарных библиотек (cryptography, lz4)

## Оптимизация

### Уменьшение размера

1. Исключите неиспользуемые модули в excludes
2. Используйте UPX для сжатия (опционально):
   ```bash
   # Добавьте в build_p2p.py
   cmd.append('--upx-dir=/path/to/upx')
   ```

### Ускорение сборки

1. Используйте `--onedir` вместо `--onefile`
2. Не используйте `--clean` если не требуется
3. Используйте кэш PyInstaller (по умолчанию включен)

## CI/CD Integration

### GitHub Actions

```yaml
- name: Build P2P executable
  run: |
    pip install -r requirements.txt
    python scripts/build_p2p.py --clean

- name: Upload artifact
  uses: actions/upload-artifact@v3
  with:
    name: p2p-executable
    path: dist/p2p.exe
```

### GitLab CI

```yaml
build:
  script:
    - pip install -r requirements.txt
    - python scripts/build_p2p.py --clean
  artifacts:
    paths:
      - dist/p2p.exe
```

## Версионирование

Для добавления версии в .exe файл, добавьте version info:

```python
# В build_p2p.py
cmd.extend([
    '--version-file', 'version_info.txt'
])
```

Создайте `version_info.txt`:
```
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(1, 0, 0, 0),
    prodvers=(1, 0, 0, 0),
    ...
  ),
  ...
)
```

## Дополнительная информация

- [PyInstaller документация](https://pyinstaller.org/)
- [P2P Core README](../README.md)
- [Metrics Dashboard документация](../dist/services/metrics_dashboard/README.md)

## Лицензия

См. LICENSE файл в корне проекта.
