"""
Менеджер хранилища для интеграции с P2P проектом
"""

import logging
from pathlib import Path
from typing import Optional, Any
from contextlib import contextmanager

from layers.secure_storage import SecureArchive

logger = logging.getLogger("StorageManager")


class P2PStorageManager:
    """
    Менеджер безопасного хранилища для P2P проекта

    Управляет:
    - Конфигурационными файлами
    - Сертификатами
    - Состоянием persistence
    - Другими конфиденциальными данными
    """

    def __init__(self, password: str, storage_path: str = "data/p2p_secure.bin"):
        """
        Инициализация менеджера хранилища

        Args:
            password: Пароль для архива
            storage_path: Путь к файлу хранилища
        """
        self.password = password
        self.storage_path = storage_path
        self.archive: Optional[SecureArchive] = None
        self._is_initialized = False
        self._modified = False  # Флаг изменений для автосохранения
        self._autosave_task = None  # Задача автосохранения

        logger.info(f"P2PStorageManager initialized (storage: {storage_path})")

    @contextmanager
    def initialize(self):
        """
        Контекстный менеджер для инициализации хранилища

        Example:
            with storage_manager.initialize():
                # Теперь можно работать с защищенными файлами
                config = storage_manager.read_config('coordinator.yaml')
        """
        try:
            # Открытие архива
            self.archive = SecureArchive(
                password=self.password,
                archive_path=self.storage_path
            )

            # Загрузка если файл существует
            if Path(self.storage_path).exists():
                self.archive.load()
                logger.info("Existing storage loaded successfully")
            else:
                logger.info("New storage will be created")

            self._is_initialized = True

            yield self

        finally:
            # Сохранение изменений
            if self.archive and self._is_initialized:
                # Создание директории если не существует
                Path(self.storage_path).parent.mkdir(parents=True, exist_ok=True)

                # Сохранение архива
                self.archive.save(self.storage_path)
                logger.info("Storage saved successfully")

            # Очистка
            self.archive = None
            self._is_initialized = False

    def _ensure_initialized(self):
        """Проверка что хранилище инициализировано"""
        if not self._is_initialized or not self.archive:
            raise RuntimeError(
                "Storage not initialized. Use 'with storage_manager.initialize():' first"
            )

    def read_config(self, config_name: str) -> str:
        """
        Чтение конфигурационного файла

        Args:
            config_name: Имя конфига (coordinator.yaml, worker.yaml)

        Returns:
            Содержимое конфига
        """
        self._ensure_initialized()

        config_path = f"config/{config_name}"

        if not self.archive.exists(config_path):
            raise FileNotFoundError(f"Config not found in storage: {config_name}")

        with self.archive.open(config_path, 'r') as f:
            return f.read()

    def write_config(self, config_name: str, content: str):
        """
        Запись конфигурационного файла

        Args:
            config_name: Имя конфига
            content: Содержимое конфига
        """
        self._ensure_initialized()

        config_path = f"config/{config_name}"
        self.archive.write_file(config_path, content)
        self._modified = True  # Помечаем что есть изменения

        logger.debug(f"Config written: {config_name}")

    def read_cert(self, cert_name: str) -> bytes:
        """
        Чтение сертификата

        Args:
            cert_name: Имя сертификата (ca_cert.cer, coordinator_cert.cer, ...)

        Returns:
            Байты сертификата
        """
        self._ensure_initialized()

        cert_path = f"certs/{cert_name}"

        if not self.archive.exists(cert_path):
            raise FileNotFoundError(f"Certificate not found: {cert_name}")

        return self.archive.read_file(cert_path)

    def write_cert(self, cert_name: str, cert_data: bytes):
        """
        Запись сертификата

        Args:
            cert_name: Имя сертификата
            cert_data: Байты сертификата
        """
        self._ensure_initialized()

        cert_path = f"certs/{cert_name}"
        self.archive.write_file(cert_path, cert_data)
        self._modified = True  # Помечаем что есть изменения

        logger.debug(f"Certificate written: {cert_name}")

    def read(self, file_name: str) -> bytes:
        """
        Чтение файла
        Args:
            file_name: Имя файла

        Returns:
            Байты файла
        """
        self._ensure_initialized()

        file_path = f"{file_name}"

        if not self.archive.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_name}")

        return self.archive.read_file(file_path)

    def write(self, file_name: str, file_data: bytes):
        """
        Запись файла

        Args:
            file_name: Имя файла
            file_data: Байты файла
        """
        self._ensure_initialized()

        file_path = f"{file_name}"
        self.archive.write_file(file_path, file_data)
        self._modified = True  # Помечаем что есть изменения

        logger.debug(f"File written: {file_name}")

    def read_state(self, state_name: str) -> str:
        """
        Чтение файла состояния

        Args:
            state_name: Имя файла (jwt_blacklist.json, gossip_state.json, ...)

        Returns:
            Содержимое файла
        """
        self._ensure_initialized()

        state_path = f"state/{state_name}"

        if not self.archive.exists(state_path):
            # Возвращаем пустой JSON если файла нет
            return "{}"

        with self.archive.open(state_path, 'r') as f:
            return f.read()

    def write_state(self, state_name: str, content: str):
        """
        Запись файла состояния

        Args:
            state_name: Имя файла
            content: Содержимое (обычно JSON)
        """
        self._ensure_initialized()

        state_path = f"state/{state_name}"
        self.archive.write_file(state_path, content)
        self._modified = True  # Помечаем что есть изменения

        logger.debug(f"State written: {state_name}")

    def list_files(self, directory: str = "") -> list:
        """
        Список файлов в директории

        Args:
            directory: Путь к директории (config, certs, state, ...)

        Returns:
            Список файлов
        """
        self._ensure_initialized()
        return self.archive.list_files(directory)

    def list_configs(self) -> list:
        """
        Список конфигурационных файлов

        Returns:
            Список файлов в директории config
        """
        return self.list_files("config")

    def list_certs(self) -> list:
        """
        Список файлов сертификатов

        Returns:
            Список файлов в директории certs
        """
        return self.list_files("certs")

    def list_data_files(self) -> list:
        """
        Список файлов данных

        Returns:
            Список файлов в корневой директории data
        """
        return self.list_files("data")

    def exists(self, path: str) -> bool:
        """Проверка существования файла"""
        self._ensure_initialized()
        return self.archive.exists(path)

    def create_nested_backup(self, backup_password: str) -> bytes:
        """
        Создание вложенного зашифрованного backup

        Args:
            backup_password: Пароль для backup архива

        Returns:
            Байты backup архива
        """
        self._ensure_initialized()

        logger.info("Creating nested encrypted backup...")
        backup_bytes = self.archive.create_nested_archive(backup_password)

        logger.info(f"Nested backup created ({len(backup_bytes)} bytes)")
        return backup_bytes

    def get_archive(self) -> SecureArchive:
        """
        Получить прямой доступ к архиву (для продвинутого использования)

        Returns:
            SecureArchive объект
        """
        self._ensure_initialized()
        return self.archive

    def save(self):
        """
        Явное сохранение хранилища на диск

        Используется для:
        - Периодического автосохранения
        - Сохранения перед shutdown
        - Ручного сохранения после важных изменений
        """
        self._ensure_initialized()

        if not self._modified:
            logger.debug("Storage not modified, skipping save")
            return

        try:
            # Создание директории если не существует
            Path(self.storage_path).parent.mkdir(parents=True, exist_ok=True)

            # Сохранение архива
            self.archive.save(self.storage_path)
            self._modified = False  # Сбрасываем флаг изменений
            logger.info(f"Storage saved successfully to {self.storage_path}")

        except Exception as e:
            logger.error(f"Failed to save storage: {e}")
            raise

    async def _autosave_loop(self, interval: int = 60):
        """
        Фоновая задача для периодического автосохранения

        Args:
            interval: Интервал сохранения в секундах (по умолчанию 60)
        """
        import asyncio

        logger.info(f"Storage autosave started (interval: {interval}s)")

        while True:
            try:
                await asyncio.sleep(interval)

                if self._modified:
                    logger.debug("Autosave: detecting changes, saving...")
                    self.save()
                else:
                    logger.debug("Autosave: no changes detected")

            except asyncio.CancelledError:
                # Последнее сохранение перед завершением
                if self._modified:
                    logger.info("Autosave task cancelled, performing final save...")
                    self.save()
                logger.info("Storage autosave stopped")
                break
            except Exception as e:
                logger.error(f"Error in autosave loop: {e}")

    def start_autosave(self, interval: int = 60):
        """
        Запустить фоновое автосохранение

        Args:
            interval: Интервал сохранения в секундах (по умолчанию 60)
        """
        import asyncio

        if self._autosave_task is not None:
            logger.warning("Autosave task already running")
            return

        try:
            loop = asyncio.get_running_loop()
            self._autosave_task = loop.create_task(self._autosave_loop(interval))
            logger.info(f"Storage autosave task started (interval: {interval}s)")
        except RuntimeError:
            logger.warning("No running event loop, autosave not started")

    def stop_autosave(self):
        """
        Остановить фоновое автосохранение
        """
        if self._autosave_task is not None:
            self._autosave_task.cancel()
            self._autosave_task = None
            logger.info("Storage autosave task stopped")


def get_storage_manager(context=None) -> Optional[P2PStorageManager]:
    """
    Получить экземпляр менеджера хранилища из контекста

    Args:
        context: P2PApplicationContext (если None, вернет None)

    Returns:
        P2PStorageManager или None если не найден
    """
    if context is None:
        return None
    return context.get_shared("storage_manager")


@contextmanager
def init_storage(password: str, storage_path=None, context=None, run_type=None):
    """
    Инициализация хранилища с регистрацией в контексте

    Args:
        password: Пароль для хранилища
        storage_path: Путь к файлу хранилища (если None, используется дефолтный)
        context: P2PApplicationContext для регистрации storage_manager

    Example:
        context = P2PApplicationContext(config)
        with init_storage(password="my_secure_password", storage_path="data/p2p.bin", context=context):
            # Весь код приложения здесь
            # storage_manager доступен через context.get_shared("storage_manager")
    """
    if run_type is not None:
        coordinator_mode = run_type
    else:
        coordinator_mode = context.config.coordinator_mode
    if not storage_path or storage_path == '':
        # Дефолтный путь без обращения к context.config
        storage_path = "data/p2p_worker.bin"
        try:
            if coordinator_mode:
                storage_path = "data/p2p_coordinator.bin"
            else:
                storage_path = "data/p2p_worker.bin"
        except Exception:
            pass

    manager = P2PStorageManager(password=password, storage_path=storage_path)

    with manager.initialize():
        # Регистрируем в контексте если он предоставлен
        if context is not None:
            context.set_shared("storage_manager", manager)

        try:
            yield manager
        finally:
            # Очищаем из контекста
            if context is not None:
                context.set_shared("storage_manager", None)
