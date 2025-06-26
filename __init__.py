# core/__init__.py
"""
Core P2P modules
"""

from .dht import AsyncDHT, DHTNode
from .p2p_node import P2PNode, PeerInfo
from .rpc_proxy import RPCProxy, RPCManager, create_service_proxy
from .auth import P2PAuth, get_current_node, get_optional_node, require_trusted_node

__all__ = [
    'AsyncDHT',
    'DHTNode',
    'P2PNode',
    'PeerInfo',
    'RPCProxy',
    'RPCManager',
    'create_service_proxy',
    'P2PAuth',
    'get_current_node',
    'get_optional_node',
    'require_trusted_node'
]

# api/__init__.py
"""
FastAPI application and routes
"""

from .main import create_app
from .routes import create_routes
from .websockets import WebSocketManager, WebSocketConnection, WebSocketMessage

__all__ = [
    'create_app',
    'create_routes',
    'WebSocketManager',
    'WebSocketConnection',
    'WebSocketMessage'
]

# admin/__init__.py
"""
Streamlit admin interface
"""

__version__ = "1.0.0"

# services/__init__.py
"""
P2P Admin System Services
"""

from .process_manager import ProcessManagerService
from .file_manager import FileManagerService
from .network_manager import NetworkManagerService
from .system_monitor import SystemMonitorService

__all__ = [
    'ProcessManagerService',
    'FileManagerService',
    'NetworkManagerService',
    'SystemMonitorService'
]

# config/__init__.py
"""
Configuration management
"""

from .settings import (
    Settings,
    AdminSettings,
    ServiceSettings,
    get_settings,
    get_admin_settings,
    get_service_settings,
    settings,
    admin_settings,
    service_settings
)

__all__ = [
    'Settings',
    'AdminSettings',
    'ServiceSettings',
    'get_settings',
    'get_admin_settings',
    'get_service_settings',
    'settings',
    'admin_settings',
    'service_settings'
]