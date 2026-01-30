"""
Event types and parsing for DOLOS deployment monitoring.

Parses JSON events emitted by the dolos_events Ansible callback plugin.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class DeployEvent:
    """Base class for deployment events."""

    type: str
    timestamp: datetime
    unix_ts: float
    task: str = ""
    play: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def host(self) -> str | None:
        """Get host from data if present."""
        return self.data.get("host")

    @property
    def vm_name(self) -> str | None:
        """Get VM name from data if present."""
        return self.data.get("vm_name")

    @property
    def error(self) -> str | None:
        """Get error message from data if present."""
        return self.data.get("error")

    @property
    def is_failure(self) -> bool:
        """Check if this event indicates a failure."""
        return self.type in (
            "task_failed",
            "host_unreachable",
            "vm_failed",
            "install_failed",
            "resource_failed",
        )

    @property
    def is_ignored(self) -> bool:
        """Check if this failure was ignored."""
        return self.data.get("ignored", False)


def parse_event(line: str) -> DeployEvent | None:
    """Parse a JSON line into a DeployEvent.

    Args:
        line: JSON string from event file

    Returns:
        DeployEvent if parsing succeeds, None otherwise
    """
    line = line.strip()
    if not line:
        return None

    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None

    event_type = obj.get("type", "unknown")
    timestamp_str = obj.get("timestamp", "")
    unix_ts = obj.get("unix_ts", 0.0)

    try:
        timestamp = datetime.fromisoformat(timestamp_str)
    except (ValueError, TypeError):
        timestamp = datetime.now()

    return DeployEvent(
        type=event_type,
        timestamp=timestamp,
        unix_ts=unix_ts,
        task=obj.get("task", ""),
        play=obj.get("play", ""),
        data=obj.get("data", {}),
    )


# Event type constants for easier matching
class EventType:
    """Constants for event types."""

    # Playbook lifecycle
    PLAYBOOK_START = "playbook_start"
    PLAYBOOK_END = "playbook_end"
    PLAY_START = "play_start"
    TASK_START = "task_start"

    # Task results
    TASK_OK = "task_ok"
    TASK_FAILED = "task_failed"
    TASK_SKIPPED = "task_skipped"
    HOST_UNREACHABLE = "host_unreachable"

    # VM provisioning
    VM_CREATING = "vm_creating"
    VM_EXISTS = "vm_exists"
    VM_PROVISIONED = "vm_provisioned"
    VM_ACTIVE = "vm_active"
    VM_IP = "vm_ip"
    VM_FAILED = "vm_failed"

    # Installation
    INSTALL_STAGE1 = "install_stage1"
    INSTALL_STAGE2 = "install_stage2"
    REBOOT_START = "reboot_start"
    REBOOT_COMPLETE = "reboot_complete"
    INSTALL_FAILED = "install_failed"

    # Teardown
    DISCOVERY_SERVERS = "discovery_servers"
    DISCOVERY_VOLUMES = "discovery_volumes"
    RESOURCE_DELETED = "resource_deleted"
    RESOURCE_FAILED = "resource_failed"

    # Statistics
    RECAP = "recap"
