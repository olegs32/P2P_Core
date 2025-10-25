"""
Модуль для персистентного хранения состояния P2P системы
Сохраняет: JWT blacklist, Gossip state, Service state
"""

import json
import logging
import asyncio
from pathlib import Path
from typing import Dict, Any, Set, Optional
from datetime import datetime
from dataclasses import asdict


class StatePersistence:
    """
    Универсальный класс для сохранения и загрузки состояния
    """

    def __init__(self, file_path: Path, auto_save_interval: int = 60):
        """
        Args:
            file_path: путь к файлу состояния
            auto_save_interval: интервал автосохранения (секунды), 0 = отключено
        """
        self.file_path = file_path
        self.auto_save_interval = auto_save_interval
        self.logger = logging.getLogger(f"Persistence:{file_path.name}")
        self._auto_save_task: Optional[asyncio.Task] = None
        self._dirty = False  # флаг изменения данных

        # Создаем директорию если не существует
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, data: Dict[str, Any], pretty: bool = True) -> bool:
        """
        Сохранить данные в файл

        Args:
            data: данные для сохранения
            pretty: форматированный JSON

        Returns:
            True если успешно сохранено
        """
        try:
            # Создаем временный файл для атомарной записи
            temp_file = self.file_path.with_suffix('.tmp')

            with open(temp_file, 'w', encoding='utf-8') as f:
                if pretty:
                    json.dump(data, f, indent=2, ensure_ascii=False, default=str)
                else:
                    json.dump(data, f, ensure_ascii=False, default=str)

            # Атомарное переименование
            temp_file.replace(self.file_path)

            self._dirty = False
            self.logger.debug(f"State saved to {self.file_path}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to save state: {e}")
            return False

    def load(self, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Загрузить данные из файла

        Args:
            default: данные по умолчанию если файл не существует

        Returns:
            загруженные данные или default
        """
        if default is None:
            default = {}

        if not self.file_path.exists():
            self.logger.info(f"State file not found: {self.file_path}, using defaults")
            return default

        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self.logger.info(f"State loaded from {self.file_path}")
            return data

        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON in state file: {e}")
            # Создаем резервную копию поврежденного файла
            backup_file = self.file_path.with_suffix('.corrupted')
            self.file_path.rename(backup_file)
            self.logger.info(f"Corrupted file backed up to {backup_file}")
            return default

        except Exception as e:
            self.logger.error(f"Failed to load state: {e}")
            return default

    def mark_dirty(self):
        """Пометить данные как измененные"""
        self._dirty = True

    async def start_auto_save(self, get_data_callback):
        """
        Запустить автосохранение

        Args:
            get_data_callback: функция для получения текущих данных
        """
        if self.auto_save_interval <= 0:
            return

        async def auto_save_loop():
            while True:
                try:
                    await asyncio.sleep(self.auto_save_interval)

                    if self._dirty:
                        data = get_data_callback()
                        self.save(data)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.logger.error(f"Error in auto-save: {e}")

        self._auto_save_task = asyncio.create_task(auto_save_loop())
        self.logger.info(f"Auto-save started (interval: {self.auto_save_interval}s)")

    async def stop_auto_save(self):
        """Остановить автосохранение"""
        if self._auto_save_task:
            self._auto_save_task.cancel()
            try:
                await self._auto_save_task
            except asyncio.CancelledError:
                pass
            self.logger.info("Auto-save stopped")


class JWTBlacklistPersistence:
    """
    Персистентное хранилище для JWT blacklist
    """

    def __init__(self, file_path: Path):
        self.persistence = StatePersistence(file_path, auto_save_interval=30)
        self.blacklisted_tokens: Set[str] = set()
        self.token_exp_times: Dict[str, float] = {}
        self.logger = logging.getLogger("JWTBlacklistPersistence")

    def load(self):
        """Загрузить blacklist из файла"""
        data = self.persistence.load({
            "blacklisted_tokens": [],
            "token_exp_times": {}
        })

        self.blacklisted_tokens = set(data.get("blacklisted_tokens", []))
        self.token_exp_times = data.get("token_exp_times", {})

        # Очистка просроченных токенов
        self._cleanup_expired()

        self.logger.info(f"Loaded {len(self.blacklisted_tokens)} blacklisted tokens")

    def save(self):
        """Сохранить blacklist в файл"""
        self._cleanup_expired()

        data = {
            "blacklisted_tokens": list(self.blacklisted_tokens),
            "token_exp_times": self.token_exp_times,
            "last_saved": datetime.now().isoformat()
        }

        self.persistence.save(data)

    def add_token(self, token: str, exp_time: float):
        """Добавить токен в blacklist"""
        self.blacklisted_tokens.add(token)
        self.token_exp_times[token] = exp_time
        self.persistence.mark_dirty()

    def is_blacklisted(self, token: str) -> bool:
        """Проверить находится ли токен в blacklist"""
        return token in self.blacklisted_tokens

    def _cleanup_expired(self):
        """Очистка просроченных токенов"""
        now = datetime.now().timestamp()
        expired = [
            token for token, exp_time in self.token_exp_times.items()
            if exp_time < now
        ]

        for token in expired:
            self.blacklisted_tokens.discard(token)
            del self.token_exp_times[token]

        if expired:
            self.logger.info(f"Cleaned up {len(expired)} expired tokens")
            self.persistence.mark_dirty()

    async def start_auto_save(self):
        """Запустить автосохранение"""
        def get_data():
            self._cleanup_expired()
            return {
                "blacklisted_tokens": list(self.blacklisted_tokens),
                "token_exp_times": self.token_exp_times,
                "last_saved": datetime.now().isoformat()
            }

        await self.persistence.start_auto_save(get_data)


class GossipStatePersistence:
    """
    Персистентное хранилище для Gossip state (известные узлы)
    """

    def __init__(self, file_path: Path):
        self.persistence = StatePersistence(file_path, auto_save_interval=60)
        self.logger = logging.getLogger("GossipStatePersistence")

    def save_nodes(self, node_registry: Dict[str, Any]):
        """
        Сохранить информацию об узлах

        Args:
            node_registry: словарь NodeInfo объектов
        """
        # Конвертируем NodeInfo в dict
        nodes_data = {}
        for node_id, node_info in node_registry.items():
            if hasattr(node_info, 'to_dict'):
                nodes_data[node_id] = node_info.to_dict()
            else:
                nodes_data[node_id] = asdict(node_info)

        data = {
            "nodes": nodes_data,
            "last_saved": datetime.now().isoformat()
        }

        self.persistence.save(data)
        self.logger.info(f"Saved {len(nodes_data)} nodes to gossip state")

    def load_nodes(self) -> Dict[str, Dict[str, Any]]:
        """
        Загрузить информацию об узлах

        Returns:
            словарь с данными узлов
        """
        data = self.persistence.load({"nodes": {}})
        nodes = data.get("nodes", {})

        self.logger.info(f"Loaded {len(nodes)} nodes from gossip state")
        return nodes

    async def start_auto_save(self, get_nodes_callback):
        """
        Запустить автосохранение

        Args:
            get_nodes_callback: функция для получения текущего node_registry
        """
        def get_data():
            node_registry = get_nodes_callback()
            nodes_data = {}

            for node_id, node_info in node_registry.items():
                if hasattr(node_info, 'to_dict'):
                    nodes_data[node_id] = node_info.to_dict()
                else:
                    nodes_data[node_id] = asdict(node_info)

            return {
                "nodes": nodes_data,
                "last_saved": datetime.now().isoformat()
            }

        await self.persistence.start_auto_save(get_data)


class ServiceStatePersistence:
    """
    Персистентное хранилище для Service state
    """

    def __init__(self, file_path: Path):
        self.persistence = StatePersistence(file_path, auto_save_interval=60)
        self.logger = logging.getLogger("ServiceStatePersistence")

    def save_services(self, services: Dict[str, Any]):
        """
        Сохранить состояние сервисов

        Args:
            services: словарь сервисов
        """
        services_data = {}

        for service_name, service_instance in services.items():
            services_data[service_name] = {
                "name": service_name,
                "status": service_instance.status.value if hasattr(service_instance.status, 'value') else str(service_instance.status),
                "info": asdict(service_instance.info) if hasattr(service_instance, 'info') else {},
                "last_updated": datetime.now().isoformat()
            }

        data = {
            "services": services_data,
            "last_saved": datetime.now().isoformat()
        }

        self.persistence.save(data)
        self.logger.info(f"Saved {len(services_data)} services to state")

    def load_services(self) -> Dict[str, Dict[str, Any]]:
        """
        Загрузить состояние сервисов

        Returns:
            словарь с данными сервисов
        """
        data = self.persistence.load({"services": {}})
        services = data.get("services", {})

        self.logger.info(f"Loaded {len(services)} services from state")
        return services

    async def start_auto_save(self, get_services_callback):
        """
        Запустить автосохранение

        Args:
            get_services_callback: функция для получения текущих сервисов
        """
        def get_data():
            services = get_services_callback()
            services_data = {}

            for service_name, service_instance in services.items():
                services_data[service_name] = {
                    "name": service_name,
                    "status": service_instance.status.value if hasattr(service_instance.status, 'value') else str(service_instance.status),
                    "info": asdict(service_instance.info) if hasattr(service_instance, 'info') else {},
                    "last_updated": datetime.now().isoformat()
                }

            return {
                "services": services_data,
                "last_saved": datetime.now().isoformat()
            }

        await self.persistence.start_auto_save(get_data)
