"""Minimal configuration module."""

from .loader import load_config
from .settings import Settings

__all__ = [
    "Settings",
    "load_config",
]