"""
Artifact model for repository system
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum


class ArtifactType(Enum):
    """Type of artifact"""
    BINARY = "binary"          # exe/elf executable
    SERVICE = "service"        # service zip/tar
    CONFIG = "config"          # yaml/json config
    DOCKER = "docker"          # docker image


class ArtifactStatus(Enum):
    """Status of artifact"""
    UPLOADING = "uploading"
    AVAILABLE = "available"
    QUARANTINE = "quarantine"  # Failed security scan
    ARCHIVED = "archived"
    DELETED = "deleted"


@dataclass
class ArtifactDependency:
    """Dependency of an artifact"""
    name: str
    version: str
    required: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Artifact:
    """Artifact metadata"""
    # Identity
    id: Optional[int] = None
    name: str = ""
    version: str = ""
    artifact_type: ArtifactType = ArtifactType.BINARY

    # Platform info
    platform: Optional[str] = None  # win-x64, linux-x64, etc
    architecture: Optional[str] = None  # x86_64, arm64, etc

    # File info
    size_bytes: int = 0
    content_hash: str = ""  # SHA256
    sha256: str = ""
    sha512: str = ""

    # Security
    signature: Optional[str] = None
    signature_key_id: Optional[str] = None
    signature_valid: bool = False
    virus_scan_status: Optional[str] = None  # clean, infected, pending
    virus_scan_date: Optional[datetime] = None

    # Metadata
    description: str = ""
    changelog: str = ""
    tags: List[str] = field(default_factory=list)
    dependencies: List[ArtifactDependency] = field(default_factory=list)

    # Upload info
    upload_date: Optional[datetime] = None
    uploader: str = ""
    uploader_ip: Optional[str] = None

    # Status
    status: ArtifactStatus = ArtifactStatus.AVAILABLE

    # Usage statistics
    download_count: int = 0
    last_download: Optional[datetime] = None
    deployed_nodes: List[str] = field(default_factory=list)

    # Storage
    storage_path: str = ""
    compressed_path: Optional[str] = None
    compression_ratio: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        data = asdict(self)
        # Convert enums to strings
        data['artifact_type'] = self.artifact_type.value
        data['status'] = self.status.value
        # Convert datetime to ISO format
        if self.upload_date:
            data['upload_date'] = self.upload_date.isoformat()
        if self.last_download:
            data['last_download'] = self.last_download.isoformat()
        if self.virus_scan_date:
            data['virus_scan_date'] = self.virus_scan_date.isoformat()
        return data

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'Artifact':
        """Create from dictionary"""
        # Convert string to enum
        if 'artifact_type' in data and isinstance(data['artifact_type'], str):
            data['artifact_type'] = ArtifactType(data['artifact_type'])
        if 'status' in data and isinstance(data['status'], str):
            data['status'] = ArtifactStatus(data['status'])

        # Convert ISO strings to datetime
        for field_name in ['upload_date', 'last_download', 'virus_scan_date']:
            if field_name in data and isinstance(data[field_name], str):
                data[field_name] = datetime.fromisoformat(data[field_name])

        # Convert dependency dicts to objects
        if 'dependencies' in data and data['dependencies']:
            data['dependencies'] = [
                ArtifactDependency(**dep) if isinstance(dep, dict) else dep
                for dep in data['dependencies']
            ]

        return Artifact(**data)

    def get_unique_id(self) -> str:
        """Get unique identifier for this artifact"""
        return f"{self.name}:{self.version}:{self.platform or 'any'}"
