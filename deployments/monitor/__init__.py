"""
DOLOS Deployment Monitor Package

Provides event-driven monitoring for Ansible deployments using structured JSON events
from the dolos_events callback plugin.

Components:
    - events: Event types and parsing
    - state: VM/Resource state machine
    - markdown_log: Structured Markdown log writer
"""

from .events import DeployEvent, parse_event
from .state import StateManager, VMState, VMStatus, ResourceState, ResourceStatus
from .markdown_log import MarkdownLogWriter

__all__ = [
    "DeployEvent",
    "parse_event",
    "StateManager",
    "VMState",
    "VMStatus",
    "ResourceState",
    "ResourceStatus",
    "MarkdownLogWriter",
]
