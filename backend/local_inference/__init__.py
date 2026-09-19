"""Optional, isolated local inference. Importing this package never loads weights."""

from .server import Settings, create_app

__all__ = ["Settings", "create_app"]
