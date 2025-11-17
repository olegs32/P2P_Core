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

from .models.artifact import Artifact, ArtifactType, ArtifactStatus, ArtifactDependency
from .models.version import SemanticVersion, VersionComparator, VersionTag
from .storage.backend import StorageBackend


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

        self.logger.info("Repository service initialized")

    async def cleanup(self):
        """Cleanup repository service"""
        self.logger.info("Repository service cleanup")

    def _register_http_endpoints(self):
        """Register HTTP endpoints with FastAPI"""
        if not self.context:
            return

        app = self.context.get_shared("fastapi_app")
        if not app:
            self.logger.warning("FastAPI app not available")
            return

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

                self.logger.info(f"Artifact uploaded: {name} v{version} ({len(file_data)} bytes)")

                return {
                    "success": True,
                    "artifact_id": artifact.id,
                    "storage_path": storage_path,
                    "sha256": artifact.sha256,
                    "size_bytes": artifact.size_bytes
                }

            except Exception as e:
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

                # Return file
                return FileResponse(
                    path=artifact.storage_path,
                    filename=f"{artifact.name}_{artifact.version}",
                    media_type="application/octet-stream"
                )

            except HTTPException:
                raise
            except Exception as e:
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

                return {
                    "success": success,
                    "message": f"Artifact {artifact.name} v{artifact.version} deleted"
                }

            except HTTPException:
                raise
            except Exception as e:
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

        self.logger.info("Repository HTTP endpoints registered")

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
