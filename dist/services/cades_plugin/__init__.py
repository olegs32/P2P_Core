"""
CAdES Plugin Service

Self-hosted digital signature service for P2P Core
"""

__version__ = "1.0.0"
__author__ = "P2P Core Team"

from .main import CAdESPluginService, Run

__all__ = ['CAdESPluginService', 'Run']
