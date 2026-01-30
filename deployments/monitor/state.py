"""
VM and Resource state machine for DOLOS deployment monitoring.

State Transitions:
    PENDING → CREATING → PROVISIONED → INSTALLING → STAGE1 → REBOOTING → STAGE2 → COMPLETED
                  ↓           ↓           ↓           ↓          ↓          ↓
                FAILED      FAILED      FAILED     FAILED      FAILED     FAILED
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable

from .events import DeployEvent, EventType


class VMStatus(Enum):
    """VM deployment status."""

    PENDING = "pending"
    CREATING = "creating"
    PROVISIONED = "provisioned"
    INSTALLING = "installing"
    STAGE1 = "stage1"
    REBOOTING = "rebooting"
    STAGE2 = "stage2"
    COMPLETED = "completed"
    FAILED = "failed"


class ResourceStatus(Enum):
    """Resource deletion status for teardown."""

    PENDING = "pending"
    DELETING = "deleting"
    DELETED = "deleted"
    FAILED = "failed"


@dataclass
class VMState:
    """State for a VM being deployed."""

    name: str
    behavior: str
    flavor: str
    status: VMStatus = VMStatus.PENDING
    ip_address: str = ""
    error_msg: str = ""
    current_task: str = ""

    # Timestamps
    provision_start: float | None = None
    provision_end: float | None = None
    install_start: float | None = None
    install_end: float | None = None

    @property
    def provision_time(self) -> str:
        """Get formatted provision completion time."""
        if self.provision_end:
            return datetime.fromtimestamp(self.provision_end).strftime("%H:%M:%S")
        return ""

    @property
    def install_time(self) -> str:
        """Get formatted install completion time."""
        if self.install_end:
            return datetime.fromtimestamp(self.install_end).strftime("%H:%M:%S")
        return ""

    @property
    def provision_duration(self) -> float | None:
        """Get provision duration in seconds."""
        if self.provision_start and self.provision_end:
            return self.provision_end - self.provision_start
        return None

    @property
    def install_duration(self) -> float | None:
        """Get install duration in seconds."""
        if self.install_start and self.install_end:
            return self.install_end - self.install_start
        return None


@dataclass
class ResourceState:
    """State for a resource being deleted (server or volume)."""

    id: str
    name: str
    resource_type: str  # "server" or "volume"
    status: ResourceStatus = ResourceStatus.PENDING
    delete_start: float | None = None
    delete_end: float | None = None
    error_msg: str = ""

    @property
    def delete_time(self) -> str:
        """Get formatted deletion time."""
        if self.delete_end:
            return datetime.fromtimestamp(self.delete_end).strftime("%H:%M:%S")
        return ""


class StateManager:
    """Manages VM and resource states based on deployment events."""

    def __init__(
        self,
        vms: dict[str, VMState] | None = None,
        on_state_change: Callable[[str, VMStatus], None] | None = None,
    ):
        """Initialize state manager.

        Args:
            vms: Pre-populated VM states (from config)
            on_state_change: Callback for state changes
        """
        self.vms: dict[str, VMState] = vms or {}
        self.servers: dict[str, ResourceState] = {}
        self.volumes: dict[str, ResourceState] = {}
        self._on_state_change = on_state_change
        self._current_phase = "idle"

    @property
    def phase(self) -> str:
        """Current deployment phase."""
        return self._current_phase

    @phase.setter
    def phase(self, value: str):
        """Set current deployment phase."""
        self._current_phase = value

    def process_event(self, event: DeployEvent):
        """Process a deployment event and update state.

        Args:
            event: Deployment event from callback plugin
        """
        handler = getattr(self, f"_handle_{event.type}", None)
        if handler:
            handler(event)

    def _set_vm_status(self, vm_name: str, status: VMStatus):
        """Set VM status and notify callback."""
        if vm_name in self.vms:
            old_status = self.vms[vm_name].status
            self.vms[vm_name].status = status
            if self._on_state_change and old_status != status:
                self._on_state_change(vm_name, status)

    # ─────────────────────────────────────────────────────────────────
    # Playbook lifecycle handlers
    # ─────────────────────────────────────────────────────────────────

    def _handle_playbook_start(self, event: DeployEvent):
        """Handle playbook start."""
        playbook = event.data.get("playbook", "")
        if "provision" in playbook:
            self._current_phase = "provisioning"
            # Mark all pending VMs as creating
            for vm in self.vms.values():
                if vm.status == VMStatus.PENDING:
                    vm.status = VMStatus.CREATING
                    vm.provision_start = event.unix_ts
        elif "install" in playbook:
            self._current_phase = "installing"
            # Mark provisioned VMs as installing
            for vm in self.vms.values():
                if vm.status in (VMStatus.PROVISIONED, VMStatus.PENDING):
                    vm.status = VMStatus.INSTALLING
                    vm.install_start = event.unix_ts
        elif "teardown" in playbook:
            self._current_phase = "teardown"

    def _handle_playbook_end(self, event: DeployEvent):
        """Handle playbook end."""
        pass  # Recap events provide final status

    # ─────────────────────────────────────────────────────────────────
    # VM provisioning handlers
    # ─────────────────────────────────────────────────────────────────

    def _handle_vm_creating(self, event: DeployEvent):
        """Handle VM creation started."""
        vm_name = event.vm_name
        if vm_name and vm_name in self.vms:
            self.vms[vm_name].status = VMStatus.CREATING
            if not self.vms[vm_name].provision_start:
                self.vms[vm_name].provision_start = event.unix_ts

    def _handle_vm_exists(self, event: DeployEvent):
        """Handle VM already exists."""
        vm_name = event.vm_name
        if vm_name and vm_name in self.vms:
            self.vms[vm_name].status = VMStatus.CREATING
            if not self.vms[vm_name].provision_start:
                self.vms[vm_name].provision_start = event.unix_ts

    def _handle_vm_provisioned(self, event: DeployEvent):
        """Handle VM reached ACTIVE state."""
        vm_name = event.vm_name
        if vm_name and vm_name in self.vms:
            self._set_vm_status(vm_name, VMStatus.PROVISIONED)
            self.vms[vm_name].provision_end = event.unix_ts

    def _handle_vm_active(self, event: DeployEvent):
        """Handle VM active (from host-based event)."""
        # Try to match by host name
        host = event.host
        if host and host in self.vms:
            self._set_vm_status(host, VMStatus.PROVISIONED)
            self.vms[host].provision_end = event.unix_ts

    def _handle_vm_ip(self, event: DeployEvent):
        """Handle VM IP address assignment."""
        vm_name = event.vm_name or event.host
        ip = event.data.get("ip", "")
        if vm_name and vm_name in self.vms and ip:
            self.vms[vm_name].ip_address = ip

    def _handle_vm_failed(self, event: DeployEvent):
        """Handle VM creation failure."""
        vm_name = event.vm_name
        if vm_name and vm_name in self.vms:
            self._set_vm_status(vm_name, VMStatus.FAILED)
            self.vms[vm_name].error_msg = event.error or "VM creation failed"

    # ─────────────────────────────────────────────────────────────────
    # Installation handlers
    # ─────────────────────────────────────────────────────────────────

    def _handle_install_stage1(self, event: DeployEvent):
        """Handle Stage 1 installation started."""
        for vm in self.vms.values():
            if vm.status == VMStatus.INSTALLING:
                vm.status = VMStatus.STAGE1
                vm.current_task = "Stage 1: System deps"

    def _handle_install_stage2(self, event: DeployEvent):
        """Handle Stage 2 installation started."""
        for vm in self.vms.values():
            if vm.status in (VMStatus.STAGE1, VMStatus.REBOOTING, VMStatus.INSTALLING):
                vm.status = VMStatus.STAGE2
                vm.current_task = "Stage 2: SUP install"

    def _handle_reboot_start(self, event: DeployEvent):
        """Handle reboot started."""
        for vm in self.vms.values():
            if vm.status == VMStatus.STAGE1:
                vm.status = VMStatus.REBOOTING
                vm.current_task = "Rebooting"

    def _handle_reboot_complete(self, event: DeployEvent):
        """Handle reboot completed."""
        host = event.host
        if host and host in self.vms:
            if self.vms[host].status == VMStatus.REBOOTING:
                self.vms[host].status = VMStatus.STAGE1  # Goes back to stage1 until stage2 starts
                self.vms[host].current_task = "Reboot complete"

    def _handle_install_failed(self, event: DeployEvent):
        """Handle installation failure."""
        host = event.host
        stage = event.data.get("stage", "?")
        if host and host in self.vms:
            self._set_vm_status(host, VMStatus.FAILED)
            self.vms[host].error_msg = f"Stage {stage}: {event.error or 'Install failed'}"

    # ─────────────────────────────────────────────────────────────────
    # Task result handlers
    # ─────────────────────────────────────────────────────────────────

    def _handle_task_start(self, event: DeployEvent):
        """Handle task start - update current task for active VMs."""
        task_name = event.data.get("name", "")[:40]
        for vm in self.vms.values():
            if vm.status in (
                VMStatus.CREATING,
                VMStatus.INSTALLING,
                VMStatus.STAGE1,
                VMStatus.REBOOTING,
                VMStatus.STAGE2,
            ):
                vm.current_task = task_name

    def _handle_task_ok(self, event: DeployEvent):
        """Handle successful task completion."""
        host = event.host
        task = event.data.get("task", "")

        # Update VM current task
        if host and host in self.vms:
            self.vms[host].current_task = task[:40]

    def _handle_task_failed(self, event: DeployEvent):
        """Handle task failure."""
        host = event.host
        if event.is_ignored:
            return

        if host and host in self.vms:
            self._set_vm_status(host, VMStatus.FAILED)
            self.vms[host].error_msg = f"{event.task}: {event.error or 'Task failed'}"

    def _handle_host_unreachable(self, event: DeployEvent):
        """Handle unreachable host."""
        host = event.host
        if host and host in self.vms:
            self._set_vm_status(host, VMStatus.FAILED)
            self.vms[host].error_msg = f"Host unreachable: {event.error or 'SSH failed'}"

    def _handle_recap(self, event: DeployEvent):
        """Handle final recap statistics."""
        host = event.host
        failures = event.data.get("failures", 0)
        unreachable = event.data.get("unreachable", 0)

        if host and host in self.vms:
            vm = self.vms[host]
            if failures > 0 or unreachable > 0:
                if vm.status != VMStatus.FAILED:
                    self._set_vm_status(host, VMStatus.FAILED)
                    if not vm.error_msg:
                        vm.error_msg = f"Recap: {failures} failures, {unreachable} unreachable"
            else:
                # Success - set appropriate final state
                if self._current_phase == "installing":
                    if vm.status not in (VMStatus.FAILED, VMStatus.COMPLETED):
                        self._set_vm_status(host, VMStatus.COMPLETED)
                        vm.install_end = event.unix_ts
                elif self._current_phase == "provisioning":
                    if vm.status not in (VMStatus.FAILED, VMStatus.PROVISIONED, VMStatus.COMPLETED):
                        self._set_vm_status(host, VMStatus.PROVISIONED)
                        vm.provision_end = event.unix_ts

    # ─────────────────────────────────────────────────────────────────
    # Teardown handlers
    # ─────────────────────────────────────────────────────────────────

    def _handle_discovery_servers(self, event: DeployEvent):
        """Handle server discovery during teardown."""
        servers = event.data.get("servers", [])
        for s in servers:
            server_id = s.get("id", "")
            server_name = s.get("name", server_id[:8])
            if server_id and server_id not in self.servers:
                self.servers[server_id] = ResourceState(
                    id=server_id,
                    name=server_name,
                    resource_type="server",
                )

    def _handle_discovery_volumes(self, event: DeployEvent):
        """Handle volume discovery during teardown."""
        volumes = event.data.get("volumes", [])
        for vol_id in volumes:
            if vol_id and vol_id not in self.volumes:
                # Don't count server IDs as volumes
                if vol_id not in self.servers:
                    self.volumes[vol_id] = ResourceState(
                        id=vol_id,
                        name=vol_id[:8],
                        resource_type="volume",
                    )

    def _handle_resource_deleted(self, event: DeployEvent):
        """Handle resource deletion confirmation."""
        name = event.data.get("name", "")
        rtype = event.data.get("type", "")

        if rtype == "server":
            # Find by name
            for s in self.servers.values():
                if s.name == name or s.id.startswith(name):
                    s.status = ResourceStatus.DELETED
                    s.delete_end = event.unix_ts
                    break
        elif rtype == "volume":
            # Find by ID prefix
            for v in self.volumes.values():
                if v.id.startswith(name) or v.name == name:
                    v.status = ResourceStatus.DELETED
                    v.delete_end = event.unix_ts
                    break

    def _handle_resource_failed(self, event: DeployEvent):
        """Handle resource deletion failure."""
        name = event.data.get("name", "")
        rtype = event.data.get("type", "")
        error = event.data.get("error", "")

        if event.is_ignored:
            return

        resources = self.servers if rtype == "server" else self.volumes
        for r in resources.values():
            if r.name == name or r.id.startswith(name):
                r.status = ResourceStatus.FAILED
                r.error_msg = error
                break

    # ─────────────────────────────────────────────────────────────────
    # Helper methods
    # ─────────────────────────────────────────────────────────────────

    def get_counts(self) -> dict[str, int]:
        """Get VM status counts."""
        counts = {
            "total": len(self.vms),
            "pending": 0,
            "creating": 0,
            "provisioned": 0,
            "installing": 0,
            "completed": 0,
            "failed": 0,
        }

        for vm in self.vms.values():
            if vm.status == VMStatus.PENDING:
                counts["pending"] += 1
            elif vm.status == VMStatus.CREATING:
                counts["creating"] += 1
            elif vm.status == VMStatus.PROVISIONED:
                counts["provisioned"] += 1
            elif vm.status in (VMStatus.INSTALLING, VMStatus.STAGE1, VMStatus.REBOOTING, VMStatus.STAGE2):
                counts["installing"] += 1
            elif vm.status == VMStatus.COMPLETED:
                counts["completed"] += 1
            elif vm.status == VMStatus.FAILED:
                counts["failed"] += 1

        return counts

    def get_resource_counts(self) -> dict[str, dict[str, int]]:
        """Get resource deletion counts."""
        return {
            "servers": {
                "total": len(self.servers),
                "pending": sum(1 for s in self.servers.values() if s.status == ResourceStatus.PENDING),
                "deleting": sum(1 for s in self.servers.values() if s.status == ResourceStatus.DELETING),
                "deleted": sum(1 for s in self.servers.values() if s.status == ResourceStatus.DELETED),
                "failed": sum(1 for s in self.servers.values() if s.status == ResourceStatus.FAILED),
            },
            "volumes": {
                "total": len(self.volumes),
                "pending": sum(1 for v in self.volumes.values() if v.status == ResourceStatus.PENDING),
                "deleting": sum(1 for v in self.volumes.values() if v.status == ResourceStatus.DELETING),
                "deleted": sum(1 for v in self.volumes.values() if v.status == ResourceStatus.DELETED),
                "failed": sum(1 for v in self.volumes.values() if v.status == ResourceStatus.FAILED),
            },
        }
