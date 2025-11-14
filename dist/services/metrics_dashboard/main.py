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
                if worker == "coordinator":
                    # Call local certs_tool
                    if hasattr(self.proxy, 'certs_tool'):
                        result = await self.proxy.certs_tool.install_pfx_from_base64(
                            pfx_base64=pfx_data,
                            password=password,
                            filename=filename
                        )
                        return JSONResponse(content=result)
                    else:
                        return JSONResponse(
                            status_code=500,
                            content={"success": False, "error": "certs_tool service not available"}
                        )
                else:
                    # Call worker's certs_tool via proxy
                    if hasattr(self.proxy, 'certs_tool'):
                        result = await self.proxy.certs_tool[worker].install_pfx_from_base64(
                            pfx_base64=pfx_data,
                            password=password,
                            filename=filename
                        )
                        return JSONResponse(content=result)
                    else:
                        return JSONResponse(
                            status_code=500,
                            content={"success": False, "error": "certs_tool service not available on worker"}
                        )

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
                        await self.proxy.certs_tool[worker].export_certificate_cer(
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
                        await self.proxy.certs_tool[worker].export_certificate_pfx(
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
                if worker == "coordinator":
                    if hasattr(self.proxy, 'certs_tool'):
                        result = await self.proxy.certs_tool.delete_certificate(
                            thumbprint=thumbprint
                        )
                        return JSONResponse(content=result)
                else:
                    if hasattr(self.proxy, 'certs_tool'):
                        result = await self.proxy.certs_tool[worker].delete_certificate(
                            thumbprint=thumbprint
                        )
                        return JSONResponse(content=result)

                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": "certs_tool service not available"}
                )

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
                            install_result = await self.proxy.certs_tool[target_worker].install_pfx_from_base64(
                                pfx_base64=pfx_base64,
                                password=password,
                                filename=f"{cert_info.get('subject_cn', 'cert')}.pfx"
                            )

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

        # Count worker services
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
                orchestrator_proxy = getattr(self.proxy.orchestrator, worker_id)
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

        # Fallback: Query gossip protocol for nodes that haven't reported
        if hasattr(self, 'context') and self.context:
            try:
                network = self.context.get_shared('network')
                if network and hasattr(network, 'gossip'):
                    gossip = network.gossip
                    if hasattr(gossip, 'node_registry'):
                        for node_id, node_info in gossip.node_registry.items():
                            # If node not in services_by_node and has services in gossip
                            if node_id not in services_by_node and hasattr(node_info, 'services'):
                                if node_info.services:
                                    self.logger.debug(f"Using gossip fallback for node {node_id} services")
                                    services_by_node[node_id] = node_info.services
            except Exception as e:
                self.logger.debug(f"Could not query gossip for services: {e}")

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
                remote_service = getattr(service_proxy, node_id)
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
            if self.proxy:
                # List of services that may provide dashboard data
                services_to_check = ['legacy_certs', 'certs_tool']

                for service_name in services_to_check:
                    try:
                        # Check if service exists and has get_dashboard_data method
                        if hasattr(self.proxy, service_name):
                            service_proxy = getattr(self.proxy, service_name)
                            if hasattr(service_proxy, 'get_dashboard_data'):
                                data = await service_proxy.get_dashboard_data()
                                if data:
                                    service_data[service_name] = data
                                    self.logger.debug(f"Collected dashboard data from local {service_name}")
                    except AttributeError:
                        # Service doesn't have get_dashboard_data method - skip
                        pass
                    except Exception as e:
                        self.logger.debug(f"Could not get dashboard data from local {service_name}: {e}")

        except Exception as e:
            self.logger.error(f"Failed to collect local service data: {e}")

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
