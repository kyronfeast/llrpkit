"""The llrpkit web dashboard (FastAPI + WebSocket, no build step).

Requires the ``dashboard`` extra: ``pip install "llrpkit[dashboard]"``.
"""

from llrpkit.dashboard.app import create_app, create_demo_app
from llrpkit.dashboard.registry import Broadcast, ManagedReader, ReaderRegistry

__all__ = ["Broadcast", "ManagedReader", "ReaderRegistry", "create_app", "create_demo_app"]
