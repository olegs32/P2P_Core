"""
Metrics Reporter Service for Workers
Reports system metrics and service states to coordinator periodically
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime

from layers.service import BaseService, service_method


class Run(BaseService):
    """Worker service for reporting metrics to coordinator"""

    SERVICE_NAME = "metrics_reporter"

    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.info.description = "Reports worker metrics to coordinator dashboard"
        self.info.dependencies = ["system"]
        self.info.domain = "monitoring"

        # Reporter state
        self.reporter_active = False
        self.reporter_task = None

        # Current interval (adaptive 30-300 seconds)
        self.current_interval = 60  # Start with 60 seconds
        self.min_interval = 30
        self.max_interval = 300

        # Statistics
        self.stats = {
            "total_reports": 0,
            "successful_reports": 0,
            "failed_reports": 0,
            "last_report_time": None,
            "last_report_status": None,
            "next_report_interval": self.current_interval,
            "uptime_start": datetime.now().isoformat()
        }

        # Store last metrics for change detection
        self.last_metrics = None

        # Worker ID (will be set from config)
        self.worker_id = None

    async def initialize(self):
        """Initialize metrics reporter"""
        self.logger.info("Metrics Reporter service initializing...")

        # Wait for proxy initialization
        await self._wait_for_proxy()

        # Get worker ID from context
        if hasattr(self, 'context') and self.context:
            if hasattr(self.context.config, 'node_id'):
                self.worker_id = self.context.config.node_id
                self.logger.info(f"Worker ID set to: {self.worker_id}")

            # Only start reporter if this is NOT a coordinator
            if hasattr(self.context.config, 'coordinator_mode'):
                is_coordinator = self.context.config.coordinator_mode
                if not is_coordinator:
                    # Start reporter automatically for workers
                    await self.start_reporter()
                    self.logger.info("Metrics reporter started automatically (worker mode)")
                else:
                    self.logger.info("Coordinator mode detected - reporter will not auto-start")
            else:
                # If coordinator_mode not set, assume worker and start
                await self.start_reporter()
                self.logger.info("Metrics reporter started (coordinator_mode not set)")
        else:
            self.logger.warning("Context not available, cannot determine worker ID")

        self.logger.info("Metrics Reporter initialized successfully")

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
            self.logger.warning("Proxy not available, reporter will not function")

    async def cleanup(self):
        """Cleanup resources"""
        self.logger.info("Metrics Reporter cleaning up...")
        await self.stop_reporter()

    async def start_reporter(self):
        """Start the metrics reporting loop"""
        if self.reporter_active:
            self.logger.warning("Reporter already active")
            return {
                "success": False,
                "message": "Reporter already active"
            }

        if not self.proxy:
            self.logger.error("Cannot start reporter: proxy not available")
            return {
                "success": False,
                "message": "Proxy not available"
            }

        if not self.worker_id:
            self.logger.error("Cannot start reporter: worker_id not set")
            return {
                "success": False,
                "message": "Worker ID not set"
            }

        self.reporter_active = True
        self.reporter_task = asyncio.create_task(self._reporter_loop())
        self.logger.info(f"Metrics reporter started (interval: {self.current_interval}s)")

        return {
            "success": True,
            "message": "Reporter started",
            "interval": self.current_interval
        }

    async def stop_reporter(self):
        """Stop the metrics reporting loop"""
        self.reporter_active = False

        if self.reporter_task:
            self.reporter_task.cancel()
            try:
                await self.reporter_task
            except asyncio.CancelledError:
                pass
            self.reporter_task = None

        self.logger.info("Metrics reporter stopped")

        return {
            "success": True,
            "message": "Reporter stopped"
        }

    async def _reporter_loop(self):
        """Main reporting loop"""
        self.logger.info("Starting metrics reporter loop")

        while self.reporter_active:
            try:
                # Collect and send metrics
                await self._collect_and_send_metrics()

                # Wait for next interval
                self.logger.debug(f"Next report in {self.current_interval} seconds")
                await asyncio.sleep(self.current_interval)

            except asyncio.CancelledError:
                self.logger.info("Reporter loop cancelled")
                break
            except Exception as e:
                self.logger.error(f"Error in reporter loop: {e}")
                # Wait a bit before retrying on error
                await asyncio.sleep(30)

    async def _collect_and_send_metrics(self):
        """Collect local metrics and send to coordinator"""
        report_time = datetime.now()
        self.stats["total_reports"] += 1

        try:
            # Collect system metrics
            metrics = await self._collect_system_metrics()

            # Collect service states
            services = await self._collect_service_states()

            # Send to coordinator
            result = await self._send_to_coordinator(metrics, services)

            # Update stats
            self.stats["successful_reports"] += 1
            self.stats["last_report_time"] = report_time.isoformat()
            self.stats["last_report_status"] = "success"

            # Update interval based on coordinator response
            if result and "next_report_interval" in result:
                self.current_interval = result["next_report_interval"]
                self.stats["next_report_interval"] = self.current_interval

            # Store metrics for next comparison
            self.last_metrics = metrics

            self.logger.info(
                f"Metrics reported successfully (report #{self.stats['total_reports']}, "
                f"next interval: {self.current_interval}s)"
            )

        except Exception as e:
            self.stats["failed_reports"] += 1
            self.stats["last_report_time"] = report_time.isoformat()
            self.stats["last_report_status"] = f"error: {str(e)}"
            self.logger.error(f"Failed to report metrics: {e}")

            # On error, increase interval slightly (but not beyond max)
            self.current_interval = min(self.current_interval * 1.5, self.max_interval)

    async def _collect_system_metrics(self) -> Dict[str, Any]:
        """Collect system metrics using system service"""
        if not self.proxy:
            return {}

        try:
            # Use local system service to get metrics
            metrics = await self.proxy.system.get_system_metrics()
            return metrics
        except Exception as e:
            self.logger.error(f"Failed to collect system metrics: {e}")
            return {}

    async def _collect_service_states(self) -> Dict[str, Any]:
        """Collect states of all running services"""
        services = {}

        try:
            # Get service manager from context or other means
            if hasattr(self, '_service_manager') and self._service_manager:
                service_manager = self._service_manager

                for service_name, service_instance in service_manager.services.items():
                    services[service_name] = {
                        "status": service_instance.status.value if hasattr(service_instance.status, 'value') else str(service_instance.status),
                        "uptime": getattr(service_instance, '_start_time', 0),
                        "description": service_instance.info.description if hasattr(service_instance, 'info') else ""
                    }

        except Exception as e:
            self.logger.error(f"Failed to collect service states: {e}")

        return services

    async def _send_to_coordinator(self, metrics: Dict[str, Any], services: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Send metrics to coordinator dashboard service"""
        if not self.proxy:
            raise Exception("Proxy not available")

        if not self.worker_id:
            raise Exception("Worker ID not set")

        try:
            # Call coordinator's metrics_dashboard.report_metrics method
            result = await self.proxy.metrics_dashboard.coordinator.report_metrics(
                worker_id=self.worker_id,
                metrics=metrics,
                services=services
            )

            return result

        except Exception as e:
            self.logger.error(f"Failed to send metrics to coordinator: {e}")
            raise

    # ============ API Methods ============

    @service_method(description="Get reporter statistics", public=True)
    async def get_stats(self) -> Dict[str, Any]:
        """Get reporter statistics"""

        # Calculate uptime
        uptime_seconds = 0
        if self.stats["uptime_start"]:
            start_time = datetime.fromisoformat(self.stats["uptime_start"])
            uptime_seconds = (datetime.now() - start_time).total_seconds()

        # Calculate success rate
        success_rate = 0
        if self.stats["total_reports"] > 0:
            success_rate = (self.stats["successful_reports"] / self.stats["total_reports"]) * 100

        return {
            "service": self.service_name,
            "worker_id": self.worker_id,
            "reporter_active": self.reporter_active,
            "current_interval": self.current_interval,
            "uptime_seconds": uptime_seconds,
            "statistics": {
                "total_reports": self.stats["total_reports"],
                "successful_reports": self.stats["successful_reports"],
                "failed_reports": self.stats["failed_reports"],
                "success_rate_percent": round(success_rate, 2)
            },
            "last_report_time": self.stats["last_report_time"],
            "last_report_status": self.stats["last_report_status"],
            "next_report_interval": self.stats["next_report_interval"]
        }

    @service_method(description="Control reporter state", public=True)
    async def control_reporter(self, action: str) -> Dict[str, Any]:
        """Control reporter state (start, stop, status)"""

        if action == "start":
            return await self.start_reporter()
        elif action == "stop":
            return await self.stop_reporter()
        elif action == "status":
            return {
                "action": "status",
                "reporter_active": self.reporter_active,
                "current_interval": self.current_interval,
                "worker_id": self.worker_id
            }
        elif action == "report_now":
            # Trigger immediate report
            if self.reporter_active:
                try:
                    await self._collect_and_send_metrics()
                    return {
                        "action": "report_now",
                        "success": True,
                        "message": "Report sent immediately"
                    }
                except Exception as e:
                    return {
                        "action": "report_now",
                        "success": False,
                        "error": str(e)
                    }
            else:
                return {
                    "action": "report_now",
                    "success": False,
                    "message": "Reporter not active"
                }
        else:
            return {
                "action": action,
                "success": False,
                "message": "Unknown action. Use: start, stop, status, report_now"
            }

    @service_method(description="Set reporting interval", public=True)
    async def set_interval(self, interval: int) -> Dict[str, Any]:
        """Set reporting interval (30-300 seconds)"""

        if not (self.min_interval <= interval <= self.max_interval):
            return {
                "success": False,
                "message": f"Interval must be between {self.min_interval} and {self.max_interval} seconds"
            }

        self.current_interval = interval
        self.stats["next_report_interval"] = interval

        return {
            "success": True,
            "message": f"Interval set to {interval} seconds",
            "interval": interval
        }
