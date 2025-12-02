"""
System-level methods and utilities for P2P Core

This package contains core system functions that are used
during application initialization and configuration.
"""

from .plot_password import (
    generate_plot_file,
    generate_password_from_plot,
    get_plot_path
)

__all__ = [
    'generate_plot_file',
    'generate_password_from_plot',
    'get_plot_path'
]
