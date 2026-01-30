#!/usr/bin/env python3
"""
DOLOS Deployment TUI

Real-time terminal UI for SUP deployment with step timings and Ansible output.

Usage:
    python deploy_tui.py exp-2                    # Full deploy
    python deploy_tui.py exp-2 --provision        # Provision only
    python deploy_tui.py exp-2 --install          # Install only
    python deploy_tui.py exp-2 --teardown         # Teardown
    python deploy_tui.py --list                   # List deployments

Inspired by PHASE/training_scripts/BATCH_TRAINING.py
"""

import argparse
import json
import os
import queue
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# Rich library for TUI
try:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.text import Text
    from rich import box
except ImportError:
    print("ERROR: 'rich' library is required. Install with: pip install rich")
    sys.exit(1)

try:
    import yaml
except ImportError:
    print("ERROR: 'pyyaml' library is required. Install with: pip install pyyaml")
    sys.exit(1)


# =============================================================================
# CONSTANTS
# =============================================================================

SCRIPT_DIR = Path(__file__).parent
PLAYBOOKS_DIR = SCRIPT_DIR / "playbooks"

# Deployment steps and their display order
DEPLOY_STEPS = ["provision", "deploy", "reboot", "install"]
TEARDOWN_STEPS = ["discover", "delete"]


# =============================================================================
# DATA CLASSES
# =============================================================================

class Status(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class SUPState:
    """State tracking for a single SUP deployment."""
    name: str
    behavior: str
    flavor: str
    status: Status = Status.PENDING
    current_step: str = ""
    step_times: Dict[str, float] = field(default_factory=dict)
    step_start_times: Dict[str, float] = field(default_factory=dict)
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    error_msg: str = ""

    @property
    def is_gpu(self) -> bool:
        """Check if this SUP uses a GPU flavor."""
        return "gpu" in self.flavor.lower()

    @property
    def needs_reboot(self) -> bool:
        """Check if this SUP needs a reboot (GPU flavors need NVIDIA driver reload)."""
        return self.is_gpu

    def start_step(self, step: str):
        """Mark a step as started."""
        now = time.time()
        if self.start_time is None:
            self.start_time = now
        self.step_start_times[step] = now
        self.current_step = step
        self.status = Status.RUNNING

    def complete_step(self, step: str):
        """Mark a step as completed."""
        if step in self.step_start_times:
            self.step_times[step] = time.time() - self.step_start_times[step]

    def skip_step(self, step: str):
        """Mark a step as skipped."""
        self.step_times[step] = -1  # -1 indicates skipped

    def fail(self, error: str = ""):
        """Mark this SUP as failed."""
        self.status = Status.FAILED
        self.end_time = time.time()
        self.error_msg = error

    def complete(self):
        """Mark this SUP as completed."""
        self.status = Status.COMPLETED
        self.end_time = time.time()

    def total_time(self) -> float:
        """Get total elapsed time for this SUP."""
        if self.start_time is None:
            return 0
        end = self.end_time if self.end_time else time.time()
        return end - self.start_time


# =============================================================================
# EVENT PARSER
# =============================================================================

class EventParser:
    """Parse DOLOS events from JSON-lines file."""

    def __init__(self, event_file: Path):
        self.event_file = event_file
        self._file_handle = None
        self._file_pos = 0

    def open(self):
        """Open the event file for reading."""
        if self.event_file.exists():
            self._file_handle = open(self.event_file, 'r')
            self._file_pos = 0

    def close(self):
        """Close the event file."""
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None

    def read_events(self) -> List[Dict[str, Any]]:
        """Read any new events from the file."""
        events = []

        if not self._file_handle:
            if self.event_file.exists():
                self.open()
            else:
                return events

        try:
            # Read new lines
            self._file_handle.seek(self._file_pos)
            for line in self._file_handle:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            self._file_pos = self._file_handle.tell()
        except Exception:
            pass

        return events


# =============================================================================
# DEPLOYMENT MONITOR
# =============================================================================

class DeploymentMonitor:
    """Track deployment state and parse Ansible events."""

    def __init__(self, deployment_name: str, config: Dict[str, Any], mode: str = "deploy"):
        self.deployment_name = deployment_name
        self.config = config
        self.mode = mode  # "deploy" or "teardown"

        # Build SUP list from config
        self.sups: Dict[str, SUPState] = {}
        self._build_sup_list()

        # Track overall progress
        self.start_time: Optional[float] = None
        self.completed_count = 0
        self.failed_count = 0

        # Current phase
        self.current_phase = ""  # "provision", "install", "teardown"

        # Output lines for display
        self.output_lines: List[str] = []
        self.max_output_lines = 50

    def _build_sup_list(self):
        """Build SUP list from config.yaml."""
        behavior_counts: Dict[str, int] = {}

        for dep in self.config.get("deployments", []):
            behavior = dep["behavior"]
            flavor = dep["flavor"]
            count = dep.get("count", 1)

            for _ in range(count):
                idx = behavior_counts.get(behavior, 0)
                behavior_counts[behavior] = idx + 1

                # Create VM name (matches provision-vms.yaml naming)
                vm_name = f"sup-{behavior.replace('.', '-')}-{idx}"

                self.sups[vm_name] = SUPState(
                    name=vm_name,
                    behavior=behavior,
                    flavor=flavor,
                )

    def process_event(self, event: Dict[str, Any]):
        """Process a DOLOS event and update state."""
        event_type = event.get("type", "")
        data = event.get("data", {})

        # Track output for display
        task = event.get("task", "")
        if task and event_type in ("task_start", "task_ok", "task_failed"):
            self._add_output_line(event_type, task, data)

        # Handle specific event types
        if event_type == "playbook_start":
            playbook = data.get("playbook", "")
            if "provision" in playbook:
                self.current_phase = "provision"
            elif "install" in playbook:
                self.current_phase = "install"
            elif "teardown" in playbook:
                self.current_phase = "teardown"

        elif event_type == "vm_creating":
            vm_name = data.get("vm_name", "")
            if vm_name in self.sups:
                self.sups[vm_name].start_step("provision")

        elif event_type == "vm_provisioned":
            vm_name = data.get("vm_name", "")
            if vm_name in self.sups:
                self.sups[vm_name].complete_step("provision")

        elif event_type == "vm_ip":
            # VM has IP, provision complete
            vm_name = data.get("vm_name", "")
            if vm_name in self.sups:
                self.sups[vm_name].complete_step("provision")

        elif event_type == "task_start":
            self._handle_task_start(task, data)

        elif event_type == "task_ok":
            self._handle_task_ok(task, data)

        elif event_type == "task_failed":
            self._handle_task_failed(task, data)

        elif event_type == "install_stage1":
            # Stage 1 started for a host
            pass

        elif event_type == "install_stage2":
            # Stage 2 started
            pass

        elif event_type == "reboot_start":
            pass

        elif event_type == "reboot_complete":
            host = data.get("host", "")
            if host in self.sups:
                self.sups[host].complete_step("reboot")

        elif event_type == "recap":
            host = data.get("host", "")
            failures = data.get("failures", 0)
            if host in self.sups:
                if failures > 0:
                    self.sups[host].fail()
                    self.failed_count += 1
                else:
                    self.sups[host].complete()
                    self.completed_count += 1

        elif event_type == "discovery_servers":
            # Teardown: servers discovered
            servers = data.get("servers", [])
            for server in servers:
                name = server.get("name", "")
                if name in self.sups:
                    self.sups[name].start_step("discover")
                    self.sups[name].complete_step("discover")

        elif event_type == "resource_deleted":
            name = data.get("name", "")
            if name in self.sups:
                self.sups[name].start_step("delete")
                self.sups[name].complete_step("delete")
                self.sups[name].complete()
                self.completed_count += 1

    def _handle_task_start(self, task: str, data: Dict[str, Any]):
        """Handle task_start events."""
        task_lower = task.lower()

        # Detect deploy phase (clone repo, wait for SSH)
        if "clone" in task_lower or "wait for ssh" in task_lower:
            # Start deploy step for all running SUPs
            for sup in self.sups.values():
                if sup.status == Status.RUNNING and "deploy" not in sup.step_times:
                    sup.start_step("deploy")

        # Detect install stages
        elif "install_sup.sh stage 1" in task_lower:
            pass  # Will be handled per-host

        elif "install_sup.sh stage 2" in task_lower:
            pass

    def _handle_task_ok(self, task: str, data: Dict[str, Any]):
        """Handle task_ok events."""
        host = data.get("host", "")
        task_lower = task.lower()

        if host not in self.sups:
            return

        sup = self.sups[host]

        # Clone repo - deploy step
        if "clone" in task_lower:
            if "deploy" in sup.step_start_times:
                sup.complete_step("deploy")

        # Stage 1 complete
        if "install_sup.sh stage 1" in task_lower:
            sup.complete_step("deploy")
            # Check if reboot is needed
            if not sup.needs_reboot:
                sup.skip_step("reboot")
            else:
                sup.start_step("reboot")

        # Reboot complete
        if "reboot" in task_lower:
            sup.complete_step("reboot")
            sup.start_step("install")

        # Stage 2 complete
        if "install_sup.sh stage 2" in task_lower:
            sup.complete_step("install")

    def _handle_task_failed(self, task: str, data: Dict[str, Any]):
        """Handle task_failed events."""
        host = data.get("host", "")
        error = data.get("error", "")
        ignored = data.get("ignored", False)

        if ignored:
            return

        if host in self.sups:
            self.sups[host].fail(error)
            self.failed_count += 1

    def _add_output_line(self, event_type: str, task: str, data: Dict[str, Any]):
        """Add a line to the output display."""
        host = data.get("host", "")

        if event_type == "task_start":
            line = f"TASK [{task}] {'*' * 40}"
        elif event_type == "task_ok":
            changed = data.get("changed", False)
            status = "changed" if changed else "ok"
            line = f"{status}: [{host}]" if host else f"{status}"
        elif event_type == "task_failed":
            error = data.get("error", "")[:60]
            line = f"FAILED: [{host}] {error}" if host else f"FAILED: {error}"
        else:
            return

        self.output_lines.append(line)
        if len(self.output_lines) > self.max_output_lines:
            self.output_lines.pop(0)

    def get_steps(self) -> List[str]:
        """Get the step list for current mode."""
        if self.mode == "teardown":
            return TEARDOWN_STEPS
        return DEPLOY_STEPS


# =============================================================================
# TUI RENDERER
# =============================================================================

class TUIRenderer:
    """Rich-based TUI renderer."""

    def __init__(self, monitor: DeploymentMonitor):
        self.monitor = monitor
        self.console = Console()

    def _format_time(self, seconds: float) -> str:
        """Format seconds as HH:MM:SS."""
        if seconds <= 0:
            return "--:--:--"
        h, r = divmod(int(seconds), 3600)
        m, s = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _format_step_time(self, seconds: float) -> str:
        """Format step time as MM:SS."""
        if seconds < 0:
            return "SKIP"
        if seconds == 0:
            return "--"
        total_mins, s = divmod(int(seconds), 60)
        if total_mins >= 60:
            h, m = divmod(total_mins, 60)
            return f"{h}:{m:02d}:{s:02d}"
        return f"{total_mins:02d}:{s:02d}"

    def _style_step_time(self, sup: SUPState, step: str) -> str:
        """Get styled step time string."""
        if step not in sup.step_times:
            if step == sup.current_step:
                # Currently running - show elapsed
                if step in sup.step_start_times:
                    elapsed = time.time() - sup.step_start_times[step]
                    return f"[yellow]{self._format_step_time(elapsed)}[/yellow]"
                return "[yellow]...[/yellow]"
            return "[dim]--[/dim]"

        duration = sup.step_times[step]
        if duration < 0:
            return "[cyan]SKIP[/cyan]"
        return f"[green]{self._format_step_time(duration)}[/green]"

    def build_display(self) -> Text:
        """Build the complete display."""
        term_width = shutil.get_terminal_size().columns
        mon = self.monitor
        elapsed = time.time() - mon.start_time if mon.start_time else 0

        display = Text()
        steps = mon.get_steps()

        # Calculate column widths
        sup_col_width = 22
        step_col_width = 8
        total_col_width = 10

        # Header row using Rich Table for proper alignment
        header = f"{'SUP':<{sup_col_width}}"
        for step in steps:
            header += f" {step:>{step_col_width}}"
        header += f" {'│':^3}"
        header += f" {'total':>{step_col_width}}"
        header += f" {'r-total':>{total_col_width}}"

        display.append_text(Text.from_markup(f"[bold]{header}[/bold]\n"))

        # SUP rows
        running_total = 0.0
        for sup in mon.sups.values():
            # SUP name with status color
            name_display = sup.name[:sup_col_width-2]
            if sup.status == Status.COMPLETED:
                row = f"[green]{name_display:<{sup_col_width}}[/green]"
            elif sup.status == Status.FAILED:
                row = f"[red]{name_display:<{sup_col_width}}[/red]"
            elif sup.status == Status.RUNNING:
                row = f"[yellow]{name_display:<{sup_col_width}}[/yellow]"
            else:
                row = f"[dim]{name_display:<{sup_col_width}}[/dim]"

            # Step times
            for step in steps:
                styled = self._style_step_time(sup, step)
                row += f" {styled:>{step_col_width}}"

            # Separator
            row += f" [dim]│[/dim] "

            # Total time
            total = sup.total_time()
            if total > 0:
                row += f" {self._format_step_time(total):>{step_col_width}}"
                if sup.status in (Status.COMPLETED, Status.FAILED):
                    running_total += total
            else:
                row += f" [dim]{'--':>{step_col_width}}[/dim]"

            # Running total
            if running_total > 0:
                row += f" [bold]{self._format_time(running_total):>{total_col_width}}[/bold]"
            else:
                row += f" [dim]{'--:--:--':>{total_col_width}}[/dim]"

            display.append_text(Text.from_markup(row + "\n"))

        # Separator
        display.append("─" * min(term_width, 100) + "\n", style="dim")

        # Progress bar
        total_sups = len(mon.sups)
        completed = mon.completed_count
        running = sum(1 for s in mon.sups.values() if s.status == Status.RUNNING)
        progress_pct = completed / total_sups if total_sups > 0 else 0
        bar_width = 40
        filled = int(bar_width * progress_pct)
        bar = "=" * filled + "-" * (bar_width - filled)

        mode_name = "DOLOS Teardown" if mon.mode == "teardown" else "DOLOS Deployment"
        header = f"{mode_name}  [{bar}]  {completed}/{total_sups}"
        if running > 0:
            header += f" ({running} running)"
        if mon.failed_count > 0:
            header += f" ({mon.failed_count} failed)"
        header += f"   Elapsed: {self._format_time(elapsed)}"
        display.append(header + "\n", style="bold")

        # Status line (colored by status)
        status_parts = []
        for sup in mon.sups.values():
            short = sup.behavior[:8]
            if sup.status == Status.COMPLETED:
                status_parts.append(f"[green]{short}[/green]")
            elif sup.status == Status.FAILED:
                status_parts.append(f"[red]{short}[/red]")
            elif sup.status == Status.RUNNING:
                status_parts.append(f"[yellow bold]{short}[/yellow bold]")
            else:
                status_parts.append(f"[dim]{short}[/dim]")

        # Wrap status line if too long
        line = "  ".join(status_parts)
        display.append_text(Text.from_markup(line + "\n"))

        # Separator
        display.append("─" * min(term_width, 100) + "\n", style="dim")

        # Output log
        for line in mon.output_lines[-20:]:
            # Truncate long lines
            if len(line) > term_width - 2:
                line = line[:term_width - 5] + "..."
            if "FAILED" in line or "ERROR" in line or "fatal:" in line.lower():
                display.append(line + "\n", style="red")
            elif "changed:" in line:
                display.append(line + "\n", style="yellow")
            elif "ok:" in line:
                display.append(line + "\n", style="green")
            elif line.startswith("TASK"):
                display.append(line + "\n", style="bold cyan")
            elif line.startswith("PLAY"):
                display.append(line + "\n", style="bold magenta")
            else:
                display.append(line + "\n")

        return display


# =============================================================================
# DEPLOYMENT RUNNER
# =============================================================================

class DeploymentRunner:
    """Run Ansible playbooks with TUI."""

    def __init__(self, deployment_name: str, args: argparse.Namespace):
        self.deployment_name = deployment_name
        self.args = args
        self.console = Console()

        # Paths
        self.deploy_dir = SCRIPT_DIR / deployment_name
        self.config_file = self.deploy_dir / "config.yaml"
        self.hosts_file = self.deploy_dir / "hosts.ini"
        self.inventory_file = self.deploy_dir / "inventory.ini"

        # Event file for callback plugin
        self.event_file = Path(tempfile.mktemp(suffix=".jsonl", prefix="dolos_events_"))

        # Load config
        self.config = self._load_config()

        # Determine mode
        if args.teardown:
            self.mode = "teardown"
        else:
            self.mode = "deploy"

        # Create monitor and renderer
        self.monitor = DeploymentMonitor(deployment_name, self.config, self.mode)
        self.renderer = TUIRenderer(self.monitor)

        # Event parser
        self.event_parser = EventParser(self.event_file)

        # Process management
        self.process: Optional[subprocess.Popen] = None
        self.output_queue: queue.Queue = queue.Queue()
        self.live: Optional[Live] = None

    def _load_config(self) -> Dict[str, Any]:
        """Load deployment config.yaml."""
        if not self.config_file.exists():
            self.console.print(f"[red]Config not found: {self.config_file}[/red]")
            sys.exit(1)

        with open(self.config_file) as f:
            return yaml.safe_load(f)

    def _read_output(self, pipe):
        """Thread: read subprocess output."""
        try:
            for line in iter(pipe.readline, ''):
                if line:
                    self.output_queue.put(line.rstrip())
            pipe.close()
        except Exception:
            pass

    def _run_playbook(self, playbook: str, inventory: Path, extra_vars: Dict[str, str] = None):
        """Run an Ansible playbook."""
        cmd = [
            "ansible-playbook",
            "-i", str(inventory),
            "-e", f"deployment_dir={self.deploy_dir}",
        ]

        if extra_vars:
            for key, value in extra_vars.items():
                cmd.extend(["-e", f"{key}={value}"])

        cmd.append(str(PLAYBOOKS_DIR / playbook))

        # Set up environment with event file
        env = os.environ.copy()
        env["DOLOS_EVENT_FILE"] = str(self.event_file)
        env["ANSIBLE_CALLBACKS_ENABLED"] = "dolos_events"
        env["ANSIBLE_CALLBACK_PLUGINS"] = str(PLAYBOOKS_DIR / "callback_plugins")
        env["ANSIBLE_STDOUT_CALLBACK"] = "default"
        env["PYTHONUNBUFFERED"] = "1"
        env["ANSIBLE_FORCE_COLOR"] = "0"  # Disable color codes for cleaner parsing

        # Add SSH config if available
        ssh_config = Path.home() / ".ssh" / "config"
        if ssh_config.exists():
            env["ANSIBLE_SSH_ARGS"] = f"-F {ssh_config}"

        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            cwd=PLAYBOOKS_DIR,
        )

        # Start reader thread
        reader = threading.Thread(
            target=self._read_output,
            args=(self.process.stdout,),
            daemon=True,
        )
        reader.start()

        # Process output and events
        while self.process.poll() is None:
            # Process stdout
            try:
                while True:
                    line = self.output_queue.get_nowait()
                    # Parse Ansible output for state updates
                    self._parse_ansible_output(line)
                    # Add to display
                    self.monitor.output_lines.append(line)
                    if len(self.monitor.output_lines) > self.monitor.max_output_lines:
                        self.monitor.output_lines.pop(0)
            except queue.Empty:
                pass

            # Process events from callback plugin
            for event in self.event_parser.read_events():
                self.monitor.process_event(event)

            # Update display
            if self.live:
                self.live.update(self.renderer.build_display())

            time.sleep(0.05)

        # Drain remaining output
        time.sleep(0.2)
        try:
            while True:
                line = self.output_queue.get_nowait()
                self._parse_ansible_output(line)
                self.monitor.output_lines.append(line)
        except queue.Empty:
            pass

        # Process remaining events
        for event in self.event_parser.read_events():
            self.monitor.process_event(event)

        return self.process.returncode

    def _parse_ansible_output(self, line: str):
        """Parse raw Ansible output for state tracking."""
        # Track TASK lines
        if line.startswith("TASK ["):
            match = re.match(r"TASK \[([^\]]+)\]", line)
            if match:
                task_name = match.group(1).lower()
                # Detect install stages from task names
                if "install_sup.sh stage 1" in task_name:
                    for sup in self.monitor.sups.values():
                        if sup.status == Status.RUNNING and "deploy" in sup.step_times:
                            sup.start_step("install")
                elif "install_sup.sh stage 2" in task_name:
                    pass  # Continue install phase

        # Track ok/changed/failed per host
        elif line.startswith("ok:") or line.startswith("changed:"):
            match = re.match(r"(?:ok|changed): \[([^\]]+)\]", line)
            if match:
                host = match.group(1)
                if host in self.monitor.sups:
                    sup = self.monitor.sups[host]
                    if sup.status != Status.RUNNING:
                        sup.status = Status.RUNNING
                        if sup.start_time is None:
                            sup.start_time = time.time()

        elif line.startswith("fatal:") or line.startswith("failed:"):
            match = re.match(r"(?:fatal|failed): \[([^\]]+)\]", line)
            if match:
                host = match.group(1)
                if host in self.monitor.sups:
                    self.monitor.sups[host].fail()

        # Track PLAY RECAP for final status
        elif "PLAY RECAP" in line:
            pass  # Events will handle this

        # Track reboot
        elif "Rebooting" in line or "rebooted" in line.lower():
            for sup in self.monitor.sups.values():
                if sup.status == Status.RUNNING and sup.needs_reboot:
                    if "reboot" not in sup.step_times:
                        sup.start_step("reboot")

    def run_provision(self) -> bool:
        """Run provision playbook."""
        if not self.hosts_file.exists():
            self.console.print(f"[red]Hosts file not found: {self.hosts_file}[/red]")
            return False

        self.monitor.current_phase = "provision"

        # Mark all SUPs as starting provision
        for sup in self.monitor.sups.values():
            sup.start_step("provision")

        rc = self._run_playbook("provision-vms.yaml", self.hosts_file)
        return rc == 0

    def run_install(self) -> bool:
        """Run install playbook."""
        if not self.inventory_file.exists():
            self.console.print(f"[red]Inventory not found: {self.inventory_file}[/red]")
            self.console.print("[yellow]Run provision first or create inventory.ini[/yellow]")
            return False

        self.monitor.current_phase = "install"

        # Mark provision as complete (if not already) and start deploy
        for sup in self.monitor.sups.values():
            if "provision" not in sup.step_times:
                sup.step_times["provision"] = 0  # Already provisioned
            sup.start_step("deploy")

        rc = self._run_playbook("install-sups.yaml", self.inventory_file)
        return rc == 0

    def run_teardown(self) -> bool:
        """Run teardown playbook."""
        if not self.hosts_file.exists():
            self.console.print(f"[red]Hosts file not found: {self.hosts_file}[/red]")
            return False

        self.monitor.current_phase = "teardown"

        rc = self._run_playbook("teardown.yaml", self.hosts_file)
        return rc == 0

    def run(self):
        """Run the deployment with TUI."""
        self.monitor.start_time = time.time()
        self._interrupted = False

        # Set up signal handler
        def signal_handler(signum, frame):
            self._interrupted = True
            if self.process:
                self.process.terminate()

        old_handler = signal.signal(signal.SIGINT, signal_handler)

        # Clear screen
        self.console.clear()

        try:
            with Live(self.renderer.build_display(), console=self.console, refresh_per_second=8) as live:
                self.live = live

                if self.args.teardown:
                    success = self.run_teardown()
                elif self.args.provision:
                    success = self.run_provision()
                elif self.args.install:
                    success = self.run_install()
                else:
                    # Full deploy: provision + install
                    success = self.run_provision()
                    if success and not self._interrupted:
                        live.update(self.renderer.build_display())
                        # Brief pause before install
                        time.sleep(2)
                        success = self.run_install()

                # Final update
                live.update(self.renderer.build_display())
                self.live = None

        except KeyboardInterrupt:
            self._interrupted = True
            self.console.print("\n[yellow]Interrupted[/yellow]")
            if self.process:
                self.process.terminate()

        finally:
            # Restore signal handler
            signal.signal(signal.SIGINT, old_handler)

            # Clean up
            self.event_parser.close()
            if self.event_file.exists():
                try:
                    self.event_file.unlink()
                except Exception:
                    pass

            if self._interrupted:
                self.console.print("\n[yellow]Deployment interrupted by user[/yellow]")
            else:
                self._print_summary()

    def _print_summary(self):
        """Print final summary."""
        self.console.print("\n")

        mon = self.monitor
        elapsed = time.time() - mon.start_time if mon.start_time else 0

        # Summary table
        table = Table(title=f"{mon.deployment_name} Deployment Summary", box=box.SIMPLE)
        table.add_column("SUP", style="cyan")
        table.add_column("Behavior")
        table.add_column("Status", justify="center")

        for step in mon.get_steps():
            table.add_column(step.capitalize(), justify="right")

        table.add_column("Total", justify="right")

        for sup in mon.sups.values():
            status = "[green]OK[/green]" if sup.status == Status.COMPLETED else \
                     "[red]FAIL[/red]" if sup.status == Status.FAILED else \
                     "[dim]--[/dim]"

            row = [sup.name, sup.behavior, status]

            for step in mon.get_steps():
                if step in sup.step_times:
                    t = sup.step_times[step]
                    if t < 0:
                        row.append("SKIP")
                    else:
                        row.append(self.renderer._format_step_time(t))
                else:
                    row.append("-")

            total = sup.total_time()
            row.append(self.renderer._format_time(total) if total > 0 else "-")

            table.add_row(*row)

        self.console.print(table)

        # Final status
        if mon.failed_count == 0 and mon.completed_count > 0:
            self.console.print(f"[bold green]All {mon.completed_count} SUPs deployed successfully[/bold green]")
        elif mon.failed_count > 0:
            self.console.print(f"[yellow]Completed: {mon.completed_count}, Failed: {mon.failed_count}[/yellow]")

        self.console.print(f"[dim]Total time: {self.renderer._format_time(elapsed)}[/dim]")


# =============================================================================
# CLI
# =============================================================================

def list_deployments():
    """List available deployments."""
    console = Console()
    console.print("[bold]Available deployments:[/bold]\n")

    for path in sorted(SCRIPT_DIR.iterdir()):
        if path.is_dir() and (path / "config.yaml").exists():
            name = path.name
            config_file = path / "config.yaml"

            try:
                with open(config_file) as f:
                    config = yaml.safe_load(f)

                # Count VMs
                total_vms = sum(d.get("count", 1) for d in config.get("deployments", []))
                desc = config.get("deployment_name", name)

                console.print(f"  [cyan]{name:<20}[/cyan] {total_vms} VMs - {desc}")
            except Exception:
                console.print(f"  [cyan]{name:<20}[/cyan] (error reading config)")


def show_deployment_preview(deployment_name: str):
    """Show preview of what would be deployed."""
    console = Console()
    deploy_dir = SCRIPT_DIR / deployment_name
    config_file = deploy_dir / "config.yaml"

    with open(config_file) as f:
        config = yaml.safe_load(f)

    monitor = DeploymentMonitor(deployment_name, config)

    console.print(f"\n[bold cyan]Deployment: {deployment_name}[/bold cyan]\n")

    # Group by flavor
    by_flavor: Dict[str, List[SUPState]] = {}
    for sup in monitor.sups.values():
        if sup.flavor not in by_flavor:
            by_flavor[sup.flavor] = []
        by_flavor[sup.flavor].append(sup)

    # Create table
    table = Table(title=f"SUPs to Deploy ({len(monitor.sups)} total)", box=box.SIMPLE)
    table.add_column("Flavor", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Reboot", justify="center")
    table.add_column("SUPs")

    for flavor, sups in sorted(by_flavor.items()):
        needs_reboot = "Yes" if sups[0].needs_reboot else "[dim]No[/dim]"
        sup_names = ", ".join(s.behavior for s in sups[:5])
        if len(sups) > 5:
            sup_names += f" (+{len(sups) - 5} more)"
        table.add_row(
            flavor[:35],
            str(len(sups)),
            needs_reboot,
            sup_names,
        )

    console.print(table)

    # Show steps
    console.print("\n[bold]Deployment Steps:[/bold]")
    console.print("  1. [cyan]provision[/cyan] - Create VMs on OpenStack")
    console.print("  2. [cyan]deploy[/cyan]    - Wait for SSH, clone repo")
    console.print("  3. [cyan]reboot[/cyan]    - Reboot for NVIDIA drivers (GPU only)")
    console.print("  4. [cyan]install[/cyan]   - Install Ollama, Python, services")
    console.print()


def main():
    parser = argparse.ArgumentParser(
        description="DOLOS Deployment TUI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python deploy_tui.py exp-2              # Full deploy
  python deploy_tui.py exp-2 --provision  # Provision VMs only
  python deploy_tui.py exp-2 --install    # Install SUPs only
  python deploy_tui.py exp-2 --teardown   # Teardown deployment
  python deploy_tui.py exp-2 --dry-run    # Preview deployment
  python deploy_tui.py --list             # List deployments
        """
    )

    parser.add_argument("deployment", nargs="?", help="Deployment name (e.g., exp-2)")
    parser.add_argument("--list", "-l", action="store_true", help="List available deployments")
    parser.add_argument("--provision", "-p", action="store_true", help="Provision VMs only")
    parser.add_argument("--install", "-i", action="store_true", help="Install SUPs only")
    parser.add_argument("--teardown", "-t", action="store_true", help="Teardown deployment")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Preview deployment without running")

    args = parser.parse_args()

    if args.list:
        list_deployments()
        return 0

    if not args.deployment:
        parser.print_help()
        return 1

    # Validate deployment exists
    deploy_dir = SCRIPT_DIR / args.deployment
    if not deploy_dir.exists() or not (deploy_dir / "config.yaml").exists():
        Console().print(f"[red]Deployment not found: {args.deployment}[/red]")
        Console().print("\nAvailable deployments:")
        list_deployments()
        return 1

    # Dry run - just show preview
    if args.dry_run:
        show_deployment_preview(args.deployment)
        return 0

    # Run deployment
    runner = DeploymentRunner(args.deployment, args)
    runner.run()

    return 0


if __name__ == "__main__":
    sys.exit(main())
