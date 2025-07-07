"""
Configuration settings for P2P Admin System
"""

import os
from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field, validator


class Settings(BaseSettings):
    """Основные настройки системы"""

    # Node settings
    node_host: str = Field(default="127.0.0.1", env="NODE_HOST")
    node_port: int = Field(default=8000, env="NODE_PORT")
    dht_port: int = Field(default=10000, env="DHT_PORT")
    node_name: Optional[str] = Field(default=None, env="NODE_NAME")

    # Bootstrap nodes
    bootstrap_nodes: List[str] = Field(default_factory=list, env="BOOTSTRAP_NODES")

    # Security
    auth_secret: str = Field(
        default="your-secret-key-change-in-production",
        env="AUTH_SECRET"
    )
    token_expire_minutes: int = Field(default=60, env="TOKEN_EXPIRE_MINUTES")
    ssl_cert: Optional[str] = Field(default=None, env="SSL_CERT")
    ssl_key: Optional[str] = Field(default=None, env="SSL_KEY")

    # API settings
    api_prefix: str = Field(default="/api/v1", env="API_PREFIX")
    cors_origins: List[str] = Field(default=["*"], env="CORS_ORIGINS")

    # WebSocket settings
    ws_heartbeat_interval: int = Field(default=30, env="WS_HEARTBEAT_INTERVAL")
    ws_connection_timeout: int = Field(default=60, env="WS_CONNECTION_TIMEOUT")

    # Task queue settings
    task_queue_size: int = Field(default=1000, env="TASK_QUEUE_SIZE")
    task_timeout: int = Field(default=300, env="TASK_TIMEOUT")
    max_concurrent_tasks: int = Field(default=10, env="MAX_CONCURRENT_TASKS")

    # DHT settings
    dht_replication_factor: int = Field(default=3, env="DHT_REPLICATION_FACTOR")
    dht_lookup_timeout: int = Field(default=10, env="DHT_LOOKUP_TIMEOUT")
    dht_store_timeout: int = Field(default=10, env="DHT_STORE_TIMEOUT")

    # P2P network settings
    peer_discovery_interval: int = Field(default=30, env="PEER_DISCOVERY_INTERVAL")
    peer_health_check_interval: int = Field(default=60, env="PEER_HEALTH_CHECK_INTERVAL")
    peer_timeout: int = Field(default=120, env="PEER_TIMEOUT")
    max_peers: int = Field(default=100, env="MAX_PEERS")

    # Service settings
    service_registry_ttl: int = Field(default=300, env="SERVICE_REGISTRY_TTL")
    service_health_check_interval: int = Field(default=30, env="SERVICE_HEALTH_CHECK_INTERVAL")

    # Monitoring settings
    metrics_enabled: bool = Field(default=True, env="METRICS_ENABLED")
    metrics_interval: int = Field(default=60, env="METRICS_INTERVAL")

    # Logging settings
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        env="LOG_FORMAT"
    )
    log_file: Optional[str] = Field(default=None, env="LOG_FILE")
    log_rotation: str = Field(default="1 day", env="LOG_ROTATION")
    log_retention: str = Field(default="7 days", env="LOG_RETENTION")

    # Storage settings
    data_dir: Path = Field(default=Path("./data"), env="DATA_DIR")
    cache_dir: Path = Field(default=Path("./cache"), env="CACHE_DIR")
    temp_dir: Path = Field(default=Path("./temp"), env="TEMP_DIR")

    # Performance settings
    worker_threads: int = Field(default=4, env="WORKER_THREADS")
    connection_pool_size: int = Field(default=100, env="CONNECTION_POOL_SIZE")
    request_timeout: int = Field(default=30, env="REQUEST_TIMEOUT")

    # Debug settings
    debug: bool = Field(default=False, env="DEBUG")
    profile_enabled: bool = Field(default=False, env="PROFILE_ENABLED")



    @validator("bootstrap_nodes", pre=True)
    def parse_bootstrap_nodes(cls, v):
        """Парсинг bootstrap узлов из строки"""
        if isinstance(v, str):
            return [n.strip() for n in v.split(",") if n.strip()]
        return v

    @validator("cors_origins", pre=True)
    def parse_cors_origins(cls, v):
        """Парсинг CORS origins из строки"""
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @validator("data_dir", "cache_dir", "temp_dir")
    def create_directories(cls, v):
        """Создание директорий если не существуют"""
        v = Path(v)
        v.mkdir(parents=True, exist_ok=True)
        return v

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "allow"


class AdminSettings(BaseSettings):
    """Настройки для Streamlit администратора"""

    # API connection
    api_url: str = Field(default="http://localhost:8000", env="API_URL")
    api_token: Optional[str] = Field(default=None, env="API_TOKEN")

    # UI settings
    page_title: str = Field(default="P2P Admin System", env="PAGE_TITLE")
    page_icon: str = Field(default="🌐", env="PAGE_ICON")
    layout: str = Field(default="wide", env="LAYOUT")

    # Update intervals (seconds)
    status_update_interval: int = Field(default=5, env="STATUS_UPDATE_INTERVAL")
    metrics_update_interval: int = Field(default=10, env="METRICS_UPDATE_INTERVAL")
    logs_update_interval: int = Field(default=3, env="LOGS_UPDATE_INTERVAL")

    # Display settings
    max_log_lines: int = Field(default=1000, env="MAX_LOG_LINES")
    max_tasks_display: int = Field(default=100, env="MAX_TASKS_DISPLAY")
    chart_history_minutes: int = Field(default=60, env="CHART_HISTORY_MINUTES")

    # Theme
    theme: str = Field(default="dark", env="THEME")
    primary_color: str = Field(default="#1f77b4", env="PRIMARY_COLOR")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


class ServiceSettings(BaseSettings):
    """Настройки для сервисов"""

    # Process Manager
    process_monitor_interval: int = Field(default=5, env="PROCESS_MONITOR_INTERVAL")
    process_restart_delay: int = Field(default=10, env="PROCESS_RESTART_DELAY")
    max_restart_attempts: int = Field(default=3, env="MAX_RESTART_ATTEMPTS")

    # File Manager
    file_watch_interval: int = Field(default=1, env="FILE_WATCH_INTERVAL")
    max_file_size: int = Field(default=100 * 1024 * 1024, env="MAX_FILE_SIZE")  # 100MB
    allowed_file_operations: List[str] = Field(
        default=["read", "write", "delete", "move", "copy"],
        env="ALLOWED_FILE_OPERATIONS"
    )

    # Network Manager
    network_scan_interval: int = Field(default=60, env="NETWORK_SCAN_INTERVAL")
    port_scan_timeout: int = Field(default=1, env="PORT_SCAN_TIMEOUT")

    # System Monitor
    system_metrics_interval: int = Field(default=5, env="SYSTEM_METRICS_INTERVAL")
    disk_usage_threshold: float = Field(default=90.0, env="DISK_USAGE_THRESHOLD")
    memory_usage_threshold: float = Field(default=85.0, env="MEMORY_USAGE_THRESHOLD")
    cpu_usage_threshold: float = Field(default=80.0, env="CPU_USAGE_THRESHOLD")

    @validator("allowed_file_operations", pre=True)
    def parse_file_operations(cls, v):
        """Парсинг разрешенных файловых операций"""
        if isinstance(v, str):
            return [op.strip() for op in v.split(",") if op.strip()]
        return v

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Singleton экземпляры
_settings = None
_admin_settings = None
_service_settings = None


def get_settings() -> Settings:
    """Получение основных настроек"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def get_admin_settings() -> AdminSettings:
    """Получение настроек администратора"""
    global _admin_settings
    if _admin_settings is None:
        _admin_settings = AdminSettings()
    return _admin_settings


def get_service_settings() -> ServiceSettings:
    """Получение настроек сервисов"""
    global _service_settings
    if _service_settings is None:
        _service_settings = ServiceSettings()
    return _service_settings


# Экспорт для удобства
settings = get_settings()
admin_settings = get_admin_settings()
service_settings = get_service_settings()

print(settings)