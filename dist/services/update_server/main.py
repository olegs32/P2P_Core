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
from typing import Dict, List, Optional, Any
from datetime import datetime

from layers.service import BaseService, service_method

from .models.update_task import (
    UpdateTask, UpdateStrategy, UpdateStatus,
    NodeUpdate, NodeUpdateStatus
)


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

    async def initialize(self):
        """Initialize update server"""
        self.logger.info("Initializing Update Server service")

    async def cleanup(self):
        """Cleanup update server"""
        pass

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

            return {"success": True, "update_id": task.id}

        except Exception as e:
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
            else:
                task.status = UpdateStatus.FAILED

            task.completed_at = datetime.now()

            # Move to history
            self.update_history.append(task)

            return result

        except Exception as e:
            self.logger.error(f"Update execution failed: {e}", exc_info=True)
            task.status = UpdateStatus.FAILED
            task.completed_at = datetime.now()
            return {"success": False, "error": str(e)}

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
                    auto_restart=False
                )

                if result.get("success"):
                    node_update.status = NodeUpdateStatus.COMPLETED
                    node_update.end_time = datetime.now()
                    task.success_count += 1
                    node_update.logs.append(f"Update completed successfully")
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
                auto_restart=False
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

            node_update.status = NodeUpdateStatus.COMPLETED
            node_update.end_time = datetime.now()
            task.success_count += 1

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
                auto_restart=False
            )
            tasks_list.append((node_id, task_coro))

        # Execute in parallel
        results = await asyncio.gather(
            *[t[1] for t in tasks_list],
            return_exceptions=True
        )

        # Process results
        for (node_id, _), result in zip(tasks_list, results):
            node_update = task.node_updates[node_id]

            if isinstance(result, Exception):
                node_update.status = NodeUpdateStatus.FAILED
                node_update.error = str(result)
                task.failure_count += 1
            elif result.get("success"):
                node_update.status = NodeUpdateStatus.COMPLETED
                task.success_count += 1
            else:
                node_update.status = NodeUpdateStatus.FAILED
                node_update.error = result.get("error", "Unknown error")
                task.failure_count += 1

            node_update.end_time = datetime.now()

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
                    else:
                        self.logger.error(f"Rollback failed for {node_id}: {result.get('error')}")

                except Exception as e:
                    self.logger.error(f"Rollback exception for {node_id}: {e}")

        task.status = UpdateStatus.ROLLED_BACK

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
