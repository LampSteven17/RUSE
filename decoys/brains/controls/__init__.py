"""Controls-mode runner — brain-agnostic deterministic floor.

Activated when behavior.json _metadata.mode == "controls". Bypasses the
brain-specific workflow loaders and runs a fixed search-and-fetch loop
straight from the CONTROLS shape contract.
"""
from .runner import run_controls

__all__ = ["run_controls"]
