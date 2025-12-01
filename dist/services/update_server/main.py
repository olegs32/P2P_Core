"""
Update Server Service - Manages cluster updates

Coordinates updates across the cluster:
- Rolling updates
- Canary deployments
- Health checks and rollback
- Update history and auditing
"""
import asyncio
import logging
import json
import time
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path

from layers.service import BaseService, service_method

# Import models using importlib for dynamic loading in ServiceLoader context
import sys
import os
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

# Load models/update_task.py
_update_task_module = _load_local_module(
    'update_server_update_task',
    os.path.join(_service_dir, 'models', 'update_task.py')
)
UpdateTask = _update_task_module.UpdateTask
UpdateStrategy = _update_task_module.UpdateStrategy
UpdateStatus = _update_task_module.UpdateStatus
NodeUpdate = _update_task_module.NodeUpdate
NodeUpdateStatus = _update_task_module.NodeUpdateStatus


class Run(BaseService):
    """Update Server service"""

    SERVICE_NAME = "update_server"

    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.info.version = "1.0.0"
        self.info.description = "Cluster update orchestration"

        # Active updates
        self.active_updates: Dict[int, UpdateTask] = {}
        self.update_id_counter = 0
        self.update_history: List[UpdateTask] = []

        # Persistence
        self.history_file = Path("data/update_server/update_history.json")
        self.history_file.parent.mkdir(parents=True, exist_ok=True)

        # Gossip integration
        self.gossip_publish_task = None
        self.gossip_publish_interval = 45  # seconds

        # Metrics tracking
        self.total_updates_started = 0
        self.total_updates_completed = 0
        self.total_updates_failed = 0
        self.total_updates_rolled_back = 0
        self.strategy_usage = {
            "rolling": 0,
            "canary": 0,
            "blue_green": 0,
            "all_at_once": 0
        }

    async def initialize(self):
        """Initialize update server"""
        self.logger.info("Initializing Update Server service")

        # Load update history from file
        await self._load_history()

        # Start gossip publishing loop
        if hasattr(self, 'context'):
            self.gossip_publish_task = asyncio.create_task(self._gossip_publish_loop())
            self.logger.info("Started gossip publishing task")

    async def cleanup(self):
        """Cleanup update server"""
        # Save history before shutdown
        await self._save_history()

        # Cancel gossip task
        if self.gossip_publish_task:
            self.gossip_publish_task.cancel()
            try:
                await self.gossip_publish_task
            except asyncio.CancelledError:
                pass
            self.logger.info("Stopped gossip publishing task")

    @service_method(description="Start cluster update", public=True)
    async def start_update(
        self,
        artifact_id: int,
        artifact_name: str,
        target_version: str,
        target_nodes: List[str],
        strategy: str = "rolling"
    ) -> Dict[str, Any]:
        """Start a cluster update"""
        try:
            self.update_id_counter += 1
            task = UpdateTask(
                id=self.update_id_counter,
                artifact_id=artifact_id,
                artifact_name=artifact_name,
                target_version=target_version,
                strategy=UpdateStrategy(strategy),
                target_nodes=target_nodes,
                created_at=datetime.now()
            )

            self.active_updates[task.id] = task

            # Track metrics
            self.total_updates_started += 1
            self.strategy_usage[strategy] += 1
            self.metrics.increment(f"updates_started_{strategy}")
            self.metrics.gauge("active_updates", len(self.active_updates))

            return {"success": True, "update_id": task.id}

        except Exception as e:
            self.metrics.increment("update_start_errors")
            return {"success": False, "error": str(e)}

    @service_method(description="Get update status", public=True)
    async def get_update_status(self, update_id: int) -> Dict[str, Any]:
        """Get status of update task"""
        task = self.active_updates.get(update_id)
        if not task:
            return {"success": False, "error": "Update not found"}

        return {"success": True, "update": task.to_dict()}

    @service_method(description="Execute update task", public=True)
    async def execute_update_task(self, update_id: int) -> Dict[str, Any]:
        """
        Execute an update task (orchestrate across nodes)

        Args:
            update_id: Update task ID

        Returns:
            Result dictionary
        """
        task = self.active_updates.get(update_id)
        if not task:
            return {"success": False, "error": "Update not found"}

        if task.status != UpdateStatus.PENDING:
            return {
                "success": False,
                "error": f"Update already in state: {task.status.value}"
            }

        try:
            task.status = UpdateStatus.IN_PROGRESS
            task.started_at = datetime.now()

            # Execute based on strategy
            if task.strategy == UpdateStrategy.ROLLING:
                result = await self._execute_rolling_update(task)
            elif task.strategy == UpdateStrategy.CANARY:
                result = await self._execute_canary_update(task)
            elif task.strategy == UpdateStrategy.BLUE_GREEN:
                result = await self._execute_blue_green_update(task)
            elif task.strategy == UpdateStrategy.ALL_AT_ONCE:
                result = await self._execute_all_at_once_update(task)
            else:
                return {
                    "success": False,
                    "error": f"Unknown strategy: {task.strategy.value}"
                }

            # Update final status
            if result["success"]:
                task.status = UpdateStatus.COMPLETED
                self.total_updates_completed += 1
                self.metrics.increment("updates_completed")
                self.metrics.increment(f"updates_completed_{task.strategy.value}")
            else:
                task.status = UpdateStatus.FAILED
                self.total_updates_failed += 1
                self.metrics.increment("updates_failed")
                self.metrics.increment(f"updates_failed_{task.strategy.value}")

            task.completed_at = datetime.now()

            # Track duration
            if task.started_at and task.completed_at:
                duration_ms = (task.completed_at - task.started_at).total_seconds() * 1000
                self.metrics.timer(f"update_duration_{task.strategy.value}", duration_ms)

            # Move to history
            self.update_history.append(task)

            # Update active count
            self.metrics.gauge("active_updates", len(self.active_updates))

            return result

        except Exception as e:
            self.logger.error(f"Update execution failed: {e}", exc_info=True)
            task.status = UpdateStatus.FAILED
            task.completed_at = datetime.now()
            self.total_updates_failed += 1
            self.metrics.increment("updates_failed")
            self.metrics.increment("update_execution_errors")
            return {"success": False, "error": str(e)}

    async def _wait_for_node_version(self, node_id: str, expected_version: str, timeout: int = 120) -> bool:
        """
        Wait for node to come back online with expected version

        Args:
            node_id: Node ID to check
            expected_version: Expected version after update
            timeout: Timeout in seconds

        Returns:
            True if node came back with expected version, False otherwise
        """
        self.logger.info(f"Waiting for {node_id} to come back with version {expected_version}")
        start_time = time.time()

        while (time.time() - start_time) < timeout:
            try:
                # Try to get system info from node
                system_info = await self.proxy.system.__getattr__(node_id).get_system_info()
                current_version = system_info.get("version", "unknown")

                if current_version == expected_version:
                    self.logger.info(f"✓ Node {node_id} confirmed with version {current_version}")
                    return True
                else:
                    self.logger.debug(f"Node {node_id} has version {current_version}, waiting for {expected_version}")

            except Exception as e:
                self.logger.debug(f"Node {node_id} not available yet: {e}")

            await asyncio.sleep(5)  # Check every 5 seconds

        self.logger.warning(f"Timeout waiting for {node_id} to return with version {expected_version}")
        return False

    async def _execute_rolling_update(self, task: UpdateTask) -> Dict[str, Any]:
        """
        Execute rolling update (one node at a time)

        Args:
            task: Update task

        Returns:
            Result dictionary
        """
        self.logger.info(f"Executing rolling update {task.id} for {len(task.target_nodes)} nodes")

        # Initialize node updates
        for node_id in task.target_nodes:
            task.node_updates[node_id] = NodeUpdate(
                node_id=node_id,
                target_version=task.target_version,
                status=NodeUpdateStatus.PENDING
            )

        # Update nodes one by one
        for node_id in task.target_nodes:
            node_update = task.node_updates[node_id]

            try:
                # Start update on node
                node_update.status = NodeUpdateStatus.DOWNLOADING
                node_update.start_time = datetime.now()

                # Call update_manager on target node
                result = await self.proxy.update_manager.__getattr__(node_id).execute_update(
                    update_id=task.id,
                    artifact_id=task.artifact_id,
                    artifact_name=task.artifact_name,
                    target_version=task.target_version,
                    backup_enabled=task.backup_enabled,
                    auto_restart=True  # Auto-restart after successful update
                )

                if result.get("success"):
                    # Update installed, now wait for restart and version confirmation
                    node_update.status = NodeUpdateStatus.RESTARTING
                    node_update.logs.append(f"Update installed, waiting for node restart...")

                    # Wait for node to come back with new version
                    version_confirmed = await self._wait_for_node_version(
                        node_id,
                        task.target_version,
                        timeout=120
                    )

                    if version_confirmed:
                        node_update.status = NodeUpdateStatus.COMPLETED
                        node_update.end_time = datetime.now()
                        task.success_count += 1
                        node_update.logs.append(f"Update completed and version confirmed")
                    else:
                        node_update.status = NodeUpdateStatus.FAILED
                        node_update.error = "Node did not come back with expected version"
                        node_update.end_time = datetime.now()
                        task.failure_count += 1
                        node_update.logs.append(f"Update failed: timeout waiting for version confirmation")
                else:
                    node_update.status = NodeUpdateStatus.FAILED
                    node_update.error = result.get("error", "Unknown error")
                    node_update.end_time = datetime.now()
                    task.failure_count += 1
                    node_update.logs.append(f"Update failed: {node_update.error}")

                    # Check if max failures exceeded
                    if task.failure_count >= task.max_failures:
                        self.logger.error(f"Max failures ({task.max_failures}) exceeded, stopping update")

                        if task.auto_rollback:
                            await self._rollback_update(task)

                        return {
                            "success": False,
                            "error": f"Max failures exceeded: {task.failure_count}/{task.max_failures}",
                            "completed_nodes": task.success_count,
                            "failed_nodes": task.failure_count
                        }

            except Exception as e:
                self.logger.error(f"Failed to update node {node_id}: {e}")
                node_update.status = NodeUpdateStatus.FAILED
                node_update.error = str(e)
                node_update.end_time = datetime.now()
                task.failure_count += 1
                node_update.logs.append(f"Exception during update: {e}")

            # Wait between nodes (configurable interval)
            if node_id != task.target_nodes[-1]:  # Don't wait after last node
                await asyncio.sleep(task.interval_seconds)

        # Calculate duration
        if task.started_at and task.completed_at:
            task.total_duration_seconds = (task.completed_at - task.started_at).total_seconds()

        return {
            "success": task.failure_count == 0,
            "completed_nodes": task.success_count,
            "failed_nodes": task.failure_count,
            "total_nodes": len(task.target_nodes),
            "duration_seconds": task.total_duration_seconds
        }

    async def _execute_canary_update(self, task: UpdateTask) -> Dict[str, Any]:
        """
        Execute canary update (test on one node first)

        Args:
            task: Update task

        Returns:
            Result dictionary
        """
        self.logger.info(f"Executing canary update {task.id}")

        # Select canary node (first node or specified)
        canary_node = task.canary_node or task.target_nodes[0]
        other_nodes = [n for n in task.target_nodes if n != canary_node]

        # Initialize node updates
        for node_id in task.target_nodes:
            task.node_updates[node_id] = NodeUpdate(
                node_id=node_id,
                target_version=task.target_version,
                status=NodeUpdateStatus.PENDING
            )

        # Phase 1: Update canary node
        self.logger.info(f"Phase 1: Updating canary node {canary_node}")
        node_update = task.node_updates[canary_node]
        node_update.status = NodeUpdateStatus.DOWNLOADING
        node_update.start_time = datetime.now()

        try:
            result = await self.proxy.update_manager.__getattr__(canary_node).execute_update(
                update_id=task.id,
                artifact_id=task.artifact_id,
                artifact_name=task.artifact_name,
                target_version=task.target_version,
                backup_enabled=task.backup_enabled,
                auto_restart=True  # Auto-restart after successful update
            )

            if not result.get("success"):
                node_update.status = NodeUpdateStatus.FAILED
                node_update.error = result.get("error")
                node_update.end_time = datetime.now()
                task.failure_count += 1

                return {
                    "success": False,
                    "error": f"Canary update failed: {node_update.error}",
                    "canary_node": canary_node
                }

            # Update installed, wait for restart and version confirmation
            node_update.status = NodeUpdateStatus.RESTARTING
            node_update.logs.append(f"Canary update installed, waiting for node restart...")

            version_confirmed = await self._wait_for_node_version(
                canary_node,
                task.target_version,
                timeout=120
            )

            if version_confirmed:
                node_update.status = NodeUpdateStatus.COMPLETED
                node_update.end_time = datetime.now()
                task.success_count += 1
                node_update.logs.append(f"Canary update completed and version confirmed")
            else:
                node_update.status = NodeUpdateStatus.FAILED
                node_update.error = "Canary did not come back with expected version"
                node_update.end_time = datetime.now()
                task.failure_count += 1

                return {
                    "success": False,
                    "error": f"Canary update failed: timeout waiting for version confirmation",
                    "canary_node": canary_node
                }

        except Exception as e:
            self.logger.error(f"Canary update failed: {e}")
            node_update.status = NodeUpdateStatus.FAILED
            node_update.error = str(e)
            node_update.end_time = datetime.now()
            task.failure_count += 1

            return {
                "success": False,
                "error": f"Canary update exception: {e}",
                "canary_node": canary_node
            }

        # Phase 2: Monitor canary
        self.logger.info(f"Phase 2: Monitoring canary for {task.canary_duration}s")
        await asyncio.sleep(task.canary_duration)

        # Phase 3: Update other nodes (rolling)
        self.logger.info(f"Phase 3: Updating remaining {len(other_nodes)} nodes")

        # Temporarily modify target nodes for rolling update
        original_nodes = task.target_nodes
        task.target_nodes = other_nodes

        result = await self._execute_rolling_update(task)

        # Restore original nodes
        task.target_nodes = original_nodes

        return result

    async def _execute_blue_green_update(self, task: UpdateTask) -> Dict[str, Any]:
        """
        Execute blue-green update (prepare new environment, then swap)

        Args:
            task: Update task

        Returns:
            Result dictionary
        """
        self.logger.info(f"Executing blue-green update {task.id}")

        # For blue-green, we would typically:
        # 1. Deploy to new "green" environment
        # 2. Test green environment
        # 3. Switch traffic to green
        # 4. Decommission blue

        # Simplified implementation: update all nodes in parallel, then activate
        return await self._execute_all_at_once_update(task)

    async def _execute_all_at_once_update(self, task: UpdateTask) -> Dict[str, Any]:
        """
        Execute update on all nodes simultaneously

        Args:
            task: Update task

        Returns:
            Result dictionary
        """
        self.logger.info(f"Executing all-at-once update {task.id} for {len(task.target_nodes)} nodes")

        # Initialize node updates
        for node_id in task.target_nodes:
            task.node_updates[node_id] = NodeUpdate(
                node_id=node_id,
                target_version=task.target_version,
                status=NodeUpdateStatus.DOWNLOADING,
                start_time=datetime.now()
            )

        # Create tasks for all nodes
        tasks_list = []
        for node_id in task.target_nodes:
            task_coro = self.proxy.update_manager.__getattr__(node_id).execute_update(
                update_id=task.id,
                artifact_id=task.artifact_id,
                artifact_name=task.artifact_name,
                target_version=task.target_version,
                backup_enabled=task.backup_enabled,
                auto_restart=True  # Auto-restart after successful update
            )
            tasks_list.append((node_id, task_coro))

        # Execute in parallel
        results = await asyncio.gather(
            *[t[1] for t in tasks_list],
            return_exceptions=True
        )

        # Process results - mark nodes as restarting if update succeeded
        nodes_to_verify = []
        for (node_id, _), result in zip(tasks_list, results):
            node_update = task.node_updates[node_id]

            if isinstance(result, Exception):
                node_update.status = NodeUpdateStatus.FAILED
                node_update.error = str(result)
                node_update.end_time = datetime.now()
                task.failure_count += 1
            elif result.get("success"):
                node_update.status = NodeUpdateStatus.RESTARTING
                node_update.logs.append(f"Update installed, waiting for node restart...")
                nodes_to_verify.append(node_id)
            else:
                node_update.status = NodeUpdateStatus.FAILED
                node_update.error = result.get("error", "Unknown error")
                node_update.end_time = datetime.now()
                task.failure_count += 1

        # Wait for all nodes to come back with new version
        if nodes_to_verify:
            self.logger.info(f"Waiting for {len(nodes_to_verify)} nodes to restart with new version...")

            verify_tasks = []
            for node_id in nodes_to_verify:
                verify_tasks.append((
                    node_id,
                    self._wait_for_node_version(node_id, task.target_version, timeout=120)
                ))

            verify_results = await asyncio.gather(
                *[t[1] for t in verify_tasks],
                return_exceptions=True
            )

            for (node_id, _), version_confirmed in zip(verify_tasks, verify_results):
                node_update = task.node_updates[node_id]

                if isinstance(version_confirmed, Exception):
                    node_update.status = NodeUpdateStatus.FAILED
                    node_update.error = f"Version check failed: {version_confirmed}"
                    node_update.end_time = datetime.now()
                    task.failure_count += 1
                elif version_confirmed:
                    node_update.status = NodeUpdateStatus.COMPLETED
                    node_update.end_time = datetime.now()
                    task.success_count += 1
                    node_update.logs.append(f"Update completed and version confirmed")
                else:
                    node_update.status = NodeUpdateStatus.FAILED
                    node_update.error = "Node did not come back with expected version"
                    node_update.end_time = datetime.now()
                    task.failure_count += 1
                    node_update.logs.append(f"Version confirmation timeout")

        # Calculate duration
        if task.started_at:
            task.completed_at = datetime.now()
            task.total_duration_seconds = (task.completed_at - task.started_at).total_seconds()

        return {
            "success": task.failure_count == 0,
            "completed_nodes": task.success_count,
            "failed_nodes": task.failure_count,
            "total_nodes": len(task.target_nodes),
            "duration_seconds": task.total_duration_seconds
        }

    async def _rollback_update(self, task: UpdateTask):
        """
        Rollback failed update on all nodes

        Args:
            task: Update task
        """
        self.logger.info(f"Rolling back update {task.id}")

        rollback_success = 0
        rollback_failed = 0

        for node_id, node_update in task.node_updates.items():
            if node_update.status == NodeUpdateStatus.COMPLETED:
                try:
                    # Attempt rollback
                    result = await self.proxy.update_manager.__getattr__(node_id).manual_rollback(
                        backup_name=f"backup_{task.artifact_name}_{datetime.now().strftime('%Y%m%d')}"
                    )

                    if result.get("success"):
                        node_update.status = NodeUpdateStatus.ROLLED_BACK
                        node_update.logs.append("Rolled back successfully")
                        rollback_success += 1
                    else:
                        self.logger.error(f"Rollback failed for {node_id}: {result.get('error')}")
                        rollback_failed += 1

                except Exception as e:
                    self.logger.error(f"Rollback exception for {node_id}: {e}")
                    rollback_failed += 1

        task.status = UpdateStatus.ROLLED_BACK
        self.total_updates_rolled_back += 1
        self.metrics.increment("updates_rolled_back")
        self.metrics.gauge("last_rollback_success_count", rollback_success)
        self.metrics.gauge("last_rollback_failed_count", rollback_failed)

    @service_method(description="Pause update", public=True)
    async def pause_update(self, update_id: int) -> Dict[str, Any]:
        """Pause an in-progress update"""
        task = self.active_updates.get(update_id)
        if not task:
            return {"success": False, "error": "Update not found"}

        if task.status != UpdateStatus.IN_PROGRESS:
            return {"success": False, "error": f"Update not in progress: {task.status.value}"}

        task.status = UpdateStatus.PAUSED
        return {"success": True, "message": "Update paused"}

    @service_method(description="Resume update", public=True)
    async def resume_update(self, update_id: int) -> Dict[str, Any]:
        """Resume a paused update"""
        task = self.active_updates.get(update_id)
        if not task:
            return {"success": False, "error": "Update not found"}

        if task.status != UpdateStatus.PAUSED:
            return {"success": False, "error": f"Update not paused: {task.status.value}"}

        task.status = UpdateStatus.IN_PROGRESS
        # Continue execution in background
        asyncio.create_task(self.execute_update_task(update_id))

        return {"success": True, "message": "Update resumed"}

    @service_method(description="Cancel update", public=True)
    async def cancel_update(self, update_id: int) -> Dict[str, Any]:
        """Cancel an update"""
        task = self.active_updates.get(update_id)
        if not task:
            return {"success": False, "error": "Update not found"}

        task.status = UpdateStatus.FAILED
        task.completed_at = datetime.now()

        # Rollback if enabled
        if task.auto_rollback:
            await self._rollback_update(task)

        # Move to history
        self.update_history.append(task)
        del self.active_updates[update_id]

        return {"success": True, "message": "Update cancelled"}

    @service_method(description="List active updates", public=True)
    async def list_active_updates(self) -> Dict[str, Any]:
        """List all active updates"""
        return {
            "success": True,
            "updates": [task.to_dict() for task in self.active_updates.values()],
            "count": len(self.active_updates)
        }

    @service_method(description="Get update history", public=True)
    async def get_update_history(self, limit: int = 10) -> Dict[str, Any]:
        """Get update history"""
        history = self.update_history[-limit:]
        return {
            "success": True,
            "history": [task.to_dict() for task in history],
            "total_count": len(self.update_history)
        }

    @service_method(description="Get update server metrics", public=True)
    async def get_metrics(self) -> Dict[str, Any]:
        """Get update server metrics"""
        return {
            "success": True,
            "metrics": {
                "total_updates_started": self.total_updates_started,
                "total_updates_completed": self.total_updates_completed,
                "total_updates_failed": self.total_updates_failed,
                "total_updates_rolled_back": self.total_updates_rolled_back,
                "active_updates_count": len(self.active_updates),
                "strategy_usage": self.strategy_usage,
                "success_rate": (
                    self.total_updates_completed / self.total_updates_started * 100
                    if self.total_updates_started > 0 else 0
                )
            }
        }

    @service_method(description="Get gossip info from all nodes", public=True)
    async def get_cluster_update_status(self) -> Dict[str, Any]:
        """
        Get update status from all nodes via gossip

        Returns:
            Dictionary with node update information
        """
        try:
            network = self.context.get_shared("network")
            if not network or not hasattr(network, 'gossip'):
                return {
                    "success": False,
                    "error": "Network gossip not available"
                }

            gossip = network.gossip
            node_registry = gossip.node_registry

            cluster_status = {}
            for node_id, node_info in node_registry.items():
                metadata = node_info.metadata
                if 'update_manager' in metadata:
                    cluster_status[node_id] = metadata['update_manager']

            return {
                "success": True,
                "cluster_status": cluster_status,
                "node_count": len(cluster_status)
            }

        except Exception as e:
            self.logger.error(f"Failed to get cluster update status: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _gossip_publish_loop(self):
        """
        Periodically publish update server information to gossip

        Publishes:
        - Active update tasks
        - Update statistics
        - Strategy usage
        """
        while True:
            try:
                await asyncio.sleep(self.gossip_publish_interval)

                network = self.context.get_shared("network")
                if not network or not hasattr(network, 'gossip'):
                    continue

                gossip = network.gossip

                # Prepare active updates summary
                active_updates_summary = []
                for task in self.active_updates.values():
                    active_updates_summary.append({
                        'update_id': task.id,
                        'artifact_name': task.artifact_name,
                        'target_version': task.target_version,
                        'strategy': task.strategy.value,
                        'status': task.status.value,
                        'target_nodes': task.target_nodes,
                        'success_count': task.success_count,
                        'failure_count': task.failure_count
                    })

                # Update gossip metadata
                if hasattr(gossip, 'self_info') and hasattr(gossip.self_info, 'metadata'):
                    gossip.self_info.metadata['update_server'] = {
                        'active_updates': active_updates_summary,
                        'active_updates_count': len(self.active_updates),
                        'total_updates_started': self.total_updates_started,
                        'total_updates_completed': self.total_updates_completed,
                        'total_updates_failed': self.total_updates_failed,
                        'total_updates_rolled_back': self.total_updates_rolled_back,
                        'strategy_usage': self.strategy_usage,
                        'last_updated': datetime.now().isoformat()
                    }

                self.logger.debug(
                    f"Published update server info to gossip: "
                    f"{len(active_updates_summary)} active updates"
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in gossip publish loop: {e}", exc_info=True)

    async def _load_history(self):
        """Load update history from file"""
        try:
            if self.history_file.exists():
                with open(self.history_file, 'r') as f:
                    history_data = json.load(f)

                # Reconstruct UpdateTask objects from saved data
                for task_dict in history_data:
                    try:
                        task = UpdateTask.from_dict(task_dict)
                        self.update_history.append(task)
                        # Update counter to avoid ID conflicts
                        if task.id >= self.update_id_counter:
                            self.update_id_counter = task.id + 1
                    except Exception as e:
                        self.logger.error(f"Failed to load history entry: {e}")

                self.logger.info(f"Loaded {len(self.update_history)} update records from history")
        except Exception as e:
            self.logger.error(f"Failed to load update history: {e}")

    async def _save_history(self):
        """Save update history to file"""
        try:
            # Convert UpdateTask objects to dictionaries
            history_data = [task.to_dict() for task in self.update_history]

            with open(self.history_file, 'w') as f:
                json.dump(history_data, f, indent=2)

            self.logger.debug(f"Saved {len(self.update_history)} update records to history")
        except Exception as e:
            self.logger.error(f"Failed to save update history: {e}")
