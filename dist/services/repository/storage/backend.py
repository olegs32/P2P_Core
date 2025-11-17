"""
Storage backend for repository files
"""
import os
import shutil
import hashlib
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any, BinaryIO
from datetime import datetime
import logging
import sys
import importlib.util

# Load artifact module dynamically (avoid relative imports in ServiceLoader context)
def _load_artifact_module():
    """Load artifact module from models directory"""
    service_dir = Path(__file__).parent.parent
    artifact_path = service_dir / 'models' / 'artifact.py'

    spec = importlib.util.spec_from_file_location('repository_artifact_for_backend', str(artifact_path))
    module = importlib.util.module_from_spec(spec)
    sys.modules['repository_artifact_for_backend'] = module
    spec.loader.exec_module(module)
    return module

_artifact_mod = _load_artifact_module()
Artifact = _artifact_mod.Artifact
ArtifactStatus = _artifact_mod.ArtifactStatus


class StorageBackend:
    """File storage backend for artifacts"""

    def __init__(self, base_path: str, db_path: str):
        """
        Initialize storage backend

        Args:
            base_path: Base directory for file storage
            db_path: Path to SQLite database
        """
        self.base_path = Path(base_path)
        self.db_path = db_path
        self.logger = logging.getLogger("Repository.Storage")

        # Create directories
        self.base_path.mkdir(parents=True, exist_ok=True)
        (self.base_path / "binaries").mkdir(exist_ok=True)
        (self.base_path / "services").mkdir(exist_ok=True)
        (self.base_path / "configs").mkdir(exist_ok=True)
        (self.base_path / "temp").mkdir(exist_ok=True)

        # Initialize database
        self._init_database()

    def _init_database(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Artifacts table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                platform TEXT,
                architecture TEXT,
                size_bytes INTEGER,
                sha256 TEXT UNIQUE NOT NULL,
                sha512 TEXT,
                signature TEXT,
                signature_key_id TEXT,
                signature_valid INTEGER DEFAULT 0,
                virus_scan_status TEXT,
                virus_scan_date TEXT,
                description TEXT,
                changelog TEXT,
                upload_date TEXT NOT NULL,
                uploader TEXT NOT NULL,
                uploader_ip TEXT,
                status TEXT NOT NULL,
                download_count INTEGER DEFAULT 0,
                last_download TEXT,
                storage_path TEXT NOT NULL,
                compressed_path TEXT,
                compression_ratio REAL DEFAULT 0.0,
                UNIQUE(name, version, platform)
            )
        """)

        # Tags table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS artifact_tags (
                artifact_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (artifact_id) REFERENCES artifacts(id) ON DELETE CASCADE,
                UNIQUE(artifact_id, tag)
            )
        """)

        # Dependencies table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS artifact_dependencies (
                artifact_id INTEGER NOT NULL,
                dependency_name TEXT NOT NULL,
                dependency_version TEXT NOT NULL,
                required INTEGER DEFAULT 1,
                FOREIGN KEY (artifact_id) REFERENCES artifacts(id) ON DELETE CASCADE
            )
        """)

        # Download stats table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS download_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artifact_id INTEGER NOT NULL,
                node_id TEXT,
                download_date TEXT NOT NULL,
                ip_address TEXT,
                success INTEGER DEFAULT 1,
                FOREIGN KEY (artifact_id) REFERENCES artifacts(id) ON DELETE CASCADE
            )
        """)

        # Audit log table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                user TEXT NOT NULL,
                action TEXT NOT NULL,
                artifact_id INTEGER,
                ip_address TEXT,
                details TEXT,
                FOREIGN KEY (artifact_id) REFERENCES artifacts(id) ON DELETE SET NULL
            )
        """)

        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_name ON artifacts(name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_version ON artifacts(version)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_platform ON artifacts(platform)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_sha256 ON artifacts(sha256)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tags_artifact ON artifact_tags(artifact_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tags_tag ON artifact_tags(tag)")

        conn.commit()
        conn.close()

        self.logger.info(f"Database initialized at {self.db_path}")

    def store_artifact(self, artifact: Artifact, file_data: BinaryIO) -> str:
        """
        Store artifact file and metadata

        Args:
            artifact: Artifact metadata
            file_data: File data stream

        Returns:
            Storage path of the artifact
        """
        # Determine subdirectory based on type
        subdir_map = {
            "binary": "binaries",
            "service": "services",
            "config": "configs",
            "docker": "binaries"
        }
        subdir = subdir_map.get(artifact.artifact_type.value, "binaries")

        # Generate storage path
        filename = f"{artifact.name}_{artifact.version}"
        if artifact.platform:
            filename += f"_{artifact.platform}"
        storage_dir = self.base_path / subdir
        storage_path = storage_dir / filename

        # Ensure unique path
        counter = 1
        original_path = storage_path
        while storage_path.exists():
            storage_path = original_path.parent / f"{original_path.stem}_{counter}{original_path.suffix}"
            counter += 1

        # Write file and calculate checksums
        sha256_hash = hashlib.sha256()
        sha512_hash = hashlib.sha512()
        size = 0

        with open(storage_path, 'wb') as f:
            while chunk := file_data.read(8192):
                sha256_hash.update(chunk)
                sha512_hash.update(chunk)
                f.write(chunk)
                size += len(chunk)

        # Update artifact metadata
        artifact.storage_path = str(storage_path)
        artifact.size_bytes = size
        artifact.sha256 = sha256_hash.hexdigest()
        artifact.sha512 = sha512_hash.hexdigest()
        artifact.content_hash = artifact.sha256
        artifact.upload_date = datetime.now()
        artifact.status = ArtifactStatus.AVAILABLE

        # Save to database
        self._save_artifact_metadata(artifact)

        self.logger.info(f"Stored artifact {artifact.name} v{artifact.version} at {storage_path}")

        return str(storage_path)

    def _save_artifact_metadata(self, artifact: Artifact):
        """Save artifact metadata to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO artifacts (
                    name, version, artifact_type, platform, architecture,
                    size_bytes, sha256, sha512, signature, signature_key_id,
                    signature_valid, virus_scan_status, virus_scan_date,
                    description, changelog, upload_date, uploader, uploader_ip,
                    status, storage_path, compressed_path, compression_ratio
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                artifact.name, artifact.version, artifact.artifact_type.value,
                artifact.platform, artifact.architecture, artifact.size_bytes,
                artifact.sha256, artifact.sha512, artifact.signature,
                artifact.signature_key_id, int(artifact.signature_valid),
                artifact.virus_scan_status,
                artifact.virus_scan_date.isoformat() if artifact.virus_scan_date else None,
                artifact.description, artifact.changelog,
                artifact.upload_date.isoformat() if artifact.upload_date else None,
                artifact.uploader, artifact.uploader_ip, artifact.status.value,
                artifact.storage_path, artifact.compressed_path, artifact.compression_ratio
            ))

            artifact.id = cursor.lastrowid

            # Save tags
            for tag in artifact.tags:
                cursor.execute("""
                    INSERT OR IGNORE INTO artifact_tags (artifact_id, tag, created_at)
                    VALUES (?, ?, ?)
                """, (artifact.id, tag, datetime.now().isoformat()))

            # Save dependencies
            for dep in artifact.dependencies:
                cursor.execute("""
                    INSERT INTO artifact_dependencies (artifact_id, dependency_name, dependency_version, required)
                    VALUES (?, ?, ?, ?)
                """, (artifact.id, dep.name, dep.version, int(dep.required)))

            conn.commit()

        except sqlite3.IntegrityError as e:
            conn.rollback()
            self.logger.error(f"Failed to save artifact: {e}")
            raise ValueError(f"Artifact already exists: {artifact.name} v{artifact.version}")

        finally:
            conn.close()

    def get_artifact(self, artifact_id: int) -> Optional[Artifact]:
        """Get artifact by ID"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,))
        row = cursor.fetchone()

        if not row:
            conn.close()
            return None

        artifact = self._row_to_artifact(row)

        # Load tags
        cursor.execute("SELECT tag FROM artifact_tags WHERE artifact_id = ?", (artifact_id,))
        artifact.tags = [row['tag'] for row in cursor.fetchall()]

        # Load dependencies
        cursor.execute("""
            SELECT dependency_name, dependency_version, required
            FROM artifact_dependencies WHERE artifact_id = ?
        """, (artifact_id,))

        from ..models.artifact import ArtifactDependency
        artifact.dependencies = [
            ArtifactDependency(
                name=row['dependency_name'],
                version=row['dependency_version'],
                required=bool(row['required'])
            )
            for row in cursor.fetchall()
        ]

        conn.close()
        return artifact

    def list_artifacts(
        self,
        artifact_type: Optional[str] = None,
        platform: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Artifact]:
        """List artifacts with optional filters"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = "SELECT DISTINCT a.* FROM artifacts a"
        conditions = []
        params = []

        if tag:
            query += " JOIN artifact_tags t ON a.id = t.artifact_id"
            conditions.append("t.tag = ?")
            params.append(tag)

        if artifact_type:
            conditions.append("a.artifact_type = ?")
            params.append(artifact_type)

        if platform:
            conditions.append("a.platform = ?")
            params.append(platform)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY a.upload_date DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        rows = cursor.fetchall()

        artifacts = [self._row_to_artifact(row) for row in rows]
        conn.close()

        return artifacts

    def _row_to_artifact(self, row: sqlite3.Row) -> Artifact:
        """Convert database row to Artifact object"""
        from ..models.artifact import ArtifactType, ArtifactStatus

        return Artifact(
            id=row['id'],
            name=row['name'],
            version=row['version'],
            artifact_type=ArtifactType(row['artifact_type']),
            platform=row['platform'],
            architecture=row['architecture'],
            size_bytes=row['size_bytes'],
            sha256=row['sha256'],
            sha512=row['sha512'],
            content_hash=row['sha256'],
            signature=row['signature'],
            signature_key_id=row['signature_key_id'],
            signature_valid=bool(row['signature_valid']),
            virus_scan_status=row['virus_scan_status'],
            virus_scan_date=datetime.fromisoformat(row['virus_scan_date']) if row['virus_scan_date'] else None,
            description=row['description'] or "",
            changelog=row['changelog'] or "",
            upload_date=datetime.fromisoformat(row['upload_date']) if row['upload_date'] else None,
            uploader=row['uploader'],
            uploader_ip=row['uploader_ip'],
            status=ArtifactStatus(row['status']),
            download_count=row['download_count'],
            last_download=datetime.fromisoformat(row['last_download']) if row['last_download'] else None,
            storage_path=row['storage_path'],
            compressed_path=row['compressed_path'],
            compression_ratio=row['compression_ratio']
        )

    def delete_artifact(self, artifact_id: int) -> bool:
        """Delete artifact and its file"""
        artifact = self.get_artifact(artifact_id)
        if not artifact:
            return False

        # Delete file
        if artifact.storage_path and os.path.exists(artifact.storage_path):
            os.remove(artifact.storage_path)

        # Delete compressed file if exists
        if artifact.compressed_path and os.path.exists(artifact.compressed_path):
            os.remove(artifact.compressed_path)

        # Delete from database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
        conn.commit()
        conn.close()

        self.logger.info(f"Deleted artifact {artifact.name} v{artifact.version}")
        return True

    def record_download(self, artifact_id: int, node_id: str, ip_address: str, success: bool = True):
        """Record a download event"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Insert download record
        cursor.execute("""
            INSERT INTO download_stats (artifact_id, node_id, download_date, ip_address, success)
            VALUES (?, ?, ?, ?, ?)
        """, (artifact_id, node_id, datetime.now().isoformat(), ip_address, int(success)))

        # Update artifact download count
        cursor.execute("""
            UPDATE artifacts
            SET download_count = download_count + 1, last_download = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), artifact_id))

        conn.commit()
        conn.close()

    def log_action(self, user: str, action: str, artifact_id: Optional[int], ip_address: str, details: str = ""):
        """Log an audit event"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO audit_log (timestamp, user, action, artifact_id, ip_address, details)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (datetime.now().isoformat(), user, action, artifact_id, ip_address, details))

        conn.commit()
        conn.close()

    def get_storage_stats(self) -> Dict[str, Any]:
        """Get storage statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        stats = {}

        # Total artifacts
        cursor.execute("SELECT COUNT(*) as count FROM artifacts")
        stats['total_artifacts'] = cursor.fetchone()[0]

        # Total size
        cursor.execute("SELECT SUM(size_bytes) as size FROM artifacts")
        stats['total_size_bytes'] = cursor.fetchone()[0] or 0

        # By type
        cursor.execute("""
            SELECT artifact_type, COUNT(*) as count, SUM(size_bytes) as size
            FROM artifacts GROUP BY artifact_type
        """)
        stats['by_type'] = {row[0]: {'count': row[1], 'size': row[2]} for row in cursor.fetchall()}

        # Total downloads
        cursor.execute("SELECT SUM(download_count) as downloads FROM artifacts")
        stats['total_downloads'] = cursor.fetchone()[0] or 0

        conn.close()
        return stats
