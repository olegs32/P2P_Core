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
