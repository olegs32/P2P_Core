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

        logger.debug(f"Certificate written: {cert_name}")

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
def init_storage(password: str, storage_path=None, context=None):
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
    if not storage_path or storage_path == '':
        # Дефолтный путь без обращения к context.config
        storage_path = "data/p2p_secure.bin"

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
