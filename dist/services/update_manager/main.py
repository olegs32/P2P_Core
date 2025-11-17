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

        # Gossip integration
        self.gossip_publish_task: Optional[asyncio.Task] = None
        self.gossip_publish_interval = 60  # seconds

        # Metrics tracking
        self.total_update_attempts = 0
        self.successful_updates = 0
        self.failed_updates = 0
        self.total_rollbacks = 0
        self.total_bytes_downloaded = 0
        self.last_update_duration_ms = 0

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

        # Start gossip publishing task
        self.gossip_publish_task = asyncio.create_task(self._gossip_publish_loop())

        self.logger.info("Update Manager initialized successfully")

    async def cleanup(self):
        """Cleanup update manager"""
        # Stop gossip publishing task
        if self.gossip_publish_task:
            self.gossip_publish_task.cancel()
            try:
                await self.gossip_publish_task
            except asyncio.CancelledError:
                pass

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

    @service_method(description="Execute update from repository artifact", public=True)
    async def execute_update(
        self,
        update_id: int,
        artifact_id: int,
        artifact_name: str,
        target_version: str,
        backup_enabled: bool = True,
        auto_restart: bool = False
    ) -> Dict[str, Any]:
        """
        Execute update from repository artifact (called by Update Server)

        Args:
            update_id: Update task ID from Update Server
            artifact_id: Artifact ID in repository
            artifact_name: Name of artifact
            target_version: Target version
            backup_enabled: Create backup before update
            auto_restart: Automatically restart after installation
        """
        self.metrics.increment("execute_update_calls")

        if self.update_in_progress:
            return {
                "success": False,
                "error": "Update already in progress",
                "phase": "busy"
            }

        if not self.proxy:
            return {
                "success": False,
                "error": "Proxy not available"
            }

        self.update_in_progress = True
        phase = "downloading"
        start_time = datetime.now()

        # Track metrics
        self.total_update_attempts += 1
        self.metrics.increment("update_attempts")

        try:
            self.logger.info(f"Executing update {update_id}: {artifact_name} -> {target_version}")

            # 1. Download artifact from repository
            phase = "downloading"
            self.logger.info(f"Downloading artifact {artifact_id} from repository...")
            download_result = await self.proxy.repository.coordinator.download_artifact_rpc(
                artifact_id=artifact_id,
                node_id=self.context.config.node_id
            )

            if not download_result.get("success"):
                return {
                    "success": False,
                    "error": f"Download failed: {download_result.get('error')}",
                    "phase": phase
                }

            # 2. Verify artifact
            phase = "validating"
            artifact_data = download_result["file_data"]
            expected_sha256 = download_result["sha256"]

            calculated_sha256 = hashlib.sha256(artifact_data).hexdigest()
            if calculated_sha256 != expected_sha256:
                self.logger.error(f"SHA256 mismatch: expected {expected_sha256}, got {calculated_sha256}")
                return {
                    "success": False,
                    "error": "Artifact integrity check failed (SHA256 mismatch)",
                    "phase": phase
                }

            self.logger.info(f"Artifact verified: {len(artifact_data)} bytes, SHA256: {calculated_sha256[:16]}...")

            # Track bytes downloaded
            self.total_bytes_downloaded += len(artifact_data)
            self.metrics.gauge("total_bytes_downloaded", self.total_bytes_downloaded)

            # 3. Save artifact to downloads
            artifact_file = self.downloads_dir / f"artifact_{artifact_id}_{target_version}.bin"
            with open(artifact_file, 'wb') as f:
                f.write(artifact_data)

            # 4. Create backup if enabled
            backup_dir = None
            if backup_enabled:
                phase = "backing_up"
                backup_dir = await self._create_backup()
                self.logger.info(f"Backup created: {backup_dir}")

            # 5. Install artifact (determine type from extension or metadata)
            phase = "installing"
            install_result = await self._install_artifact(
                artifact_file,
                artifact_name,
                target_version
            )

            if not install_result["success"]:
                # Rollback on failure
                if backup_dir:
                    phase = "rolling_back"
                    self.logger.error("Installation failed, rolling back...")
                    await self._rollback(backup_dir)
                return {
                    "success": False,
                    "error": install_result.get("error", "Installation failed"),
                    "phase": phase
                }

            # 6. Update version
            self.current_version = target_version
            await self._save_state()

            phase = "completed"
            self.logger.info(f"Update {update_id} installed successfully")

            # Track success metrics
            self.successful_updates += 1
            duration_ms = (datetime.now() - start_time).total_seconds() * 1000
            self.last_update_duration_ms = duration_ms

            self.metrics.increment("execute_update_success")
            self.metrics.increment("successful_updates")
            self.metrics.timer("update_duration_ms", duration_ms)
            self.metrics.gauge("last_update_duration_ms", duration_ms)

            result = {
                "success": True,
                "update_id": update_id,
                "version": target_version,
                "backup_dir": str(backup_dir) if backup_dir else None,
                "phase": phase,
                "duration_ms": duration_ms,
                "message": "Update installed successfully"
            }

            # 7. Auto restart if requested
            if auto_restart:
                self.logger.info("Auto-restart requested, scheduling restart...")
                result["restarting"] = True
                asyncio.create_task(self._schedule_restart())

            return result

        except Exception as e:
            self.logger.error(f"Failed to execute update: {e}", exc_info=True)

            # Track failure metrics
            self.failed_updates += 1
            self.metrics.increment("execute_update_errors")
            self.metrics.increment("failed_updates")

            return {
                "success": False,
                "error": str(e),
                "phase": phase
            }
        finally:
            self.update_in_progress = False

    @service_method(description="Download and install update (legacy method)", public=True)
    async def install_update(
        self,
        version: str,
        auto_restart: bool = False
    ) -> Dict[str, Any]:
        """
        Download and install an update (legacy method for package-based updates)

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

    async def _install_artifact(
        self,
        artifact_file: Path,
        artifact_name: str,
        version: str
    ) -> Dict[str, Any]:
        """
        Install artifact based on its type

        Args:
            artifact_file: Path to downloaded artifact
            artifact_name: Name of artifact
            version: Target version

        Returns:
            Result dictionary
        """
        try:
            # Determine artifact type by name or extension
            if artifact_name.endswith(('.tar.gz', '.tgz')):
                # Service package (tarball)
                return await self._install_package(artifact_file, version)

            elif artifact_name.endswith(('.exe', '.bin', '')):
                # Binary executable
                return await self._install_binary(artifact_file, artifact_name)

            elif artifact_name.endswith(('.zip',)):
                # Zip archive
                return await self._install_zip(artifact_file, version)

            elif 'service' in artifact_name.lower():
                # Assume service package
                return await self._install_service(artifact_file, artifact_name, version)

            else:
                # Default: treat as binary
                self.logger.warning(f"Unknown artifact type for {artifact_name}, treating as binary")
                return await self._install_binary(artifact_file, artifact_name)

        except Exception as e:
            self.logger.error(f"Failed to install artifact: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def _install_binary(self, binary_file: Path, artifact_name: str) -> Dict[str, Any]:
        """
        Install binary executable (replace current process)

        Args:
            binary_file: Path to binary file
            artifact_name: Name of artifact

        Returns:
            Result dictionary
        """
        try:
            import sys
            import platform

            self.logger.info(f"Installing binary: {artifact_name}")

            # Get current executable path
            current_exe = Path(sys.executable).resolve()
            self.logger.info(f"Current executable: {current_exe}")

            # Make binary executable (Unix)
            if platform.system() != "Windows":
                os.chmod(binary_file, 0o755)

            # Platform-specific installation
            if platform.system() == "Windows":
                # On Windows, cannot replace running executable directly
                # Use rename trick: current -> .old, new -> current
                temp_old = current_exe.with_suffix(".old")

                if current_exe.exists():
                    os.chmod(current_exe, 0o755)
                    shutil.move(str(current_exe), str(temp_old))

                shutil.copy2(binary_file, current_exe)

                # Try to remove old file (may fail if locked)
                try:
                    if temp_old.exists():
                        os.remove(temp_old)
                except:
                    self.logger.debug(f"Could not remove {temp_old} (will be cleaned on restart)")

            else:
                # On Unix, can replace directly (old inode continues running)
                shutil.copy2(binary_file, current_exe)
                os.chmod(current_exe, 0o755)

            self.logger.info("Binary installed successfully")
            self.logger.warning("RESTART REQUIRED for update to take effect")

            return {
                "success": True,
                "message": "Binary installed, restart required",
                "restart_required": True
            }

        except Exception as e:
            self.logger.error(f"Failed to install binary: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def _install_service(
        self,
        service_file: Path,
        service_name: str,
        version: str
    ) -> Dict[str, Any]:
        """
        Install service package to dist/services

        Args:
            service_file: Path to service package
            service_name: Name of service
            version: Version

        Returns:
            Result dictionary
        """
        try:
            self.logger.info(f"Installing service: {service_name}")

            # Extract service name from artifact name
            # e.g., "my_service-1.0.0.tar.gz" -> "my_service"
            base_name = service_name.split('-')[0].replace('.tar.gz', '').replace('.tgz', '')

            # Target directory
            services_dir = Path("dist/services")
            service_dir = services_dir / base_name

            # Extract package
            extract_dir = self.downloads_dir / f"extract_{base_name}_{version}"
            extract_dir.mkdir(parents=True, exist_ok=True)

            if service_file.suffix in ['.tar', '.gz', '.tgz']:
                with tarfile.open(service_file, 'r:*') as tar:
                    tar.extractall(extract_dir)
            else:
                raise ValueError(f"Unsupported service package format: {service_file.suffix}")

            # Copy to services directory
            if service_dir.exists():
                # Backup existing service
                backup_service = service_dir.with_name(f"{service_dir.name}.backup")
                if backup_service.exists():
                    shutil.rmtree(backup_service)
                shutil.move(str(service_dir), str(backup_service))

            # Install new service
            shutil.copytree(extract_dir, service_dir, dirs_exist_ok=True)

            # Cleanup
            shutil.rmtree(extract_dir)

            self.logger.info(f"Service {base_name} installed successfully")

            return {
                "success": True,
                "message": f"Service {base_name} installed",
                "service_name": base_name,
                "service_dir": str(service_dir)
            }

        except Exception as e:
            self.logger.error(f"Failed to install service: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def _install_zip(self, zip_file: Path, version: str) -> Dict[str, Any]:
        """
        Install from zip archive

        Args:
            zip_file: Path to zip file
            version: Version

        Returns:
            Result dictionary
        """
        try:
            import zipfile

            self.logger.info(f"Installing from zip: {zip_file.name}")

            # Extract to temporary directory
            extract_dir = self.downloads_dir / f"extract_{version}"
            extract_dir.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(zip_file, 'r') as zf:
                zf.extractall(extract_dir)

            # Copy files to installation directories
            for item in extract_dir.iterdir():
                if item.is_dir():
                    dst = Path(item.name)
                    shutil.copytree(item, dst, dirs_exist_ok=True)
                    self.logger.info(f"Updated: {item.name}")

            # Cleanup
            shutil.rmtree(extract_dir)

            return {
                "success": True,
                "message": "Zip package installed"
            }

        except Exception as e:
            self.logger.error(f"Failed to install zip: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

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
            self.total_rollbacks += 1
            self.metrics.increment("manual_rollback_success")
            self.metrics.gauge("total_rollbacks", self.total_rollbacks)
        else:
            self.metrics.increment("manual_rollback_errors")

        return result

    @service_method(description="Get update manager metrics", public=True)
    async def get_metrics(self) -> Dict[str, Any]:
        """
        Get update manager metrics and statistics

        Returns:
            Dictionary with update manager metrics
        """
        try:
            return {
                "success": True,
                "metrics": {
                    # Current state
                    "current_version": self.current_version,
                    "update_in_progress": self.update_in_progress,
                    "last_check_time": self.last_check_time,

                    # Update counters
                    "total_update_attempts": self.total_update_attempts,
                    "successful_updates": self.successful_updates,
                    "failed_updates": self.failed_updates,
                    "total_rollbacks": self.total_rollbacks,

                    # Bandwidth
                    "total_bytes_downloaded": self.total_bytes_downloaded,

                    # Performance
                    "last_update_duration_ms": self.last_update_duration_ms,

                    # Success rate
                    "success_rate": (
                        self.successful_updates / self.total_update_attempts * 100
                        if self.total_update_attempts > 0 else 0
                    ),

                    # Average metrics
                    "avg_download_size": (
                        self.total_bytes_downloaded / self.successful_updates
                        if self.successful_updates > 0 else 0
                    )
                }
            }

        except Exception as e:
            self.logger.error(f"Failed to get update manager metrics: {e}")
            return {"success": False, "error": str(e)}

    async def _gossip_publish_loop(self):
        """
        Periodically publish node version information to gossip
        Allows cluster to track current versions on each node
        """
        self.logger.info("Starting gossip publish loop for version tracking")

        while True:
            try:
                await asyncio.sleep(self.gossip_publish_interval)

                # Get network component for gossip access
                if not self.context:
                    continue

                network = self.context.get_shared("network")
                if not network or not hasattr(network, 'gossip'):
                    self.logger.debug("Network/gossip not available yet")
                    continue

                # Publish current version to gossip metadata
                gossip = network.gossip
                if hasattr(gossip, 'local_node_metadata'):
                    gossip.local_node_metadata['update_manager'] = {
                        'current_version': self.current_version,
                        'update_in_progress': self.update_in_progress,
                        'last_check_time': self.last_check_time,
                        'backups_count': len(list(self.backups_dir.iterdir())) if self.backups_dir.exists() else 0,
                        'last_updated': datetime.now().isoformat()
                    }

                    self.logger.debug(f"Published version {self.current_version} to gossip")
                    self.metrics.increment("gossip_publishes")

            except asyncio.CancelledError:
                self.logger.info("Gossip publish loop cancelled")
                break
            except Exception as e:
                self.logger.error(f"Error in gossip publish loop: {e}")
                self.metrics.increment("gossip_publish_errors")
                await asyncio.sleep(10)  # Back off on error

    @service_method(description="Check for available updates from gossip", public=True)
    async def check_updates_from_gossip(self) -> Dict[str, Any]:
        """
        Check for available updates by reading repository info from gossip

        Returns:
            Available updates for this node
        """
        try:
            if not self.context:
                return {"success": False, "error": "Context not available"}

            network = self.context.get_shared("network")
            if not network or not hasattr(network, 'gossip'):
                return {"success": False, "error": "Gossip not available"}

            gossip = network.gossip
            available_updates = []

            # Look for repository info in gossip
            for node_id, node_info in gossip.node_registry.items():
                if hasattr(node_info, 'metadata') and 'repository' in node_info.metadata:
                    repo_data = node_info.metadata['repository']
                    if 'available_versions' in repo_data:
                        # Check for updates
                        for artifact_name, artifact_info in repo_data['available_versions'].items():
                            if self._is_newer_version(artifact_info['version'], self.current_version):
                                available_updates.append({
                                    'artifact_name': artifact_name,
                                    'current_version': self.current_version,
                                    'available_version': artifact_info['version'],
                                    'artifact_id': artifact_info['artifact_id'],
                                    'source_node': node_id,
                                    'artifact_type': artifact_info['artifact_type']
                                })

            return {
                "success": True,
                "available_updates": available_updates,
                "has_updates": len(available_updates) > 0,
                "current_version": self.current_version
            }

        except Exception as e:
            self.logger.error(f"Failed to check updates from gossip: {e}")
            return {"success": False, "error": str(e)}

    def _is_newer_version(self, candidate: str, current: str) -> bool:
        """
        Check if candidate version is newer than current

        Args:
            candidate: Candidate version string
            current: Current version string

        Returns:
            True if candidate is newer
        """
        return self._compare_versions(candidate, current) > 0
