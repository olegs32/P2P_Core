"""
Log Collector Service - Centralized logging for P2P cluster

This service collects logs from all nodes and provides API to query them.
Similar to syslog but built into the P2P framework.
"""

import logging
import time
from collections import deque
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from threading import Lock

from layers.service import BaseService, service_method


@dataclass
class LogEntry:
    """Single log entry"""
    timestamp: str  # ISO format
    node_id: str
    level: str  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    logger_name: str  # e.g., "Service.system", "Gossip", etc.
    message: str
    module: str = ""
    funcName: str = ""
    lineno: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LogCollector(BaseService):
    """
    Centralized log collector service

    Collects logs from all nodes in the cluster and provides
    API for querying with filtering.
    """

    SERVICE_NAME = "log_collector"

    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)

        self.info.version = "1.0.0"
        self.info.description = "Centralized log collector for P2P cluster"

        # Configuration
        self.max_logs = 1000  # Will be overridden from config

        # Storage: {node_id: deque(LogEntry)}
        self.logs_by_node: Dict[str, deque] = {}
        self.lock = Lock()

        # Log levels for filtering
        self.log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

    async def initialize(self):
        """Initialize log collector"""
        # Get config from context if available
        if hasattr(self, 'context') and self.context:
            config = self.context.config
            self.max_logs = getattr(config, 'max_log_entries', 1000)
            self.node_id = config.node_id

        self.logger.info(f"Log collector initialized (max_logs: {self.max_logs})")

    async def cleanup(self):
        """Cleanup on shutdown"""
        with self.lock:
            total_logs = sum(len(logs) for logs in self.logs_by_node.values())
        self.logger.info(f"Log collector stopping (collected {total_logs} logs from {len(self.logs_by_node)} nodes)")

    @service_method(description="Add log entries from a node", public=True)
    async def add_logs(self, node_id: str, logs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Add log entries from a worker node

        Args:
            node_id: Node identifier
            logs: List of log entries as dicts

        Returns:
            Status dict
        """
        if not logs:
            return {"success": True, "added": 0}

        with self.lock:
            # Initialize deque for this node if needed
            if node_id not in self.logs_by_node:
                self.logs_by_node[node_id] = deque(maxlen=self.max_logs)

            node_logs = self.logs_by_node[node_id]

            # Add each log entry
            for log_dict in logs:
                # Ensure node_id is set
                log_dict['node_id'] = node_id

                # Create LogEntry and add to deque
                try:
                    log_entry = LogEntry(**log_dict)
                    node_logs.append(log_entry)
                except Exception as e:
                    self.logger.error(f"Failed to parse log entry: {e}")
                    continue

        self.metrics.increment("logs_received")
        self.metrics.gauge(f"logs_stored_{node_id}", len(self.logs_by_node[node_id]))

        return {
            "success": True,
            "added": len(logs),
            "total_stored": len(self.logs_by_node[node_id])
        }

    @service_method(description="Get logs with filtering", public=True)
    async def get_logs(
        self,
        node_id: Optional[str] = None,
        level: Optional[str] = None,
        logger_name: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> Dict[str, Any]:
        """
        Get logs with optional filtering

        Args:
            node_id: Filter by node (None = all nodes)
            level: Filter by log level (None = all levels)
            logger_name: Filter by logger name (None = all loggers)
            limit: Maximum number of logs to return
            offset: Skip first N logs

        Returns:
            Dict with logs and metadata
        """
        with self.lock:
            # Collect logs from selected nodes
            if node_id:
                if node_id not in self.logs_by_node:
                    return {
                        "logs": [],
                        "total": 0,
                        "nodes": []
                    }
                all_logs = list(self.logs_by_node[node_id])
            else:
                # Merge logs from all nodes
                all_logs = []
                for node_logs in self.logs_by_node.values():
                    all_logs.extend(list(node_logs))

            # Filter by level
            if level and level in self.log_levels:
                all_logs = [log for log in all_logs if log.level == level]

            # Filter by logger_name (contains)
            if logger_name:
                all_logs = [log for log in all_logs if logger_name.lower() in log.logger_name.lower()]

            # Sort by timestamp (newest first)
            all_logs.sort(key=lambda x: x.timestamp, reverse=True)

            # Apply pagination
            total = len(all_logs)
            paginated_logs = all_logs[offset:offset + limit]

            # Convert to dicts
            log_dicts = [log.to_dict() for log in paginated_logs]

            # Get list of available nodes
            available_nodes = list(self.logs_by_node.keys())

        return {
            "logs": log_dicts,
            "total": total,
            "limit": limit,
            "offset": offset,
            "nodes": available_nodes
        }

    @service_method(description="Get available log sources", public=True)
    async def get_log_sources(self) -> Dict[str, Any]:
        """
        Get list of available log sources (nodes and loggers)

        Returns:
            Dict with nodes and unique logger names
        """
        with self.lock:
            nodes = list(self.logs_by_node.keys())

            # Collect unique logger names
            logger_names = set()
            for node_logs in self.logs_by_node.values():
                for log in node_logs:
                    logger_names.add(log.logger_name)

        return {
            "nodes": sorted(nodes),
            "loggers": sorted(list(logger_names)),
            "log_levels": self.log_levels
        }

    @service_method(description="Clear logs", public=True)
    async def clear_logs(self, node_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Clear logs for a node or all nodes

        Args:
            node_id: Node to clear (None = all nodes)

        Returns:
            Status dict
        """
        with self.lock:
            if node_id:
                if node_id in self.logs_by_node:
                    count = len(self.logs_by_node[node_id])
                    self.logs_by_node[node_id].clear()
                    return {"success": True, "cleared": count, "node": node_id}
                else:
                    return {"success": False, "error": "Node not found"}
            else:
                total = sum(len(logs) for logs in self.logs_by_node.values())
                self.logs_by_node.clear()
                return {"success": True, "cleared": total, "node": "all"}

    @service_method(description="Get statistics", public=True)
    async def get_stats(self) -> Dict[str, Any]:
        """Get log collector statistics"""
        with self.lock:
            stats = {
                "total_nodes": len(self.logs_by_node),
                "total_logs": sum(len(logs) for logs in self.logs_by_node.values()),
                "max_logs_per_node": self.max_logs,
                "nodes": {}
            }

            for node_id, logs in self.logs_by_node.items():
                stats["nodes"][node_id] = {
                    "log_count": len(logs),
                    "oldest_log": logs[0].timestamp if logs else None,
                    "newest_log": logs[-1].timestamp if logs else None
                }

        return stats


class P2PLogHandler(logging.Handler):
    """
    Custom logging handler that captures logs and stores them in memory
    for later transmission to coordinator
    """

    def __init__(self, node_id: str, max_logs: int = 1000):
        super().__init__()
        self.node_id = node_id
        self.buffer = deque(maxlen=max_logs)
        self.lock = Lock()
        self.last_sent_index = 0

    def emit(self, record: logging.LogRecord):
        """Capture log record"""
        try:
            # Create log entry
            log_entry = LogEntry(
                timestamp=datetime.fromtimestamp(record.created).isoformat(),
                node_id=self.node_id,
                level=record.levelname,
                logger_name=record.name,
                message=record.getMessage(),
                module=record.module,
                funcName=record.funcName,
                lineno=record.lineno
            )

            with self.lock:
                self.buffer.append(log_entry)
        except Exception:
            self.handleError(record)

    def get_new_logs(self) -> List[Dict[str, Any]]:
        """
        Get logs that haven't been sent yet

        Returns:
            List of new log dicts
        """
        with self.lock:
            # Get all logs from buffer (deque doesn't track sent/unsent easily)
            # So we'll return all and let the collector deduplicate
            new_logs = [log.to_dict() for log in self.buffer]

            # Clear buffer after retrieval (logs are now on coordinator)
            self.buffer.clear()

            return new_logs
