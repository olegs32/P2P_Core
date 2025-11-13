#!/usr/bin/env python3
"""
PyInstaller build script for P2P Core application

Создает исполняемый файл p2p.exe со всеми зависимостями:
- Все модули layers
- Все сервисы из dist/services
- Ресурсы (templates, exe файлы)
- Конфигурационные файлы

Использование:
    python scripts/build_p2p.py [--clean] [--onedir] [--debug]

Опции:
    --clean     Удалить build/ и dist/ перед сборкой
    --onedir    Создать папку с файлами вместо одного .exe (быстрее)
    --debug     Включить отладочный вывод в консоли
"""

import sys
import shutil
import subprocess
from pathlib import Path
import argparse

# Корневая директория проекта
PROJECT_ROOT = Path(__file__).parent.parent.absolute()
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
SERVICES_DIR = PROJECT_ROOT / "dist" / "services"


def clean_build():
    """Удаляет директории build и dist"""
    print("🧹 Очистка предыдущих сборок...")

    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
        print(f"   Удалено: {BUILD_DIR}")

    if DIST_DIR.exists():
        # Сохраняем services директорию
        services_backup = SERVICES_DIR
        if services_backup.exists():
            temp_services = PROJECT_ROOT / "services_backup"
            shutil.move(str(services_backup), str(temp_services))
            print(f"   Сохранены сервисы во временной папке")

        shutil.rmtree(DIST_DIR)
        print(f"   Удалено: {DIST_DIR}")

        # Восстанавливаем services
        if temp_services.exists():
            DIST_DIR.mkdir()
            shutil.move(str(temp_services), str(services_backup))
            print(f"   Восстановлены сервисы")


def collect_services():
    """Собирает список всех сервисов для включения"""
    services = []
    if SERVICES_DIR.exists():
        for service_dir in SERVICES_DIR.iterdir():
            if service_dir.is_dir() and not service_dir.name.startswith('__'):
                services.append(service_dir.name)
    return services


def collect_data_files():
    """Собирает все data файлы для включения в сборку"""
    data_files = []

    # Сервисы (весь dist/services/)
    if SERVICES_DIR.exists():
        # Добавляем всю папку services рекурсивно
        data_files.append(f"{SERVICES_DIR};dist/services")

    # .env файл (если есть)
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        data_files.append(f"{env_file};.")

    # dist/.env (если есть)
    dist_env = DIST_DIR / ".env"
    if dist_env.exists():
        data_files.append(f"{dist_env};dist")

    return data_files


def collect_hidden_imports():
    """Собирает список скрытых импортов"""
    hidden = [
        # Стандартные библиотеки
        'asyncio',
        'logging',
        'pathlib',
        'typing',
        'json',
        'datetime',
        'hashlib',
        'base64',

        # Зависимости проекта
        'httpx',
        'fastapi',
        'uvicorn',
        'pydantic',
        'starlette',
        'PyJWT',
        'psutil',
        'cachetools',
        'cryptography',
        'lz4',
        'yaml',
        'dotenv',

        # Uvicorn dependencies
        'uvicorn.lifespan.on',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.http.auto',
        'uvicorn.logging',

        # FastAPI dependencies
        'fastapi.responses',
        'starlette.responses',
        'starlette.templating',

        # Layers модули
        'layers',
        'layers.application_context',
        'layers.transport',
        'layers.network',
        'layers.cache',
        'layers.service',
        'layers.ssl_helper',
        'layers.storage_manager',
        'layers.secure_storage',
        'layers.persistence',
        'layers.rate_limiter',
        'layers.local_service_bridge',
    ]

    # Добавляем все сервисы
    services = collect_services()
    for service in services:
        hidden.append(f'services.{service}')
        hidden.append(f'services.{service}.main')

    return hidden


def build_pyinstaller(onedir=False, debug=False):
    """Запускает PyInstaller с собранными параметрами"""
    print("\n🔨 Начинаем сборку с PyInstaller...")

    # Базовые параметры
    cmd = [
        'pyinstaller',
        '--name=p2p',
        '--noconfirm',
        f'--workpath={BUILD_DIR}',
        f'--specpath={BUILD_DIR}',
    ]

    # Режим сборки
    if onedir:
        cmd.append('--onedir')
        print("   Режим: папка с файлами (--onedir)")
    else:
        cmd.append('--onefile')
        print("   Режим: один исполняемый файл (--onefile)")

    # Консоль/GUI
    if debug:
        cmd.append('--console')
        cmd.append('--debug=all')
        print("   Отладка: включена")
    else:
        cmd.append('--console')  # Оставляем консоль для логов
        print("   Отладка: выключена")

    # Hidden imports
    hidden_imports = collect_hidden_imports()
    print(f"   Скрытых импортов: {len(hidden_imports)}")
    for imp in hidden_imports:
        cmd.extend(['--hidden-import', imp])

    # Data files
    data_files = collect_data_files()
    print(f"   Файлов данных: {len(data_files)}")
    for data in data_files:
        cmd.extend(['--add-data', data])

    # Пути для поиска модулей
    cmd.extend(['--paths', str(PROJECT_ROOT)])
    cmd.extend(['--paths', str(PROJECT_ROOT / 'layers')])

    # Исключаем ненужные модули для уменьшения размера
    excludes = [
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PIL',
        'tkinter',
        'PyQt5',
        'wx',
    ]
    for exc in excludes:
        cmd.extend(['--exclude-module', exc])

    # Главный файл
    cmd.append(str(PROJECT_ROOT / 'p2p.py'))

    print(f"\n   Команда PyInstaller:")
    print(f"   {' '.join(cmd)}\n")

    # Запускаем
    try:
        result = subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)
        print("\n✅ Сборка завершена успешно!")
        return True
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Ошибка сборки: {e}")
        return False


def show_result_info(onedir=False):
    """Показывает информацию о результате сборки"""
    print("\n" + "="*60)
    print("📦 Результат сборки:")
    print("="*60)

    if onedir:
        exe_path = DIST_DIR / "p2p" / "p2p.exe"
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / 1024 / 1024
            print(f"✅ Исполняемый файл: {exe_path}")
            print(f"   Размер: {size_mb:.1f} MB")
            print(f"   Папка с файлами: {DIST_DIR / 'p2p'}")
        else:
            print(f"❌ Файл не найден: {exe_path}")
    else:
        exe_path = DIST_DIR / "p2p.exe"
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / 1024 / 1024
            print(f"✅ Исполняемый файл: {exe_path}")
            print(f"   Размер: {size_mb:.1f} MB")
        else:
            print(f"❌ Файл не найден: {exe_path}")

    # Проверяем сервисы
    services_path = DIST_DIR / "services"
    if services_path.exists():
        services = list(services_path.iterdir())
        print(f"\n📂 Сервисы ({len(services)}):")
        for service in sorted(services):
            if service.is_dir():
                print(f"   - {service.name}")

    print("\n" + "="*60)
    print("🚀 Использование:")
    print("="*60)
    print(f"Координатор:")
    print(f"  {exe_path} coordinator --port 8001 --address 127.0.0.1 --node-id coord1")
    print(f"\nВоркер:")
    print(f"  {exe_path} worker --port 8100 --coord 127.0.0.1:8001 --node-id worker1")
    print("="*60 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="PyInstaller build script for P2P Core",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--clean', action='store_true',
                       help='Удалить build/ и dist/ перед сборкой')
    parser.add_argument('--onedir', action='store_true',
                       help='Создать папку с файлами вместо одного .exe')
    parser.add_argument('--debug', action='store_true',
                       help='Включить отладочный вывод')

    args = parser.parse_args()

    print("="*60)
    print("🔧 PyInstaller Build Script for P2P Core")
    print("="*60)
    print(f"Корневая папка: {PROJECT_ROOT}")
    print(f"Сервисы: {SERVICES_DIR}")

    # Проверяем наличие p2p.py
    main_file = PROJECT_ROOT / "p2p.py"
    if not main_file.exists():
        print(f"\n❌ Ошибка: Не найден главный файл {main_file}")
        return 1

    # Очистка
    if args.clean:
        clean_build()

    # Сборка
    success = build_pyinstaller(onedir=args.onedir, debug=args.debug)

    if success:
        show_result_info(onedir=args.onedir)
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
