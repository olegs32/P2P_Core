"""
Update Server Service

Сервер обновлений для P2P кластера.
Запускается только на координаторе.

Функции:
- Хранение пакетов обновлений
- Генерация и проверка цифровых подписей
- Предоставление API для скачивания обновлений
- Управление версиями
"""

import os
import json
import hashlib
import tarfile
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.backends import default_backend

from layers.service import BaseService, service_method


class Run(BaseService):
    """Update Server - сервер обновлений для кластера"""

    def __init__(self, context=None):
        super().__init__(context)
        self.service_name = "update_server"
        self.updates_dir = Path("data/updates")
        self.packages_dir = self.updates_dir / "packages"
        self.metadata_file = self.updates_dir / "updates_metadata.json"
        self.private_key_file = self.updates_dir / "signing_key.pem"
        self.public_key_file = self.updates_dir / "signing_key.pub"

        self.private_key = None
        self.public_key = None
        self.updates_metadata = {}

    async def initialize(self):
        """Initialize update server"""
        # Only run on coordinator
        if hasattr(self.context, 'config') and hasattr(self.context.config, 'coordinator_mode'):
            if not self.context.config.coordinator_mode:
                self.logger.info("Update server skipped - not a coordinator")
                return

        self.logger.info("Initializing Update Server...")

        # Create directories
        self.updates_dir.mkdir(parents=True, exist_ok=True)
        self.packages_dir.mkdir(parents=True, exist_ok=True)

        # Load or generate signing keys
        await self._load_or_generate_keys()

        # Load metadata
        await self._load_metadata()

        self.logger.info("Update Server initialized successfully")

    async def _load_or_generate_keys(self):
        """Load existing keys or generate new RSA key pair"""
        if self.private_key_file.exists() and self.public_key_file.exists():
            # Load existing keys
            with open(self.private_key_file, 'rb') as f:
                self.private_key = serialization.load_pem_private_key(
                    f.read(),
                    password=None,
                    backend=default_backend()
                )

            with open(self.public_key_file, 'rb') as f:
                self.public_key = serialization.load_pem_public_key(
                    f.read(),
                    backend=default_backend()
                )

            self.logger.info("Loaded existing RSA signing keys")
        else:
            # Generate new keys
            self.logger.info("Generating new RSA signing keys (4096 bit)...")
            self.private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=4096,
                backend=default_backend()
            )
            self.public_key = self.private_key.public_key()

            # Save private key
            private_pem = self.private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
            with open(self.private_key_file, 'wb') as f:
                f.write(private_pem)
            os.chmod(self.private_key_file, 0o600)  # Only owner can read

            # Save public key
            public_pem = self.public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            )
            with open(self.public_key_file, 'wb') as f:
                f.write(public_pem)

            self.logger.info("Generated and saved new RSA keys")

    async def _load_metadata(self):
        """Load updates metadata"""
        if self.metadata_file.exists():
            with open(self.metadata_file, 'r') as f:
                self.updates_metadata = json.load(f)
            self.logger.info(f"Loaded metadata for {len(self.updates_metadata)} updates")
        else:
            self.updates_metadata = {}
            await self._save_metadata()

    async def _save_metadata(self):
        """Save updates metadata"""
        with open(self.metadata_file, 'w') as f:
            json.dump(self.updates_metadata, f, indent=2)

    def _sign_data(self, data: bytes) -> bytes:
        """Sign data with private key"""
        signature = self.private_key.sign(
            data,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        return signature

    def _verify_signature(self, data: bytes, signature: bytes) -> bool:
        """Verify signature with public key"""
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

    def _calculate_file_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of file"""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    @service_method(description="Upload new update package (coordinator only)", public=True)
    async def upload_update(
        self,
        version: str,
        package_data: bytes,
        description: str = "",
        target_nodes: str = "all"  # "all", "workers", "coordinator"
    ) -> Dict[str, Any]:
        """
        Upload a new update package

        Args:
            version: Version string (e.g., "1.2.3")
            package_data: Binary data of tar.gz package
            description: Update description
            target_nodes: Target nodes ("all", "workers", "coordinator")
        """
        self.metrics.increment("upload_update_calls")

        try:
            # Save package file
            package_file = self.packages_dir / f"update-{version}.tar.gz"

            if package_file.exists():
                return {
                    "success": False,
                    "error": f"Version {version} already exists"
                }

            # Write package
            with open(package_file, 'wb') as f:
                f.write(package_data)

            # Calculate hash
            file_hash = self._calculate_file_hash(package_file)

            # Sign the file
            signature = self._sign_data(package_data)
            signature_file = self.packages_dir / f"update-{version}.tar.gz.sig"
            with open(signature_file, 'wb') as f:
                f.write(signature)

            # Create metadata
            metadata = {
                "version": version,
                "filename": package_file.name,
                "size": len(package_data),
                "hash": file_hash,
                "signature_file": signature_file.name,
                "description": description,
                "target_nodes": target_nodes,
                "uploaded_at": datetime.now().isoformat(),
                "status": "available"
            }

            self.updates_metadata[version] = metadata
            await self._save_metadata()

            self.logger.info(f"Update package uploaded: {version} ({len(package_data)} bytes)")
            self.metrics.increment("upload_update_success")

            return {
                "success": True,
                "version": version,
                "hash": file_hash,
                "size": len(package_data)
            }

        except Exception as e:
            self.logger.error(f"Failed to upload update: {e}")
            self.metrics.increment("upload_update_errors")
            return {
                "success": False,
                "error": str(e)
            }

    @service_method(description="List available updates", public=True)
    async def list_updates(
        self,
        target_node_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        List available updates

        Args:
            target_node_type: Filter by target ("all", "workers", "coordinator", None for all)
        """
        self.metrics.increment("list_updates_calls")

        updates = []
        for version, metadata in self.updates_metadata.items():
            # Filter by target if specified
            if target_node_type and metadata.get("target_nodes") not in ["all", target_node_type]:
                continue

            updates.append({
                "version": version,
                "size": metadata.get("size"),
                "hash": metadata.get("hash"),
                "description": metadata.get("description", ""),
                "target_nodes": metadata.get("target_nodes", "all"),
                "uploaded_at": metadata.get("uploaded_at"),
                "status": metadata.get("status", "available")
            })

        # Sort by upload date (newest first)
        updates.sort(key=lambda x: x.get("uploaded_at", ""), reverse=True)

        return {
            "success": True,
            "updates": updates,
            "count": len(updates)
        }

    @service_method(description="Get update package data", public=True)
    async def download_update(self, version: str) -> Dict[str, Any]:
        """
        Download update package

        Args:
            version: Version to download
        """
        self.metrics.increment("download_update_calls")

        try:
            if version not in self.updates_metadata:
                return {
                    "success": False,
                    "error": f"Version {version} not found"
                }

            metadata = self.updates_metadata[version]
            package_file = self.packages_dir / metadata["filename"]
            signature_file = self.packages_dir / metadata["signature_file"]

            if not package_file.exists():
                return {
                    "success": False,
                    "error": f"Package file not found: {package_file}"
                }

            # Read package data
            with open(package_file, 'rb') as f:
                package_data = f.read()

            # Read signature
            with open(signature_file, 'rb') as f:
                signature = f.read()

            # Verify hash
            calculated_hash = hashlib.sha256(package_data).hexdigest()
            if calculated_hash != metadata["hash"]:
                self.logger.error(f"Hash mismatch for {version}")
                return {
                    "success": False,
                    "error": "Package integrity check failed"
                }

            self.metrics.increment("download_update_success")
            self.logger.info(f"Update package downloaded: {version}")

            return {
                "success": True,
                "version": version,
                "package_data": package_data.hex(),  # Hex encode for JSON
                "signature": signature.hex(),
                "hash": calculated_hash,
                "size": len(package_data),
                "description": metadata.get("description", "")
            }

        except Exception as e:
            self.logger.error(f"Failed to download update: {e}")
            self.metrics.increment("download_update_errors")
            return {
                "success": False,
                "error": str(e)
            }

    @service_method(description="Get public signing key", public=True)
    async def get_public_key(self) -> Dict[str, Any]:
        """Get public key for signature verification"""
        self.metrics.increment("get_public_key_calls")

        try:
            public_pem = self.public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            )

            return {
                "success": True,
                "public_key": public_pem.decode('utf-8')
            }

        except Exception as e:
            self.logger.error(f"Failed to get public key: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    @service_method(description="Delete update version", public=True)
    async def delete_update(self, version: str) -> Dict[str, Any]:
        """
        Delete an update version

        Args:
            version: Version to delete
        """
        self.metrics.increment("delete_update_calls")

        try:
            if version not in self.updates_metadata:
                return {
                    "success": False,
                    "error": f"Version {version} not found"
                }

            metadata = self.updates_metadata[version]
            package_file = self.packages_dir / metadata["filename"]
            signature_file = self.packages_dir / metadata["signature_file"]

            # Delete files
            if package_file.exists():
                package_file.unlink()
            if signature_file.exists():
                signature_file.unlink()

            # Remove from metadata
            del self.updates_metadata[version]
            await self._save_metadata()

            self.logger.info(f"Update package deleted: {version}")
            self.metrics.increment("delete_update_success")

            return {
                "success": True,
                "version": version
            }

        except Exception as e:
            self.logger.error(f"Failed to delete update: {e}")
            self.metrics.increment("delete_update_errors")
            return {
                "success": False,
                "error": str(e)
            }

    @service_method(description="Get update server status", public=True)
    async def get_status(self) -> Dict[str, Any]:
        """Get update server status"""
        return {
            "success": True,
            "total_updates": len(self.updates_metadata),
            "updates_dir": str(self.updates_dir),
            "has_signing_key": self.private_key is not None,
            "available_versions": list(self.updates_metadata.keys())
        }
