"""
File management service for P2P Admin System
"""

import os
import shutil
import asyncio
import hashlib
import aiofiles
import logging
from pathlib import Path
from typing import List, Dict, Optional, Union
from datetime import datetime
import fnmatch
import stat

logger = logging.getLogger(__name__)


class FileManagerService:
    """Сервис управления файлами"""

    def __init__(self, base_path: str = "/", allowed_operations: List[str] = None):
        self.base_path = Path(base_path)
        self.allowed_operations = allowed_operations or [
            "read", "write", "delete", "move", "copy", "list"
        ]

        # Ограничения
        self.max_file_size = 100 * 1024 * 1024  # 100MB
        self.forbidden_paths = [
            "/etc/shadow",
            "/etc/passwd",
            "/root",
            "/boot",
            "/sys",
            "/proc"
        ]

    def _check_path_access(self, path: Path) -> bool:
        """Проверка доступа к пути"""
        # Проверка абсолютного пути
        abs_path = path.resolve()

        # Проверка запрещенных путей
        for forbidden in self.forbidden_paths:
            if str(abs_path).startswith(forbidden):
                logger.warning(f"Access denied to forbidden path: {abs_path}")
                return False

        # Проверка выхода за пределы base_path
        try:
            abs_path.relative_to(self.base_path)
        except ValueError:
            logger.warning(f"Path outside base directory: {abs_path}")
            return False

        return True

    def _get_file_stats(self, path: Path) -> dict:
        """Получение статистики файла"""
        try:
            stat_info = path.stat()

            return {
                "name": path.name,
                "path": str(path),
                "size": stat_info.st_size,
                "mode": oct(stat_info.st_mode),
                "uid": stat_info.st_uid,
                "gid": stat_info.st_gid,
                "atime": datetime.fromtimestamp(stat_info.st_atime).isoformat(),
                "mtime": datetime.fromtimestamp(stat_info.st_mtime).isoformat(),
                "ctime": datetime.fromtimestamp(stat_info.st_ctime).isoformat(),
                "is_file": path.is_file(),
                "is_dir": path.is_dir(),
                "is_link": path.is_symlink(),
                "permissions": {
                    "readable": os.access(path, os.R_OK),
                    "writable": os.access(path, os.W_OK),
                    "executable": os.access(path, os.X_OK)
                }
            }
        except Exception as e:
            logger.error(f"Failed to get stats for {path}: {e}")
            return None

    async def read_file(self, file_path: str, encoding: str = 'utf-8') -> dict:
        """Чтение файла"""
        if "read" not in self.allowed_operations:
            return {"status": "error", "message": "Read operation not allowed"}

        path = Path(file_path)

        # Проверка доступа
        if not self._check_path_access(path):
            return {"status": "error", "message": "Access denied"}

        if not path.exists():
            return {"status": "error", "message": "File not found"}

        if not path.is_file():
            return {"status": "error", "message": "Not a file"}

        # Проверка размера
        if path.stat().st_size > self.max_file_size:
            return {"status": "error", "message": "File too large"}

        try:
            # Определение типа файла
            is_binary = self._is_binary_file(path)

            if is_binary:
                # Чтение бинарного файла
                async with aiofiles.open(path, 'rb') as f:
                    content = await f.read()

                return {
                    "status": "success",
                    "path": str(path),
                    "content": content.hex(),  # Hex представление
                    "encoding": "binary",
                    "size": len(content),
                    "hash": hashlib.sha256(content).hexdigest()
                }
            else:
                # Чтение текстового файла
                async with aiofiles.open(path, 'r', encoding=encoding) as f:
                    content = await f.read()

                return {
                    "status": "success",
                    "path": str(path),
                    "content": content,
                    "encoding": encoding,
                    "size": len(content),
                    "lines": content.count('\n') + 1
                }

        except Exception as e:
            logger.error(f"Failed to read file {path}: {e}")
            return {"status": "error", "message": str(e)}

    async def write_file(self, file_path: str, content: Union[str, bytes],
                         encoding: str = 'utf-8', create_dirs: bool = True) -> dict:
        """Запись в файл"""
        if "write" not in self.allowed_operations:
            return {"status": "error", "message": "Write operation not allowed"}

        path = Path(file_path)

        # Проверка доступа
        if not self._check_path_access(path):
            return {"status": "error", "message": "Access denied"}

        try:
            # Создание директорий если нужно
            if create_dirs and not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)

            # Определение режима записи
            if isinstance(content, bytes):
                mode = 'wb'
                data = content
            else:
                mode = 'w'
                data = content

            # Резервная копия если файл существует
            backup_path = None
            if path.exists():
                backup_path = path.with_suffix(path.suffix + '.bak')
                shutil.copy2(path, backup_path)

            # Запись файла
            async with aiofiles.open(path, mode, encoding=encoding if mode == 'w' else None) as f:
                await f.write(data)

            # Удаление резервной копии при успехе
            if backup_path and backup_path.exists():
                backup_path.unlink()

            return {
                "status": "success",
                "path": str(path),
                "size": path.stat().st_size,
                "backed_up": backup_path is not None
            }

        except Exception as e:
            logger.error(f"Failed to write file {path}: {e}")

            # Восстановление из резервной копии
            if backup_path and backup_path.exists():
                shutil.move(backup_path, path)

            return {"status": "error", "message": str(e)}

    async def delete_file(self, file_path: str, recursive: bool = False) -> dict:
        """Удаление файла или директории"""
        if "delete" not in self.allowed_operations:
            return {"status": "error", "message": "Delete operation not allowed"}

        path = Path(file_path)

        # Проверка доступа
        if not self._check_path_access(path):
            return {"status": "error", "message": "Access denied"}

        if not path.exists():
            return {"status": "error", "message": "File not found"}

        try:
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                if recursive:
                    shutil.rmtree(path)
                else:
                    path.rmdir()
            else:
                return {"status": "error", "message": "Unknown file type"}

            return {
                "status": "success",
                "path": str(path),
                "deleted": True
            }

        except Exception as e:
            logger.error(f"Failed to delete {path}: {e}")
            return {"status": "error", "message": str(e)}

    async def move_file(self, source_path: str, dest_path: str) -> dict:
        """Перемещение файла"""
        if "move" not in self.allowed_operations:
            return {"status": "error", "message": "Move operation not allowed"}

        source = Path(source_path)
        dest = Path(dest_path)

        # Проверка доступа
        if not self._check_path_access(source) or not self._check_path_access(dest):
            return {"status": "error", "message": "Access denied"}

        if not source.exists():
            return {"status": "error", "message": "Source file not found"}

        try:
            # Создание директории назначения
            dest.parent.mkdir(parents=True, exist_ok=True)

            # Перемещение
            shutil.move(str(source), str(dest))

            return {
                "status": "success",
                "source": str(source),
                "destination": str(dest)
            }

        except Exception as e:
            logger.error(f"Failed to move {source} to {dest}: {e}")
            return {"status": "error", "message": str(e)}

    async def copy_file(self, source_path: str, dest_path: str) -> dict:
        """Копирование файла"""
        if "copy" not in self.allowed_operations:
            return {"status": "error", "message": "Copy operation not allowed"}

        source = Path(source_path)
        dest = Path(dest_path)

        # Проверка доступа
        if not self._check_path_access(source) or not self._check_path_access(dest):
            return {"status": "error", "message": "Access denied"}

        if not source.exists():
            return {"status": "error", "message": "Source file not found"}

        try:
            # Создание директории назначения
            dest.parent.mkdir(parents=True, exist_ok=True)

            # Копирование
            if source.is_file():
                shutil.copy2(str(source), str(dest))
            elif source.is_dir():
                shutil.copytree(str(source), str(dest))

            return {
                "status": "success",
                "source": str(source),
                "destination": str(dest)
            }

        except Exception as e:
            logger.error(f"Failed to copy {source} to {dest}: {e}")
            return {"status": "error", "message": str(e)}

    async def list_directory(self, dir_path: str = "/", pattern: str = None) -> dict:
        """Список файлов в директории"""
        if "list" not in self.allowed_operations:
            return {"status": "error", "message": "List operation not allowed"}

        path = Path(dir_path)

        # Проверка доступа
        if not self._check_path_access(path):
            return {"status": "error", "message": "Access denied"}

        if not path.exists():
            return {"status": "error", "message": "Directory not found"}

        if not path.is_dir():
            return {"status": "error", "message": "Not a directory"}

        try:
            files = []

            for item in path.iterdir():
                # Фильтрация по паттерну
                if pattern and not fnmatch.fnmatch(item.name, pattern):
                    continue

                # Получение информации о файле
                file_info = self._get_file_stats(item)
                if file_info:
                    files.append(file_info)

            # Сортировка: сначала директории, потом файлы
            files.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

            return {
                "status": "success",
                "path": str(path),
                "files": files,
                "count": len(files)
            }

        except Exception as e:
            logger.error(f"Failed to list directory {path}: {e}")
            return {"status": "error", "message": str(e)}

    async def get_file_info(self, file_path: str) -> dict:
        """Получение информации о файле"""
        path = Path(file_path)

        # Проверка доступа
        if not self._check_path_access(path):
            return {"status": "error", "message": "Access denied"}

        if not path.exists():
            return {"status": "error", "message": "File not found"}

        try:
            info = self._get_file_stats(path)
            if not info:
                return {"status": "error", "message": "Failed to get file info"}

            # Дополнительная информация
            if path.is_file():
                # Хэш файла
                if path.stat().st_size < 10 * 1024 * 1024:  # Только для файлов < 10MB
                    with open(path, 'rb') as f:
                        info["hash"] = {
                            "md5": hashlib.md5(f.read()).hexdigest(),
                            "sha256": hashlib.sha256(f.read()).hexdigest()
                        }

                # MIME тип
                info["mime_type"] = self._get_mime_type(path)

            return {
                "status": "success",
                "info": info
            }

        except Exception as e:
            logger.error(f"Failed to get info for {path}: {e}")
            return {"status": "error", "message": str(e)}

    async def search_files(self, search_path: str, pattern: str,
                           recursive: bool = True, max_results: int = 100) -> dict:
        """Поиск файлов"""
        path = Path(search_path)

        # Проверка доступа
        if not self._check_path_access(path):
            return {"status": "error", "message": "Access denied"}

        if not path.exists():
            return {"status": "error", "message": "Path not found"}

        try:
            results = []
            count = 0

            if recursive:
                # Рекурсивный поиск
                for root, dirs, files in os.walk(path):
                    for name in files + dirs:
                        if fnmatch.fnmatch(name, pattern):
                            full_path = Path(root) / name
                            file_info = self._get_file_stats(full_path)
                            if file_info:
                                results.append(file_info)
                                count += 1

                                if count >= max_results:
                                    break

                    if count >= max_results:
                        break
            else:
                # Поиск только в текущей директории
                for item in path.iterdir():
                    if fnmatch.fnmatch(item.name, pattern):
                        file_info = self._get_file_stats(item)
                        if file_info:
                            results.append(file_info)
                            count += 1

                            if count >= max_results:
                                break

            return {
                "status": "success",
                "results": results,
                "count": len(results),
                "truncated": count >= max_results
            }

        except Exception as e:
            logger.error(f"Failed to search files: {e}")
            return {"status": "error", "message": str(e)}

    def _is_binary_file(self, path: Path) -> bool:
        """Проверка, является ли файл бинарным"""
        try:
            with open(path, 'rb') as f:
                chunk = f.read(1024)
                if b'\0' in chunk:  # Нулевой байт обычно указывает на бинарный файл
                    return True

                # Проверка на высокий процент непечатаемых символов
                text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)))
                non_text = len([b for b in chunk if b not in text_chars])

                return non_text / len(chunk) > 0.3

        except Exception:
            return True

    def _get_mime_type(self, path: Path) -> str:
        """Определение MIME типа файла"""
        # Простое определение по расширению
        ext = path.suffix.lower()
        mime_types = {
            '.txt': 'text/plain',
            '.html': 'text/html',
            '.css': 'text/css',
            '.js': 'application/javascript',
            '.json': 'application/json',
            '.xml': 'application/xml',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.pdf': 'application/pdf',
            '.zip': 'application/zip',
            '.tar': 'application/x-tar',
            '.gz': 'application/gzip'
        }

        return mime_types.get(ext, 'application/octet-stream')