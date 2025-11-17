"""
Repository Service - Artifact storage and management

Provides centralized storage for:
- Binary executables (exe/elf)
- Service packages (zip/tar)
- Configuration files
- Docker images

Features:
- Content-addressable storage with deduplication
- Version management with semantic versioning
- Security: signatures, checksums, virus scanning
- Compression and archiving
- P2P distribution support
- RESTful API
"""
import os
import io
import logging
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

from layers.service import BaseService, service_method
from fastapi import UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

# Import models using importlib for dynamic loading in ServiceLoader context
import sys
import importlib.util

def _load_local_module(module_name: str, file_path: str):
    """Load a module from file path"""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

# Get service directory
_service_dir = os.path.dirname(os.path.abspath(__file__))

# Load models/artifact.py
_artifact_module = _load_local_module(
    'repository_artifact',
    os.path.join(_service_dir, 'models', 'artifact.py')
)
Artifact = _artifact_module.Artifact
ArtifactType = _artifact_module.ArtifactType
ArtifactStatus = _artifact_module.ArtifactStatus
ArtifactDependency = _artifact_module.ArtifactDependency

# Load models/version.py
_version_module = _load_local_module(
    'repository_version',
    os.path.join(_service_dir, 'models', 'version.py')
)
SemanticVersion = _version_module.SemanticVersion
VersionComparator = _version_module.VersionComparator
VersionTag = _version_module.VersionTag

# Load storage/backend.py
_backend_module = _load_local_module(
    'repository_storage_backend',
    os.path.join(_service_dir, 'storage', 'backend.py')
)
StorageBackend = _backend_module.StorageBackend


class Run(BaseService):
    """Repository service for artifact management"""

    SERVICE_NAME = "repository"

    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.info.version = "1.0.0"
        self.info.description = "Artifact repository with version management"

        # Storage backend
        self.storage: Optional[StorageBackend] = None
        self.storage_path = Path("data/repository")
        self.db_path = "data/repository/repository.db"

        # Gossip integration
        self.gossip_publish_task: Optional[asyncio.Task] = None
        self.gossip_publish_interval = 30  # seconds

        # Metrics tracking
        self.total_uploads = 0
        self.total_downloads = 0
        self.total_deletes = 0
        self.upload_errors = 0
        self.download_errors = 0
        self.total_bytes_uploaded = 0
        self.total_bytes_downloaded = 0

    async def initialize(self):
        """Initialize repository service"""
        self.logger.info("Initializing Repository service")

        # Create storage directories
        self.storage_path.mkdir(parents=True, exist_ok=True)

        # Initialize storage backend
        self.storage = StorageBackend(
            base_path=str(self.storage_path),
            db_path=self.db_path
        )

        # Register HTTP endpoints if on coordinator
        if self.context and self.context.config.coordinator_mode:
            self._register_http_endpoints()

            # Start gossip publishing task for coordinators
            self.gossip_publish_task = asyncio.create_task(self._gossip_publish_loop())

        self.logger.info("Repository service initialized")

    async def cleanup(self):
        """Cleanup repository service"""
        self.logger.info("Repository service cleanup")

        # Stop gossip publishing task
        if self.gossip_publish_task:
            self.gossip_publish_task.cancel()
            try:
                await self.gossip_publish_task
            except asyncio.CancelledError:
                pass

    def _register_http_endpoints(self):
        """Register HTTP endpoints with FastAPI"""
        if not self.context:
            self.logger.error("Cannot register HTTP endpoints: context not available")
            return

        app = self.context.get_shared("fastapi_app")
        if not app:
            self.logger.error("Cannot register HTTP endpoints: FastAPI app not available")
            return

        self.logger.info("Registering repository HTTP endpoints...")

        @app.post("/api/repository/upload")
        async def upload_artifact(
            request: Request,
            file: UploadFile = File(...),
            name: str = Form(...),
            version: str = Form(...),
            artifact_type: str = Form(...),
            platform: Optional[str] = Form(None),
            architecture: Optional[str] = Form(None),
            description: Optional[str] = Form(""),
            changelog: Optional[str] = Form(""),
            tags: Optional[str] = Form(""),  # Comma-separated
            signature: Optional[str] = Form(None)
        ):
            """Upload new artifact"""
            try:
                # Parse artifact type
                try:
                    art_type = ArtifactType(artifact_type)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Invalid artifact type: {artifact_type}")

                # Create artifact metadata
                artifact = Artifact(
                    name=name,
                    version=version,
                    artifact_type=art_type,
                    platform=platform,
                    architecture=architecture,
                    description=description,
                    changelog=changelog,
                    signature=signature,
                    uploader=request.client.host,
                    uploader_ip=request.client.host,
                    tags=[t.strip() for t in tags.split(",") if t.strip()] if tags else []
                )

                # Store artifact
                file_data = await file.read()
                file_stream = io.BytesIO(file_data)

                storage_path = self.storage.store_artifact(artifact, file_stream)

                # Log action
                self.storage.log_action(
                    user=request.client.host,
                    action="upload",
                    artifact_id=artifact.id,
                    ip_address=request.client.host,
                    details=f"Uploaded {name} v{version}"
                )

                # Track metrics
                self.total_uploads += 1
                self.total_bytes_uploaded += len(file_data)
                self.metrics.increment("artifacts_uploaded")
                self.metrics.increment(f"artifacts_uploaded_{artifact_type}")
                self.metrics.gauge("total_uploads", self.total_uploads)
                self.metrics.gauge("total_bytes_uploaded", self.total_bytes_uploaded)

                self.logger.info(f"Artifact uploaded: {name} v{version} ({len(file_data)} bytes)")

                return {
                    "success": True,
                    "artifact_id": artifact.id,
                    "storage_path": storage_path,
                    "sha256": artifact.sha256,
                    "size_bytes": artifact.size_bytes
                }

            except Exception as e:
                self.upload_errors += 1
                self.metrics.increment("upload_errors")
                self.logger.error(f"Failed to upload artifact: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/repository/artifacts")
        async def list_artifacts(
            artifact_type: Optional[str] = None,
            platform: Optional[str] = None,
            tag: Optional[str] = None,
            limit: int = 100,
            offset: int = 0
        ):
            """List artifacts with optional filters"""
            try:
                artifacts = self.storage.list_artifacts(
                    artifact_type=artifact_type,
                    platform=platform,
                    tag=tag,
                    limit=limit,
                    offset=offset
                )

                return {
                    "success": True,
                    "artifacts": [art.to_dict() for art in artifacts],
                    "count": len(artifacts)
                }

            except Exception as e:
                self.logger.error(f"Failed to list artifacts: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/repository/artifacts/{artifact_id}")
        async def get_artifact_details(artifact_id: int):
            """Get artifact details"""
            try:
                artifact = self.storage.get_artifact(artifact_id)
                if not artifact:
                    raise HTTPException(status_code=404, detail="Artifact not found")

                return {
                    "success": True,
                    "artifact": artifact.to_dict()
                }

            except HTTPException:
                raise
            except Exception as e:
                self.logger.error(f"Failed to get artifact: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/repository/artifacts/{artifact_id}/download")
        async def download_artifact(artifact_id: int, request: Request):
            """Download artifact file"""
            try:
                artifact = self.storage.get_artifact(artifact_id)
                if not artifact:
                    raise HTTPException(status_code=404, detail="Artifact not found")

                if not os.path.exists(artifact.storage_path):
                    raise HTTPException(status_code=404, detail="Artifact file not found")

                # Record download
                self.storage.record_download(
                    artifact_id=artifact_id,
                    node_id=request.client.host,
                    ip_address=request.client.host
                )

                # Track metrics
                self.total_downloads += 1
                self.total_bytes_downloaded += artifact.size_bytes
                self.metrics.increment("artifacts_downloaded")
                self.metrics.increment(f"artifacts_downloaded_{artifact.artifact_type.value}")
                self.metrics.gauge("total_downloads", self.total_downloads)
                self.metrics.gauge("total_bytes_downloaded", self.total_bytes_downloaded)

                # Return file
                return FileResponse(
                    path=artifact.storage_path,
                    filename=f"{artifact.name}_{artifact.version}",
                    media_type="application/octet-stream"
                )

            except HTTPException:
                raise
            except Exception as e:
                self.download_errors += 1
                self.metrics.increment("download_errors")
                self.logger.error(f"Failed to download artifact: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @app.delete("/api/repository/artifacts/{artifact_id}")
        async def delete_artifact(artifact_id: int, request: Request):
            """Delete artifact"""
            try:
                artifact = self.storage.get_artifact(artifact_id)
                if not artifact:
                    raise HTTPException(status_code=404, detail="Artifact not found")

                # Log action before deletion
                self.storage.log_action(
                    user=request.client.host,
                    action="delete",
                    artifact_id=artifact_id,
                    ip_address=request.client.host,
                    details=f"Deleted {artifact.name} v{artifact.version}"
                )

                success = self.storage.delete_artifact(artifact_id)

                # Track metrics
                if success:
                    self.total_deletes += 1
                    self.metrics.increment("artifacts_deleted")
                    self.metrics.gauge("total_deletes", self.total_deletes)

                return {
                    "success": success,
                    "message": f"Artifact {artifact.name} v{artifact.version} deleted"
                }

            except HTTPException:
                raise
            except Exception as e:
                self.metrics.increment("delete_errors")
                self.logger.error(f"Failed to delete artifact: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/repository/stats")
        async def get_stats():
            """Get repository statistics"""
            try:
                stats = self.storage.get_storage_stats()
                return {
                    "success": True,
                    "stats": stats
                }
            except Exception as e:
                self.logger.error(f"Failed to get stats: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        self.logger.info("Repository HTTP endpoints registered successfully:")
        self.logger.info("  POST /api/repository/upload")
        self.logger.info("  GET  /api/repository/artifacts")
        self.logger.info("  GET  /api/repository/artifacts/{artifact_id}")
        self.logger.info("  GET  /api/repository/artifacts/{artifact_id}/download")
        self.logger.info("  DELETE /api/repository/artifacts/{artifact_id}")
        self.logger.info("  GET  /api/repository/stats")

    # RPC Methods

    @service_method(description="Upload artifact via RPC", public=True)
    async def upload_artifact_rpc(
        self,
        name: str,
        version: str,
        artifact_type: str,
        file_data: bytes,
        platform: Optional[str] = None,
        description: str = "",
        tags: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Upload artifact via RPC"""
        try:
            # Create artifact
            artifact = Artifact(
                name=name,
                version=version,
                artifact_type=ArtifactType(artifact_type),
                platform=platform,
                description=description,
                tags=tags or [],
                uploader="rpc-client"
            )

            # Store artifact
            file_stream = io.BytesIO(file_data)
            storage_path = self.storage.store_artifact(artifact, file_stream)

            self.metrics.increment("artifacts_uploaded")

            return {
                "success": True,
                "artifact_id": artifact.id,
                "sha256": artifact.sha256,
                "storage_path": storage_path
            }

        except Exception as e:
            self.logger.error(f"Failed to upload artifact: {e}")
            self.metrics.increment("upload_errors")
            return {"success": False, "error": str(e)}

    @service_method(description="List artifacts", public=True)
    async def list_artifacts_rpc(
        self,
        artifact_type: Optional[str] = None,
        platform: Optional[str] = None,
        tag: Optional[str] = None
    ) -> Dict[str, Any]:
        """List artifacts"""
        try:
            artifacts = self.storage.list_artifacts(
                artifact_type=artifact_type,
                platform=platform,
                tag=tag
            )

            return {
                "success": True,
                "artifacts": [art.to_dict() for art in artifacts]
            }

        except Exception as e:
            self.logger.error(f"Failed to list artifacts: {e}")
            return {"success": False, "error": str(e)}

    @service_method(description="Get artifact details", public=True)
    async def get_artifact_details_rpc(self, artifact_id: int) -> Dict[str, Any]:
        """Get artifact details"""
        try:
            artifact = self.storage.get_artifact(artifact_id)
            if not artifact:
                return {"success": False, "error": "Artifact not found"}

            return {
                "success": True,
                "artifact": artifact.to_dict()
            }

        except Exception as e:
            self.logger.error(f"Failed to get artifact: {e}")
            return {"success": False, "error": str(e)}

    @service_method(description="Download artifact", public=True)
    async def download_artifact_rpc(self, artifact_id: int, node_id: str) -> Dict[str, Any]:
        """Download artifact file"""
        try:
            artifact = self.storage.get_artifact(artifact_id)
            if not artifact:
                return {"success": False, "error": "Artifact not found"}

            if not os.path.exists(artifact.storage_path):
                return {"success": False, "error": "Artifact file not found"}

            # Read file
            with open(artifact.storage_path, 'rb') as f:
                file_data = f.read()

            # Record download
            self.storage.record_download(artifact_id, node_id, node_id)

            self.metrics.increment("artifacts_downloaded")

            return {
                "success": True,
                "file_data": file_data,
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes
            }

        except Exception as e:
            self.logger.error(f"Failed to download artifact: {e}")
            self.metrics.increment("download_errors")
            return {"success": False, "error": str(e)}

    @service_method(description="Get repository statistics", public=True)
    async def get_stats_rpc(self) -> Dict[str, Any]:
        """Get repository statistics"""
        try:
            stats = self.storage.get_storage_stats()
            return {
                "success": True,
                "stats": stats
            }
        except Exception as e:
            self.logger.error(f"Failed to get stats: {e}")
            return {"success": False, "error": str(e)}

    @service_method(description="Search artifacts by version", public=True)
    async def search_by_version(
        self,
        name: str,
        min_version: Optional[str] = None,
        max_version: Optional[str] = None
    ) -> Dict[str, Any]:
        """Search artifacts by version range"""
        try:
            all_artifacts = self.storage.list_artifacts()
            matching = []

            for artifact in all_artifacts:
                if artifact.name != name:
                    continue

                try:
                    ver = SemanticVersion.parse(artifact.version)

                    if min_version:
                        min_ver = SemanticVersion.parse(min_version)
                        if ver < min_ver:
                            continue

                    if max_version:
                        max_ver = SemanticVersion.parse(max_version)
                        if ver > max_ver:
                            continue

                    matching.append(artifact)

                except ValueError:
                    # Skip if version parsing fails
                    continue

            # Sort by version
            versions = [a.version for a in matching]
            sorted_versions = VersionComparator.sort_versions(versions)

            # Sort artifacts by sorted versions
            sorted_artifacts = []
            for v in sorted_versions:
                for a in matching:
                    if a.version == v:
                        sorted_artifacts.append(a)
                        break

            return {
                "success": True,
                "artifacts": [art.to_dict() for art in sorted_artifacts],
                "count": len(sorted_artifacts)
            }

        except Exception as e:
            self.logger.error(f"Failed to search artifacts: {e}")
            return {"success": False, "error": str(e)}

    async def _gossip_publish_loop(self):
        """
        Periodically publish repository information to gossip
        Allows all nodes to discover available versions
        """
        self.logger.info("Starting gossip publish loop")

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

                # Get latest versions by artifact type
                latest_versions = await self._get_latest_versions_summary()

                # Publish to gossip metadata
                gossip = network.gossip
                if hasattr(gossip, 'self_info') and hasattr(gossip.self_info, 'metadata'):
                    gossip.self_info.metadata['repository'] = {
                        'available_versions': latest_versions,
                        'last_updated': datetime.now().isoformat(),
                        'total_artifacts': len(latest_versions)
                    }

                    self.logger.debug(f"Published {len(latest_versions)} versions to gossip")
                    self.metrics.increment("gossip_publishes")

            except asyncio.CancelledError:
                self.logger.info("Gossip publish loop cancelled")
                break
            except Exception as e:
                self.logger.error(f"Error in gossip publish loop: {e}")
                self.metrics.increment("gossip_publish_errors")
                await asyncio.sleep(10)  # Back off on error

    async def _get_latest_versions_summary(self) -> Dict[str, Any]:
        """
        Get summary of latest versions for each artifact

        Returns:
            Dictionary mapping artifact names to their latest versions
        """
        try:
            artifacts = self.storage.list_artifacts(limit=1000)

            # Group by name and find latest version
            latest_by_name = {}
            for artifact in artifacts:
                name = artifact.name
                if name not in latest_by_name:
                    latest_by_name[name] = artifact
                else:
                    # Compare versions
                    try:
                        current_ver = SemanticVersion.parse(artifact.version)
                        stored_ver = SemanticVersion.parse(latest_by_name[name].version)
                        if current_ver > stored_ver:
                            latest_by_name[name] = artifact
                    except:
                        # If parsing fails, use string comparison
                        if artifact.version > latest_by_name[name].version:
                            latest_by_name[name] = artifact

            # Create summary
            summary = {}
            for name, artifact in latest_by_name.items():
                summary[name] = {
                    'version': artifact.version,
                    'artifact_id': artifact.id,
                    'artifact_type': artifact.artifact_type.value,
                    'platform': artifact.platform,
                    'size_bytes': artifact.size_bytes,
                    'upload_date': artifact.upload_date.isoformat() if artifact.upload_date else None
                }

            return summary

        except Exception as e:
            self.logger.error(f"Failed to get latest versions summary: {e}")
            return {}

    @service_method(description="Get repository gossip info", public=True)
    async def get_gossip_info(self) -> Dict[str, Any]:
        """
        Get repository information from gossip across cluster

        Returns:
            Dictionary with repository info from all nodes
        """
        try:
            if not self.context:
                return {"success": False, "error": "Context not available"}

            network = self.context.get_shared("network")
            if not network or not hasattr(network, 'gossip'):
                return {"success": False, "error": "Gossip not available"}

            gossip = network.gossip
            repo_info = {}

            # Collect repository info from all nodes
            for node_id, node_info in gossip.node_registry.items():
                if hasattr(node_info, 'metadata') and 'repository' in node_info.metadata:
                    repo_info[node_id] = node_info.metadata['repository']

            return {
                "success": True,
                "repository_nodes": repo_info,
                "count": len(repo_info)
            }

        except Exception as e:
            self.logger.error(f"Failed to get gossip info: {e}")
            return {"success": False, "error": str(e)}

    @service_method(description="Get repository metrics", public=True)
    async def get_metrics(self) -> Dict[str, Any]:
        """
        Get repository metrics and statistics

        Returns:
            Dictionary with repository metrics
        """
        try:
            # Get storage stats
            storage_stats = self.storage.get_storage_stats() if self.storage else {}

            return {
                "success": True,
                "metrics": {
                    # Operation counts
                    "total_uploads": self.total_uploads,
                    "total_downloads": self.total_downloads,
                    "total_deletes": self.total_deletes,

                    # Error counts
                    "upload_errors": self.upload_errors,
                    "download_errors": self.download_errors,

                    # Bandwidth
                    "total_bytes_uploaded": self.total_bytes_uploaded,
                    "total_bytes_downloaded": self.total_bytes_downloaded,

                    # Storage stats
                    "total_artifacts": storage_stats.get('total_artifacts', 0),
                    "total_size_bytes": storage_stats.get('total_size_bytes', 0),
                    "by_type": storage_stats.get('by_type', {}),
                    "total_downloads_all_time": storage_stats.get('total_downloads', 0),

                    # Averages
                    "avg_upload_size": (
                        self.total_bytes_uploaded / self.total_uploads
                        if self.total_uploads > 0 else 0
                    ),
                    "avg_download_size": (
                        self.total_bytes_downloaded / self.total_downloads
                        if self.total_downloads > 0 else 0
                    )
                }
            }

        except Exception as e:
            self.logger.error(f"Failed to get repository metrics: {e}")
            return {"success": False, "error": str(e)}
