import asyncio
import tarfile
import tempfile
import shutil
import json
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional, Union
from datetime import datetime
import logging
import sys
import os
from layers.service import BaseService, service_method



class ServiceOrchestratorError(Exception):
    """Базовое исключение оркестратора сервисов"""
    pass


class ServiceInstallationError(ServiceOrchestratorError):
    """Ошибка установки сервиса"""
    pass


class ServiceManagementError(ServiceOrchestratorError):
    """Ошибка управления сервисом"""
    pass


class ServiceDistributionError(ServiceOrchestratorError):
    """Ошибка распространения сервиса"""
    pass


class ServiceOrchestrator(BaseService):
    """
    Оркестратор сервисов для P2P Core

    Управляет жизненным циклом сервисов:
    - Установка сервисов из .tar.gz архивов
    - Запуск, остановка, перезапуск сервисов
    - Распространение сервисов между узлами
    - Мониторинг состояния сервисов
    """

    SERVICE_NAME = "orchestrator"

    def __init__(self, service_name: str = "orchestrator", proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.services_dir = self._get_services_directory()
        self.installed_services: Dict[str, Dict[str, Any]] = {}
        self.service_metadata_file = self.services_dir / "services_metadata.json"

        # Создаем директорию сервисов если её нет
        self.services_dir.mkdir(exist_ok=True)

        # Загружаем метаданные установленных сервисов
        self._load_services_metadata()

    def _get_services_directory(self) -> Path:
        """Получить директорию сервисов"""
        if getattr(sys, 'frozen', False):
            exe_dir = Path(sys.executable).parent
        else:
            exe_dir = Path(__file__).parent

        services_dir = exe_dir / "services"
        if not services_dir.exists():
            services_dir = Path.cwd() / "services"

        return services_dir

    def _load_services_metadata(self):
        """Загрузить метаданные установленных сервисов"""
        try:
            if self.service_metadata_file.exists():
                with open(self.service_metadata_file, 'r', encoding='utf-8') as f:
                    self.installed_services = json.load(f)
                self.logger.info(f"Loaded metadata for {len(self.installed_services)} services")
        except Exception as e:
            self.logger.warning(f"Failed to load services metadata: {e}")
            self.installed_services = {}

    def _save_services_metadata(self):
        """Сохранить метаданные установленных сервисов"""
        try:
            with open(self.service_metadata_file, 'w', encoding='utf-8') as f:
                json.dump(self.installed_services, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"Failed to save services metadata: {e}")

    def _calculate_file_hash(self, file_path: Path) -> str:
        """Вычислить SHA256 хеш файла"""
        hash_sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()

    def _validate_service_archive(self, archive_path: Path) -> Dict[str, Any]:
        """Валидация архива сервиса"""
        if not archive_path.exists():
            raise ServiceInstallationError(f"Archive not found: {archive_path}")

        if not tarfile.is_tarfile(archive_path):
            raise ServiceInstallationError(f"Invalid tar.gz archive: {archive_path}")

        validation_result = {
            "valid": True,
            "service_name": None,
            "has_main_py": False,
            "has_manifest": False,
            "manifest_data": None,
            "files": []
        }

        try:
            with tarfile.open(archive_path, 'r:gz') as tar:
                members = tar.getnames()
                validation_result["files"] = members

                # Проверяем структуру архива
                root_dirs = set()
                for member in members:
                    parts = member.split('/')
                    if len(parts) > 0:
                        root_dirs.add(parts[0])

                if len(root_dirs) != 1:
                    raise ServiceInstallationError(
                        "Archive must contain exactly one root directory with service name"
                    )

                service_name = list(root_dirs)[0]
                validation_result["service_name"] = service_name

                # Проверяем наличие main.py
                main_py_path = f"{service_name}/main.py"
                if main_py_path in members:
                    validation_result["has_main_py"] = True
                else:
                    raise ServiceInstallationError(f"Missing main.py in service {service_name}")

                # Проверяем наличие манифеста (опционально)
                manifest_path = f"{service_name}/manifest.json"
                if manifest_path in members:
                    validation_result["has_manifest"] = True
                    manifest_file = tar.extractfile(manifest_path)
                    if manifest_file:
                        try:
                            manifest_data = json.load(manifest_file)
                            validation_result["manifest_data"] = manifest_data
                        except json.JSONDecodeError as e:
                            self.logger.warning(f"Invalid manifest.json in {service_name}: {e}")

        except tarfile.TarError as e:
            raise ServiceInstallationError(f"Error reading archive: {e}")

        return validation_result

    async def initialize(self):
        """Инициализация оркестратора"""
        self.logger.info("Initializing Service Orchestrator")

        # Проверяем состояние установленных сервисов
        await self._verify_installed_services()

        self.logger.info(f"Service Orchestrator initialized with {len(self.installed_services)} services")

    async def cleanup(self):
        """Очистка ресурсов при остановке"""
        self.logger.info("Service Orchestrator cleanup")
        self._save_services_metadata()

    async def _verify_installed_services(self):
        """Проверить состояние установленных сервисов"""
        verified_services = {}

        for service_name, metadata in self.installed_services.items():
            service_path = self.services_dir / service_name
            main_py_path = service_path / "main.py"

            if service_path.exists() and main_py_path.exists():
                verified_services[service_name] = metadata
                self.logger.debug(f"Verified service: {service_name}")
            else:
                self.logger.warning(f"Service directory missing: {service_name}")

        self.installed_services = verified_services
        if len(verified_services) != len(self.installed_services):
            self._save_services_metadata()

    @service_method(description="Install service from tar.gz archive", public=True)
    async def install_service(self, archive_data: bytes, force_reinstall: bool = False) -> Dict[str, Any]:
        """
        Установить сервис из архива .tar.gz

        Args:
            archive_data: Данные архива в формате bytes
            force_reinstall: Принудительная переустановка если сервис уже существует

        Returns:
            Результат установки сервиса
        """
        try:
            # Создаем временный файл для архива
            with tempfile.NamedTemporaryFile(suffix='.tar.gz', delete=False) as temp_file:
                temp_file.write(archive_data)
                temp_archive_path = Path(temp_file.name)

            try:
                # Валидируем архив
                validation = self._validate_service_archive(temp_archive_path)
                service_name = validation["service_name"]

                self.logger.info(f"Installing service: {service_name}")

                # Проверяем, не установлен ли уже сервис
                if service_name in self.installed_services and not force_reinstall:
                    raise ServiceInstallationError(
                        f"Service {service_name} already installed. Use force_reinstall=True to override."
                    )

                # Если сервис уже запущен, останавливаем его
                if service_name in self.installed_services:
                    await self._stop_service_if_running(service_name)

                # Удаляем старую версию если есть
                service_path = self.services_dir / service_name
                if service_path.exists():
                    shutil.rmtree(service_path)

                # Распаковываем архив
                with tarfile.open(temp_archive_path, 'r:gz') as tar:
                    tar.extractall(path=self.services_dir)

                # Вычисляем хеш архива
                archive_hash = self._calculate_file_hash(temp_archive_path)

                # Сохраняем метаданные
                self.installed_services[service_name] = {
                    "installed_at": datetime.now().isoformat(),
                    "archive_hash": archive_hash,
                    "manifest": validation.get("manifest_data"),
                    "files_count": len(validation["files"]),
                    "status": "installed"
                }

                self._save_services_metadata()

                # Пытаемся загрузить и запустить сервис через ServiceManager
                success = await self._load_and_start_service(service_name)

                result = {
                    "success": True,
                    "service_name": service_name,
                    "installed_at": self.installed_services[service_name]["installed_at"],
                    "archive_hash": archive_hash,
                    "auto_started": success
                }

                self.logger.info(f"Service {service_name} installed successfully")
                return result

            finally:
                # Удаляем временный файл
                if temp_archive_path.exists():
                    temp_archive_path.unlink()

        except Exception as e:
            self.logger.error(f"Failed to install service: {e}")
            raise ServiceInstallationError(f"Installation failed: {e}")

    async def _stop_service_if_running(self, service_name: str):
        """Остановить сервис если он запущен"""
        # Получаем ServiceManager напрямую (это не сервис, а внутренний компонент)
        from layers.service import get_global_service_manager
        service_manager = get_global_service_manager()

        if not service_manager:
            self.logger.warning("ServiceManager not available")
            return

        try:
            # Проверяем, запущен ли сервис
            service_instance = service_manager.registry.get_service(service_name)
            if not service_instance:
                self.logger.debug(f"Service {service_name} is not running")
                return

            # Останавливаем сервис через registry
            await service_manager.registry.stop_service(service_name)
            self.logger.info(f"Stopped running service: {service_name}")

        except Exception as e:
            self.logger.warning(f"Could not stop service {service_name}: {e}")

    async def _load_and_start_service(self, service_name: str) -> bool:
        """Загрузить и запустить сервис через ServiceManager"""
        try:
            # Получаем ServiceManager напрямую
            from layers.service import get_global_service_manager
            service_manager = get_global_service_manager()

            if not service_manager:
                self.logger.warning("ServiceManager not available")
                return False

            # Проверяем, не запущен ли уже сервис
            if service_manager.registry.get_service(service_name):
                self.logger.info(f"Service {service_name} is already running")
                return True

            # Загружаем и запускаем сервис
            service_path = self.services_dir / service_name
            if not service_path.exists():
                self.logger.error(f"Service directory not found: {service_path}")
                return False

            # Используем load_service_from_directory из ServiceManager
            success = await service_manager.load_service_from_directory(str(service_path))

            if success:
                self.logger.info(f"Service {service_name} loaded and started")
                return True
            else:
                self.logger.error(f"Failed to load service: {service_name}")
                return False

        except Exception as e:
            self.logger.error(f"Error loading service {service_name}: {e}")
            return False

    @service_method(description="Uninstall service", public=True)
    async def uninstall_service(self, service_name: str) -> Dict[str, Any]:
        """
        Удалить установленный сервис

        Args:
            service_name: Имя сервиса для удаления

        Returns:
            Результат удаления сервиса
        """
        try:
            if service_name not in self.installed_services:
                raise ServiceManagementError(f"Service {service_name} is not installed")

            self.logger.info(f"Uninstalling service: {service_name}")

            # Останавливаем сервис если запущен
            await self._stop_service_if_running(service_name)

            # Удаляем директорию сервиса
            service_path = self.services_dir / service_name
            if service_path.exists():
                shutil.rmtree(service_path)

            # Удаляем метаданные
            del self.installed_services[service_name]
            self._save_services_metadata()

            self.logger.info(f"Service {service_name} uninstalled successfully")

            return {
                "success": True,
                "service_name": service_name,
                "uninstalled_at": datetime.now().isoformat()
            }

        except Exception as e:
            self.logger.error(f"Failed to uninstall service {service_name}: {e}")
            raise ServiceManagementError(f"Uninstallation failed: {e}")

    @service_method(description="Start installed service", public=True)
    async def start_service(self, service_name: str) -> Dict[str, Any]:
        """
        Запустить установленный сервис

        Args:
            service_name: Имя сервиса для запуска

        Returns:
            Результат запуска сервиса
        """
        try:
            if service_name not in self.installed_services:
                raise ServiceManagementError(f"Service {service_name} is not installed")

            self.logger.info(f"Starting service: {service_name}")

            success = await self._load_and_start_service(service_name)

            return {
                "success": success,
                "service_name": service_name,
                "started_at": datetime.now().isoformat() if success else None,
                "message": "Service started successfully" if success else "Failed to start service"
            }

        except Exception as e:
            self.logger.error(f"Failed to start service {service_name}: {e}")
            raise ServiceManagementError(f"Start failed: {e}")

    @service_method(description="Stop running service", public=True)
    async def stop_service(self, service_name: str) -> Dict[str, Any]:
        """
        Остановить запущенный сервис

        Args:
            service_name: Имя сервиса для остановки

        Returns:
            Результат остановки сервиса
        """
        try:
            self.logger.info(f"Stopping service: {service_name}")

            await self._stop_service_if_running(service_name)

            return {
                "success": True,
                "service_name": service_name,
                "stopped_at": datetime.now().isoformat()
            }

        except Exception as e:
            self.logger.error(f"Failed to stop service {service_name}: {e}")
            raise ServiceManagementError(f"Stop failed: {e}")

    @service_method(description="Restart service", public=True)
    async def restart_service(self, service_name: str) -> Dict[str, Any]:
        """
        Перезапустить сервис

        Args:
            service_name: Имя сервиса для перезапуска

        Returns:
            Результат перезапуска сервиса
        """
        try:
            self.logger.info(f"Restarting service: {service_name}")

            # Останавливаем
            await self._stop_service_if_running(service_name)

            # Небольшая пауза
            await asyncio.sleep(1)

            # Запускаем
            success = await self._load_and_start_service(service_name)

            return {
                "success": success,
                "service_name": service_name,
                "restarted_at": datetime.now().isoformat() if success else None,
                "message": "Service restarted successfully" if success else "Failed to restart service"
            }

        except Exception as e:
            self.logger.error(f"Failed to restart service {service_name}: {e}")
            raise ServiceManagementError(f"Restart failed: {e}")

    @service_method(description="Get list of installed services", public=True)
    async def list_services(self) -> Dict[str, Any]:
        """
        Получить список всех установленных сервисов

        Returns:
            Список сервисов с их статусами
        """
        running_services = set()

        # Получаем ServiceManager напрямую
        from layers.service import get_global_service_manager
        service_manager = get_global_service_manager()

        if service_manager:
            try:
                # Получаем список запущенных сервисов напрямую
                running_services = set(service_manager.registry.list_services())
            except Exception as e:
                self.logger.warning(f"Could not get running services list: {e}")

        services_info = {}

        for service_name, metadata in self.installed_services.items():
            service_path = self.services_dir / service_name

            services_info[service_name] = {
                "installed": True,
                "running": service_name in running_services,
                "installed_at": metadata.get("installed_at"),
                "archive_hash": metadata.get("archive_hash"),
                "manifest": metadata.get("manifest"),
                "files_count": metadata.get("files_count", 0),
                "directory_exists": service_path.exists()
            }

        return {
            "total_installed": len(self.installed_services),
            "total_running": len(running_services),
            "services": services_info
        }

    @service_method(description="Get detailed service information", public=True)
    async def get_service_info(self) -> Dict[str, Any]:
        """
        Получение информации о самом сервисе orchestrator
        """
        # Вызываем базовый метод для получения стандартной информации
        base_info = await super().get_service_info()

        # Добавляем специфичную для orchestrator информацию
        total_running = 0
        manager_available = False

        # Получаем ServiceManager напрямую
        from layers.service import get_global_service_manager
        service_manager = get_global_service_manager()

        if service_manager:
            try:
                # Прямой доступ к registry для получения списка сервисов
                running_services = service_manager.registry.list_services()
                total_running = len(running_services)
                manager_available = True
            except Exception as e:
                self.logger.warning(f"Could not get running services count: {e}")

        orchestrator_info = {
            **base_info,
            "orchestrator_specific": {
                "services_directory": str(self.services_dir),
                "total_installed": len(self.installed_services),
                "total_running": total_running,
                "proxy_available": self.proxy is not None,
                "service_manager_available": manager_available,
                "installed_services": list(self.installed_services.keys())
            }
        }
        return orchestrator_info

    @service_method(description="Get detailed information about specific service", public=True)
    async def get_service_details(self, service_name: str) -> Dict[str, Any]:
        """
        Получить детальную информацию о конкретном сервисе

        Args:
            service_name: Имя сервиса

        Returns:
            Детальная информация о сервисе
        """
        if service_name not in self.installed_services:
            raise ServiceManagementError(f"Service {service_name} is not installed")

        metadata = self.installed_services[service_name]
        service_path = self.services_dir / service_name

        # Получаем ServiceManager напрямую
        from layers.service import get_global_service_manager
        service_manager = get_global_service_manager()

        is_running = False
        if service_manager:
            try:
                # Проверяем, запущен ли сервис
                service_instance = service_manager.registry.get_service(service_name)
                is_running = service_instance is not None
            except Exception as e:
                self.logger.warning(f"Could not check if service is running: {e}")

        service_info = {
            "name": service_name,
            "installed": True,
            "running": is_running,
            "directory_path": str(service_path),
            "directory_exists": service_path.exists(),
            **metadata
        }

        # Добавляем информацию о запущенном сервисе
        if is_running and service_manager:
            try:
                # Получаем информацию о сервисе напрямую
                service_instance = service_manager.registry.get_service(service_name)
                if service_instance:
                    running_info = await service_instance.get_service_info()
                    service_info["runtime_info"] = running_info
            except Exception as e:
                self.logger.warning(f"Failed to get runtime info for {service_name}: {e}")

        return service_info

    @service_method(description="Create service archive for distribution", public=True)
    async def export_service(self, service_name: str) -> bytes:
        """
        Создать .tar.gz архив сервиса для распространения

        Args:
            service_name: Имя сервиса для экспорта

        Returns:
            Данные архива в формате bytes
        """
        try:
            if service_name not in self.installed_services:
                raise ServiceManagementError(f"Service {service_name} is not installed")

            service_path = self.services_dir / service_name
            if not service_path.exists():
                raise ServiceManagementError(f"Service directory not found: {service_path}")

            self.logger.info(f"Exporting service: {service_name}")

            # Создаем временный архив
            with tempfile.NamedTemporaryFile(suffix='.tar.gz', delete=False) as temp_file:
                temp_archive_path = Path(temp_file.name)

            try:
                # Создаем архив
                with tarfile.open(temp_archive_path, 'w:gz') as tar:
                    tar.add(service_path, arcname=service_name)

                # Читаем архив в память
                with open(temp_archive_path, 'rb') as f:
                    archive_data = f.read()

                self.logger.info(f"Service {service_name} exported successfully, size: {len(archive_data)} bytes")
                return archive_data

            finally:
                # Удаляем временный файл
                if temp_archive_path.exists():
                    temp_archive_path.unlink()

        except Exception as e:
            self.logger.error(f"Failed to export service {service_name}: {e}")
            raise ServiceDistributionError(f"Export failed: {e}")

    @service_method(description="Distribute service to remote nodes", public=True)
    async def distribute_service(self, service_name: str, target_nodes: List[str]) -> Dict[str, Any]:
        """
        Распространить сервис на удаленные узлы

        Args:
            service_name: Имя сервиса для распространения
            target_nodes: Список целевых узлов (node_id)

        Returns:
            Результаты распространения на каждый узел
        """
        try:
            if not self.proxy:
                raise ServiceDistributionError("No proxy client available for distribution")

            self.logger.info(f"Distributing service {service_name} to {len(target_nodes)} nodes")

            # Экспортируем сервис
            archive_data = await self.export_service(service_name)

            distribution_results = {}

            for node_id in target_nodes:
                try:
                    self.logger.info(f"Sending service {service_name} to node {node_id}")

                    # Отправляем через proxy
                    result = await self.proxy.call_remote_method(
                        node_id=node_id,
                        method_path="orchestrator/install_service",
                        params={
                            "archive_data": archive_data,
                            "force_reinstall": False
                        }
                    )

                    distribution_results[node_id] = {
                        "success": True,
                        "result": result,
                        "distributed_at": datetime.now().isoformat()
                    }

                    self.logger.info(f"Successfully distributed service {service_name} to node {node_id}")

                except Exception as e:
                    self.logger.error(f"Failed to distribute service {service_name} to node {node_id}: {e}")
                    distribution_results[node_id] = {
                        "success": False,
                        "error": str(e),
                        "failed_at": datetime.now().isoformat()
                    }

            successful_distributions = sum(1 for result in distribution_results.values() if result["success"])

            return {
                "service_name": service_name,
                "total_nodes": len(target_nodes),
                "successful_distributions": successful_distributions,
                "failed_distributions": len(target_nodes) - successful_distributions,
                "results": distribution_results
            }

        except Exception as e:
            self.logger.error(f"Failed to distribute service {service_name}: {e}")
            raise ServiceDistributionError(f"Distribution failed: {e}")

    @service_method(description="Get orchestrator status and statistics", public=True)
    async def get_orchestrator_status(self) -> Dict[str, Any]:
        """
        Получить статус оркестратора и статистику

        Returns:
            Статус оркестратора и статистика по сервисам
        """
        total_installed = len(self.installed_services)
        total_running = 0
        manager_available = False
        running_names = set()

        # Получаем ServiceManager напрямую
        from layers.service import get_global_service_manager
        service_manager = get_global_service_manager()

        if service_manager:
            try:
                # Получаем список запущенных сервисов напрямую
                running_names = set(service_manager.registry.list_services())
                total_running = len(running_names)
                manager_available = True
            except Exception as e:
                self.logger.warning(f"Could not get running services for stats: {e}")

        # Статистика по сервисам
        service_stats = {
            "installed_but_not_running": 0,
            "running_but_not_installed": 0,
            "healthy": 0
        }

        if manager_available:
            installed_names = set(self.installed_services.keys())

            service_stats["installed_but_not_running"] = len(installed_names - running_names)
            service_stats["running_but_not_installed"] = len(running_names - installed_names)
            service_stats["healthy"] = len(installed_names & running_names)
        else:
            service_stats["installed_but_not_running"] = total_installed

        return {
            "orchestrator_status": "running",
            "services_directory": str(self.services_dir),
            "proxy_available": self.proxy is not None,
            "service_manager_available": manager_available,
            "statistics": {
                "total_installed": total_installed,
                "total_running": total_running,
                **service_stats
            },
            "uptime": "N/A",
            "last_check": datetime.now().isoformat()
        }


# Точка входа для загрузки сервиса
class Run(ServiceOrchestrator):
    """Класс для загрузки оркестратора сервисов"""
    pass