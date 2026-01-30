"""
Markdown log writer for DOLOS deployment monitoring.

Produces structured Markdown logs that are easy for Claude to read and debug.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TextIO

from .events import DeployEvent
from .state import StateManager, VMState, VMStatus, ResourceState, ResourceStatus


class MarkdownLogWriter:
    """Writes structured Markdown deployment logs."""

    def __init__(self, log_path: Path, deployment_name: str):
        """Initialize the Markdown log writer.

        Args:
            log_path: Path to write the log file
            deployment_name: Name of the deployment
        """
        self.log_path = log_path
        self.deployment_name = deployment_name
        self.start_time = datetime.now()
        self._file: TextIO | None = None
        self._timeline_events: list[tuple[datetime, str, str, str]] = []
        self._errors: list[dict] = []

    def open(self):
        """Open the log file and write header."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.log_path, "w")
        self._write_header()

    def close(self, state_manager: StateManager):
        """Close the log file after writing summary."""
        if self._file:
            self._write_timeline()
            self._write_errors()
            self._write_final_summary(state_manager)
            self._file.close()
            self._file = None

    def _write(self, text: str):
        """Write text to log file."""
        if self._file:
            self._file.write(text)
            self._file.flush()

    def _write_header(self):
        """Write log header."""
        self._write(f"""# DOLOS Deployment Log

## Summary
- **Deployment**: {self.deployment_name}
- **Started**: {self.start_time.isoformat()}
- **Log File**: {self.log_path.name}

---

""")

    def log_event(self, event: DeployEvent, state_manager: StateManager):
        """Log a deployment event.

        Args:
            event: Deployment event
            state_manager: Current state manager
        """
        ts = event.timestamp.strftime("%H:%M:%S")

        # Log significant events to timeline
        if event.type == "playbook_start":
            playbook = event.data.get("playbook", "unknown")
            self._timeline_events.append((event.timestamp, "INFO", "Playbook", f"Started: {playbook}"))

        elif event.type == "vm_provisioned":
            vm_name = event.vm_name or "unknown"
            vm = state_manager.vms.get(vm_name)
            ip = vm.ip_address if vm else ""
            status = f"provisioned{f' (IP: {ip})' if ip else ''}"
            self._timeline_events.append((event.timestamp, "OK", vm_name, status))

        elif event.type == "vm_ip":
            vm_name = event.vm_name or event.host or "unknown"
            ip = event.data.get("ip", "")
            self._timeline_events.append((event.timestamp, "OK", vm_name, f"IP assigned: {ip}"))

        elif event.type in ("vm_failed", "install_failed"):
            vm_name = event.vm_name or event.host or "unknown"
            error = event.error or "Unknown error"
            self._timeline_events.append((event.timestamp, "FAIL", vm_name, error[:60]))

            # Record detailed error
            self._errors.append({
                "timestamp": event.timestamp,
                "vm_name": vm_name,
                "task": event.task,
                "error": error,
                "type": event.type,
            })

        elif event.type == "task_failed" and not event.is_ignored:
            host = event.host or "unknown"
            error = event.error or "Task failed"
            self._timeline_events.append((event.timestamp, "FAIL", host, f"{event.task[:30]}: {error[:30]}"))

            self._errors.append({
                "timestamp": event.timestamp,
                "vm_name": host,
                "task": event.task,
                "error": error,
                "type": "task_failed",
                "stderr": event.data.get("stderr", ""),
            })

        elif event.type == "host_unreachable":
            host = event.host or "unknown"
            self._timeline_events.append((event.timestamp, "FAIL", host, "Host unreachable"))

            self._errors.append({
                "timestamp": event.timestamp,
                "vm_name": host,
                "task": event.task,
                "error": event.error or "SSH connection failed",
                "type": "unreachable",
            })

        elif event.type == "recap":
            host = event.host or "unknown"
            failures = event.data.get("failures", 0)
            unreachable = event.data.get("unreachable", 0)
            ok = event.data.get("ok", 0)

            if failures > 0 or unreachable > 0:
                self._timeline_events.append(
                    (event.timestamp, "FAIL", host, f"Recap: {failures} failures, {unreachable} unreachable")
                )
            else:
                self._timeline_events.append(
                    (event.timestamp, "OK", host, f"Recap: ok={ok}, failures={failures}")
                )

        elif event.type == "resource_deleted":
            name = event.data.get("name", "unknown")
            rtype = event.data.get("type", "resource")
            self._timeline_events.append((event.timestamp, "OK", name, f"{rtype} deleted"))

        elif event.type == "playbook_end":
            playbook = event.data.get("playbook", "unknown")
            elapsed = event.data.get("elapsed", 0)
            self._timeline_events.append(
                (event.timestamp, "INFO", "Playbook", f"Completed: {playbook} ({elapsed:.1f}s)")
            )

    def _write_timeline(self):
        """Write timeline section."""
        if not self._timeline_events:
            return

        self._write("## Timeline\n\n")

        current_date = None
        for ts, status, target, message in sorted(self._timeline_events):
            date_str = ts.strftime("%Y-%m-%d")
            if date_str != current_date:
                current_date = date_str
                self._write(f"### {date_str}\n\n")

            time_str = ts.strftime("%H:%M:%S")
            status_marker = {
                "OK": "[OK]",
                "FAIL": "[FAIL]",
                "INFO": "[INFO]",
            }.get(status, f"[{status}]")

            self._write(f"- `{time_str}` {status_marker} **{target}**: {message}\n")

        self._write("\n")

    def _write_errors(self):
        """Write detailed error sections."""
        if not self._errors:
            return

        self._write("## Errors\n\n")

        for i, err in enumerate(self._errors, 1):
            ts = err["timestamp"].strftime("%H:%M:%S")
            vm = err["vm_name"]
            task = err["task"]
            error_msg = err["error"]
            err_type = err["type"]

            self._write(f"### Error {i}: {vm} at {ts}\n\n")
            self._write(f"**Task**: {task}\n\n")
            self._write(f"**Type**: {err_type}\n\n")
            self._write("**Error Message**:\n")
            self._write(f"```\n{error_msg}\n```\n\n")

            # Add stderr if present
            stderr = err.get("stderr", "")
            if stderr:
                self._write("**Stderr**:\n")
                self._write(f"```\n{stderr[:500]}\n```\n\n")

            # Add suggested fix based on error type
            fix = self._suggest_fix(err)
            if fix:
                self._write(f"**Suggested Fix**: {fix}\n\n")

            self._write("---\n\n")

    def _suggest_fix(self, error: dict) -> str:
        """Suggest a fix based on error type and message."""
        err_type = error["type"]
        error_msg = error.get("error", "").lower()
        task = error.get("task", "").lower()

        if err_type == "unreachable":
            return "Check if the VM is running and SSH is accessible. Verify security groups allow SSH (port 22)."

        if "timeout" in error_msg:
            return "Increase timeout value or check for resource constraints on OpenStack."

        if "quota" in error_msg or "exceeded" in error_msg:
            return "Check OpenStack quota limits. Consider reducing deployment size or requesting quota increase."

        if "no valid host" in error_msg:
            return "OpenStack cannot schedule the VM. Check compute node capacity and flavor availability."

        if "stage 1" in task or "stage1" in err_type:
            return "Stage 1 (system deps/drivers) failed. Check cloud-init logs on VM: /var/log/cloud-init-output.log"

        if "stage 2" in task or "stage2" in err_type:
            return "Stage 2 (SUP installation) failed. Check INSTALL_SUP.sh logs on VM or verify Ollama availability."

        if "nvidia" in error_msg or "cuda" in error_msg:
            return "NVIDIA driver installation failed. Verify GPU flavor has actual GPU and check driver compatibility."

        if "apt" in error_msg or "package" in error_msg:
            return "Package installation failed. Check network connectivity and apt cache: apt update"

        if "git" in error_msg or "clone" in error_msg:
            return "Git clone failed. Check network access to GitHub and repository availability."

        return ""

    def _write_final_summary(self, state_manager: StateManager):
        """Write final summary table."""
        self._write("## Final Summary\n\n")

        # Determine overall status
        counts = state_manager.get_counts()
        total = counts["total"]
        completed = counts["completed"]
        failed = counts["failed"]
        provisioned = counts["provisioned"]

        if failed > 0:
            overall_status = "COMPLETED WITH ERRORS"
        elif completed == total:
            overall_status = "SUCCESS"
        elif provisioned == total:
            overall_status = "PROVISIONED (Install pending)"
        else:
            overall_status = "INCOMPLETE"

        elapsed = (datetime.now() - self.start_time).total_seconds()
        elapsed_str = f"{int(elapsed // 3600):02d}:{int((elapsed % 3600) // 60):02d}:{int(elapsed % 60):02d}"

        self._write(f"- **Status**: {overall_status}\n")
        self._write(f"- **Total Time**: {elapsed_str}\n")
        self._write(f"- **VMs**: {completed}/{total} completed, {failed} failed\n\n")

        # VM table
        if state_manager.vms:
            self._write("| VM Name | Behavior | Status | Provision | Install | Error |\n")
            self._write("|---------|----------|--------|-----------|---------|-------|\n")

            for vm_name in sorted(state_manager.vms.keys()):
                vm = state_manager.vms[vm_name]
                status = "[OK]" if vm.status == VMStatus.COMPLETED else (
                    "[FAIL]" if vm.status == VMStatus.FAILED else f"[{vm.status.value.upper()}]"
                )
                prov_time = vm.provision_time or "-"
                inst_time = vm.install_time or "-"
                error = vm.error_msg[:30] if vm.error_msg else ""

                self._write(f"| {vm_name} | {vm.behavior} | {status} | {prov_time} | {inst_time} | {error} |\n")

            self._write("\n")

        # Resource summary for teardown
        if state_manager.servers or state_manager.volumes:
            rcounts = state_manager.get_resource_counts()

            self._write("### Teardown Resources\n\n")
            self._write(f"- **Servers**: {rcounts['servers']['deleted']}/{rcounts['servers']['total']} deleted\n")
            self._write(f"- **Volumes**: {rcounts['volumes']['deleted']}/{rcounts['volumes']['total']} deleted\n")

            if rcounts["servers"]["failed"] > 0 or rcounts["volumes"]["failed"] > 0:
                self._write(f"- **Failed**: {rcounts['servers']['failed']} servers, {rcounts['volumes']['failed']} volumes\n")

            self._write("\n")


def create_log_path(logs_dir: Path, deployment_name: str) -> Path:
    """Create a unique log file path.

    Args:
        logs_dir: Directory for log files
        deployment_name: Name of the deployment

    Returns:
        Path for the new log file
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return logs_dir / f"deploy-{deployment_name}-{timestamp}.md"
