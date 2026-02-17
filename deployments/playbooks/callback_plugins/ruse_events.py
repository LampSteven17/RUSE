"""
RUSE Events Callback Plugin

Ansible callback plugin that emits structured JSON events for deployment monitoring.
Events are written to the file specified by RUSE_EVENT_FILE environment variable.

Event Types:
    - playbook_start/end: Lifecycle boundaries
    - vm_creating/created/failed: Provision phase
    - install_stage1/reboot/stage2/complete/failed: Install phase
    - discovery_servers/volumes: Teardown discovery
    - resource_deleted/failed: Teardown progress
    - recap: Final per-host statistics

Usage:
    RUSE_EVENT_FILE=/tmp/events.jsonl ansible-playbook playbook.yaml
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Any

from ansible.plugins.callback import CallbackBase

DOCUMENTATION = """
    name: ruse_events
    type: notification
    short_description: Emit JSON events for RUSE deployment monitoring
    description:
        - Writes structured JSON events to a file for TUI consumption
        - Enable with callbacks_enabled = ruse_events in ansible.cfg
    requirements:
        - Set RUSE_EVENT_FILE environment variable to output path
"""


class CallbackModule(CallbackBase):
    """Ansible callback plugin for RUSE deployment events."""

    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "ruse_events"
    CALLBACK_NEEDS_WHITELIST = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.event_file = os.environ.get("RUSE_EVENT_FILE")
        self._file_handle = None
        self._current_task = ""
        self._current_play = ""
        self._playbook_name = ""
        self._start_time = None

        # Patterns for extracting info from task output
        self._vm_created_re = re.compile(r"CREATED:\s*(sup-[^\s]+)")
        self._vm_exists_re = re.compile(r"EXISTS:\s*(sup-[^\s]+)")
        self._vm_active_re = re.compile(r"ACTIVE")
        self._vm_deleted_re = re.compile(r"DELETED:\s*(sup-[^\s)]+)")
        self._ip_re = re.compile(r"(\d+\.\d+\.\d+\.\d+)")
        self._server_list_re = re.compile(r"([a-f0-9-]{36})\s+(sup-[^\s\\]+)")
        self._volume_id_re = re.compile(r"\b([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\b")

    def _open_file(self):
        """Open the event file for writing."""
        if self.event_file and not self._file_handle:
            try:
                self._file_handle = open(self.event_file, "a", buffering=1)  # Line buffered
            except Exception as e:
                self._display.warning(f"RUSE: Cannot open event file {self.event_file}: {e}")

    def _emit(self, event_type: str, data: dict[str, Any] | None = None):
        """Emit a JSON event to the event file."""
        if not self._file_handle:
            return

        event = {
            "type": event_type,
            "timestamp": datetime.now().isoformat(),
            "unix_ts": time.time(),
            "task": self._current_task,
            "play": self._current_play,
        }
        if data:
            event["data"] = data

        try:
            self._file_handle.write(json.dumps(event) + "\n")
            self._file_handle.flush()
        except Exception:
            pass

    def v2_playbook_on_start(self, playbook):
        """Called when playbook starts."""
        self._open_file()
        self._start_time = time.time()
        self._playbook_name = os.path.basename(playbook._file_name)
        self._emit("playbook_start", {
            "playbook": self._playbook_name,
        })

    def v2_playbook_on_play_start(self, play):
        """Called when a play starts."""
        self._current_play = play.get_name()
        self._emit("play_start", {
            "name": self._current_play,
        })

        # Detect feedback distribution play
        if "feedback" in self._current_play.lower():
            self._emit("install_feedback")

    def v2_playbook_on_task_start(self, task, is_conditional):
        """Called when a task starts."""
        self._current_task = task.get_name()
        self._emit("task_start", {
            "name": self._current_task,
        })

        # Detect install phases from task names (global — fallback for
        # v2_runner_on_start which provides per-host granularity)
        task_name = self._current_task.lower()
        if "cloud-init" in task_name:
            self._emit("install_preparing")
        elif "install_sup.sh stage 1" in task_name:
            self._emit("install_stage1")
        elif "install_sup.sh stage 2" in task_name:
            self._emit("install_stage2")
        elif "reboot" in task_name:
            self._emit("reboot_start")

    def v2_runner_on_ok(self, result):
        """Called when a task succeeds."""
        host = result._host.get_name()
        task = result._task.get_name()
        stdout = result._result.get("stdout", "")
        changed = result._result.get("changed", False)

        self._emit("task_ok", {
            "host": host,
            "task": task,
            "changed": changed,
        })

        # Parse stdout for specific events
        self._parse_stdout(host, task, stdout)

        # Check for reboot completion
        if "reboot" in task.lower() and result._result.get("rebooted", False):
            self._emit("reboot_complete", {"host": host})

        # Detect per-host Stage 2 completion (async task finished)
        task_lower = task.lower()
        if "install_sup" in task_lower and "stage 2" in task_lower:
            self._emit("install_complete", {"host": host})

    def v2_runner_on_failed(self, result, ignore_errors=False):
        """Called when a task fails."""
        host = result._host.get_name()
        task = result._task.get_name()
        msg = result._result.get("msg", str(result._result))
        stderr = result._result.get("stderr", "")

        self._emit("task_failed", {
            "host": host,
            "task": task,
            "error": msg,
            "stderr": stderr,
            "ignored": ignore_errors,
        })

        # Emit specific failure events
        task_lower = task.lower()
        if "create" in task_lower and "vm" in task_lower:
            # Extract VM name from loop item if available
            vm_name = self._get_loop_item_vm(result)
            if vm_name:
                self._emit("vm_failed", {
                    "host": host,
                    "vm_name": vm_name,
                    "error": msg,
                })
        elif "install_sup" in task_lower:
            stage = "1" if "stage 1" in task_lower else "2"
            self._emit("install_failed", {
                "host": host,
                "stage": stage,
                "error": msg,
            })

    def v2_runner_on_unreachable(self, result):
        """Called when a host is unreachable."""
        host = result._host.get_name()
        msg = result._result.get("msg", "Host unreachable")

        self._emit("host_unreachable", {
            "host": host,
            "error": msg,
        })

    def v2_runner_retry(self, result):
        """Called when a task is retried (e.g., polling for VM ACTIVE status)."""
        task = result._task.get_name()
        retries = result._result.get("retries", 0)
        attempts = result._result.get("attempts", 0)
        remaining = retries - attempts if retries else 0

        vm_name = self._get_loop_item_vm(result)

        # Fallback: extract VM name from the shell command or stdout
        if not vm_name:
            for field in ("cmd", "stdout"):
                text = result._result.get(field, "")
                if isinstance(text, str):
                    m = re.search(r"(sup-\S+)", text)
                    if m:
                        vm_name = m.group(1).rstrip('"\'')
                        break

        target = vm_name or result._host.get_name()

        self._emit("retry", {
            "host": result._host.get_name(),
            "task": task,
            "vm_name": vm_name or "",
            "retries_remaining": remaining,
        })

        # Write a marker line to stdout for the log tail renderer.
        # The default callback also prints "FAILED - RETRYING:" but with the
        # ansible host (e.g., axes) instead of the target VM name.
        self._display.display(
            f"RUSE_RETRY: {target}: {task} ({remaining} retries left)"
        )

    def v2_runner_on_start(self, host, task):
        """Called when a task starts executing for a specific host.

        Provides per-host install phase tracking (more accurate than
        v2_playbook_on_task_start which fires globally).
        """
        task_name = task.get_name().lower()
        host_name = host.get_name()

        if "cloud-init" in task_name:
            self._emit("install_preparing", {"host": host_name})
        elif "install_sup.sh stage 1" in task_name:
            self._emit("install_stage1", {"host": host_name})
        elif "install_sup.sh stage 2" in task_name:
            self._emit("install_stage2", {"host": host_name})
        elif "reboot" in task_name and "nvidia" in task_name:
            self._emit("reboot_start", {"host": host_name})

    def v2_runner_on_skipped(self, result):
        """Called when a task is skipped."""
        host = result._host.get_name()
        task = result._task.get_name()

        self._emit("task_skipped", {
            "host": host,
            "task": task,
        })

    def v2_playbook_on_stats(self, stats):
        """Called at the end with final statistics."""
        hosts = stats.processed.keys()
        summary = {}

        for host in hosts:
            s = stats.summarize(host)
            summary[host] = {
                "ok": s["ok"],
                "changed": s["changed"],
                "unreachable": s["unreachable"],
                "failures": s["failures"],
                "skipped": s["skipped"],
                "rescued": s.get("rescued", 0),
                "ignored": s.get("ignored", 0),
            }

            # Emit per-host recap
            self._emit("recap", {
                "host": host,
                **summary[host],
            })

        # Emit playbook end
        elapsed = time.time() - self._start_time if self._start_time else 0
        self._emit("playbook_end", {
            "playbook": self._playbook_name,
            "elapsed": elapsed,
            "summary": summary,
        })

        # Close file handle
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None

    def _parse_stdout(self, host: str, task: str, stdout: str):
        """Parse stdout for specific deployment events."""
        if not stdout:
            return

        task_lower = task.lower()

        # VM creation events
        if "create" in task_lower:
            if m := self._vm_created_re.search(stdout):
                vm_name = m.group(1)
                self._emit("vm_creating", {"vm_name": vm_name})
            elif m := self._vm_exists_re.search(stdout):
                vm_name = m.group(1)
                self._emit("vm_exists", {"vm_name": vm_name})

        # VM active (provisioned)
        if "wait for all vms" in task_lower or "active" in task_lower:
            if self._vm_active_re.search(stdout):
                # Try to extract VM name from loop label
                self._emit("vm_active", {"host": host})

        # IP address extraction
        if "get vm ips" in task_lower:
            if m := self._ip_re.search(stdout):
                ip = m.group(1)
                self._emit("vm_ip", {"host": host, "ip": ip})

        # Teardown: server list discovery
        if "get list of" in task_lower and "server" in task_lower:
            servers = self._server_list_re.findall(stdout)
            if servers:
                self._emit("discovery_servers", {
                    "servers": [{"id": s[0], "name": s[1]} for s in servers],
                })

        # Teardown: volume discovery
        if "volume" in task_lower and ("get" in task_lower or "list" in task_lower):
            volumes = self._volume_id_re.findall(stdout)
            if volumes:
                # Filter out duplicates and server IDs
                unique_volumes = list(set(volumes))
                self._emit("discovery_volumes", {
                    "volumes": unique_volumes,
                })

        # Teardown: deletion confirmations
        if m := self._vm_deleted_re.search(stdout):
            name = m.group(1)
            self._emit("resource_deleted", {
                "name": name,
                "type": "server",
            })

        # Volume deletion (check for DELETED pattern in stdout)
        if "delete" in task_lower and "volume" in task_lower:
            if "DELETED:" in stdout:
                vol_match = self._volume_id_re.search(stdout)
                if vol_match:
                    self._emit("resource_deleted", {
                        "name": vol_match.group(1),
                        "type": "volume",
                    })

    def _get_loop_item_vm(self, result) -> str | None:
        """Extract VM name from loop item in result."""
        item = result._result.get("item", {})
        if isinstance(item, dict):
            behavior = item.get("behavior", "")
            index = item.get("index", 0)
            if behavior:
                dep_id = os.environ.get("RUSE_DEPLOYMENT_ID", "")
                prefix = f"sup-{dep_id}-" if dep_id else "sup-"
                return f"{prefix}{behavior.replace('.', '-')}-{index}"
        elif isinstance(item, str):
            # Might be a server list line like "uuid sup-name"
            if "sup-" in item:
                parts = item.split()
                for p in parts:
                    if p.startswith("sup-"):
                        return p
        return None

    def v2_runner_item_on_ok(self, result):
        """Called when a loop item succeeds."""
        host = result._host.get_name()
        task = result._task.get_name()
        item = result._result.get("item", {})
        stdout = result._result.get("stdout", "")

        # Handle VM creation loop items
        task_lower = task.lower()
        if "create" in task_lower:
            vm_name = self._get_loop_item_vm(result)
            if vm_name:
                if "CREATED:" in stdout:
                    self._emit("vm_creating", {"vm_name": vm_name})
                elif "EXISTS:" in stdout:
                    self._emit("vm_exists", {"vm_name": vm_name})

        # Handle VM ACTIVE status
        if "wait for all vms" in task_lower or "active" in task_lower:
            vm_name = self._get_loop_item_vm(result)
            if vm_name and "ACTIVE" in stdout:
                self._emit("vm_provisioned", {"vm_name": vm_name})

        # Handle IP extraction
        if "get vm ips" in task_lower:
            vm_name = self._get_loop_item_vm(result)
            if vm_name and stdout:
                if m := self._ip_re.search(stdout):
                    self._emit("vm_ip", {"vm_name": vm_name, "ip": m.group(1)})

        # Handle deletion confirmations
        if "delete" in task_lower or "wait for" in task_lower:
            stdout_stripped = stdout.strip()
            if "DELETED" in stdout_stripped:
                # Try regex first (DELETED: sup-name)
                if m := self._vm_deleted_re.search(stdout):
                    self._emit("resource_deleted", {"name": m.group(1), "type": "server"})
                elif m := self._volume_id_re.search(stdout):
                    self._emit("resource_deleted", {"name": m.group(1)[:8], "type": "volume"})
                elif stdout_stripped == "DELETED":
                    # Bare "DELETED" — extract server name from loop item
                    vm_name = self._get_loop_item_vm(result)
                    if vm_name:
                        self._emit("resource_deleted", {"name": vm_name, "type": "server"})

    def v2_runner_item_on_failed(self, result):
        """Called when a loop item fails."""
        host = result._host.get_name()
        task = result._task.get_name()
        msg = result._result.get("msg", "")
        item = result._result.get("item", {})
        ignore = result._result.get("ignore_errors", False)

        vm_name = self._get_loop_item_vm(result)

        task_lower = task.lower()
        if "create" in task_lower and vm_name:
            self._emit("vm_failed", {
                "vm_name": vm_name,
                "error": msg,
                "ignored": ignore,
            })
        elif "delete" in task_lower:
            if vm_name:
                self._emit("resource_failed", {
                    "name": vm_name,
                    "type": "server",
                    "error": msg,
                    "ignored": ignore,
                })
            elif isinstance(item, str):
                # Volume ID
                self._emit("resource_failed", {
                    "name": item[:8] if len(item) >= 8 else item,
                    "type": "volume",
                    "error": msg,
                    "ignored": ignore,
                })
