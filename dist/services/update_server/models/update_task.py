"""
Update task model
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Any, Optional
from enum import Enum


class UpdateStrategy(Enum):
    """Update deployment strategy"""
    ROLLING = "rolling"          # One by one
    CANARY = "canary"            # Test node first
    BLUE_GREEN = "blue_green"    # Swap environments
    ALL_AT_ONCE = "all_at_once"  # All nodes simultaneously


class UpdateStatus(Enum):
    """Update task status"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class NodeUpdateStatus(Enum):
    """Status of update on individual node"""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    INSTALLING = "installing"
    RESTARTING = "restarting"
    HEALTH_CHECK = "health_check"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class NodeUpdate:
    """Update status for individual node"""
    node_id: str
    status: NodeUpdateStatus = NodeUpdateStatus.PENDING
    progress: int = 0  # 0-100%
    current_version: str = ""
    target_version: str = ""
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    error: Optional[str] = None
    logs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['status'] = self.status.value
        if self.start_time:
            data['start_time'] = self.start_time.isoformat()
        if self.end_time:
            data['end_time'] = self.end_time.isoformat()
        return data

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'NodeUpdate':
        """Create from dictionary"""
        # Convert string to enum
        if 'status' in data and isinstance(data['status'], str):
            data['status'] = NodeUpdateStatus(data['status'])

        # Convert ISO strings to datetime
        if 'start_time' in data and isinstance(data['start_time'], str):
            data['start_time'] = datetime.fromisoformat(data['start_time'])
        if 'end_time' in data and isinstance(data['end_time'], str):
            data['end_time'] = datetime.fromisoformat(data['end_time'])

        return NodeUpdate(**data)


@dataclass
class UpdateTask:
    """Update task for cluster"""
    id: Optional[int] = None
    artifact_id: int = 0
    artifact_name: str = ""
    target_version: str = ""
    strategy: UpdateStrategy = UpdateStrategy.ROLLING
    status: UpdateStatus = UpdateStatus.PENDING

    # Target nodes
    target_nodes: List[str] = field(default_factory=list)
    node_updates: Dict[str, NodeUpdate] = field(default_factory=dict)

    # Strategy config
    interval_seconds: int = 30  # Between nodes in rolling update
    canary_node: Optional[str] = None
    canary_duration: int = 60  # Seconds to wait for canary
    max_failures: int = 1  # Max failures before stopping

    # Metadata
    created_by: str = ""
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Settings
    backup_enabled: bool = True
    auto_rollback: bool = True
    health_check_timeout: int = 30

    # Results
    success_count: int = 0
    failure_count: int = 0
    total_duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['strategy'] = self.strategy.value
        data['status'] = self.status.value
        data['node_updates'] = {k: v.to_dict() for k, v in self.node_updates.items()}
        if self.created_at:
            data['created_at'] = self.created_at.isoformat()
        if self.started_at:
            data['started_at'] = self.started_at.isoformat()
        if self.completed_at:
            data['completed_at'] = self.completed_at.isoformat()
        return data

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'UpdateTask':
        """Create from dictionary"""
        # Convert string to enum
        if 'strategy' in data and isinstance(data['strategy'], str):
            data['strategy'] = UpdateStrategy(data['strategy'])
        if 'status' in data and isinstance(data['status'], str):
            data['status'] = UpdateStatus(data['status'])

        # Convert ISO strings to datetime
        if 'created_at' in data and isinstance(data['created_at'], str):
            data['created_at'] = datetime.fromisoformat(data['created_at'])
        if 'started_at' in data and isinstance(data['started_at'], str):
            data['started_at'] = datetime.fromisoformat(data['started_at'])
        if 'completed_at' in data and isinstance(data['completed_at'], str):
            data['completed_at'] = datetime.fromisoformat(data['completed_at'])

        # Convert node_updates dicts to NodeUpdate objects
        if 'node_updates' in data and data['node_updates']:
            data['node_updates'] = {
                k: NodeUpdate.from_dict(v) if isinstance(v, dict) else v
                for k, v in data['node_updates'].items()
            }

        return UpdateTask(**data)
