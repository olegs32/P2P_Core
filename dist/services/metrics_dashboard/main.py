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

        for worker_id, last_seen in self.worker_last_seen.items():
            if last_seen < stale_threshold:
                stale_workers.append(worker_id)

        for worker_id in stale_workers:
            self.logger.info(f"Removing stale worker: {worker_id}")
            self.worker_metrics.pop(worker_id, None)
            self.worker_last_seen.pop(worker_id, None)
            self.metrics_history.pop(worker_id, None)

        # Update active workers count
        self.stats["active_workers"] = len(self.worker_metrics)

    async def _collect_coordinator_metrics(self):
        """Collect current coordinator metrics"""
        if not self.proxy:
            return

        try:
            # Get system metrics
            metrics = await self.proxy.system.get_system_metrics()

            # Get service states from service manager
            services = {}
            if hasattr(self, '_service_manager') and self._service_manager:
                for service_name, service_instance in self._service_manager.services.items():
                    services[service_name] = {
                        "status": service_instance.status.value if hasattr(service_instance.status, 'value') else str(service_instance.status),
                        "uptime": getattr(service_instance, '_start_time', 0),
                        "description": service_instance.info.description if hasattr(service_instance, 'info') else ""
                    }

            timestamp = datetime.now()

            self.coordinator_metrics = {
                "node_id": "coordinator",
                "timestamp": timestamp.isoformat(),
                "metrics": metrics,
                "services": services
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
        services: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Workers call this method to report their metrics

        Args:
            worker_id: Unique worker identifier
            metrics: System metrics (CPU, memory, etc)
            services: Service states and metrics
        """
        timestamp = datetime.now()

        # Store worker metrics
        self.worker_metrics[worker_id] = {
            "metrics": metrics,
            "services": services or {},
            "timestamp": timestamp.isoformat()
        }

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

        self.logger.debug(f"Received metrics from worker {worker_id}")

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
        Control a service on a specific worker

        Args:
            worker_id: Worker identifier
            service_name: Name of the service
            action: Action to perform (start, stop, restart)
        """
        if not self.proxy:
            return {
                "success": False,
                "error": "Proxy not available"
            }

        # Validate action
        if action not in ["start", "stop", "restart"]:
            return {
                "success": False,
                "error": f"Invalid action: {action}"
            }

        try:
            # Call the orchestrator service on the specific worker
            # This assumes orchestrator service exists on workers
            result = await getattr(
                self.proxy.orchestrator,
                worker_id
            ).control_service(service_name, action)

            return {
                "success": True,
                "worker_id": worker_id,
                "service_name": service_name,
                "action": action,
                "result": result
            }

        except Exception as e:
            self.logger.error(f"Failed to control service {service_name} on {worker_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    @service_method(description="Get list of all services across cluster", public=True)
    async def get_cluster_services(self) -> Dict[str, Any]:
        """Get all services running across the cluster"""

        services_by_node = {}

        # Coordinator services
        if self.coordinator_metrics:
            services_by_node["coordinator"] = self.coordinator_metrics.get("services", {})

        # Worker services
        for worker_id, worker_data in self.worker_metrics.items():
            services_by_node[worker_id] = worker_data.get("services", {})

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
            # Determine if this is coordinator or worker
            if node_id == "coordinator":
                # Local call to get service metrics
                metrics = await self.proxy[f"{service_name}"].get_metrics()
            else:
                # Remote call to worker
                metrics = await self.proxy[f"{service_name}"][node_id].get_metrics()

            return {
                "success": True,
                "node_id": node_id,
                "service_name": service_name,
                "metrics": metrics
            }

        except Exception as e:
            self.logger.error(f"Failed to get metrics for {service_name} on {node_id}: {e}")
            return {
                "success": False,
                "error": str(e),
                "node_id": node_id,
                "service_name": service_name
            }
