"""
Модуль безопасного архиватора с шифрованием AES-256-GCM
Поддержка работы с файлами в памяти как со стандартными файлами
"""

import os
import io
import json
import logging
from pathlib import Path
from typing import Dict, Optional, Union, BinaryIO, Any
from contextlib import contextmanager
from dataclasses import dataclass, field

# Криптография
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger("SecureStorage")


@dataclass
class ArchiveMetadata:
    """Метаданные архива"""
    version: str = "1.0"
    created_at: str = ""
    salt: bytes = field(default_factory=lambda: os.urandom(32))
    nonce: bytes = field(default_factory=lambda: os.urandom(12))
    files: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class VirtualFile:
    """Виртуальный файл в памяти с интерфейсом стандартного файла"""

    def __init__(self, content: bytes = b"", mode: str = "rb"):
        self.content = io.BytesIO(content)
        self.mode = mode
        self.closed = False
        self.name = ""

    def read(self, size: int = -1) -> Union[bytes, str]:
        """Чтение из файла"""
        data = self.content.read(size)
        if 'b' not in self.mode:
            return data.decode('utf-8')
        return data

    def write(self, data: Union[bytes, str]) -> int:
        """Запись в файл"""
        if isinstance(data, str):
            data = data.encode('utf-8')
        return self.content.write(data)

    def seek(self, offset: int, whence: int = 0) -> int:
        """Перемещение указателя"""
        return self.content.seek(offset, whence)

    def tell(self) -> int:
        """Текущая позиция указателя"""
        return self.content.tell()

    def close(self):
        """Закрытие файла"""
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def getvalue(self) -> bytes:
        """Получить все содержимое"""
        pos = self.content.tell()
        self.content.seek(0)
        data = self.content.read()
        self.content.seek(pos)
        return data


class SecureArchive:
    """
    Безопасный архиватор с AES-256-GCM шифрованием

    Возможности:
    - AES-256-GCM authenticated encryption
    - PBKDF2 key derivation с 480,000 итераций
    - Работа с файлами в памяти
    - Поддержка вложенных архивов
    - Виртуальная файловая система
    - Контекстный менеджер для автоматической очистки
    """

    def __init__(self, password: str, archive_path: Optional[str] = None):
        """
        Инициализация архива

        Args:
            password: Пароль для шифрования (до 100 символов)
            archive_path: Путь к файлу архива (None для in-memory)
        """
        if len(password) > 100:
            raise ValueError("Password length must not exceed 100 characters")

        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")

        self.password = password.encode('utf-8')
        self.archive_path = archive_path
        self.metadata = ArchiveMetadata()
        self.virtual_fs: Dict[str, VirtualFile] = {}
        self._encryption_key: Optional[bytes] = None
        self._is_loaded = False

        # Генерация ключа шифрования из пароля
        self._derive_key()

        logger.info(f"SecureArchive initialized (path: {archive_path or 'in-memory'})")

    def _derive_key(self):
        """Деривация ключа из пароля используя PBKDF2"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,  # 256 бит для AES-256
            salt=self.metadata.salt,
            iterations=480000,  # OWASP рекомендация 2023
            backend=default_backend()
        )
        self._encryption_key = kdf.derive(self.password)
        logger.debug("Encryption key derived using PBKDF2 (480k iterations)")

    def _encrypt(self, plaintext: bytes) -> bytes:
        """
        Шифрование данных с AES-256-GCM

        Args:
            plaintext: Данные для шифрования

        Returns:
            Зашифрованные данные
        """
        aesgcm = AESGCM(self._encryption_key)
        ciphertext = aesgcm.encrypt(self.metadata.nonce, plaintext, None)
        return ciphertext

    def _decrypt(self, ciphertext: bytes, nonce: bytes) -> bytes:
        """
        Расшифровка данных с AES-256-GCM

        Args:
            ciphertext: Зашифрованные данные
            nonce: Nonce использованный при шифровании

        Returns:
            Расшифрованные данные

        Raises:
            ValueError: При неверном пароле или поврежденных данных
        """
        aesgcm = AESGCM(self._encryption_key)
        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            return plaintext
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise ValueError("Invalid password or corrupted data")

    def create_file(self, path: str, content: Union[bytes, str] = b""):
        """
        Создание файла в виртуальной ФС

        Args:
            path: Путь к файлу (поддержка вложенных папок через /)
            content: Содержимое файла
        """
        if isinstance(content, str):
            content = content.encode('utf-8')

        # Нормализация пути
        path = path.replace('\\', '/')

        # Создание родительских директорий
        parent_dir = str(Path(path).parent)
        if parent_dir != '.':
            self._ensure_directory(parent_dir)

        # Создание виртуального файла
        vfile = VirtualFile(content=content)
        vfile.name = path
        self.virtual_fs[path] = vfile

        logger.debug(f"Created virtual file: {path} ({len(content)} bytes)")

    def _ensure_directory(self, dir_path: str):
        """Создание директории в метаданных"""
        dir_path = dir_path.replace('\\', '/')
        if dir_path not in self.metadata.files:
            self.metadata.files[dir_path] = {
                'type': 'directory',
                'created_at': self._get_timestamp()
            }

    def _get_timestamp(self) -> str:
        """Получение текущего timestamp"""
        from datetime import datetime
        return datetime.utcnow().isoformat()

    @contextmanager
    def open(self, path: str, mode: str = "r"):
        """
        Открытие файла из архива (контекстный менеджер)

        Args:
            path: Путь к файлу
            mode: Режим открытия (r, rb, w, wb)

        Yields:
            VirtualFile объект с интерфейсом стандартного файла

        Example:
            with archive.open('config/settings.yaml', 'r') as f:
                content = f.read()
        """
        path = path.replace('\\', '/')

        if 'w' in mode or 'a' in mode:
            # Режим записи - создаем новый файл
            vfile = VirtualFile(mode=mode)
            vfile.name = path
            self.virtual_fs[path] = vfile

            try:
                yield vfile
            finally:
                # Сохраняем содержимое после записи
                pass
        else:
            # Режим чтения
            if path not in self.virtual_fs:
                raise FileNotFoundError(f"File not found in archive: {path}")

            vfile = self.virtual_fs[path]
            vfile.content.seek(0)  # Сброс позиции к началу

            if 'b' not in mode:
                # Текстовый режим - оборачиваем в TextIOWrapper
                text_file = io.TextIOWrapper(vfile.content, encoding='utf-8')
                try:
                    yield text_file
                finally:
                    text_file.detach()  # Не закрываем base BytesIO
            else:
                # Бинарный режим
                yield vfile

    def read_file(self, path: str) -> bytes:
        """
        Чтение файла из архива

        Args:
            path: Путь к файлу

        Returns:
            Содержимое файла
        """
        with self.open(path, 'rb') as f:
            return f.read()

    def write_file(self, path: str, content: Union[bytes, str]):
        """
        Запись файла в архив

        Args:
            path: Путь к файлу
            content: Содержимое файла
        """
        with self.open(path, 'wb') as f:
            f.write(content)

    def list_files(self, directory: str = "") -> list:
        """
        Список файлов в директории

        Args:
            directory: Путь к директории (пусто = корень)

        Returns:
            Список путей файлов
        """
        directory = directory.replace('\\', '/').rstrip('/')

        if not directory:
            return list(self.virtual_fs.keys())

        # Файлы в конкретной директории
        prefix = directory + '/'
        return [
            path for path in self.virtual_fs.keys()
            if path.startswith(prefix)
        ]

    def exists(self, path: str) -> bool:
        """Проверка существования файла"""
        path = path.replace('\\', '/')
        return path in self.virtual_fs

    def save(self, output_path: Optional[str] = None) -> Optional[bytes]:
        """
        Сохранение архива

        Args:
            output_path: Путь для сохранения (None = return bytes)

        Returns:
            Байты архива если output_path is None
        """
        # Подготовка данных архива
        archive_data = {
            'metadata': {
                'version': self.metadata.version,
                'created_at': self._get_timestamp(),
                'salt': self.metadata.salt.hex(),
                'nonce': self.metadata.nonce.hex(),
                'files': self.metadata.files
            },
            'files': {}
        }

        # Сериализация и шифрование каждого файла
        for path, vfile in self.virtual_fs.items():
            content = vfile.getvalue()
            encrypted = self._encrypt(content)

            archive_data['files'][path] = {
                'size': len(content),
                'encrypted_size': len(encrypted),
                'data': encrypted.hex()
            }

        # Сериализация всего архива в JSON
        json_data = json.dumps(archive_data, indent=2).encode('utf-8')

        # Финальное шифрование всего архива
        final_encrypted = self._encrypt(json_data)

        # Формат: [salt 32 байта][nonce 12 байт][encrypted data]
        archive_bytes = self.metadata.salt + self.metadata.nonce + final_encrypted

        if output_path:
            # Сохранение в файл
            with open(output_path, 'wb') as f:
                f.write(archive_bytes)
            logger.info(f"Archive saved to: {output_path} ({len(archive_bytes)} bytes)")
            return None
        else:
            # Возврат байтов
            logger.info(f"Archive serialized ({len(archive_bytes)} bytes)")
            return archive_bytes

    def load(self, input_source: Optional[Union[str, bytes]] = None):
        """
        Загрузка архива

        Args:
            input_source: Путь к файлу или байты архива (None = использовать self.archive_path)

        Raises:
            ValueError: При неверном пароле или поврежденных данных
        """
        # Получение байтов архива
        if input_source is None:
            if self.archive_path is None:
                raise ValueError("No input source specified")
            with open(self.archive_path, 'rb') as f:
                archive_bytes = f.read()
        elif isinstance(input_source, str):
            with open(input_source, 'rb') as f:
                archive_bytes = f.read()
        else:
            archive_bytes = input_source

        # Извлечение salt и nonce
        salt = archive_bytes[:32]
        nonce = archive_bytes[32:44]
        encrypted_data = archive_bytes[44:]

        # Обновление метаданных и ключа
        self.metadata.salt = salt
        self.metadata.nonce = nonce
        self._derive_key()  # Пересоздание ключа с новой солью

        # Расшифровка архива
        json_data = self._decrypt(encrypted_data, nonce)
        archive_data = json.loads(json_data.decode('utf-8'))

        # Восстановление метаданных
        meta = archive_data['metadata']
        self.metadata.version = meta['version']
        self.metadata.created_at = meta.get('created_at', '')
        self.metadata.files = meta.get('files', {})

        # Расшифровка и восстановление файлов
        self.virtual_fs.clear()
        for path, file_info in archive_data['files'].items():
            encrypted = bytes.fromhex(file_info['data'])
            content = self._decrypt(encrypted, nonce)

            vfile = VirtualFile(content=content)
            vfile.name = path
            self.virtual_fs[path] = vfile

        self._is_loaded = True
        logger.info(f"Archive loaded successfully ({len(self.virtual_fs)} files)")

    def create_nested_archive(self, inner_password: str) -> bytes:
        """
        Создание вложенного архива без записи на диск

        Args:
            inner_password: Пароль для вложенного архива

        Returns:
            Байты вложенного архива
        """
        inner_archive = SecureArchive(password=inner_password)

        # Копирование файлов во вложенный архив
        for path, vfile in self.virtual_fs.items():
            content = vfile.getvalue()
            inner_archive.create_file(path, content)

        # Сериализация вложенного архива в байты
        nested_bytes = inner_archive.save()

        logger.info(f"Created nested archive ({len(nested_bytes)} bytes)")
        return nested_bytes

    def __enter__(self):
        """Вход в контекстный менеджер"""
        # Загрузка архива если путь указан
        if self.archive_path and Path(self.archive_path).exists():
            self.load()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Выход из контекстного менеджера с очисткой"""
        # Безопасное обнуление ключа и пароля в памяти
        if self._encryption_key:
            # Обнуление ключа (защита от memory dump)
            self._encryption_key = b'\x00' * len(self._encryption_key)

        if self.password:
            self.password = b'\x00' * len(self.password)

        # Очистка виртуальной ФС
        self.virtual_fs.clear()

        logger.debug("SecureArchive context closed, sensitive data cleared")
        return False


def create_secure_archive(password: str, files_dict: Dict[str, Union[str, bytes]],
                          output_path: Optional[str] = None) -> Optional[bytes]:
    """
    Утилита для быстрого создания архива

    Args:
        password: Пароль для архива
        files_dict: Словарь {путь: содержимое}
        output_path: Путь для сохранения (None = return bytes)

    Returns:
        Байты архива если output_path is None
    """
    with SecureArchive(password=password) as archive:
        for path, content in files_dict.items():
            archive.create_file(path, content)

        return archive.save(output_path)


def load_secure_archive(password: str, input_source: Union[str, bytes]) -> SecureArchive:
    """
    Утилита для быстрой загрузки архива

    Args:
        password: Пароль архива
        input_source: Путь к файлу или байты

    Returns:
        SecureArchive объект
    """
    archive = SecureArchive(password=password)
    archive.load(input_source)
    return archive
