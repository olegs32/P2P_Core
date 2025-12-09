"""
Metrics Dashboard Service for P2P Coordinator
Provides web interface for monitoring and managing coordinator and workers
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

from layers.service import BaseService, service_method
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


class Run(BaseService):
    """Dashboard service for monitoring and managing P2P cluster"""

    SERVICE_NAME = "metrics_dashboard"

    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.info.description = "Web dashboard for cluster monitoring and management"
        self.info.dependencies = ["system"]
        self.info.domain = "monitoring"

        # Storage for worker metrics
        self.worker_metrics: Dict[str, Dict[str, Any]] = {}
        self.worker_last_seen: Dict[str, datetime] = {}

        # Storage for coordinator metrics
        self.coordinator_metrics: Dict[str, Any] = {}
        self.coordinator_last_update: Optional[datetime] = None

        # Metrics history for graphing (last 100 points per worker)
        self.metrics_history: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

        # Service states
        self.service_states: Dict[str, Dict[str, Any]] = {}

        # Service data (custom data from services like certificates, configs, etc.)
        self.service_data: Dict[str, Dict[str, Any]] = {}

        # Background tasks
        self.cleanup_task = None
        self.coordinator_metrics_task = None

        # WebSocket connections
        self.active_websockets = set()

        # Statistics
        self.stats = {
            "total_updates": 0,
            "active_workers": 0,
            "last_cleanup": None,
            "uptime_start": datetime.now().isoformat()
        }

    async def initialize(self):
        """Initialize dashboard service"""
        self.logger.info("Metrics Dashboard service initializing...")

        # Wait for proxy initialization
        await self._wait_for_proxy()

        # Check if this is coordinator - dashboard should only run on coordinator
        is_coordinator = self.context.config.coordinator_mode
        if is_coordinator:
            # Register HTTP endpoints
            self._register_http_endpoints()

            # Start cleanup task for stale workers
            self.cleanup_task = asyncio.create_task(self._cleanup_loop())

            # Start coordinator metrics collection task
            self.coordinator_metrics_task = asyncio.create_task(self._coordinator_metrics_loop())

            # Collect initial coordinator metrics
            await self._collect_coordinator_metrics()

            # Register listener for new logs (event-driven)
            self._register_log_listener()

            self.logger.info("Metrics Dashboard initialized successfully")
            return True
        else:
            self.logger.info("Dashboard service disabled on worker nodes - skipping initialization")
            return False

    def _register_http_endpoints(self):
        """Register HTTP endpoints for the dashboard"""
        from fastapi import FastAPI, Request
        from fastapi.responses import HTMLResponse, FileResponse
        from pathlib import Path

        # Get FastAPI app from service manager or context
        app = self._get_fastapi_app()
        if not app:
            self.logger.warning("FastAPI app not available, HTTP endpoints not registered")
            return

        # Get template path
        template_path = Path(__file__).parent / "templates" / "dashboard.html"

        # Dashboard HTML page
        @app.get("/dashboard", response_class=HTMLResponse)
        async def dashboard_page():
            """Serve the dashboard HTML page"""
            try:
                with open(template_path, 'r', encoding='utf-8') as f:
                    return f.read()
            except Exception as e:
                self.logger.error(f"Failed to load dashboard template: {e}")
                return HTMLResponse(
                    content="<h1>Dashboard Error</h1><p>Failed to load dashboard template</p>",
                    status_code=500
                )

        # API endpoints for dashboard data
        @app.get("/api/dashboard/full-data")
        async def get_full_dashboard_data():
            """Get all dashboard data in a single request (optimized)"""
            # Get cluster metrics with gossip fallback
            metrics_data = await self.get_cluster_metrics()

            # Collect history for all nodes
            history_data = {}

            # Coordinator history
            coord_history = await self.get_metrics_history("coordinator", limit=50)
            history_data["coordinator"] = coord_history.get("history", [])

            # Worker history
            for worker_id in metrics_data.get("workers", {}).keys():
                worker_history = await self.get_metrics_history(worker_id, limit=50)
                history_data[worker_id] = worker_history.get("history", [])

            # Return everything in one response
            return {
                "timestamp": datetime.now().isoformat(),
                "metrics": metrics_data,
                "history": history_data
            }

        @app.get("/api/dashboard/metrics")
        async def get_dashboard_metrics():
            """Get all cluster metrics"""
            return await self.get_cluster_metrics()

        @app.get("/api/dashboard/history/{node_id}")
        async def get_node_history(node_id: str):
            """Get metrics history for a specific node"""
            return await self.get_metrics_history(node_id, limit=50)

        @app.get("/api/dashboard/stats")
        async def get_dashboard_statistics():
            """Get dashboard statistics"""
            return await self.get_dashboard_stats()

        # Logs API endpoints
        @app.get("/api/logs/sources")
        async def get_log_sources():
            """Get available log sources (nodes and loggers)"""
            try:
                if hasattr(self.proxy, 'log_collector'):
                    return await self.proxy.log_collector.get_log_sources()
                else:
                    return {"nodes": [], "loggers": [], "log_levels": []}
            except Exception as e:
                self.logger.error(f"Error getting log sources: {e}")
                return {"nodes": [], "loggers": [], "log_levels": [], "error": str(e)}

        @app.get("/api/logs")
        async def get_logs(
            node_id: Optional[str] = None,
            level: Optional[str] = None,
            logger_name: Optional[str] = None,
            limit: int = 100,
            offset: int = 0
        ):
            """Get logs with filtering"""
            try:
                if hasattr(self.proxy, 'log_collector'):
                    return await self.proxy.log_collector.get_logs(
                        node_id=node_id,
                        level=level,
                        logger_name=logger_name,
                        limit=limit,
                        offset=offset
                    )
                else:
                    return {"logs": [], "total": 0, "nodes": []}
            except Exception as e:
                self.logger.error(f"Error getting logs: {e}")
                return {"logs": [], "total": 0, "nodes": [], "error": str(e)}

        @app.post("/api/logs/clear")
        async def clear_logs(request: Request):
            """Clear logs for a node or all nodes"""
            try:
                data = await request.json()
                node_id = data.get('node_id')

                if hasattr(self.proxy, 'log_collector'):
                    return await self.proxy.log_collector.clear_logs(node_id=node_id)
                else:
                    return {"success": False, "error": "log_collector not available"}
            except Exception as e:
                self.logger.error(f"Error clearing logs: {e}")
                return {"success": False, "error": str(e)}

        # Gossip API endpoint
        @app.get("/api/gossip/data")
        async def get_gossip_data():
            """Get gossip protocol data from network layer"""
            try:
                network = self.context.get_shared("network")
                if not network:
                    return {"error": "Network layer not available"}

                gossip = network.gossip

                # Получаем данные из gossip
                nodes_data = []
                for node_id, node_info in gossip.node_registry.items():
                    nodes_data.append({
                        "node_id": node_id,
                        "address": node_info.address,
                        "port": node_info.port,
                        "role": node_info.role,
                        "status": node_info.status,
                        "last_seen": node_info.last_seen.isoformat() if node_info.last_seen else None,
                        "metadata": node_info.metadata,
                        "services": list(node_info.services.keys()) if node_info.services else [],
                        "capabilities": node_info.capabilities,
                        "addresses": node_info.addresses
                    })

                return {
                    "current_node_id": gossip.node_id,
                    "gossip_version": gossip.gossip_version,
                    "peer_versions": gossip.peer_versions,
                    "nodes": nodes_data,
                    "cluster_stats": gossip.get_cluster_stats(),
                    "gossip_interval": gossip.gossip_interval if hasattr(gossip, 'gossip_interval') else None,
                    "compression_enabled": gossip.compression_enabled if hasattr(gossip, 'compression_enabled') else False
                }
            except Exception as e:
                self.logger.error(f"Error getting gossip data: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
                return {"error": str(e)}

        @app.post("/api/dashboard/control-service")
        async def control_service_endpoint(request: Request):
            """Control a service on a worker"""
            data = await request.json()
            worker_id = data.get('worker_id')
            service_name = data.get('service_name')
            action = data.get('action')

            if not all([worker_id, service_name, action]):
                return {
                    "success": False,
                    "error": "Missing required parameters"
                }

            return await self.control_service(worker_id, service_name, action)

        @app.post("/api/dashboard/get-config")
        async def get_config_endpoint(request: Request):
            """Get configuration from a node"""
            data = await request.json()
            node_id = data.get('node_id', 'coordinator')

            try:
                if node_id == 'coordinator':
                    system_proxy = self.proxy.system
                else:
                    system_proxy = self.proxy.system.__getattr__(node_id)

                result = await system_proxy.get_config()
                return result
            except Exception as e:
                self.logger.error(f"Failed to get config from {node_id}: {e}")
                return {"success": False, "error": str(e)}

        @app.post("/api/dashboard/update-config")
        async def update_config_endpoint(request: Request):
            """Update configuration on a node"""
            data = await request.json()
            node_id = data.get('node_id', 'coordinator')
            config_updates = data.get('config_updates', {})

            try:
                if node_id == 'coordinator':
                    system_proxy = self.proxy.system
                else:
                    system_proxy = self.proxy.system.__getattr__(node_id)

                result = await system_proxy.update_config(config_updates=config_updates)
                return result
            except Exception as e:
                self.logger.error(f"Failed to update config on {node_id}: {e}")
                return {"success": False, "error": str(e)}

        @app.post("/api/dashboard/list-storage-files")
        async def list_storage_files_endpoint(request: Request):
            """List storage files on a node"""
            data = await request.json()
            node_id = data.get('node_id', 'coordinator')

            try:
                if node_id == 'coordinator':
                    system_proxy = self.proxy.system
                else:
                    system_proxy = self.proxy.system.__getattr__(node_id)

                result = await system_proxy.list_storage_files()
                return result
            except Exception as e:
                self.logger.error(f"Failed to list storage files on {node_id}: {e}")
                return {"success": False, "error": str(e)}

        @app.post("/api/dashboard/get-storage-file")
        async def get_storage_file_endpoint(request: Request):
            """Get storage file content from a node"""
            data = await request.json()
            node_id = data.get('node_id', 'coordinator')
            filename = data.get('filename')
            file_type = data.get('file_type', 'data')

            try:
                if node_id == 'coordinator':
                    system_proxy = self.proxy.system
                else:
                    system_proxy = self.proxy.system.__getattr__(node_id)

                result = await system_proxy.get_storage_file(filename=filename, file_type=file_type)
                return result
            except Exception as e:
                self.logger.error(f"Failed to get storage file from {node_id}: {e}")
                return {"success": False, "error": str(e)}

        @app.post("/api/dashboard/add-storage-file")
        async def add_storage_file_endpoint(request: Request):
            """Add storage file to a node"""
            data = await request.json()
            node_id = data.get('node_id', 'coordinator')
            filename = data.get('filename')
            content = data.get('content')
            file_type = data.get('file_type', 'data')
            is_binary = data.get('is_binary', False)

            try:
                if node_id == 'coordinator':
                    system_proxy = self.proxy.system
                else:
                    system_proxy = self.proxy.system.__getattr__(node_id)

                result = await system_proxy.add_storage_file(
                    filename=filename,
                    content=content,
                    file_type=file_type,
                    is_binary=is_binary
                )
                return result
            except Exception as e:
                self.logger.error(f"Failed to add storage file to {node_id}: {e}")
                return {"success": False, "error": str(e)}

        @app.post("/api/dashboard/delete-storage-file")
        async def delete_storage_file_endpoint(request: Request):
            """Delete storage file from a node"""
            data = await request.json()
            node_id = data.get('node_id', 'coordinator')
            filename = data.get('filename')
            file_type = data.get('file_type', 'data')

            try:
                if node_id == 'coordinator':
                    system_proxy = self.proxy.system
                else:
                    system_proxy = self.proxy.system.__getattr__(node_id)

                result = await system_proxy.delete_storage_file(filename=filename, file_type=file_type)
                return result
            except Exception as e:
                self.logger.error(f"Failed to delete storage file from {node_id}: {e}")
                return {"success": False, "error": str(e)}

        # Service Editor Endpoints
        @app.post("/api/services/p2p-list")
        async def list_p2p_services_endpoint(request: Request):
            """List all available P2P services"""
            data = await request.json()
            node_id = data.get('node_id', 'coordinator')

            try:
                if node_id == 'coordinator':
                    system_proxy = self.proxy.system
                else:
                    system_proxy = self.proxy.system.__getattr__(node_id)

                result = await system_proxy.list_p2p_services()
                return result
            except Exception as e:
                self.logger.error(f"Failed to list P2P services on {node_id}: {e}")
                return {"success": False, "error": str(e)}

        @app.post("/api/services/files")
        async def list_service_files_endpoint(request: Request):
            """List files in a service directory"""
            data = await request.json()
            node_id = data.get('node_id', 'coordinator')
            service_name = data.get('service_name')

            if not service_name:
                return {"success": False, "error": "service_name is required"}

            try:
                if node_id == 'coordinator':
                    system_proxy = self.proxy.system
                else:
                    system_proxy = self.proxy.system.__getattr__(node_id)

                result = await system_proxy.list_service_files(service_name=service_name)
                return result
            except Exception as e:
                self.logger.error(f"Failed to list service files on {node_id}: {e}")
                return {"success": False, "error": str(e)}

        @app.post("/api/services/file/get")
        async def get_service_file_endpoint(request: Request):
            """Get content of a service file"""
            data = await request.json()
            node_id = data.get('node_id', 'coordinator')
            service_name = data.get('service_name')
            file_path = data.get('file_path')

            if not service_name or not file_path:
                return {"success": False, "error": "service_name and file_path are required"}

            try:
                if node_id == 'coordinator':
                    system_proxy = self.proxy.system
                else:
                    system_proxy = self.proxy.system.__getattr__(node_id)

                result = await system_proxy.get_service_file(
                    service_name=service_name,
                    file_path=file_path
                )
                return result
            except Exception as e:
                self.logger.error(f"Failed to get service file on {node_id}: {e}")
                return {"success": False, "error": str(e)}

        @app.post("/api/services/file/update")
        async def update_service_file_endpoint(request: Request):
            """Update content of a service file"""
            data = await request.json()
            node_id = data.get('node_id', 'coordinator')
            service_name = data.get('service_name')
            file_path = data.get('file_path')
            content = data.get('content')
            is_binary = data.get('is_binary', False)

            if not service_name or not file_path or content is None:
                return {"success": False, "error": "service_name, file_path, and content are required"}

            try:
                if node_id == 'coordinator':
                    system_proxy = self.proxy.system
                else:
                    system_proxy = self.proxy.system.__getattr__(node_id)

                result = await system_proxy.update_service_file(
                    service_name=service_name,
                    file_path=file_path,
                    content=content,
                    is_binary=is_binary
                )
                return result
            except Exception as e:
                self.logger.error(f"Failed to update service file on {node_id}: {e}")
                return {"success": False, "error": str(e)}

        @app.post("/api/services/file/delete")
        async def delete_service_file_endpoint(request: Request):
            """Delete a service file"""
            data = await request.json()
            node_id = data.get('node_id', 'coordinator')
            service_name = data.get('service_name')
            file_path = data.get('file_path')

            if not service_name or not file_path:
                return {"success": False, "error": "service_name and file_path are required"}

            try:
                if node_id == 'coordinator':
                    system_proxy = self.proxy.system
                else:
                    system_proxy = self.proxy.system.__getattr__(node_id)

                result = await system_proxy.delete_service_file(
                    service_name=service_name,
                    file_path=file_path
                )
                return result
            except Exception as e:
                self.logger.error(f"Failed to delete service file on {node_id}: {e}")
                return {"success": False, "error": str(e)}

        @app.post("/api/services/file/rename")
        async def rename_service_file_endpoint(request: Request):
            """Rename a service file"""
            data = await request.json()
            node_id = data.get('node_id', 'coordinator')
            service_name = data.get('service_name')
            old_path = data.get('old_path')
            new_name = data.get('new_name')

            if not service_name or not old_path or not new_name:
                return {"success": False, "error": "service_name, old_path, and new_name are required"}

            try:
                if node_id == 'coordinator':
                    system_proxy = self.proxy.system
                else:
                    system_proxy = self.proxy.system.__getattr__(node_id)

                result = await system_proxy.rename_service_file(
                    service_name=service_name,
                    old_path=old_path,
                    new_name=new_name
                )
                return result
            except Exception as e:
                self.logger.error(f"Failed to rename service file on {node_id}: {e}")
                return {"success": False, "error": str(e)}

        @app.post("/api/services/manifest")
        async def get_service_manifest_endpoint(request: Request):
            """Get service manifest.json"""
            data = await request.json()
            node_id = data.get('node_id', 'coordinator')
            service_name = data.get('service_name')

            if not service_name:
                return {"success": False, "error": "service_name is required"}

            try:
                if node_id == 'coordinator':
                    system_proxy = self.proxy.system
                else:
                    system_proxy = self.proxy.system.__getattr__(node_id)

                result = await system_proxy.get_service_manifest(service_name=service_name)
                return result
            except Exception as e:
                self.logger.error(f"Failed to get service manifest on {node_id}: {e}")
                return {"success": False, "error": str(e)}

        @app.post("/api/services/version/update")
        async def update_service_version_endpoint(request: Request):
            """Update service version in manifest"""
            data = await request.json()
            node_id = data.get('node_id', 'coordinator')
            service_name = data.get('service_name')
            version = data.get('version')

            if not service_name or not version:
                return {"success": False, "error": "service_name and version are required"}

            try:
                if node_id == 'coordinator':
                    system_proxy = self.proxy.system
                else:
                    system_proxy = self.proxy.system.__getattr__(node_id)

                result = await system_proxy.update_service_version(
                    service_name=service_name,
                    version=version
                )
                return result
            except Exception as e:
                self.logger.error(f"Failed to update service version on {node_id}: {e}")
                return {"success": False, "error": str(e)}

        @app.post("/api/services/version/increment")
        async def increment_service_version_endpoint(request: Request):
            """Increment service version in manifest"""
            data = await request.json()
            node_id = data.get('node_id', 'coordinator')
            service_name = data.get('service_name')

            if not service_name:
                return {"success": False, "error": "service_name is required"}

            try:
                if node_id == 'coordinator':
                    system_proxy = self.proxy.system
                else:
                    system_proxy = self.proxy.system.__getattr__(node_id)

                result = await system_proxy.increment_service_version(service_name=service_name)
                return result
            except Exception as e:
                self.logger.error(f"Failed to increment service version on {node_id}: {e}")
                return {"success": False, "error": str(e)}

        @app.get("/api/dashboard/service/{node_id}/{service_name}/metrics")
        async def get_service_metrics(node_id: str, service_name: str):
            """Get detailed metrics for a specific service on a node"""
            return await self.get_service_metrics(node_id, service_name)

        @app.get("/api/dashboard/service-data")
        async def get_service_data(service_type: Optional[str] = None):
            """Get custom service data from all workers"""
            data = await self.get_service_data(service_type)

            # Transform data for specific service types
            if service_type == "certificates":
                # Flatten certificate data for easier frontend consumption
                certificates = []
                for worker_id, services in data.get("service_data", {}).items():
                    for service_name, service_info in services.items():
                        if service_info.get("service_type") == "certificates":
                            certs = service_info.get("certificates", [])
                            for cert in certs:
                                cert_copy = cert.copy()
                                cert_copy["worker"] = worker_id
                                certificates.append(cert_copy)

                return {
                    "timestamp": data.get("timestamp"),
                    "certificates": certificates,
                    "total": len(certificates)
                }

            return data

        @app.post("/api/certificates/install")
        async def install_certificate(request: Request):
            """Install certificate from uploaded PFX"""
            try:
                data = await request.json()
                worker = data.get('worker')
                pfx_data = data.get('pfx_data')
                password = data.get('password')
                filename = data.get('filename', 'cert.pfx')

                if not worker or not pfx_data or not password:
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Missing required fields"}
                    )

                # Call the appropriate service based on worker
                result = None
                if worker == "coordinator":
                    # Call local certs_tool
                    if hasattr(self.proxy, 'certs_tool'):
                        result = await self.proxy.certs_tool.install_pfx_from_base64(
                            pfx_base64=pfx_data,
                            password=password,
                            filename=filename
                        )
                    else:
                        return JSONResponse(
                            status_code=500,
                            content={"success": False, "error": "certs_tool service not available"}
                        )
                else:
                    # Call worker's certs_tool via proxy
                    if hasattr(self.proxy, 'certs_tool'):
                        result = await self.proxy.certs_tool.__getattr__(worker).install_pfx_from_base64(
                            pfx_base64=pfx_data,
                            password=password,
                            filename=filename
                        )
                    else:
                        return JSONResponse(
                            status_code=500,
                            content={"success": False, "error": "certs_tool service not available on worker"}
                        )

                # If installation was successful, immediately refresh certificate data
                if result and result.get("success"):
                    self.logger.info(f"Certificate installed successfully, refreshing data from {worker}")
                    try:
                        # Get fresh certificate data from the worker
                        if worker == "coordinator":
                            fresh_data = await self.proxy.certs_tool.get_dashboard_data()
                        else:
                            fresh_data = await self.proxy.certs_tool.__getattr__(worker).get_dashboard_data()

                        # Update the service_data cache
                        if worker not in self.service_data:
                            self.service_data[worker] = {}
                        self.service_data[worker]["certs_tool"] = fresh_data
                        self.logger.info(f"Successfully updated certificate cache for {worker}")
                    except Exception as e:
                        self.logger.warning(f"Failed to refresh certificate data: {e}")
                        # Don't fail the installation, just log the warning

                return JSONResponse(content=result)

            except Exception as e:
                self.logger.error(f"Error installing certificate: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @app.get("/api/certificates/export-cer")
        async def export_cer(worker: str, cert_id: str):
            """Export certificate as CER file"""
            import tempfile
            from fastapi.responses import FileResponse

            try:
                # Get certificate info to find container/thumbprint
                certificates_data = await self.get_service_data(service_type="certificates")
                cert_info = None

                for worker_id, services in certificates_data.get("service_data", {}).items():
                    if worker_id == worker:
                        for service_name, service_info in services.items():
                            for cert in service_info.get("certificates", []):
                                if cert.get("id") == cert_id:
                                    cert_info = cert
                                    break

                if not cert_info:
                    return JSONResponse(
                        status_code=404,
                        content={"success": False, "error": "Certificate not found"}
                    )

                # Create temporary file for export
                with tempfile.NamedTemporaryFile(mode='wb', suffix='.cer', delete=False) as tmp_file:
                    tmp_path = tmp_file.name

                # Export CER
                if worker == "coordinator":
                    if hasattr(self.proxy, 'certs_tool'):
                        await self.proxy.certs_tool.export_certificate_cer(
                            container_name=cert_info.get("container"),
                            thumbprint=cert_info.get("thumbprint"),
                            output_path=tmp_path
                        )
                else:
                    if hasattr(self.proxy, 'certs_tool'):
                        await self.proxy.certs_tool.__getattr__(worker).export_certificate_cer(
                            container_name=cert_info.get("container"),
                            thumbprint=cert_info.get("thumbprint"),
                            output_path=tmp_path
                        )

                # Return file
                return FileResponse(
                    tmp_path,
                    media_type="application/x-x509-ca-cert",
                    filename=f"{cert_info.get('subject_cn', 'certificate')}.cer"
                )

            except Exception as e:
                self.logger.error(f"Error exporting CER: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @app.get("/api/certificates/export-pfx")
        async def export_pfx(worker: str, cert_id: str, password: str):
            """Export certificate as PFX file"""
            import tempfile
            from fastapi.responses import FileResponse

            try:
                # Get certificate info to find container
                certificates_data = await self.get_service_data(service_type="certificates")
                cert_info = None

                for worker_id, services in certificates_data.get("service_data", {}).items():
                    if worker_id == worker:
                        for service_name, service_info in services.items():
                            for cert in service_info.get("certificates", []):
                                if cert.get("id") == cert_id:
                                    cert_info = cert
                                    break

                if not cert_info:
                    return JSONResponse(
                        status_code=404,
                        content={"success": False, "error": "Certificate not found"}
                    )

                # Create temporary file for export
                with tempfile.NamedTemporaryFile(mode='wb', suffix='.pfx', delete=False) as tmp_file:
                    tmp_path = tmp_file.name

                # Export PFX
                if worker == "coordinator":
                    if hasattr(self.proxy, 'certs_tool'):
                        await self.proxy.certs_tool.export_certificate_pfx(
                            container_name=cert_info.get("container"),
                            output_path=tmp_path,
                            password=password
                        )
                else:
                    if hasattr(self.proxy, 'certs_tool'):
                        await self.proxy.certs_tool.__getattr__(worker).export_certificate_pfx(
                            container_name=cert_info.get("container"),
                            output_path=tmp_path,
                            password=password
                        )

                # Return file
                return FileResponse(
                    tmp_path,
                    media_type="application/x-pkcs12",
                    filename=f"{cert_info.get('subject_cn', 'certificate')}.pfx"
                )

            except Exception as e:
                self.logger.error(f"Error exporting PFX: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @app.post("/api/certificates/delete")
        async def delete_certificate(request: Request):
            """Delete certificate"""
            try:
                data = await request.json()
                worker = data.get('worker')
                cert_id = data.get('cert_id')

                if not worker or not cert_id:
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Missing required fields"}
                    )

                # Get certificate info to find thumbprint
                certificates_data = await self.get_service_data(service_type="certificates")
                cert_info = None

                for worker_id, services in certificates_data.get("service_data", {}).items():
                    if worker_id == worker:
                        for service_name, service_info in services.items():
                            for cert in service_info.get("certificates", []):
                                if cert.get("id") == cert_id:
                                    cert_info = cert
                                    break

                if not cert_info:
                    self.logger.error(f"Certificate not found: worker={worker}, cert_id={cert_id}")
                    return JSONResponse(
                        status_code=404,
                        content={"success": False, "error": "Certificate not found"}
                    )

                thumbprint = cert_info.get("thumbprint", "")
                self.logger.info(f"Deleting certificate: worker={worker}, cert_id={cert_id}")

                if not thumbprint:
                    self.logger.error(f"Certificate thumbprint is empty for cert_id={cert_id}")
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Certificate thumbprint is empty"}
                    )

                # Delete certificate
                result = None
                if worker == "coordinator":
                    if hasattr(self.proxy, 'certs_tool'):
                        result = await self.proxy.certs_tool.delete_certificate(
                            thumbprint=thumbprint
                        )
                    else:
                        return JSONResponse(
                            status_code=500,
                            content={"success": False, "error": "certs_tool service not available"}
                        )
                else:
                    if hasattr(self.proxy, 'certs_tool'):
                        result = await self.proxy.certs_tool.__getattr__(worker).delete_certificate(
                            thumbprint=thumbprint
                        )
                    else:
                        return JSONResponse(
                            status_code=500,
                            content={"success": False, "error": "certs_tool service not available"}
                        )

                # If deletion was successful, immediately refresh certificate data
                if result and result.get("success"):
                    self.logger.info(f"Certificate deleted successfully, refreshing data from {worker}")
                    try:
                        # Get fresh certificate data from the worker
                        if worker == "coordinator":
                            fresh_data = await self.proxy.certs_tool.get_dashboard_data()
                        else:
                            fresh_data = await self.proxy.certs_tool.__getattr__(worker).get_dashboard_data()

                        # Update the service_data cache
                        if worker not in self.service_data:
                            self.service_data[worker] = {}
                        self.service_data[worker]["certs_tool"] = fresh_data
                        self.logger.info(f"Successfully updated certificate cache for {worker}")
                    except Exception as e:
                        self.logger.warning(f"Failed to refresh certificate data: {e}")
                        # Don't fail the deletion, just log the warning

                return JSONResponse(content=result)

            except Exception as e:
                self.logger.error(f"Error deleting certificate: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @app.post("/api/certificates/deploy")
        async def deploy_certificate(request: Request):
            """Deploy certificate from coordinator to worker"""
            import tempfile
            import base64

            try:
                data = await request.json()
                cert_id = data.get('cert_id')
                source_worker = data.get('source_worker')
                target_worker = data.get('target_worker')
                password = data.get('password')

                if not cert_id or not source_worker or not target_worker or not password:
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Missing required fields"}
                    )

                if source_worker != 'coordinator':
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Can only deploy from coordinator"}
                    )

                self.logger.info(f"Deploying certificate {cert_id} from {source_worker} to {target_worker}")

                # Get certificate info from coordinator
                certificates_data = await self.get_service_data(service_type="certificates")
                cert_info = None

                for worker_id, services in certificates_data.get("service_data", {}).items():
                    if worker_id == source_worker:
                        for service_name, service_info in services.items():
                            for cert in service_info.get("certificates", []):
                                if cert.get("id") == cert_id:
                                    cert_info = cert
                                    break

                if not cert_info:
                    return JSONResponse(
                        status_code=404,
                        content={"success": False, "error": "Certificate not found on coordinator"}
                    )

                container = cert_info.get("container")
                if not container:
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Certificate container not found"}
                    )

                # Step 1: Export PFX from coordinator
                with tempfile.NamedTemporaryFile(mode='wb', suffix='.pfx', delete=False) as tmp_file:
                    tmp_pfx_path = tmp_file.name

                try:
                    if hasattr(self.proxy, 'certs_tool'):
                        export_result = await self.proxy.certs_tool.export_certificate_pfx(
                            container_name=container,
                            output_path=tmp_pfx_path,
                            password=password
                        )

                        if not export_result:
                            return JSONResponse(
                                status_code=500,
                                content={"success": False, "error": "Failed to export certificate from coordinator"}
                            )

                        # Step 2: Read PFX file and encode to base64
                        with open(tmp_pfx_path, 'rb') as f:
                            pfx_data = f.read()
                            pfx_base64 = base64.b64encode(pfx_data).decode('utf-8')

                        # Step 3: Install on target worker
                        if hasattr(self.proxy, 'certs_tool'):
                            install_result = await self.proxy.certs_tool.__getattr__(target_worker).install_pfx_from_base64(
                                pfx_base64=pfx_base64,
                                password=password,
                                filename=f"{cert_info.get('subject_cn', 'cert')}.pfx"
                            )

                            # If deployment was successful, immediately refresh certificate data
                            if install_result and install_result.get("success"):
                                self.logger.info(f"Certificate deployed successfully to {target_worker}, refreshing data")
                                try:
                                    # Get fresh certificate data from the target worker
                                    fresh_data = await self.proxy.certs_tool.__getattr__(target_worker).get_dashboard_data()

                                    # Update the service_data cache
                                    if target_worker not in self.service_data:
                                        self.service_data[target_worker] = {}
                                    self.service_data[target_worker]["certs_tool"] = fresh_data
                                    self.logger.info(f"Successfully updated certificate cache for {target_worker}")
                                except Exception as e:
                                    self.logger.warning(f"Failed to refresh certificate data: {e}")
                                    # Don't fail the deployment, just log the warning

                            return JSONResponse(content=install_result)
                        else:
                            return JSONResponse(
                                status_code=500,
                                content={"success": False, "error": "certs_tool not available on target worker"}
                            )

                    else:
                        return JSONResponse(
                            status_code=500,
                            content={"success": False, "error": "certs_tool not available on coordinator"}
                        )

                finally:
                    # Clean up temporary file
                    try:
                        import os
                        os.unlink(tmp_pfx_path)
                    except Exception as e:
                        self.logger.warning(f"Failed to delete temporary file {tmp_pfx_path}: {e}")

            except Exception as e:
                self.logger.error(f"Error deploying certificate: {e}", exc_info=True)
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @app.post("/api/dashboard/certificates/bulk-deploy")
        async def bulk_deploy_certificates(request: Request):
            """Bulk deploy certificates from coordinator to worker"""
            try:
                data = await request.json()
                cert_ids = data.get('cert_ids', [])  # List of cert IDs to deploy
                target_worker = data.get('target_worker')
                current_password = data.get('current_password')
                new_password = data.get('new_password')  # Optional, if empty use current_password

                # Validate inputs
                if not cert_ids or not target_worker or not current_password:
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Missing required fields (cert_ids, target_worker, current_password)"}
                    )

                if not new_password:
                    new_password = current_password

                self.logger.info(f"Bulk deploying {len(cert_ids)} certificates to {target_worker}")

                # Get coordinator certificates
                certificates_data = await self.get_service_data(service_type="certificates")
                coordinator_certs = {}

                for worker_id, services in certificates_data.get("service_data", {}).items():
                    if worker_id == "coordinator":
                        for service_name, service_info in services.items():
                            for cert in service_info.get("certificates", []):
                                coordinator_certs[cert.get("id")] = cert

                # Find certificates to deploy
                certs_to_deploy = []
                missing_certs = []

                for cert_id in cert_ids:
                    if cert_id in coordinator_certs:
                        certs_to_deploy.append(coordinator_certs[cert_id])
                    else:
                        missing_certs.append(cert_id)
                        self.logger.warning(f"Certificate not found: {cert_id}")

                if not certs_to_deploy:
                    return JSONResponse(
                        status_code=404,
                        content={"success": False, "error": "No certificates found to deploy"}
                    )

                # Export all certificates to PFX bytes on coordinator
                pfx_list = []
                export_errors = []

                for cert in certs_to_deploy:
                    container = cert.get("container")
                    if not container:
                        export_errors.append(f"No container for {cert.get('subject_cn', 'unknown')}")
                        self.logger.error(f"Certificate {cert.get('id')} has no container")
                        continue

                    try:
                        # Export PFX to memory (without saving to disk)
                        if hasattr(self.proxy, 'certs_tool'):
                            export_result = await self.proxy.certs_tool.export_pfx_to_bytes(
                                container_name=container,
                                password=current_password
                            )

                            if export_result.get("success"):
                                pfx_list.append({
                                    "pfx_base64": export_result.get("pfx_base64"),
                                    "filename": f"{cert.get('subject_cn', 'cert')}.pfx"
                                })
                                self.logger.info(f"Exported {cert.get('subject_cn')} successfully")
                            else:
                                export_errors.append(f"Failed to export {cert.get('subject_cn')}: {export_result.get('error')}")
                                self.logger.error(f"Failed to export {cert.get('subject_cn')}: {export_result.get('error')}")
                        else:
                            return JSONResponse(
                                status_code=500,
                                content={"success": False, "error": "certs_tool not available on coordinator"}
                            )

                    except Exception as e:
                        export_errors.append(f"Error exporting {cert.get('subject_cn')}: {str(e)}")
                        self.logger.error(f"Error exporting {cert.get('subject_cn')}: {e}", exc_info=True)

                if not pfx_list:
                    return JSONResponse(
                        status_code=500,
                        content={
                            "success": False,
                            "error": "Failed to export any certificates",
                            "export_errors": export_errors
                        }
                    )

                # Batch install on target worker
                if hasattr(self.proxy, 'certs_tool'):
                    install_result = await self.proxy.certs_tool.__getattr__(target_worker).batch_install_pfx_from_bytes(
                        pfx_list=pfx_list,
                        current_password=current_password,
                        new_password=new_password
                    )

                    # Update certificate data on worker after successful installation
                    if install_result.get("success") and install_result.get("success_count", 0) > 0:
                        try:
                            self.logger.info(f"Bulk deployment successful to {target_worker}, refreshing data")
                            # Get fresh certificate data from the target worker
                            fresh_data = await self.proxy.certs_tool.__getattr__(target_worker).get_dashboard_data()

                            # Update the service_data cache
                            if target_worker not in self.service_data:
                                self.service_data[target_worker] = {}
                            self.service_data[target_worker]["certs_tool"] = fresh_data
                            self.logger.info(f"Successfully updated certificate cache for {target_worker}")
                        except Exception as e:
                            self.logger.warning(f"Failed to refresh certificate data: {e}")

                    # Combine export errors with install results
                    response = {
                        **install_result,
                        "export_errors": export_errors,
                        "missing_certs": missing_certs
                    }

                    return JSONResponse(content=response)
                else:
                    return JSONResponse(
                        status_code=500,
                        content={"success": False, "error": "certs_tool not available on target worker"}
                    )

            except Exception as e:
                self.logger.error(f"Error in bulk deploy: {e}", exc_info=True)
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @app.post("/api/dashboard/certificates/bulk-delete")
        async def bulk_delete_certificates(request: Request):
            """Bulk delete certificates from a worker or coordinator"""
            try:
                data = await request.json()
                cert_ids = data.get('cert_ids', [])  # List of cert IDs to delete
                target_node = data.get('target_node')  # Node where certificates are located

                # Validate inputs
                if not cert_ids or not target_node:
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Missing required fields (cert_ids, target_node)"}
                    )

                self.logger.info(f"Bulk deleting {len(cert_ids)} certificates from {target_node}")

                # Get certificates from target node
                certificates_data = await self.get_service_data(service_type="certificates")
                node_certs = {}

                for worker_id, services in certificates_data.get("service_data", {}).items():
                    if worker_id == target_node:
                        for service_name, service_info in services.items():
                            for cert in service_info.get("certificates", []):
                                node_certs[cert.get("id")] = cert

                # Find certificates to delete
                certs_to_delete = []
                missing_certs = []

                for cert_id in cert_ids:
                    if cert_id in node_certs:
                        certs_to_delete.append(node_certs[cert_id])
                    else:
                        missing_certs.append(cert_id)
                        self.logger.warning(f"Certificate not found: {cert_id}")

                if not certs_to_delete:
                    return JSONResponse(
                        status_code=404,
                        content={"success": False, "error": "No certificates found to delete"}
                    )

                # Delete certificates one by one
                results = []
                success_count = 0
                fail_count = 0

                for cert in certs_to_delete:
                    thumbprint = cert.get("thumbprint")
                    if not thumbprint:
                        results.append({
                            "cert_id": cert.get("id"),
                            "subject": cert.get("subject", "Unknown"),
                            "success": False,
                            "error": "No thumbprint available"
                        })
                        fail_count += 1
                        continue

                    try:
                        # Delete certificate on target node
                        if target_node == "coordinator":
                            if hasattr(self.proxy, 'certs_tool'):
                                delete_result = await self.proxy.certs_tool.delete_certificate(
                                    thumbprint=thumbprint
                                )
                            else:
                                results.append({
                                    "cert_id": cert.get("id"),
                                    "subject": cert.get("subject", "Unknown"),
                                    "success": False,
                                    "error": "certs_tool not available on coordinator"
                                })
                                fail_count += 1
                                continue
                        else:
                            if hasattr(self.proxy, 'certs_tool'):
                                delete_result = await self.proxy.certs_tool.__getattr__(target_node).delete_certificate(
                                    thumbprint=thumbprint
                                )
                            else:
                                results.append({
                                    "cert_id": cert.get("id"),
                                    "subject": cert.get("subject", "Unknown"),
                                    "success": False,
                                    "error": "certs_tool not available on target node"
                                })
                                fail_count += 1
                                continue

                        if delete_result.get("success"):
                            results.append({
                                "cert_id": cert.get("id"),
                                "subject": cert.get("subject", "Unknown"),
                                "success": True,
                                "error": ""
                            })
                            success_count += 1
                            self.logger.info(f"Successfully deleted certificate: {cert.get('subject')}")
                        else:
                            results.append({
                                "cert_id": cert.get("id"),
                                "subject": cert.get("subject", "Unknown"),
                                "success": False,
                                "error": delete_result.get("error", "Unknown error")
                            })
                            fail_count += 1
                            self.logger.error(f"Failed to delete certificate: {cert.get('subject')}")

                    except Exception as e:
                        results.append({
                            "cert_id": cert.get("id"),
                            "subject": cert.get("subject", "Unknown"),
                            "success": False,
                            "error": str(e)
                        })
                        fail_count += 1
                        self.logger.error(f"Error deleting certificate {cert.get('subject')}: {e}", exc_info=True)

                # Refresh certificate data on target node after deletion
                if success_count > 0:
                    try:
                        self.logger.info(f"Bulk deletion successful on {target_node}, refreshing data")
                        # Get fresh certificate data from the target node
                        if target_node == "coordinator":
                            fresh_data = await self.proxy.certs_tool.get_dashboard_data()
                        else:
                            fresh_data = await self.proxy.certs_tool.__getattr__(target_node).get_dashboard_data()

                        # Update the service_data cache
                        if target_node not in self.service_data:
                            self.service_data[target_node] = {}
                        self.service_data[target_node]["certs_tool"] = fresh_data
                        self.logger.info(f"Successfully updated certificate cache for {target_node}")
                    except Exception as e:
                        self.logger.warning(f"Failed to refresh certificate data: {e}")

                self.logger.info(f"Bulk delete complete: {success_count} succeeded, {fail_count} failed")

                return JSONResponse(content={
                    "success": success_count > 0,
                    "total": len(cert_ids),
                    "success_count": success_count,
                    "fail_count": fail_count,
                    "results": results,
                    "missing_certs": missing_certs
                })

            except Exception as e:
                self.logger.error(f"Error in bulk delete: {e}", exc_info=True)
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        # Service deployment endpoints
        @app.get("/api/services/list")
        async def list_services():
            """Get list of services with versions from coordinator"""
            try:
                if hasattr(self.proxy, 'orchestrator'):
                    result = await self.proxy.orchestrator.get_services_with_versions()
                    return JSONResponse(content=result)
                else:
                    return JSONResponse(
                        status_code=500,
                        content={"success": False, "error": "orchestrator service not available"}
                    )
            except Exception as e:
                self.logger.error(f"Error getting services list: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @app.post("/api/services/deploy")
        async def deploy_service(request: Request):
            """Deploy service from coordinator to workers"""
            try:
                data = await request.json()
                service_name = data.get('service_name')
                target_workers = data.get('target_workers', [])
                force_reinstall = data.get('force_reinstall', False)

                if not service_name or not target_workers:
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Missing service_name or target_workers"}
                    )

                self.logger.info(f"Deploying service {service_name} to workers: {target_workers}")

                if hasattr(self.proxy, 'orchestrator'):
                    result = await self.proxy.orchestrator.deploy_service_to_workers(
                        service_name=service_name,
                        target_workers=target_workers,
                        force_reinstall=force_reinstall
                    )
                    return JSONResponse(content=result)
                else:
                    return JSONResponse(
                        status_code=500,
                        content={"success": False, "error": "orchestrator service not available"}
                    )

            except Exception as e:
                self.logger.error(f"Error deploying service: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @app.post("/api/services/update")
        async def update_service(request: Request):
            """Update service on workers if coordinator has newer version"""
            try:
                data = await request.json()
                service_name = data.get('service_name')
                target_workers = data.get('target_workers')  # Can be None to update all

                if not service_name:
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Missing service_name"}
                    )

                self.logger.info(f"Updating service {service_name} on workers")

                if hasattr(self.proxy, 'orchestrator'):
                    result = await self.proxy.orchestrator.update_service_on_workers(
                        service_name=service_name,
                        target_workers=target_workers
                    )
                    return JSONResponse(content=result)
                else:
                    return JSONResponse(
                        status_code=500,
                        content={"success": False, "error": "orchestrator service not available"}
                    )

            except Exception as e:
                self.logger.error(f"Error updating service: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @app.post("/api/services/compare-versions")
        async def compare_versions(request: Request):
            """Compare service versions across workers"""
            try:
                data = await request.json()
                service_name = data.get('service_name')
                worker_nodes = data.get('worker_nodes', [])

                if not service_name or not worker_nodes:
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Missing service_name or worker_nodes"}
                    )

                self.logger.info(f"Comparing versions for service {service_name}")

                if hasattr(self.proxy, 'orchestrator'):
                    result = await self.proxy.orchestrator.compare_service_versions(
                        service_name=service_name,
                        worker_nodes=worker_nodes
                    )
                    return JSONResponse(content=result)
                else:
                    return JSONResponse(
                        status_code=500,
                        content={"success": False, "error": "orchestrator service not available"}
                    )

            except Exception as e:
                self.logger.error(f"Error comparing versions: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        # ============ Update Server API Endpoints ============

        @app.get("/api/dashboard/updates/active")
        async def get_active_updates():
            """Get list of active update tasks"""
            try:
                if hasattr(self.proxy, 'update_server'):
                    result = await self.proxy.update_server.list_active_updates()
                    return JSONResponse(content=result)
                else:
                    return JSONResponse(
                        status_code=404,
                        content={"success": False, "error": "update_server service not available"}
                    )
            except Exception as e:
                self.logger.error(f"Error getting active updates: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @app.get("/api/dashboard/updates/history")
        async def get_update_history(limit: int = 20):
            """Get update history"""
            try:
                if hasattr(self.proxy, 'update_server'):
                    result = await self.proxy.update_server.get_update_history(limit=limit)
                    return JSONResponse(content=result)
                else:
                    return JSONResponse(
                        status_code=404,
                        content={"success": False, "error": "update_server service not available"}
                    )
            except Exception as e:
                self.logger.error(f"Error getting update history: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @app.post("/api/dashboard/updates/start")
        async def start_update(request: Request):
            """Start a new update task"""
            try:
                data = await request.json()

                if hasattr(self.proxy, 'update_server'):
                    result = await self.proxy.update_server.start_update(
                        artifact_id=data.get('artifact_id'),
                        artifact_name=data.get('artifact_name'),
                        target_version=data.get('target_version'),
                        target_nodes=data.get('target_nodes', []),
                        strategy=data.get('strategy', 'rolling')
                    )
                    return JSONResponse(content=result)
                else:
                    return JSONResponse(
                        status_code=404,
                        content={"success": False, "error": "update_server service not available"}
                    )
            except Exception as e:
                self.logger.error(f"Error starting update: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @app.post("/api/dashboard/updates/execute")
        async def execute_update(request: Request):
            """Execute an update task"""
            try:
                data = await request.json()
                update_id = data.get('update_id')

                if not update_id:
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "update_id is required"}
                    )

                if hasattr(self.proxy, 'update_server'):
                    result = await self.proxy.update_server.execute_update_task(update_id=update_id)
                    return JSONResponse(content=result)
                else:
                    return JSONResponse(
                        status_code=404,
                        content={"success": False, "error": "update_server service not available"}
                    )
            except Exception as e:
                self.logger.error(f"Error executing update: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @app.post("/api/dashboard/updates/pause")
        async def pause_update(request: Request):
            """Pause an update task"""
            try:
                data = await request.json()
                update_id = data.get('update_id')

                if not update_id:
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "update_id is required"}
                    )

                if hasattr(self.proxy, 'update_server'):
                    result = await self.proxy.update_server.pause_update(update_id=update_id)
                    return JSONResponse(content=result)
                else:
                    return JSONResponse(
                        status_code=404,
                        content={"success": False, "error": "update_server service not available"}
                    )
            except Exception as e:
                self.logger.error(f"Error pausing update: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        @app.post("/api/dashboard/updates/cancel")
        async def cancel_update(request: Request):
            """Cancel an update task"""
            try:
                data = await request.json()
                update_id = data.get('update_id')

                if not update_id:
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "update_id is required"}
                    )

                if hasattr(self.proxy, 'update_server'):
                    result = await self.proxy.update_server.cancel_update(update_id=update_id)
                    return JSONResponse(content=result)
                else:
                    return JSONResponse(
                        status_code=404,
                        content={"success": False, "error": "update_server service not available"}
                    )
            except Exception as e:
                self.logger.error(f"Error cancelling update: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": str(e)}
                )

        # WebSocket endpoint for real-time updates
        from fastapi import WebSocket, WebSocketDisconnect
        import json

        @app.websocket("/ws/dashboard")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket endpoint for real-time dashboard updates"""
            await websocket.accept()
            self.active_websockets.add(websocket)

            try:
                # Send initial data immediately (includes logs and log sources)
                initial_data = await self._gather_ws_data()

                # Add logs, log sources, and service data to initial load only
                if hasattr(self.proxy, 'log_collector'):
                    try:
                        logs_data = await self.proxy.log_collector.get_logs(
                            node_id=None,
                            level=None,
                            logger_name=None,
                            limit=100,
                            offset=0
                        )
                        initial_data["logs"] = logs_data

                        log_sources = await self.proxy.log_collector.get_log_sources()
                        initial_data["log_sources"] = log_sources
                    except Exception as e:
                        self.logger.debug(f"Failed to get logs for initial load: {e}")

                # Add service data (certificates) to initial load only
                try:
                    service_data = await self.get_service_data(service_type="certificates")
                    initial_data["service_data"] = service_data
                except Exception as e:
                    self.logger.debug(f"Failed to get service data for initial load: {e}")

                await websocket.send_json({
                    "type": "initial",
                    "data": initial_data
                })

                # Keep connection alive and send updates periodically
                while True:
                    try:
                        # Wait for ping from client (heartbeat) or timeout
                        message = await asyncio.wait_for(
                            websocket.receive_text(),
                            timeout=10.0
                        )

                        # Handle ping - send pong AND update data
                        if message == "ping":
                            # Gather fresh data
                            update_data = await self._gather_ws_data()

                            # Send pong
                            await websocket.send_json({"type": "pong"})

                            # Send update
                            await websocket.send_json({
                                "type": "update",
                                "data": update_data,
                                "timestamp": datetime.now().isoformat()
                            })

                    except asyncio.TimeoutError:
                        # No message received in 10 seconds, send update anyway
                        update_data = await self._gather_ws_data()
                        await websocket.send_json({
                            "type": "update",
                            "data": update_data,
                            "timestamp": datetime.now().isoformat()
                        })

            except WebSocketDisconnect:
                pass  # Client disconnected normally
            except Exception as e:
                self.logger.error(f"WebSocket error: {e}")
            finally:
                self.active_websockets.discard(websocket)

        self.logger.debug("WebSocket endpoint registered at /ws/dashboard")

        # Mount static files directory
        static_dir = Path(__file__).parent / "static"
        if static_dir.exists():
            app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
            self.logger.debug(f"Static files mounted from {static_dir}")
        else:
            self.logger.warning(f"Static directory not found at {static_dir} - CDN fallback will be used")

        # Register Hash Jobs API
        self._register_hash_jobs_api(app)

        self.logger.info("Dashboard HTTP endpoints registered")

    def _get_fastapi_app(self):
        """Get FastAPI app from service manager or context"""
        try:
            # Try to get from service manager (rpc handler has app)
            if hasattr(self, '_service_manager') and self._service_manager:
                if hasattr(self._service_manager, 'rpc'):
                    if hasattr(self._service_manager.rpc, 'app'):
                        return self._service_manager.rpc.app

            # Try to get from context
            if hasattr(self, 'context') and self.context:
                service_layer = self.context.get_shared("service_layer")
                if service_layer and hasattr(service_layer, 'app'):
                    return service_layer.app

            return None

        except Exception as e:
            self.logger.error(f"Error getting FastAPI app: {e}")
            return None

    async def _wait_for_proxy(self):
        """Wait for proxy client injection"""
        retry_count = 0
        max_retries = 30

        while self.proxy is None and retry_count < max_retries:
            self.logger.debug(f"Waiting for proxy... {retry_count + 1}/{max_retries}")
            await asyncio.sleep(2)
            retry_count += 1

        if self.proxy:
            self.logger.info("Proxy initialized successfully")
        else:
            self.logger.warning("Proxy not available, some features may be limited")

    async def _gather_ws_data(self):
        """Gather all data for WebSocket updates"""
        try:
            # Collect metrics
            metrics_data = await self.get_cluster_metrics()

            # Collect metrics history for charts
            history_data = {}

            # Coordinator history
            if "coordinator" in self.metrics_history:
                history_data["coordinator"] = self.metrics_history["coordinator"][-50:]  # Last 50 points

            # Worker histories
            for worker_id in self.worker_metrics.keys():
                if worker_id in self.metrics_history:
                    history_data[worker_id] = self.metrics_history[worker_id][-50:]

            # NOTE: Logs are now event-driven via WebSocket (not sent in periodic updates)
            # Service data (certificates) removed from periodic updates to avoid spam

            # Collect hash jobs data for real-time updates
            hash_jobs_data = {}
            try:
                if hasattr(self.proxy, 'hash_coordinator'):
                    # Get all active jobs
                    jobs_result = await self.proxy.hash_coordinator.get_all_jobs()
                    hash_jobs_data["jobs"] = jobs_result.get("jobs", [])

                    # Get workers distribution
                    network = self.context.get_shared("network")
                    if network:
                        workers_distribution = []
                        node_registry = network.gossip.node_registry

                        # Find coordinator for chunk data
                        coordinator_nodes = [n for n in node_registry.values() if n.role == "coordinator"]
                        coordinator = coordinator_nodes[0] if coordinator_nodes else None

                        for node_id, node_info in node_registry.items():
                            if node_info.role == "worker":
                                worker_status = node_info.metadata.get("hash_worker_status", {})
                                if worker_status:
                                    worker_data = {
                                        "worker_id": node_id,
                                        "address": f"{node_info.address}:{node_info.port}",
                                        "current_job": worker_status.get("job_id"),
                                        "current_chunk": worker_status.get("chunk_id"),
                                        "status": worker_status.get("status", "idle"),
                                        "progress": worker_status.get("progress", 0),
                                        "total_hashes": worker_status.get("total_hashes", 0),
                                        "completed_chunks": worker_status.get("completed_chunks", 0),
                                        "chunks": []
                                    }

                                    # Collect chunks assigned to this worker from coordinator metadata
                                    if coordinator:
                                        for key, value in coordinator.metadata.items():
                                            if key.startswith("hash_batches_"):
                                                job_id = key.replace("hash_batches_", "")

                                                # Parse versioned batches format
                                                if isinstance(value, dict):
                                                    for version_key, batch_data in value.items():
                                                        if isinstance(batch_data, dict) and "chunks" in batch_data:
                                                            for chunk_id, chunk_data in batch_data["chunks"].items():
                                                                if chunk_data.get("assigned_worker") == node_id:
                                                                    chunk_info = {
                                                                        "job_id": job_id,
                                                                        "chunk_id": int(chunk_id),
                                                                        "start_index": chunk_data.get("start_index", 0),
                                                                        "end_index": chunk_data.get("end_index", 0),
                                                                        "size": chunk_data.get("end_index", 0) - chunk_data.get("start_index", 0),
                                                                        "status": chunk_data.get("status", "unknown"),
                                                                        "progress": chunk_data.get("progress", 0)
                                                                    }
                                                                    worker_data["chunks"].append(chunk_info)

                                    workers_distribution.append(worker_data)

                        # Calculate summary statistics
                        total_chunks = 0
                        total_hashes = 0
                        for worker in workers_distribution:
                            total_chunks += len(worker["chunks"])
                            total_hashes += worker["total_hashes"]

                        hash_jobs_data["workers"] = workers_distribution
                        hash_jobs_data["summary"] = {
                            "total_workers": len(workers_distribution),
                            "active_workers": len([w for w in workers_distribution if w["status"] in ["working", "solved"]]),
                            "total_chunks": total_chunks,
                            "total_hashes_computed": total_hashes
                        }
                    else:
                        hash_jobs_data["workers"] = []
                        hash_jobs_data["summary"] = {
                            "total_workers": 0,
                            "active_workers": 0,
                            "total_chunks": 0,
                            "total_hashes_computed": 0
                        }
                else:
                    hash_jobs_data["jobs"] = []
                    hash_jobs_data["workers"] = []
                    hash_jobs_data["summary"] = {
                        "total_workers": 0,
                        "active_workers": 0,
                        "total_chunks": 0,
                        "total_hashes_computed": 0
                    }
            except Exception as e:
                self.logger.debug(f"Failed to get hash jobs data: {e}")
                hash_jobs_data["jobs"] = []
                hash_jobs_data["workers"] = []
                hash_jobs_data["summary"] = {
                    "total_workers": 0,
                    "active_workers": 0,
                    "total_chunks": 0,
                    "total_hashes_computed": 0
                }

            # Collect gossip data for real-time updates
            gossip_data = {}
            try:
                network = self.context.get_shared("network")
                if network:
                    gossip = network.gossip

                    # Collect nodes data for tables
                    nodes_data = []
                    for node_id, node_info in gossip.node_registry.items():
                        nodes_data.append({
                            "node_id": node_id,
                            "address": node_info.address,
                            "port": node_info.port,
                            "role": node_info.role,
                            "status": node_info.status,
                            "last_seen": node_info.last_seen.isoformat() if node_info.last_seen else None,
                            "metadata": node_info.metadata,
                            "services": list(node_info.services.keys()) if node_info.services else [],
                            "capabilities": node_info.capabilities,
                            "addresses": node_info.addresses
                        })

                    gossip_data = {
                        "current_node_id": gossip.node_id,
                        "gossip_version": gossip.gossip_version,
                        "peer_versions": gossip.peer_versions,
                        "nodes": nodes_data,
                        "cluster_stats": gossip.get_cluster_stats(),
                        "nodes_count": len(gossip.node_registry)
                    }
            except Exception as e:
                self.logger.debug(f"Failed to get gossip data: {e}")
                gossip_data = {}

            return {
                "metrics": metrics_data,
                "history": history_data,
                "hash_jobs": hash_jobs_data,
                "gossip": gossip_data,
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            self.logger.error(f"Error gathering WebSocket data: {e}")
            return {
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }

    def _register_log_listener(self):
        """Register listener for new logs from log_collector (event-driven)"""
        try:
            # Get log_collector service from service manager
            if hasattr(self, 'context') and self.context:
                service_manager = self.context.get_shared('service_manager')
                if service_manager and hasattr(service_manager, 'services'):
                    log_collector = service_manager.services.get('log_collector')
                    if log_collector and hasattr(log_collector, 'add_new_log_listener'):
                        log_collector.add_new_log_listener(self._on_new_logs)
                        self.logger.info("Registered event-driven log listener")
                    else:
                        self.logger.warning("log_collector service not found or doesn't support listeners")
        except Exception as e:
            self.logger.error(f"Failed to register log listener: {e}")

    async def _on_new_logs(self, node_id: str, new_logs: list):
        """
        Called when new logs arrive at log_collector (event-driven)
        Broadcasts new logs to all connected WebSocket clients
        """
        if not self.active_websockets:
            return  # No clients connected

        try:
            # Prepare message
            message = {
                "type": "new_logs",
                "node_id": node_id,
                "logs": new_logs,
                "timestamp": datetime.now().isoformat()
            }

            # Broadcast to all connected clients
            disconnected = []
            for websocket in self.active_websockets:
                try:
                    await websocket.send_json(message)
                except Exception as e:
                    self.logger.debug(f"Failed to send to WebSocket client: {e}")
                    disconnected.append(websocket)

            # Remove disconnected clients
            for ws in disconnected:
                self.active_websockets.discard(ws)

            self.logger.debug(f"Broadcasted {len(new_logs)} new logs from {node_id} to {len(self.active_websockets)} clients")

        except Exception as e:
            self.logger.error(f"Error broadcasting new logs: {e}")

    def _register_hash_jobs_api(self, app):
        """Register Hash Jobs API endpoints"""

        @app.post("/api/hash/create-job")
        async def create_hash_job(request: Request):
            """Create new hash computation job"""
            try:
                data = await request.json()
                job_id = data.get('job_id')
                mode = data.get('mode', 'brute')
                hash_algo = data.get('hash_algo', 'sha256')
                target_hash = data.get('target_hash')
                target_hashes = data.get('target_hashes')
                base_chunk_size = data.get('base_chunk_size', 1000000)
                lookahead_batches = data.get('lookahead_batches', 10)

                # Brute force parameters
                charset = data.get('charset')
                length = data.get('length')

                # Dictionary attack parameters
                wordlist = data.get('wordlist')
                mutations = data.get('mutations')

                # WPA/WPA2 parameters
                ssid = data.get('ssid')

                # Validation
                if not job_id:
                    return {"success": False, "error": "job_id is required"}

                if mode == 'brute':
                    if not charset or not length:
                        return {"success": False, "error": "charset and length are required for brute force mode"}
                elif mode == 'dictionary':
                    if not wordlist or len(wordlist) == 0:
                        return {"success": False, "error": "wordlist is required for dictionary mode"}

                # Call hash_coordinator service (local call, no .coordinator suffix)
                result = await self.proxy.hash_coordinator.create_job(
                    job_id=job_id,
                    mode=mode,
                    charset=charset,
                    length=length,
                    wordlist=wordlist,
                    mutations=mutations,
                    hash_algo=hash_algo,
                    target_hash=target_hash,
                    target_hashes=target_hashes,
                    ssid=ssid,
                    base_chunk_size=base_chunk_size,
                    lookahead_batches=lookahead_batches
                )

                return result

            except Exception as e:
                self.logger.error(f"Failed to create hash job: {e}")
                return {"success": False, "error": str(e)}

        @app.get("/api/hash/jobs")
        async def get_hash_jobs():
            """Get all active hash jobs"""
            try:
                result = await self.proxy.hash_coordinator.get_all_jobs()
                return result

            except Exception as e:
                self.logger.error(f"Failed to get hash jobs: {e}")
                return {"success": False, "jobs": [], "error": str(e)}

        @app.get("/api/hash/job-status")
        async def get_job_status(job_id: str):
            """Get status of specific hash job"""
            try:
                result = await self.proxy.hash_coordinator.get_job_status(job_id=job_id)
                return result

            except Exception as e:
                self.logger.error(f"Failed to get job status: {e}")
                return {"success": False, "error": str(e)}

        @app.get("/api/hash/workers-distribution")
        async def get_workers_distribution():
            """Get hash workers chunk distribution from gossip"""
            try:
                # Get network component to access gossip
                network = self.context.get_shared("network")
                if not network:
                    return {"success": False, "error": "Network not available", "workers": []}

                # Collect worker distribution data
                workers_distribution = []
                total_chunks = 0
                total_hashes = 0

                # Get all nodes from gossip
                node_registry = network.gossip.node_registry

                # Find all workers
                for node_id, node_info in node_registry.items():
                    if node_info.role == "worker":
                        # Get worker status from metadata
                        worker_status = node_info.metadata.get("hash_worker_status", {})

                        if worker_status:
                            current_job = worker_status.get("job_id")
                            current_chunk = worker_status.get("chunk_id")
                            status = worker_status.get("status", "idle")
                            progress = worker_status.get("progress", 0)
                            total_hashes_computed = worker_status.get("total_hashes", 0)
                            completed_chunks_count = worker_status.get("completed_chunks", 0)

                            worker_data = {
                                "worker_id": node_id,
                                "address": f"{node_info.address}:{node_info.port}",
                                "current_job": current_job,
                                "current_chunk": current_chunk,
                                "status": status,
                                "progress": progress,
                                "total_hashes": total_hashes_computed,
                                "completed_chunks": completed_chunks_count,
                                "chunks": []
                            }

                            # Collect chunks assigned to this worker
                            # Look through all job batches in coordinator metadata
                            coordinator_nodes = [n for n in node_registry.values() if n.role == "coordinator"]
                            if coordinator_nodes:
                                coordinator = coordinator_nodes[0]

                                # Find all hash_batches_* keys
                                for key, value in coordinator.metadata.items():
                                    if key.startswith("hash_batches_"):
                                        job_id = key.replace("hash_batches_", "")
                                        batches_data = value

                                        if isinstance(batches_data, dict) and "chunks" in batches_data:
                                            for chunk_id, chunk_data in batches_data["chunks"].items():
                                                if chunk_data.get("assigned_worker") == node_id:
                                                    chunk_info = {
                                                        "job_id": job_id,
                                                        "chunk_id": int(chunk_id),
                                                        "start_index": chunk_data.get("start_index", 0),
                                                        "end_index": chunk_data.get("end_index", 0),
                                                        "size": chunk_data.get("end_index", 0) - chunk_data.get("start_index", 0),
                                                        "status": chunk_data.get("status", "unknown"),
                                                        "progress": chunk_data.get("progress", 0)
                                                    }

                                                    worker_data["chunks"].append(chunk_info)
                                                    total_chunks += 1

                            total_hashes += total_hashes_computed
                            workers_distribution.append(worker_data)

                return {
                    "success": True,
                    "timestamp": datetime.now().isoformat(),
                    "workers": workers_distribution,
                    "summary": {
                        "total_workers": len(workers_distribution),
                        "total_chunks": total_chunks,
                        "total_hashes_computed": total_hashes,
                        "active_workers": len([w for w in workers_distribution if w["status"] in ["working", "solved"]])
                    }
                }

            except Exception as e:
                self.logger.error(f"Failed to get workers distribution: {e}", exc_info=True)
                return {"success": False, "error": str(e), "workers": []}

    async def cleanup(self):
        """Cleanup resources"""
        self.logger.info("Metrics Dashboard cleaning up...")

        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass

        if self.coordinator_metrics_task:
            self.coordinator_metrics_task.cancel()
            try:
                await self.coordinator_metrics_task
            except asyncio.CancelledError:
                pass

    async def _cleanup_loop(self):
        """Remove stale worker metrics periodically"""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                await self._remove_stale_workers()
                self.stats["last_cleanup"] = datetime.now().isoformat()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in cleanup loop: {e}")

    async def _coordinator_metrics_loop(self):
        """Collect coordinator metrics periodically"""
        while True:
            try:
                await asyncio.sleep(5)  # Collect every 5 seconds
                await self._collect_coordinator_metrics()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in coordinator metrics loop: {e}")

    async def _remove_stale_workers(self):
        """Remove workers that haven't reported in 10 minutes"""
        stale_threshold = datetime.now() - timedelta(minutes=10)
        stale_workers = []

        self.logger.debug(f"Cleanup check: {len(self.worker_last_seen)} workers tracked")

        for worker_id, last_seen in self.worker_last_seen.items():
            age_seconds = (datetime.now() - last_seen).total_seconds()
            if last_seen < stale_threshold:
                self.logger.warning(f"Worker '{worker_id}' is stale (last seen {age_seconds:.0f}s ago)")
                stale_workers.append(worker_id)
            else:
                self.logger.debug(f"Worker '{worker_id}' is active (last seen {age_seconds:.0f}s ago)")

        for worker_id in stale_workers:
            self.logger.warning(f"🗑️  Removing stale worker: {worker_id}")
            self.worker_metrics.pop(worker_id, None)
            self.worker_last_seen.pop(worker_id, None)
            self.metrics_history.pop(worker_id, None)

        # Update active workers count
        self.stats["active_workers"] = len(self.worker_metrics)

        if stale_workers:
            self.logger.info(f"Cleanup complete: removed {len(stale_workers)} stale workers. Active workers: {self.stats['active_workers']}")

    async def _collect_coordinator_metrics(self):
        """Collect current coordinator metrics"""
        if not self.proxy:
            return

        try:
            # Get system metrics
            metrics = await self.proxy.system.get_system_metrics()

            # Get ALL services (installed and running) from orchestrator
            services = {}
            try:
                orchestrator_info = await self.proxy.orchestrator.list_services()
                all_services = orchestrator_info.get("services", [])

                # Handle both list and dict formats (for backward compatibility)
                if isinstance(all_services, dict):
                    # Old format: {"service_name": {...}}
                    all_services = [{"name": k, **v} for k, v in all_services.items()]

                self.logger.debug(f"Orchestrator returned {len(all_services)} services")

                for service_info in all_services:
                    service_name = service_info.get("name")
                    if service_name:
                        services[service_name] = {
                            "status": "running" if service_info.get("running") else "stopped",
                            "installed": service_info.get("installed", False),
                            "description": service_info.get("description", "")
                        }
            except Exception as e:
                self.logger.warning(f"Could not get services from orchestrator: {e}")
                # Fallback to service_manager via context
                try:
                    if hasattr(self.context, 'get_shared'):
                        service_manager = self.context.get_shared('service_manager')
                        if service_manager and hasattr(service_manager, 'services'):
                            self.logger.info(f"Using fallback: service_manager has {len(service_manager.services)} services")
                            for service_name, service_instance in service_manager.services.items():
                                services[service_name] = {
                                    "status": service_instance.status.value if hasattr(service_instance.status, 'value') else str(service_instance.status),
                                    "uptime": getattr(service_instance, '_start_time', 0),
                                    "description": service_instance.info.description if hasattr(service_instance, 'info') else ""
                                }
                except Exception as fallback_error:
                    self.logger.error(f"Fallback also failed: {fallback_error}")

            timestamp = datetime.now()

            # Build cluster state
            cluster_state = {
                "total_workers": len(self.worker_metrics),
                "connected_workers": len([w for w in self.worker_metrics.values() if w.get("metrics")]),
                "total_nodes": len(self.worker_metrics) + 1,  # +1 for coordinator
                "nodes": ["coordinator"] + list(self.worker_metrics.keys())
            }

            self.coordinator_metrics = {
                "node_id": "coordinator",
                "timestamp": timestamp.isoformat(),
                "metrics": metrics,
                "services": services,
                "cluster_state": cluster_state
            }
            self.coordinator_last_update = timestamp

            # Add to history for coordinator (same as workers)
            history_entry = {
                "timestamp": timestamp.isoformat(),
                "cpu_percent": metrics.get("cpu_percent", 0),
                "memory_percent": metrics.get("memory", {}).get("percent", 0),
                "disk_percent": metrics.get("disk", {}).get("percent", 0)
            }

            self.metrics_history["coordinator"].append(history_entry)
            if len(self.metrics_history["coordinator"]) > 100:
                self.metrics_history["coordinator"].pop(0)

        except Exception as e:
            self.logger.error(f"Failed to collect coordinator metrics: {e}")

    # ============ API Methods ============

    @service_method(description="Report worker metrics to coordinator", public=True)
    async def report_metrics(
        self,
        worker_id: str,
        metrics: Dict[str, Any],
        services: Optional[Dict[str, Any]] = None,
        service_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Workers call this method to report their metrics

        Args:
            worker_id: Unique worker identifier
            metrics: System metrics (CPU, memory, etc)
            services: Service states and metrics
            service_data: Custom data from services (certificates, configs, etc.)
        """
        timestamp = datetime.now()

        # Store worker metrics
        self.worker_metrics[worker_id] = {
            "metrics": metrics,
            "services": services or {},
            "timestamp": timestamp.isoformat()
        }

        # Store service data separately
        if service_data:
            self.service_data[worker_id] = service_data
            self.logger.debug(f"Received service data from worker {worker_id}: {list(service_data.keys())}")

        # Update last seen
        self.worker_last_seen[worker_id] = timestamp

        # Add to history (keep last 100 points)
        history_entry = {
            "timestamp": timestamp.isoformat(),
            "cpu_percent": metrics.get("cpu_percent", 0),
            "memory_percent": metrics.get("memory", {}).get("percent", 0),
            "disk_percent": metrics.get("disk", {}).get("percent", 0)
        }

        self.metrics_history[worker_id].append(history_entry)
        if len(self.metrics_history[worker_id]) > 100:
            self.metrics_history[worker_id].pop(0)

        # Update stats
        self.stats["total_updates"] += 1
        self.stats["active_workers"] = len(self.worker_metrics)

        self.logger.info(f"✅ Metrics received from worker '{worker_id}' | Active workers: {len(self.worker_metrics)} | Total updates: {self.stats['total_updates']}")

        return {
            "status": "received",
            "worker_id": worker_id,
            "timestamp": timestamp.isoformat(),
            "next_report_interval": self._calculate_next_interval(worker_id)
        }

    def _calculate_next_interval(self, worker_id: str) -> int:
        """
        Calculate adaptive reporting interval (30-300 seconds)
        Based on metric changes and system load
        """
        # Default interval
        base_interval = 60

        # Get recent history
        history = self.metrics_history.get(worker_id, [])
        if len(history) < 2:
            return base_interval

        # Check recent changes
        recent = history[-10:]  # Last 10 points
        cpu_variance = self._calculate_variance([h["cpu_percent"] for h in recent])
        memory_variance = self._calculate_variance([h["memory_percent"] for h in recent])

        # If metrics are stable (low variance), increase interval
        # If metrics are changing (high variance), decrease interval
        if cpu_variance < 5 and memory_variance < 5:
            # Stable metrics - report less frequently
            interval = min(300, base_interval * 2)
        elif cpu_variance > 20 or memory_variance > 20:
            # Volatile metrics - report more frequently
            interval = max(10, base_interval // 2)
        else:
            interval = base_interval

        return interval

    def _calculate_variance(self, values: List[float]) -> float:
        """Calculate variance of a list of values"""
        if not values:
            return 0

        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return variance ** 0.5  # Return standard deviation

    @service_method(description="Get all cluster metrics", public=True)
    async def get_cluster_metrics(self) -> Dict[str, Any]:
        """Get metrics for coordinator and all workers"""

        # Refresh coordinator metrics
        await self._collect_coordinator_metrics()

        # Calculate uptime
        uptime_seconds = 0
        if self.stats["uptime_start"]:
            start_time = datetime.fromisoformat(self.stats["uptime_start"])
            uptime_seconds = (datetime.now() - start_time).total_seconds()

        # Count services across all nodes
        total_services = 0
        active_services = 0

        # Count coordinator services
        if self.coordinator_metrics and "services" in self.coordinator_metrics:
            services = self.coordinator_metrics.get("services", {})
            total_services += len(services)
            active_services += sum(1 for s in services.values()
                                  if isinstance(s, dict) and s.get("status") == "running")

        # Apply gossip fallback for worker services BEFORE counting
        # This ensures we use fresher data from gossip when metrics_reporter hasn't sent services yet
        if hasattr(self, 'context') and self.context:
            try:
                network = self.context.get_shared('network')
                if network and hasattr(network, 'gossip'):
                    gossip = network.gossip
                    if hasattr(gossip, 'node_registry'):
                        for node_id, node_info in gossip.node_registry.items():
                            # Skip coordinator
                            if node_info.role == 'coordinator':
                                continue

                            # Check if we have this worker in worker_metrics
                            if node_id in self.worker_metrics:
                                current_services = self.worker_metrics[node_id].get("services", {})
                                gossip_services = node_info.services if hasattr(node_info, 'services') else {}

                                # Use gossip if current services are empty or gossip has more
                                if not current_services and gossip_services:
                                    self.logger.info(f"Using gossip services for {node_id}: {len(gossip_services)} services")
                                    self.worker_metrics[node_id]["services"] = gossip_services
                                elif len(gossip_services) > len(current_services):
                                    self.logger.info(f"Updating {node_id} from gossip: {len(gossip_services)} vs {len(current_services)} services")
                                    self.worker_metrics[node_id]["services"] = gossip_services
            except Exception as e:
                self.logger.error(f"Error applying gossip fallback: {e}")

        # Count worker services (AFTER gossip fallback)
        for worker_data in self.worker_metrics.values():
            services = worker_data.get("services", {})
            total_services += len(services)
            active_services += sum(1 for s in services.values()
                                  if isinstance(s, dict) and s.get("status") == "running")

        return {
            "timestamp": datetime.now().isoformat(),
            "coordinator": self.coordinator_metrics,
            "workers": self.worker_metrics,
            "stats": {
                **self.stats,
                "active_workers": len(self.worker_metrics),
                "uptime_seconds": uptime_seconds,
                "total_services": total_services,
                "active_services": active_services
            }
        }

    @service_method(description="Get metrics history for a specific node", public=True)
    async def get_metrics_history(
        self,
        node_id: str,
        limit: int = 50
    ) -> Dict[str, Any]:
        """Get historical metrics for a node"""

        history = self.metrics_history.get(node_id, [])

        return {
            "node_id": node_id,
            "history": history[-limit:],
            "count": len(history)
        }

    @service_method(description="Get dashboard statistics", public=True)
    async def get_dashboard_stats(self) -> Dict[str, Any]:
        """Get overall dashboard statistics"""

        # Calculate uptime
        uptime_seconds = 0
        if self.stats["uptime_start"]:
            start_time = datetime.fromisoformat(self.stats["uptime_start"])
            uptime_seconds = (datetime.now() - start_time).total_seconds()

        # Count services across all nodes
        total_services = 0
        active_services = 0

        for worker_data in self.worker_metrics.values():
            services = worker_data.get("services", {})
            total_services += len(services)
            active_services += sum(1 for s in services.values()
                                  if s.get("status") == "running")

        return {
            "uptime_seconds": uptime_seconds,
            "active_workers": len(self.worker_metrics),
            "total_updates": self.stats["total_updates"],
            "total_services": total_services,
            "active_services": active_services,
            "last_cleanup": self.stats["last_cleanup"]
        }

    @service_method(description="Control service on a worker", public=True)
    async def control_service(
        self,
        worker_id: str,
        service_name: str,
        action: str
    ) -> Dict[str, Any]:
        """
        Control a service on a specific worker using orchestrator

        This method calls the orchestrator service on the target worker
        via P2P RPC to control the service.

        Args:
            worker_id: Worker identifier
            service_name: Name of the service
            action: Action to perform (start, stop, restart)
        """
        # Validate action
        if action not in ["start", "stop", "restart"]:
            return {
                "success": False,
                "error": f"Invalid action: {action}"
            }

        if not self.proxy:
            return {
                "success": False,
                "error": "Proxy not available"
            }

        try:
            self.logger.info(f"Sending {action} command for service {service_name} on {worker_id}")

            # Determine if this is a local (coordinator) or remote (worker) call
            is_local_coordinator = worker_id.lower() == "coordinator"

            if is_local_coordinator:
                # Call local orchestrator directly (we are on coordinator)
                orchestrator_proxy = self.proxy.orchestrator
                self.logger.debug(f"Using local orchestrator for {worker_id}")
            else:
                # Call remote orchestrator via P2P RPC
                orchestrator_proxy = self.proxy.orchestrator.__getattr__(worker_id)
                self.logger.debug(f"Using remote orchestrator for {worker_id}")

            # Call the appropriate orchestrator method based on action
            if action == "start":
                result = await orchestrator_proxy.start_service(service_name)
            elif action == "stop":
                result = await orchestrator_proxy.stop_service(service_name)
            elif action == "restart":
                result = await orchestrator_proxy.restart_service(service_name)
            else:
                return {
                    "success": False,
                    "error": f"Unknown action: {action}"
                }

            self.logger.info(f"Service control result for {service_name} on {worker_id}: {result}")
            return result

        except Exception as e:
            self.logger.error(f"Failed to control service {service_name} on {worker_id}: {e}")
            return {
                "success": False,
                "error": f"Failed to {action} service: {str(e)}",
            "worker_id": worker_id,
            "service_name": service_name,
            "action": action,
            "note": "You can stop/start services by connecting to the worker directly"
        }

    @service_method(description="Get list of all services across cluster", public=True)
    async def get_cluster_services(self) -> Dict[str, Any]:
        """Get all services running across the cluster"""

        services_by_node = {}

        # Coordinator services
        if self.coordinator_metrics:
            services_by_node["coordinator"] = self.coordinator_metrics.get("services", {})

        # Worker services (from metrics reports)
        for worker_id, worker_data in self.worker_metrics.items():
            services_by_node[worker_id] = worker_data.get("services", {})

        # Fallback: Query gossip protocol for nodes that haven't reported OR have empty services
        if hasattr(self, 'context') and self.context:
            try:
                network = self.context.get_shared('network')
                if network and hasattr(network, 'gossip'):
                    gossip = network.gossip
                    if hasattr(gossip, 'node_registry'):
                        for node_id, node_info in gossip.node_registry.items():
                            # Use gossip if:
                            # 1. Node not in services_by_node yet
                            # 2. OR node has empty/no services in services_by_node but has services in gossip
                            current_services = services_by_node.get(node_id, {})
                            gossip_services = node_info.services if hasattr(node_info, 'services') else {}

                            if not current_services and gossip_services:
                                self.logger.info(f"Using gossip for node {node_id}: {len(gossip_services)} services from gossip")
                                services_by_node[node_id] = gossip_services
                            elif len(gossip_services) > len(current_services):
                                self.logger.info(f"Updating node {node_id}: gossip has more services ({len(gossip_services)} vs {len(current_services)})")
                                services_by_node[node_id] = gossip_services
            except Exception as e:
                self.logger.error(f"Could not query gossip for services: {e}")

        return {
            "timestamp": datetime.now().isoformat(),
            "services": services_by_node
        }

    @service_method(description="Clear metrics history", public=True)
    async def clear_metrics_history(self, node_id: Optional[str] = None) -> Dict[str, Any]:
        """Clear metrics history for a specific node or all nodes"""

        if node_id:
            if node_id in self.metrics_history:
                count = len(self.metrics_history[node_id])
                self.metrics_history[node_id].clear()
                return {
                    "success": True,
                    "node_id": node_id,
                    "cleared_records": count
                }
            else:
                return {
                    "success": False,
                    "error": f"Node {node_id} not found"
                }
        else:
            # Clear all
            total_cleared = sum(len(h) for h in self.metrics_history.values())
            self.metrics_history.clear()
            return {
                "success": True,
                "cleared_records": total_cleared,
                "message": "All metrics history cleared"
            }

    @service_method(description="Get detailed metrics for a service", public=True)
    async def get_service_metrics(self, node_id: str, service_name: str) -> Dict[str, Any]:
        """Get detailed metrics and information for a specific service on a node"""

        if not self.proxy:
            return {
                "success": False,
                "error": "Proxy not available"
            }

        try:
            # Get service proxy
            service_proxy = getattr(self.proxy, service_name)

            # Determine if this is coordinator or worker
            if node_id == "coordinator":
                # Local call to get service info (includes metrics)
                service_info = await service_proxy.get_service_info()
            else:
                # Remote call to worker - get target node proxy
                remote_service = service_proxy.__getattr__(node_id)
                service_info = await remote_service.get_service_info()

            # Ensure service_info is a dict (convert if needed)
            if not isinstance(service_info, dict):
                # Convert to dict if it's an object with __dict__
                if hasattr(service_info, '__dict__'):
                    service_info = service_info.__dict__
                elif hasattr(service_info, 'to_dict'):
                    service_info = service_info.to_dict()
                else:
                    # Try to convert using vars()
                    try:
                        service_info = vars(service_info)
                    except TypeError:
                        # Last resort: convert to string representation
                        self.logger.warning(f"Could not convert service_info to dict for {service_name} on {node_id}, type: {type(service_info)}")
                        service_info = {"raw_data": str(service_info)}

            return {
                "success": True,
                "node_id": node_id,
                "service_name": service_name,
                "metrics": service_info
            }

        except Exception as e:
            self.logger.error(f"Failed to get metrics for {service_name} on {node_id}: {e}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return {
                "success": False,
                "error": str(e),
                "node_id": node_id,
                "service_name": service_name
            }

    async def _collect_local_service_data(self) -> Dict[str, Any]:
        """
        Collect custom data from local services (coordinator's services)
        Similar to metrics_reporter._collect_service_data()
        """
        service_data = {}

        try:
            if not self.proxy:
                self.logger.warning("Proxy not available for collecting local service data")
                return service_data

            # List of services that may provide dashboard data
            services_to_check = ['legacy_certs', 'certs_tool']
            self.logger.debug(f"Checking local services: {services_to_check}")

            for service_name in services_to_check:
                try:
                    # Direct method check in registry instead of hasattr
                    # This is more reliable in PyInstaller builds
                    method_path = f"{service_name}/get_dashboard_data"

                    if self.context and hasattr(self.context, '_method_registry'):
                        registry = self.context._method_registry
                        self.logger.debug(f"Checking registry for {method_path}, registry has {len(registry)} methods")

                        if method_path in registry:
                            self.logger.info(f"Found {method_path} in registry, collecting data...")
                            # Method exists in registry, call it directly
                            service_proxy = getattr(self.proxy, service_name)
                            data = await service_proxy.get_dashboard_data()
                            if data:
                                service_data[service_name] = data
                                self.logger.info(f"✓ Collected dashboard data from local {service_name}")
                            else:
                                self.logger.debug(f"No data returned from {service_name}")
                        else:
                            self.logger.debug(f"Method {method_path} not found in registry")
                    else:
                        # Fallback to hasattr check (for compatibility)
                        self.logger.debug(f"Using fallback hasattr check for {service_name}")
                        if hasattr(self.proxy, service_name):
                            service_proxy = getattr(self.proxy, service_name)
                            if hasattr(service_proxy, 'get_dashboard_data'):
                                data = await service_proxy.get_dashboard_data()
                                if data:
                                    service_data[service_name] = data
                                    self.logger.info(f"✓ Collected dashboard data from local {service_name} (via fallback)")
                except AttributeError as e:
                    # Service doesn't have get_dashboard_data method - skip
                    self.logger.debug(f"AttributeError for {service_name}: {e}")
                except Exception as e:
                    self.logger.warning(f"Could not get dashboard data from local {service_name}: {e}")

            self.logger.debug(f"Collected data from {len(service_data)} local services")

        except Exception as e:
            self.logger.error(f"Failed to collect local service data: {e}", exc_info=True)

        return service_data

    @service_method(description="Get service data from all workers", public=True)
    async def get_service_data(self, service_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Get custom service data from all workers and coordinator

        Args:
            service_type: Optional filter by service type (e.g., 'certificates')

        Returns:
            Dict with service data from all workers and coordinator
        """
        result = {}

        # Collect service data from all workers
        for worker_id, worker_service_data in self.service_data.items():
            result[worker_id] = {}

            for service_name, data in worker_service_data.items():
                # Filter by service type if specified
                if service_type:
                    if data.get("service_type") == service_type:
                        result[worker_id][service_name] = data
                else:
                    result[worker_id][service_name] = data

        # Collect service data from coordinator's local services
        coordinator_data = await self._collect_local_service_data()
        if coordinator_data:
            result["coordinator"] = {}
            for service_name, data in coordinator_data.items():
                # Filter by service type if specified
                if service_type:
                    if data.get("service_type") == service_type:
                        result["coordinator"][service_name] = data
                else:
                    result["coordinator"][service_name] = data

        return {
            "timestamp": datetime.now().isoformat(),
            "service_data": result
        }
