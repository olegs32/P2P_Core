"""
Update Manager Service

Менеджер обновлений для P2P узлов.
Работает на всех узлах (координаторе и воркерах).

Функции:
- Проверка доступных обновлений
- Скачивание обновлений с координатора
- Проверка цифровых подписей
- Установка обновлений
- Backup и rollback
"""

import os
import json
import hashlib
import tarfile
import shutil
import subprocess
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

from layers.service import BaseService, service_method


class Run(BaseService):
    """Update Manager - управление обновлениями узла"""

    def __init__(self, service_name: str = "update_manager", proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.updates_dir = Path("data/update_manager")
        self.downloads_dir = self.updates_dir / "downloads"
        self.backups_dir = self.updates_dir / "backups"
        self.state_file = self.updates_dir / "update_state.json"

        self.public_key = None
        self.current_version = "1.0.0"  # TODO: Load from system
        self.update_in_progress = False
        self.last_check_time = None

    async def initialize(self):
        """Initialize update manager"""
        self.logger.info("Initializing Update Manager...")

        # Create directories
        self.updates_dir.mkdir(parents=True, exist_ok=True)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)

        # Load state
        await self._load_state()

        # Try to get public key from coordinator (non-critical, will retry later if needed)
        await self._fetch_public_key()

        self.logger.info("Update Manager initialized successfully")

    async def _load_state(self):
        """Load update manager state"""
        if self.state_file.exists():
            with open(self.state_file, 'r') as f:
                state = json.load(f)
                self.current_version = state.get("current_version", "1.0.0")
                self.last_check_time = state.get("last_check_time")
            self.logger.info(f"Current version: {self.current_version}")

    async def _save_state(self):
        """Save update manager state"""
        state = {
            "current_version": self.current_version,
            "last_check_time": self.last_check_time,
            "last_updated": datetime.now().isoformat()
        }
        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)

    async def _fetch_public_key(self):
        """Fetch public signing key from coordinator"""
        if not self.proxy:
            self.logger.debug("Proxy not available, cannot fetch public key")
            return False

        try:
            # Check if update_server service is available
            if not hasattr(self.proxy, 'update_server'):
                self.logger.debug("update_server service not available yet")
                return False

            # Try to get from coordinator's update_server
            result = await self.proxy.update_server.coordinator.get_public_key()

            if result.get("success"):
                public_key_pem = result["public_key"].encode('utf-8')
                self.public_key = serialization.load_pem_public_key(
                    public_key_pem,
                    backend=default_backend()
                )
                self.logger.info("Public signing key loaded from coordinator")
                return True
            else:
                self.logger.debug(f"Failed to get public key: {result.get('error')}")
                return False

        except Exception as e:
            self.logger.debug(f"Could not fetch public key from coordinator: {e}")
            return False

    async def _verify_signature(self, data: bytes, signature: bytes) -> bool:
        """Verify signature with public key"""
        # Try to fetch public key if not available
        if not self.public_key:
            self.logger.info("Public key not available, attempting to fetch from coordinator...")
            if not await self._fetch_public_key():
                self.logger.error("Public key not available for verification and could not be fetched")
                return False

        try:
            self.public_key.verify(
                signature,
                data,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )
            return True
        except Exception as e:
            self.logger.error(f"Signature verification failed: {e}")
            return False

    @service_method(description="Check for available updates", public=True)
    async def check_updates(self) -> Dict[str, Any]:
        """Check for available updates from coordinator"""
        self.metrics.increment("check_updates_calls")

        if not self.proxy:
            return {
                "success": False,
                "error": "Proxy not available"
            }

        try:
            # Determine node type
            node_type = "coordinator" if self.context.config.coordinator_mode else "workers"

            # Query coordinator for updates
            result = await self.proxy.update_server.coordinator.list_updates(
                target_node_type=node_type
            )

            if not result.get("success"):
                return result

            # Filter updates newer than current version
            updates = result.get("updates", [])
            available_updates = []

            for update in updates:
                # Simple version comparison (can be improved)
                if self._compare_versions(update["version"], self.current_version) > 0:
                    available_updates.append(update)

            self.last_check_time = datetime.now().isoformat()
            await self._save_state()

            self.metrics.increment("check_updates_success")

            return {
                "success": True,
                "current_version": self.current_version,
                "available_updates": available_updates,
                "has_updates": len(available_updates) > 0,
                "last_check": self.last_check_time
            }

        except Exception as e:
            self.logger.error(f"Failed to check updates: {e}")
            self.metrics.increment("check_updates_errors")
            return {
                "success": False,
                "error": str(e)
            }

    def _compare_versions(self, v1: str, v2: str) -> int:
        """
        Compare two version strings
        Returns: 1 if v1 > v2, -1 if v1 < v2, 0 if equal
        """
        try:
            parts1 = [int(x) for x in v1.split('.')]
            parts2 = [int(x) for x in v2.split('.')]

            # Pad to same length
            while len(parts1) < len(parts2):
                parts1.append(0)
            while len(parts2) < len(parts1):
                parts2.append(0)

            for p1, p2 in zip(parts1, parts2):
                if p1 > p2:
                    return 1
                elif p1 < p2:
                    return -1
            return 0
        except:
            return 0

    @service_method(description="Download and install update", public=True)
    async def install_update(
        self,
        version: str,
        auto_restart: bool = False
    ) -> Dict[str, Any]:
        """
        Download and install an update

        Args:
            version: Version to install
            auto_restart: Automatically restart after installation
        """
        self.metrics.increment("install_update_calls")

        if self.update_in_progress:
            return {
                "success": False,
                "error": "Update already in progress"
            }

        if not self.proxy:
            return {
                "success": False,
                "error": "Proxy not available"
            }

        self.update_in_progress = True

        try:
            # 1. Download update package
            self.logger.info(f"Downloading update {version}...")
            download_result = await self.proxy.update_server.coordinator.download_update(version)

            if not download_result.get("success"):
                return download_result

            # 2. Verify package
            package_data = bytes.fromhex(download_result["package_data"])
            signature = bytes.fromhex(download_result["signature"])
            expected_hash = download_result["hash"]

            # Verify hash
            calculated_hash = hashlib.sha256(package_data).hexdigest()
            if calculated_hash != expected_hash:
                self.logger.error("Package hash mismatch!")
                return {
                    "success": False,
                    "error": "Package integrity check failed"
                }

            # Verify signature
            if not await self._verify_signature(package_data, signature):
                self.logger.error("Signature verification failed!")
                return {
                    "success": False,
                    "error": "Signature verification failed - package may be tampered"
                }

            self.logger.info("Package verified successfully")

            # 3. Save package
            package_file = self.downloads_dir / f"update-{version}.tar.gz"
            with open(package_file, 'wb') as f:
                f.write(package_data)

            # 4. Create backup of current version
            backup_dir = await self._create_backup()
            self.logger.info(f"Backup created: {backup_dir}")

            # 5. Extract and install update
            install_result = await self._install_package(package_file, version)

            if not install_result["success"]:
                # Rollback on failure
                self.logger.error("Installation failed, rolling back...")
                await self._rollback(backup_dir)
                return install_result

            # 6. Update version
            self.current_version = version
            await self._save_state()

            self.logger.info(f"Update {version} installed successfully")
            self.metrics.increment("install_update_success")

            result = {
                "success": True,
                "version": version,
                "backup_dir": str(backup_dir),
                "message": "Update installed successfully"
            }

            # 7. Auto restart if requested
            if auto_restart:
                self.logger.info("Auto-restart requested, scheduling restart...")
                result["restarting"] = True
                # Schedule restart after returning response
                asyncio.create_task(self._schedule_restart())

            return result

        except Exception as e:
            self.logger.error(f"Failed to install update: {e}")
            self.metrics.increment("install_update_errors")
            return {
                "success": False,
                "error": str(e)
            }
        finally:
            self.update_in_progress = False

    async def _create_backup(self) -> Path:
        """Create backup of current installation"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self.backups_dir / f"backup_{self.current_version}_{timestamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)

        # Backup critical directories
        dirs_to_backup = ["dist/services", "layers", "methods"]

        for dir_name in dirs_to_backup:
            src_dir = Path(dir_name)
            if src_dir.exists():
                dst_dir = backup_dir / dir_name
                shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)

        # Save version info
        version_file = backup_dir / "version.txt"
        with open(version_file, 'w') as f:
            f.write(f"{self.current_version}\n")

        return backup_dir

    async def _install_package(self, package_file: Path, version: str) -> Dict[str, Any]:
        """Extract and install update package"""
        try:
            # Extract package to temporary directory
            extract_dir = self.downloads_dir / f"extract_{version}"
            extract_dir.mkdir(parents=True, exist_ok=True)

            with tarfile.open(package_file, 'r:gz') as tar:
                tar.extractall(extract_dir)

            # Copy files to installation directories
            # Assuming package structure mirrors installation
            for item in extract_dir.iterdir():
                if item.is_dir():
                    dst = Path(item.name)
                    shutil.copytree(item, dst, dirs_exist_ok=True)
                    self.logger.info(f"Updated: {item.name}")

            # Cleanup
            shutil.rmtree(extract_dir)

            return {
                "success": True,
                "message": "Package installed"
            }

        except Exception as e:
            self.logger.error(f"Failed to install package: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _rollback(self, backup_dir: Path) -> Dict[str, Any]:
        """Rollback to backup version"""
        try:
            self.logger.info(f"Rolling back to backup: {backup_dir}")

            # Restore from backup
            for item in backup_dir.iterdir():
                if item.name == "version.txt":
                    continue

                if item.is_dir():
                    dst = Path(item.name)
                    if dst.exists():
                        shutil.rmtree(dst)
                    shutil.copytree(item, dst)

            # Restore version
            version_file = backup_dir / "version.txt"
            if version_file.exists():
                with open(version_file, 'r') as f:
                    self.current_version = f.read().strip()
                await self._save_state()

            self.logger.info("Rollback completed successfully")
            return {
                "success": True,
                "message": "Rolled back to previous version"
            }

        except Exception as e:
            self.logger.error(f"Rollback failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _schedule_restart(self):
        """Schedule system restart after a delay"""
        await asyncio.sleep(5)  # Wait 5 seconds for response to be sent
        self.logger.info("Restarting system...")

        try:
            # Trigger graceful shutdown
            if self.context:
                self.context._shutdown_event.set()
        except Exception as e:
            self.logger.error(f"Failed to trigger shutdown: {e}")

    @service_method(description="Get update manager status", public=True)
    async def get_status(self) -> Dict[str, Any]:
        """Get update manager status"""
        return {
            "success": True,
            "current_version": self.current_version,
            "update_in_progress": self.update_in_progress,
            "last_check_time": self.last_check_time,
            "has_public_key": self.public_key is not None,
            "backups_count": len(list(self.backups_dir.iterdir())) if self.backups_dir.exists() else 0
        }

    @service_method(description="List available backups", public=True)
    async def list_backups(self) -> Dict[str, Any]:
        """List available backup versions"""
        backups = []

        if self.backups_dir.exists():
            for backup_dir in sorted(self.backups_dir.iterdir(), reverse=True):
                if backup_dir.is_dir():
                    version_file = backup_dir / "version.txt"
                    version = "unknown"
                    if version_file.exists():
                        with open(version_file, 'r') as f:
                            version = f.read().strip()

                    backups.append({
                        "name": backup_dir.name,
                        "version": version,
                        "path": str(backup_dir),
                        "created": datetime.fromtimestamp(backup_dir.stat().st_mtime).isoformat()
                    })

        return {
            "success": True,
            "backups": backups,
            "count": len(backups)
        }

    @service_method(description="Manually rollback to a backup", public=True)
    async def manual_rollback(self, backup_name: str) -> Dict[str, Any]:
        """
        Manually rollback to a specific backup

        Args:
            backup_name: Name of the backup directory
        """
        self.metrics.increment("manual_rollback_calls")

        backup_dir = self.backups_dir / backup_name

        if not backup_dir.exists():
            return {
                "success": False,
                "error": f"Backup not found: {backup_name}"
            }

        result = await self._rollback(backup_dir)

        if result["success"]:
            self.metrics.increment("manual_rollback_success")
        else:
            self.metrics.increment("manual_rollback_errors")

        return result
