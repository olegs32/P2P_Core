#!/usr/bin/env python3
"""
Утилита для миграции конфигов и сертификатов в защищенный архив
"""

import sys
import os
from pathlib import Path
from getpass import getpass

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from layers.storage_manager import P2PStorageManager


def migrate_to_secure_storage(password, storage_path):
    """Миграция файлов в защищенное хранилище"""
    storage_path = f"data/{storage_path}"

    print("=" * 60)
    print("P2P Secure Storage Migration Tool")
    print("=" * 60)
    print()

    # Запрос пароля
    print("Enter password for secure storage (8-100 characters):")
    # password = getpass("Password: ")

    if len(password) < 8:
        print("❌ Error: Password must be at least 8 characters")
        return False

    if len(password) > 100:
        print("❌ Error: Password must not exceed 100 characters")
        return False

    # Подтверждение пароля
    # password_confirm = getpass("Confirm password: ")

    # if password != password_confirm:
    #     print("❌ Error: Passwords do not match")
    #     return False

    print()
    print("Creating secure storage...")
    print()

    # Создание менеджера хранилища
    storage_path = "data/p2p_secure.bin"
    manager = P2PStorageManager(password=password, storage_path=storage_path)

    with manager.initialize():
        files_migrated = 0

        # Миграция конфигов
        print("📁 Migrating configuration files...")
        config_dir = Path("config")
        if config_dir.exists():
            for config_file in config_dir.glob("*.yaml"):
                try:
                    with open(config_file, 'r', encoding='utf-8') as f:
                        content = f.read()

                    manager.write_config(config_file.name, content)
                    print(f"  ✅ {config_file.name}")
                    files_migrated += 1
                except Exception as e:
                    print(f"  ❌ {config_file.name}: {e}")

        # Миграция сертификатов
        print()
        print("🔐 Migrating certificates...")
        certs_dir = Path("certs")
        if certs_dir.exists():
            for cert_file in certs_dir.glob("*"):
                if cert_file.is_file():
                    try:
                        with open(cert_file, 'rb') as f:
                            content = f.read()

                        manager.write_cert(cert_file.name, content)
                        print(f"  ✅ {cert_file.name}")
                        files_migrated += 1
                    except Exception as e:
                        print(f"  ❌ {cert_file.name}: {e}")

        # Миграция state файлов
        print()
        print("💾 Migrating state files...")
        for state_pattern in ["data/*/*.json", "*.json"]:
            for state_file in Path("..").glob(state_pattern):
                if state_file.is_file() and state_file.name.endswith('.json'):
                    try:
                        with open(state_file, 'r', encoding='utf-8') as f:
                            content = f.read()

                        manager.write_state(state_file.name, content)
                        print(f"  ✅ {state_file.name}")
                        files_migrated += 1
                    except Exception as e:
                        print(f"  ❌ {state_file.name}: {e}")

    print()
    print("=" * 60)
    print(f"✅ Migration completed!")
    print(f"   Files migrated: {files_migrated}")
    print(f"   Storage location: {storage_path}")
    print()
    print("⚠️  IMPORTANT:")
    print("   1. Backup your password securely")
    print("   2. Test loading the storage before deleting original files")
    print("   3. Keep the original files until you verify the storage works")
    print("=" * 60)

    return True


def test_storage(password, storage_path):
    """Тестирование защищенного хранилища"""

    print("=" * 60)
    print("P2P Secure Storage Test")
    print("=" * 60)
    print()
    storage_path = f"data/{storage_path}"

    if not Path(storage_path).exists():
        print(f"❌ Error: Storage file not found: {storage_path}")
        return False

    try:
        manager = P2PStorageManager(password=password, storage_path=storage_path)

        with manager.initialize():
            print("✅ Storage loaded successfully!")
            print()

            # Список ВСЕХ файлов в корне архива
            print(f"📦 All files in archive ({storage_path}):")
            all_files = manager.list_files("")
            for f in all_files:
                print(f"  - {f}")
            print()

            # Список конфигов
            print("📁 Configuration files:")
            config_files = manager.list_files("config")
            for f in config_files:
                print(f"  - {f}")

            # Список сертификатов
            print()
            print("🔐 Certificates:")
            cert_files = manager.list_files("certs")
            for f in cert_files:
                print(f"  - {f}")

            # Список state файлов
            print()
            print("💾 State files:")
            state_files = manager.list_files("state")
            for f in state_files:
                print(f"  - {f}")

            print()
            print(f"✅ Total files: {len(all_files)}")

    except ValueError as e:
        print(f"❌ Error: {e}")
        print("   (Invalid password or corrupted storage)")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

    return True

def create_backup(password, storage_path):
    """Создание вложенного backup архива"""

    print("=" * 60)
    print("P2P Secure Storage Backup")
    print("=" * 60)
    print()
    storage_path = f"data/{storage_path}"

    # Основной пароль
    # password = getpass("Enter storage password: ")

    # storage_path = "data/p2p_secure.bin"

    if not Path(storage_path).exists():
        print(f"❌ Error: Storage file not found: {storage_path}")
        return False

    # Пароль для backup
    print()
    print("Enter password for backup archive (can be different):")
    backup_password = getpass("Backup password: ")

    if len(backup_password) < 8:
        print("❌ Error: Backup password must be at least 8 characters")
        return False

    try:
        manager = P2PStorageManager(password=password, storage_path=storage_path)

        with manager.initialize():
            print()
            print("Creating nested encrypted backup...")

            backup_bytes = manager.create_nested_backup(backup_password)

            # Сохранение backup
            backup_path = "data/p2p_backup.bin"
            Path(backup_path).parent.mkdir(parents=True, exist_ok=True)

            with open(backup_path, 'wb') as f:
                f.write(backup_bytes)

            print(f"✅ Backup created: {backup_path}")
            print(f"   Size: {len(backup_bytes)} bytes")
            print()
            print("⚠️  Store backup password securely (it's different from main password)")

    except ValueError as e:
        print(f"❌ Error: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

    return True


def extract_and_repack(password, storage_path):
    """Распаковка архива для редактирования и обратная упаковка"""

    print("=" * 60)
    print("P2P Secure Storage Extract & Repack")
    print("=" * 60)
    print()

    extract_dir = Path(f"data/storage/{storage_path}")
    storage_path = f"data/{storage_path}"

    if not Path(storage_path).exists():
        print(f"❌ Error: Storage file not found: {storage_path}")
        return False

    try:
        manager = P2PStorageManager(password=password, storage_path=storage_path)

        with manager.initialize():
            print("📦 Extracting files...")
            print()

            # Очистка и создание директории
            if extract_dir.exists():
                import shutil
                shutil.rmtree(extract_dir)
            extract_dir.mkdir(parents=True, exist_ok=True)

            files_extracted = 0

            # Извлечение конфигов В ПОДПАПКУ
            (extract_dir / "config").mkdir(exist_ok=True)
            for filename in manager.list_files("config"):
                content = manager.read_config(filename)
                with open(extract_dir / "config" / filename, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"  ✅ config/{filename}")
                files_extracted += 1

            # Извлечение сертификатов В ПОДПАПКУ
            (extract_dir / "certs").mkdir(exist_ok=True)
            for filename in manager.list_files("certs"):
                content = manager.read_cert(filename)
                with open(extract_dir / "certs" / filename, 'wb') as f:
                    f.write(content)
                print(f"  ✅ certs/{filename}")
                files_extracted += 1

            # Извлечение state файлов В ПОДПАПКУ
            (extract_dir / "state").mkdir(exist_ok=True)
            for filename in manager.list_files("state"):
                content = manager.read_state(filename)
                with open(extract_dir / "state" / filename, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"  ✅ state/{filename}")
                files_extracted += 1

            print()
            print(f"✅ Extracted {files_extracted} files to: {extract_dir}")
            print()
            print("=" * 60)
            print("You can now edit files in the 'storage' folder")
            print("=" * 60)
            print()

        # Ожидание подтверждения
        response = input("Pack updated files back to archive? (yes/no): ").strip().lower()

        if response not in ['yes', 'y', 'да', '']:
            print("❌ Repacking cancelled")
            return False

        print()
        print("📦 Packing updated files...")
        print()

        # Удаление старого архива
        import os
        if os.path.exists(storage_path):
            os.remove(storage_path)

        # Создание нового архива
        manager = P2PStorageManager(password=password, storage_path=storage_path)

        with manager.initialize():
            files_packed = 0

            # Упаковка конфигов
            config_path = extract_dir / "config"
            if config_path.exists():
                for config_file in config_path.glob("*"):
                    if config_file.is_file():
                        with open(config_file, 'r', encoding='utf-8') as f:
                            content = f.read()
                        manager.write_config(f"config/{config_file.name}", content)
                        print(f"  ✅ config/{config_file.name}")
                        files_packed += 1

            # Упаковка сертификатов
            certs_path = extract_dir / "certs"
            if certs_path.exists():
                for cert_file in certs_path.glob("*"):
                    if cert_file.is_file():
                        with open(cert_file, 'rb') as f:
                            content = f.read()
                        manager.write_cert(cert_file.name, content)
                        print(f"  ✅ certs/{cert_file.name}")
                        files_packed += 1

            # Упаковка state файлов
            state_path = extract_dir / "state"
            if state_path.exists():
                for state_file in state_path.glob("*"):
                    if state_file.is_file():
                        with open(state_file, 'r', encoding='utf-8') as f:
                            content = f.read()
                        manager.write_state(state_file.name, content)
                        print(f"  ✅ state/{state_file.name}")
                        files_packed += 1

            print()
            print(f"✅ Packed {files_packed} files back to: {storage_path}")

    except ValueError as e:
        print(f"❌ Error: {e}")
        return False
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error: {e}")
        return False

    return True


def main():
    password = 'mypass1234'
    if not input('Coordinator store? [Y,N] Y') == '':
        storage_path = "p2p_worker.bin"
        # storage_path = "p2p_secure.bin"
    else:
        storage_path = "p2p_coordinator.bin"
    """Главная функция"""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python storage_tool.py migrate    # Migrate files to secure storage")
        print("  python storage_tool.py test       # Test storage access")
        print("  python storage_tool.py backup     # Create nested backup")
        print("  python storage_tool.py extract    # Extract, edit and repack storage - Run!")
        storage_path = f"{storage_path}"
        success = extract_and_repack(password, storage_path)
        return

    command = sys.argv[1]

    if command == "migrate":
        success = migrate_to_secure_storage(password, storage_path)
    elif command == "test":
        success = test_storage(password, storage_path)
    elif command == "backup":
        success = create_backup(password, storage_path)
    elif command == "extract":
        success = extract_and_repack(password, storage_path)
    else:
        success = extract_and_repack(password, storage_path)

        # print(f"Unknown command: {command}")
        # success = False

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
