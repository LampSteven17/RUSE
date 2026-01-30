#!/usr/bin/env python3
"""
DOLOS Deployment Monitor

Event-driven TUI for tracking VM provisioning and SUP installation.
Uses structured JSON events from the dolos_events Ansible callback plugin.

Usage:
    python deploy_monitor.py                    # Interactive menu
    python deploy_monitor.py exp-2              # Full deploy (provision + install)
    python deploy_monitor.py exp-2 --provision  # Provision only
    python deploy_monitor.py exp-2 --install    # Install only
    python deploy_monitor.py exp-2 --dry-run    # Preview VMs
    python deploy_monitor.py --list             # List deployments
    python deploy_monitor.py --teardown-all     # Delete all sup-* VMs
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

# Suppress threading exception output to prevent display corruption
def _silent_excepthook(args):
    pass
threading.excepthook = _silent_excepthook

try:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich import box
except ImportError:
    print("ERROR: 'rich' library required. Install: pip install rich")
    sys.exit(1)

try:
    import yaml
except ImportError:
    print("ERROR: 'pyyaml' library required. Install: pip install pyyaml")
    sys.exit(1)

from monitor import (
    StateManager,
    VMState,
    VMStatus,
    ResourceStatus,
    MarkdownLogWriter,
    parse_event,
)
from monitor.markdown_log import create_log_path


# =============================================================================
# INTERACTIVE MENU
# =============================================================================


class InteractiveMenu:
    """Interactive TUI menu for deployment operations."""

    HEADER = """
+---------------------------------------------------------------+
|           DOLOS-DEPLOY - SUP Deployment Tool                  |
|                    Interactive Monitor                        |
+---------------------------------------------------------------+
"""

    def __init__(self):
        self.console = Console()
        self.script_dir = Path(__file__).parent
        self.playbooks_dir = self.script_dir / "playbooks"

    def _get_deployments(self) -> list[str]:
        """Get available deployments."""
        deployments = []
        for d in self.script_dir.iterdir():
            if d.is_dir() and (d / "config.yaml").exists():
                deployments.append(d.name)
        return sorted(deployments)

    def _load_deployment_info(self, deployment: str) -> dict:
        """Load deployment info."""
        config_file = self.script_dir / deployment / "config.yaml"
        inventory_file = self.script_dir / deployment / "inventory.ini"

        info = {
            "name": deployment,
            "has_inventory": inventory_file.exists(),
            "vm_count": 0,
            "behaviors": [],
        }

        if config_file.exists():
            with open(config_file) as f:
                config = yaml.safe_load(f)
            info["deployment_name"] = config.get("deployment_name", deployment)
            for dep in config.get("deployments", []):
                info["vm_count"] += dep.get("count", 1)
                if dep["behavior"] not in info["behaviors"]:
                    info["behaviors"].append(dep["behavior"])

        return info

    def _select_deployment(self, prompt: str = "Select deployment") -> Optional[str]:
        """Show deployment selection menu."""
        deployments = self._get_deployments()
        if not deployments:
            self.console.print("[red]No deployments found![/red]")
            return None

        table = Table(box=box.ROUNDED, show_header=True)
        table.add_column("#", style="cyan", width=4)
        table.add_column("Deployment", style="bold")
        table.add_column("VMs", justify="right")
        table.add_column("Status", justify="center")

        for i, dep in enumerate(deployments, 1):
            info = self._load_deployment_info(dep)
            status = "[green]Ready[/green]" if info["has_inventory"] else "[yellow]Not provisioned[/yellow]"
            table.add_row(str(i), dep, str(info["vm_count"]), status)

        self.console.print(f"\n[bold]{prompt}:[/bold]")
        self.console.print(table)

        try:
            choice = self.console.input("\n[cyan]Enter number (or 'q' to cancel):[/cyan] ").strip()
            if choice.lower() == "q":
                return None
            idx = int(choice) - 1
            if 0 <= idx < len(deployments):
                return deployments[idx]
        except (ValueError, KeyboardInterrupt):
            pass
        return None

    def run(self):
        """Run the interactive menu."""
        while True:
            os.system("clear" if os.name == "posix" else "cls")
            self.console.print(self.HEADER)

            menu = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
            menu.add_column("Option", style="cyan bold", width=4)
            menu.add_column("Description")

            menu.add_row("1", "Deploy (provision VMs + install SUPs)")
            menu.add_row("2", "Teardown (delete VMs and volumes)")
            menu.add_row("3", "Exit")

            self.console.print(menu)

            try:
                choice = self.console.input("\n[cyan]Select option [1-3]:[/cyan] ").strip()
            except KeyboardInterrupt:
                self.console.print("\n[dim]Goodbye![/dim]")
                break

            if choice == "1":
                deployment = self._select_deployment()
                if deployment:
                    args = argparse.Namespace(
                        deployment=deployment,
                        provision=False,
                        install=False,
                        dry_run=False,
                        list=False,
                        teardown_all=False,
                    )
                    try:
                        monitor = DeploymentMonitor(deployment, args)
                        self.console.print(f"\n[bold]Starting deployment: {deployment}[/bold]")
                        self.console.print(f"[dim]VMs to deploy: {len(monitor.state_manager.vms)}[/dim]")
                        monitor.run()
                        self.console.input("\n[dim]Press Enter to continue...[/dim]")
                    except FileNotFoundError as e:
                        self.console.print(f"[red]Error: {e}[/red]")
                        self.console.input("\n[dim]Press Enter to continue...[/dim]")

            elif choice == "2":
                self.console.print("\n[bold]Teardown Options:[/bold]")
                deployments = self._get_deployments()

                table = Table(box=box.ROUNDED, show_header=True)
                table.add_column("#", style="cyan", width=4)
                table.add_column("Option")

                for i, dep in enumerate(deployments, 1):
                    table.add_row(str(i), dep)
                table.add_row("A", "[red]Delete ALL servers and volumes[/red]")

                self.console.print(table)

                try:
                    tc = self.console.input("\n[cyan]Select deployment or 'A' for all:[/cyan] ").strip()
                except KeyboardInterrupt:
                    continue

                if tc.upper() == "A":
                    self._run_teardown_all()
                    self.console.input("\n[dim]Press Enter to continue...[/dim]")
                else:
                    try:
                        idx = int(tc) - 1
                        if 0 <= idx < len(deployments):
                            self._run_teardown(deployments[idx])
                            self.console.input("\n[dim]Press Enter to continue...[/dim]")
                    except ValueError:
                        pass

            elif choice == "3":
                self.console.print("\n[dim]Goodbye![/dim]")
                break

    def _run_teardown(self, deployment: str):
        """Run teardown for a specific deployment."""
        self.console.print(f"\n[bold red]WARNING: Delete all VMs for {deployment}[/bold red]")
        try:
            confirm = self.console.input("[yellow]Are you sure? [y/N]:[/yellow] ").strip().lower()
        except KeyboardInterrupt:
            return

        if confirm not in ("y", "yes"):
            return

        deploy_dir = self.script_dir / deployment
        hosts_ini = deploy_dir / "hosts.ini"
        if not hosts_ini.exists():
            self.console.print(f"[red]Error: hosts.ini not found[/red]")
            return

        playbook = self.playbooks_dir / "teardown.yaml"
        cmd = [
            "ansible-playbook",
            "-i", str(hosts_ini),
            "-e", f"deployment_dir={deploy_dir}",
            str(playbook),
        ]

        env = os.environ.copy()
        env["ANSIBLE_FORCE_COLOR"] = "true"

        self.console.print("\n[bold]Running teardown...[/bold]\n")
        subprocess.run(cmd, cwd=str(self.playbooks_dir), env=env)
        self.console.print("\n[green]Teardown complete![/green]")

    def _run_teardown_all(self):
        """Run teardown for ALL sup-* resources."""
        self.console.print("\n[bold red]WARNING: Delete ALL sup-* servers and volumes![/bold red]")
        try:
            confirm = self.console.input("\n[red]Type 'DELETE ALL' to confirm:[/red] ").strip()
        except KeyboardInterrupt:
            return

        if confirm != "DELETE ALL":
            self.console.print("[dim]Cancelled[/dim]")
            return

        hosts_ini = None
        for dep in self._get_deployments():
            h = self.script_dir / dep / "hosts.ini"
            if h.exists():
                hosts_ini = h
                break

        if not hosts_ini:
            self.console.print("[red]Error: No hosts.ini found[/red]")
            return

        monitor = TeardownMonitor(hosts_ini)
        monitor.run()


# =============================================================================
# DEPLOYMENT MONITOR
# =============================================================================


class DeploymentMonitor:
    """Event-driven deployment monitor with Rich TUI."""

    FLAVOR_SHORT = {
        "v100-1gpu.14vcpu.28g": "V100",
        "rtx2080ti-A-1gpu.14vcpu.28g": "RTX-A",
        "rtx2080ti-1gpu.14vcpu.28g": "RTX",
        "v1.14vcpu.28g": "CPU",
    }

    def __init__(self, deployment: str, args: argparse.Namespace):
        self.deployment = deployment
        self.args = args
        self.console = Console()
        self.script_dir = Path(__file__).parent
        self.deploy_dir = self.script_dir / deployment
        self.playbooks_dir = self.script_dir / "playbooks"
        self.logs_dir = self.script_dir / "logs"
        self.config_file = self.deploy_dir / "config.yaml"
        self.inventory_file = self.deploy_dir / "inventory.ini"
        self.ssh_config_file = self.deploy_dir / "ssh_config_snippet.txt"

        # Initialize state manager
        self.state_manager = StateManager()
        self._load_config()

        # Event processing
        self._event_file: Optional[Path] = None
        self._stop_event = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None

        # TUI
        self.live: Optional[Live] = None
        self.start_time: Optional[float] = None
        self._current_activity: str = "Starting..."
        self._ansible_log_path: Optional[Path] = None

        # Logging
        self.log_writer: Optional[MarkdownLogWriter] = None

    def _load_config(self):
        """Load deployment config and build VM list."""
        if not self.config_file.exists():
            raise FileNotFoundError(f"Config not found: {self.config_file}")

        with open(self.config_file) as f:
            config = yaml.safe_load(f)

        self.deployment_name = config.get("deployment_name", self.deployment)
        self.flavor_capacity = config.get("flavor_capacity", {})

        # Build VM states
        behavior_counts: dict[str, int] = {}
        for dep in config.get("deployments", []):
            behavior = dep["behavior"]
            flavor = dep["flavor"]
            count = dep.get("count", 1)

            for _ in range(count):
                idx = behavior_counts.get(behavior, 0)
                behavior_counts[behavior] = idx + 1
                vm_name = f"sup-{behavior.replace('.', '-')}-{idx}"
                self.state_manager.vms[vm_name] = VMState(
                    name=vm_name,
                    behavior=behavior,
                    flavor=flavor,
                )

    def _format_duration(self, seconds: float) -> str:
        """Format duration as HH:MM:SS."""
        h, r = divmod(int(seconds), 3600)
        m, s = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _short_flavor(self, flavor: str) -> str:
        """Get short flavor name."""
        return self.FLAVOR_SHORT.get(flavor, flavor[:6])

    def _vm_sort_key(self, vm_name: str) -> tuple:
        """Sort VMs: C, M, B, BC, S, SC order."""
        parts = vm_name.replace("sup-", "").split("-")
        behavior = parts[0] if parts else ""

        order = {"C": 0, "M": 1, "B": 2, "BC": 3, "S": 4, "SC": 5}

        if behavior.startswith("BC"):
            cat, rest = "BC", behavior[2:]
        elif behavior.startswith("SC"):
            cat, rest = "SC", behavior[2:]
        elif behavior.startswith("C"):
            cat, rest = "C", behavior[1:]
        elif behavior.startswith("M"):
            cat, rest = "M", behavior[1:]
        elif behavior.startswith("B"):
            cat, rest = "B", behavior[1:]
        elif behavior.startswith("S"):
            cat, rest = "S", behavior[1:]
        else:
            cat, rest = "Z", behavior

        num = 0
        variant = ""
        for i, c in enumerate(rest):
            if c.isdigit():
                num = num * 10 + int(c)
            else:
                variant = rest[i:]
                break

        try:
            instance = int(parts[-1]) if parts else 0
        except ValueError:
            instance = 0

        return (order.get(cat, 99), num, variant, instance)

    def _sorted_vms(self) -> list[VMState]:
        """Get VMs in sorted order."""
        return sorted(self.state_manager.vms.values(), key=lambda v: self._vm_sort_key(v.name))

    def _get_current_activity(self) -> str:
        """Read the last TASK line from ansible log."""
        if not self._ansible_log_path or not self._ansible_log_path.exists():
            return self._current_activity

        try:
            with open(self._ansible_log_path, "r") as f:
                lines = f.readlines()
                # Find the last TASK line
                for line in reversed(lines[-50:]):
                    if line.startswith("TASK ["):
                        # Extract task name: TASK [task name] ***
                        task = line.split("[", 1)[1].split("]")[0]
                        self._current_activity = task[:60]
                        return self._current_activity
                    elif line.startswith("PLAY ["):
                        play = line.split("[", 1)[1].split("]")[0]
                        self._current_activity = f"PLAY: {play[:55]}"
                        return self._current_activity
        except Exception:
            pass

        return self._current_activity

    def _build_display(self) -> Table:
        """Build the TUI display table."""
        elapsed = time.time() - self.start_time if self.start_time else 0
        counts = self.state_manager.get_counts()
        total = counts["total"]

        # Progress calculation
        phase = self.state_manager.phase
        if phase == "provisioning":
            done = counts["provisioned"] + counts["installing"] + counts["completed"]
            phase_label = "Provisioning"
        elif phase == "installing":
            done = counts["completed"]
            phase_label = "Installing"
        else:
            done = 0
            phase_label = "Idle"

        pct = done / total if total > 0 else 0
        bar_w = 25
        bar = "\u2588" * int(bar_w * pct) + "\u2591" * (bar_w - int(bar_w * pct))

        # Get current activity from ansible log
        activity = self._get_current_activity()

        # Build table
        table = Table(
            title=f"DOLOS Deploy: {self.deployment_name}  [{bar}] {int(pct*100)}%  {phase_label}  {self._format_duration(elapsed)}",
            caption=f"[dim]{activity}[/dim]",
            box=box.SIMPLE,
            show_header=True,
            header_style="bold",
            title_style="bold cyan",
        )

        table.add_column("VM", style="cyan", width=22)
        table.add_column("HW", width=5)
        table.add_column("Provisioned", justify="center", width=12)
        table.add_column("Installed", justify="center", width=12)
        table.add_column("Status", width=8)

        for vm in self._sorted_vms():
            hw = self._short_flavor(vm.flavor)

            # Provision column
            if vm.provision_end:
                prov = f"[green]{vm.provision_time}[/green]"
            elif vm.status in (VMStatus.CREATING,):
                prov = "[yellow]...[/yellow]"
            elif vm.status == VMStatus.FAILED and not vm.provision_end:
                prov = "[red]FAIL[/red]"
            else:
                prov = "[dim]--:--:--[/dim]"

            # Install column
            if vm.install_end:
                inst = f"[green]{vm.install_time}[/green]"
            elif vm.status in (VMStatus.INSTALLING, VMStatus.STAGE1, VMStatus.REBOOTING, VMStatus.STAGE2):
                inst = "[yellow]...[/yellow]"
            elif vm.status == VMStatus.FAILED and vm.provision_end:
                inst = "[red]FAIL[/red]"
            else:
                inst = "[dim]--:--:--[/dim]"

            # Status column
            status_map = {
                VMStatus.COMPLETED: "[green]OK[/green]",
                VMStatus.FAILED: "[red]FAIL[/red]",
                VMStatus.PROVISIONED: "[blue]PROV[/blue]",
                VMStatus.CREATING: "[yellow]...[/yellow]",
                VMStatus.INSTALLING: "[yellow]...[/yellow]",
                VMStatus.STAGE1: "[yellow]S1[/yellow]",
                VMStatus.REBOOTING: "[yellow]RBT[/yellow]",
                VMStatus.STAGE2: "[yellow]S2[/yellow]",
            }
            status = status_map.get(vm.status, "[dim]--[/dim]")

            table.add_row(vm.name, hw, prov, inst, status)

        return table

    def _event_reader(self, event_path: Path):
        """Background thread: read and process events from file."""
        import sys

        # Suppress thread exception output
        def silent_excepthook(args):
            pass
        threading.excepthook = silent_excepthook

        # Wait for file to exist
        for _ in range(100):
            if event_path.exists():
                break
            time.sleep(0.1)

        if not event_path.exists():
            return

        try:
            with open(event_path, "r") as f:
                while not self._stop_event.is_set():
                    try:
                        line = f.readline()
                        if line:
                            event = parse_event(line)
                            if event:
                                self.state_manager.process_event(event)
                                if self.log_writer:
                                    self.log_writer.log_event(event, self.state_manager)
                        else:
                            time.sleep(0.05)
                    except Exception:
                        pass  # Skip malformed events
        except Exception:
            pass

    def _run_ansible(self, playbook: str, inventory: str) -> int:
        """Run an Ansible playbook with event capture."""
        # Create temp event file
        fd, event_path = tempfile.mkstemp(suffix=".jsonl", prefix="dolos_events_")
        os.close(fd)
        self._event_file = Path(event_path)

        # Create log file for ansible output
        self._ansible_log_path = self.logs_dir / f"ansible-{self.deployment}-{int(time.time())}.log"
        self._ansible_log_file = open(self._ansible_log_path, "w")

        # Set up environment - suppress stdout callbacks to avoid Rich display corruption
        env = os.environ.copy()
        env["DOLOS_EVENT_FILE"] = str(self._event_file)
        env["ANSIBLE_STDOUT_CALLBACK"] = "minimal"  # Override yaml callback
        env["ANSIBLE_NOCOLOR"] = "1"  # No ANSI codes in log file
        env["ANSIBLE_FORCE_COLOR"] = "0"

        cmd = [
            "ansible-playbook",
            "-i", inventory,
            "-e", f"deployment_dir={self.deploy_dir}",
            playbook,
        ]

        # Start event reader thread
        self._stop_event.clear()
        self._reader_thread = threading.Thread(
            target=self._event_reader,
            args=(self._event_file,),
            daemon=True,
        )
        self._reader_thread.start()

        # Run playbook - redirect output to log file
        process = subprocess.Popen(
            cmd,
            stdout=self._ansible_log_file,
            stderr=subprocess.STDOUT,
            cwd=str(self.playbooks_dir),
            env=env,
        )

        # Update display while running
        while process.poll() is None:
            if self.live:
                self.live.update(self._build_display())
            time.sleep(0.1)

        # Stop reader and clean up
        self._stop_event.set()
        if self._reader_thread:
            self._reader_thread.join(timeout=1)

        # Close ansible log file
        if hasattr(self, "_ansible_log_file") and self._ansible_log_file:
            self._ansible_log_file.close()

        # Clean up event file
        try:
            self._event_file.unlink()
        except Exception:
            pass

        return process.returncode

    def run_provision(self) -> bool:
        """Run VM provisioning."""
        self.state_manager.phase = "provisioning"
        hosts_ini = self.deploy_dir / "hosts.ini"

        if not hosts_ini.exists():
            self.console.print(f"[red]Error: hosts.ini not found at {hosts_ini}[/red]")
            return False

        # Mark VMs as creating
        for vm in self.state_manager.vms.values():
            vm.status = VMStatus.CREATING
            vm.provision_start = time.time()

        playbook = str(self.playbooks_dir / "provision-vms.yaml")
        rc = self._run_ansible(playbook, str(hosts_ini))

        # Update any VMs still in creating state
        for vm in self.state_manager.vms.values():
            if vm.status == VMStatus.CREATING:
                if rc == 0:
                    vm.status = VMStatus.PROVISIONED
                    vm.provision_end = time.time()
                else:
                    vm.status = VMStatus.FAILED

        return rc == 0

    def run_install(self) -> bool:
        """Run SUP installation."""
        self.state_manager.phase = "installing"

        if not self.inventory_file.exists():
            self.console.print("[red]Error: inventory.ini not found. Run provision first.[/red]")
            return False

        # Mark provisioned VMs as installing
        for vm in self.state_manager.vms.values():
            if vm.status in (VMStatus.PROVISIONED, VMStatus.PENDING):
                vm.status = VMStatus.INSTALLING
                vm.install_start = time.time()

        playbook = str(self.playbooks_dir / "install-sups.yaml")
        rc = self._run_ansible(playbook, str(self.inventory_file))

        # Update VMs still in installing state
        for vm in self.state_manager.vms.values():
            if vm.status in (VMStatus.INSTALLING, VMStatus.STAGE1, VMStatus.REBOOTING, VMStatus.STAGE2):
                if rc == 0:
                    vm.status = VMStatus.COMPLETED
                    vm.install_end = time.time()

        return rc == 0

    def run(self):
        """Run the deployment."""
        self.start_time = time.time()

        # Initialize markdown log
        log_path = create_log_path(self.logs_dir, self.deployment)
        self.log_writer = MarkdownLogWriter(log_path, self.deployment_name)
        self.log_writer.open()

        try:
            with Live(
                self._build_display(),
                console=self.console,
                refresh_per_second=4,
                screen=True,
            ) as live:
                self.live = live

                if self.args.provision or not self.args.install:
                    success = self.run_provision()
                    live.update(self._build_display())

                    if not success and not self.args.provision:
                        return

                    if not self.args.provision:
                        time.sleep(2)

                if self.args.install or not self.args.provision:
                    self.run_install()
                    live.update(self._build_display())

                self.live = None

        except KeyboardInterrupt:
            self.console.print("\n[yellow]Interrupted[/yellow]")
        finally:
            # Close markdown log
            if self.log_writer:
                self.log_writer.close(self.state_manager)

            self._print_summary()

    def _print_summary(self):
        """Print final summary."""
        elapsed = time.time() - self.start_time if self.start_time else 0
        counts = self.state_manager.get_counts()

        self.console.print("\n")
        self.console.print("=" * 70)
        self.console.print(f"[bold]  DEPLOYMENT COMPLETE: {self.deployment_name}[/bold]")
        self.console.print("=" * 70)

        self.console.print(f"\n  Total VMs: {counts['total']}")
        if counts["completed"] > 0:
            self.console.print(f"  [green]Installed:   {counts['completed']}[/green]")
        if counts["provisioned"] > 0:
            self.console.print(f"  [yellow]Provisioned: {counts['provisioned']}[/yellow]")
        if counts["failed"] > 0:
            self.console.print(f"  [red]Failed:      {counts['failed']}[/red]")
        self.console.print(f"  [dim]Total time:  {self._format_duration(elapsed)}[/dim]")

        # Show failed VMs
        failed_vms = [v for v in self.state_manager.vms.values() if v.status == VMStatus.FAILED]
        if failed_vms:
            self.console.print(f"\n[red]Failed VMs:[/red]")
            for vm in sorted(failed_vms, key=lambda v: self._vm_sort_key(v.name)):
                self.console.print(f"  [red]x[/red] {vm.name}: {vm.error_msg[:50]}")

        # Final status
        if counts["failed"] == 0 and counts["completed"] == counts["total"]:
            self.console.print(f"\n[bold green]All {counts['total']} VMs completed successfully![/bold green]")
        elif counts["failed"] > 0:
            self.console.print(f"\n[yellow]Completed with {counts['failed']} failure(s)[/yellow]")

        # Log file location
        if self.log_writer:
            self.console.print(f"\n[dim]Log: {self.log_writer.log_path}[/dim]")

        # SSH config
        if self.ssh_config_file.exists():
            self.console.print("\n" + "-" * 70)
            self.console.print("[bold]  SSH CONFIG[/bold] - Copy to ~/.ssh/config")
            self.console.print("-" * 70)
            with open(self.ssh_config_file) as f:
                self.console.print(f.read(), highlight=False)
            self.console.print("-" * 70)


# =============================================================================
# TEARDOWN MONITOR
# =============================================================================


class TeardownMonitor:
    """Teardown monitor with live table."""

    def __init__(self, hosts_ini: Path):
        self.console = Console()
        self.hosts_ini = hosts_ini
        self.playbooks_dir = Path(__file__).parent / "playbooks"
        self.logs_dir = Path(__file__).parent / "logs"

        self.state_manager = StateManager()
        self.start_time: Optional[float] = None
        self.phase = "Discovering"
        self.live: Optional[Live] = None
        self._current_activity: str = "Starting..."
        self._ansible_log_path: Optional[Path] = None

        self._event_file: Optional[Path] = None
        self._stop_event = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None
        self.log_writer: Optional[MarkdownLogWriter] = None

    def _format_duration(self, seconds: float) -> str:
        h, r = divmod(int(seconds), 3600)
        m, s = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _resource_sort_key(self, name: str) -> tuple:
        """Sort sup-* resources."""
        if not name.startswith("sup-"):
            return (99, name)

        parts = name.replace("sup-", "").split("-")
        behavior = parts[0] if parts else ""
        order = {"C": 0, "M": 1, "B": 2, "BC": 3, "S": 4, "SC": 5}

        if behavior.startswith("BC"):
            cat, rest = "BC", behavior[2:]
        elif behavior.startswith("SC"):
            cat, rest = "SC", behavior[2:]
        elif behavior.startswith("C"):
            cat, rest = "C", behavior[1:]
        elif behavior.startswith("M"):
            cat, rest = "M", behavior[1:]
        elif behavior.startswith("B"):
            cat, rest = "B", behavior[1:]
        elif behavior.startswith("S"):
            cat, rest = "S", behavior[1:]
        else:
            return (99, name)

        num = 0
        for c in rest:
            if c.isdigit():
                num = num * 10 + int(c)
            else:
                break

        try:
            instance = int(parts[-1]) if parts else 0
        except ValueError:
            instance = 0

        return (order.get(cat, 99), num, instance)

    def _get_current_activity(self) -> str:
        """Read the last TASK line from ansible log."""
        if not self._ansible_log_path or not self._ansible_log_path.exists():
            return self._current_activity

        try:
            with open(self._ansible_log_path, "r") as f:
                lines = f.readlines()
                for line in reversed(lines[-50:]):
                    if line.startswith("TASK ["):
                        task = line.split("[", 1)[1].split("]")[0]
                        self._current_activity = task[:60]
                        return self._current_activity
                    elif line.startswith("PLAY ["):
                        play = line.split("[", 1)[1].split("]")[0]
                        self._current_activity = f"PLAY: {play[:55]}"
                        return self._current_activity
                    elif "sup-" in line and ("DELETED" in line or "deleting" in line.lower()):
                        # Show deletion progress
                        self._current_activity = line.strip()[:60]
                        return self._current_activity
        except Exception:
            pass

        return self._current_activity

    def _build_display(self) -> Table:
        """Build teardown display table."""
        elapsed = self._format_duration(time.time() - self.start_time) if self.start_time else "00:00:00"
        rcounts = self.state_manager.get_resource_counts()

        servers_total = rcounts["servers"]["total"]
        servers_deleted = rcounts["servers"]["deleted"]
        volumes_total = rcounts["volumes"]["total"]
        volumes_deleted = rcounts["volumes"]["deleted"]

        total = servers_total + volumes_total
        done = servers_deleted + volumes_deleted
        pct = int(done / total * 100) if total > 0 else 0
        bar_w = 25
        bar = "\u2588" * int(bar_w * done / total) + "\u2591" * (bar_w - int(bar_w * done / total)) if total > 0 else "\u2591" * bar_w

        # Get current activity
        activity = self._get_current_activity()

        table = Table(
            title=f"DOLOS Teardown ALL  [{bar}] {pct}%  {self.phase}  Elapsed: {elapsed}",
            caption=f"[dim]{activity}[/dim]",
            box=box.SIMPLE,
            show_header=True,
            header_style="bold",
            title_style="bold red",
        )

        table.add_column("Server", style="cyan", width=24)
        table.add_column("Status", justify="center", width=10)
        table.add_column("Deleted At", justify="center", width=12)

        sorted_servers = sorted(
            self.state_manager.servers.values(),
            key=lambda s: self._resource_sort_key(s.name)
        )

        # Show message if no servers discovered yet
        if not sorted_servers:
            table.add_row("[dim]Waiting for server discovery...[/dim]", "", "")

        for server in sorted_servers:
            if server.status == ResourceStatus.DELETED:
                status = "[green]DELETED[/green]"
                del_time = server.delete_time
            elif server.status == ResourceStatus.DELETING:
                status = "[yellow]DELETING[/yellow]"
                del_time = "[yellow]...[/yellow]"
            elif server.status == ResourceStatus.FAILED:
                status = "[red]FAILED[/red]"
                del_time = "[red]--[/red]"
            else:
                status = "[dim]PENDING[/dim]"
                del_time = "[dim]--:--:--[/dim]"

            table.add_row(server.name, status, del_time)

        if self.state_manager.volumes:
            table.add_row("", "", "")
            table.add_row(f"[bold]Volumes ({volumes_deleted}/{volumes_total})[/bold]", "", "")

            vol_pending = rcounts["volumes"]["pending"]
            vol_deleting = rcounts["volumes"]["deleting"]
            vol_deleted = rcounts["volumes"]["deleted"]

            if vol_pending > 0:
                table.add_row(f"  {vol_pending} pending", "[dim]PENDING[/dim]", "")
            if vol_deleting > 0:
                table.add_row(f"  {vol_deleting} in progress", "[yellow]DELETING[/yellow]", "")
            if vol_deleted > 0:
                table.add_row(f"  {vol_deleted} deleted", "[green]DELETED[/green]", "")

        return table

    def _event_reader(self, event_path: Path):
        """Background thread: read events."""
        # Suppress thread exception output
        def silent_excepthook(args):
            pass
        threading.excepthook = silent_excepthook

        # Wait for file to exist
        for _ in range(100):
            if event_path.exists():
                break
            time.sleep(0.1)

        if not event_path.exists():
            return

        try:
            with open(event_path, "r") as f:
                while not self._stop_event.is_set():
                    try:
                        line = f.readline()
                        if line:
                            event = parse_event(line)
                            if event:
                                self.state_manager.process_event(event)
                                # Update phase based on events
                                if event.type == "discovery_servers":
                                    self.phase = "Found servers"
                                elif event.type == "discovery_volumes":
                                    self.phase = "Found volumes"
                                elif event.type == "resource_deleted":
                                    rtype = event.data.get("type", "")
                                    self.phase = f"Deleting {rtype}s"
                                elif event.type == "playbook_end":
                                    self.phase = "Complete"

                                if self.log_writer:
                                    self.log_writer.log_event(event, self.state_manager)
                        else:
                            time.sleep(0.05)
                    except Exception:
                        pass  # Skip malformed events
        except Exception:
            pass

    def run(self) -> bool:
        """Run teardown with live display."""
        self.start_time = time.time()

        playbook = self.playbooks_dir / "teardown-all.yaml"
        if not playbook.exists():
            self.console.print(f"[red]Error: {playbook} not found[/red]")
            return False

        # Create event file
        fd, event_path = tempfile.mkstemp(suffix=".jsonl", prefix="dolos_teardown_")
        os.close(fd)
        self._event_file = Path(event_path)

        # Initialize log
        log_path = create_log_path(self.logs_dir, "teardown-all")
        self.log_writer = MarkdownLogWriter(log_path, "Teardown ALL")
        self.log_writer.open()

        # Create log file for ansible output
        self._ansible_log_path = self.logs_dir / f"ansible-teardown-{int(time.time())}.log"
        self._ansible_log_file = open(self._ansible_log_path, "w")

        # Set up environment - suppress stdout callbacks
        env = os.environ.copy()
        env["DOLOS_EVENT_FILE"] = str(self._event_file)
        env["ANSIBLE_STDOUT_CALLBACK"] = "minimal"
        env["ANSIBLE_NOCOLOR"] = "1"
        env["ANSIBLE_FORCE_COLOR"] = "0"

        cmd = [
            "ansible-playbook",
            "-i", str(self.hosts_ini),
            str(playbook),
        ]

        # Start reader
        self._stop_event.clear()
        self._reader_thread = threading.Thread(
            target=self._event_reader,
            args=(self._event_file,),
            daemon=True,
        )
        self._reader_thread.start()

        process = subprocess.Popen(
            cmd,
            stdout=self._ansible_log_file,
            stderr=subprocess.STDOUT,
            cwd=str(self.playbooks_dir),
            env=env,
        )

        try:
            with Live(
                self._build_display(),
                console=self.console,
                refresh_per_second=4,
                screen=True,
            ) as live:
                self.live = live

                while process.poll() is None:
                    live.update(self._build_display())
                    time.sleep(0.1)

                self.phase = "Complete"
                live.update(self._build_display())
                time.sleep(1)
                self.live = None

        except KeyboardInterrupt:
            self.console.print("\n[yellow]Interrupted[/yellow]")
            process.terminate()
            return False
        finally:
            self._stop_event.set()
            if self._reader_thread:
                self._reader_thread.join(timeout=1)

            # Close ansible log file
            if hasattr(self, "_ansible_log_file") and self._ansible_log_file:
                self._ansible_log_file.close()

            if self.log_writer:
                self.log_writer.close(self.state_manager)

            try:
                self._event_file.unlink()
            except Exception:
                pass

        self._print_summary()
        return process.returncode == 0

    def _print_summary(self):
        """Print teardown summary."""
        elapsed = self._format_duration(time.time() - self.start_time) if self.start_time else "00:00:00"
        rcounts = self.state_manager.get_resource_counts()

        self.console.print("=" * 70)
        self.console.print("[bold]  TEARDOWN COMPLETE[/bold]")
        self.console.print("=" * 70)

        if self.state_manager.servers:
            self.console.print(f"\n[bold]Servers ({rcounts['servers']['deleted']} deleted):[/bold]")
            sorted_servers = sorted(
                self.state_manager.servers.values(),
                key=lambda s: self._resource_sort_key(s.name)
            )
            for server in sorted_servers:
                if server.status == ResourceStatus.DELETED:
                    self.console.print(f"  [green]v[/green] {server.name}  [dim]{server.delete_time}[/dim]")
                elif server.status == ResourceStatus.FAILED:
                    self.console.print(f"  [red]x[/red] {server.name}  [red]FAILED[/red]")
                else:
                    self.console.print(f"  [dim]?[/dim] {server.name}")

        if self.state_manager.volumes:
            self.console.print(f"\n[bold]Volumes: {rcounts['volumes']['deleted']} deleted[/bold]")

        self.console.print(f"\n[dim]Total time: {elapsed}[/dim]")

        if self.log_writer:
            self.console.print(f"[dim]Log: {self.log_writer.log_path}[/dim]")


# =============================================================================
# MAIN
# =============================================================================


def list_deployments(script_dir: Path) -> list[str]:
    """List available deployments."""
    deployments = []
    for d in script_dir.iterdir():
        if d.is_dir() and (d / "config.yaml").exists():
            deployments.append(d.name)
    return sorted(deployments)


def main():
    parser = argparse.ArgumentParser(
        description="DOLOS Deployment Monitor - Event-driven TUI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                    # Interactive menu
  %(prog)s exp-2              # Full deploy
  %(prog)s exp-2 --provision  # Provision only
  %(prog)s exp-2 --install    # Install only
  %(prog)s exp-2 --dry-run    # Preview VMs
  %(prog)s --list             # List deployments
  %(prog)s --teardown-all     # Delete ALL sup-* servers/volumes
"""
    )

    parser.add_argument("deployment", nargs="?", help="Deployment name")
    parser.add_argument("--list", action="store_true", help="List deployments")
    parser.add_argument("--provision", action="store_true", help="Provision only")
    parser.add_argument("--install", action="store_true", help="Install only")
    parser.add_argument("--dry-run", action="store_true", help="Preview VMs")
    parser.add_argument("--teardown-all", action="store_true", help="Delete all sup-* resources")

    args = parser.parse_args()
    script_dir = Path(__file__).parent
    console = Console()

    if args.list:
        deployments = list_deployments(script_dir)
        if deployments:
            console.print("[bold]Available deployments:[/bold]")
            for i, d in enumerate(deployments, 1):
                console.print(f"  {i}) {d}")
        else:
            console.print("[yellow]No deployments found[/yellow]")
        return 0

    if args.teardown_all:
        console.print("[bold red]WARNING: Delete ALL sup-* servers and volumes![/bold red]")
        try:
            confirm = console.input("\n[red]Type 'DELETE ALL' to confirm:[/red] ").strip()
        except KeyboardInterrupt:
            console.print("\n[dim]Cancelled[/dim]")
            return 1

        if confirm != "DELETE ALL":
            console.print("[dim]Cancelled[/dim]")
            return 1

        hosts_ini = None
        for dep in list_deployments(script_dir):
            h = script_dir / dep / "hosts.ini"
            if h.exists():
                hosts_ini = h
                break

        if not hosts_ini:
            console.print("[red]Error: No hosts.ini found[/red]")
            return 1

        monitor = TeardownMonitor(hosts_ini)
        success = monitor.run()
        return 0 if success else 1

    if not args.deployment:
        menu = InteractiveMenu()
        menu.run()
        return 0

    try:
        monitor = DeploymentMonitor(args.deployment, args)
    except FileNotFoundError as e:
        console.print(f"[red]Error: {e}[/red]")
        return 1

    if args.dry_run:
        console.print(f"[bold]Deployment: {monitor.deployment_name}[/bold]")
        console.print(f"VMs to deploy ({len(monitor.state_manager.vms)}):\n")

        categories = [
            ("Control (C)", lambda v: v.behavior.startswith("C") and not v.behavior.startswith("BC")),
            ("MCHP (M)", lambda v: v.behavior.startswith("M")),
            ("BrowserUse GPU (B)", lambda v: v.behavior.startswith("B") and not v.behavior.startswith("BC")),
            ("BrowserUse CPU (BC)", lambda v: v.behavior.startswith("BC")),
            ("SmolAgents GPU (S)", lambda v: v.behavior.startswith("S") and not v.behavior.startswith("SC")),
            ("SmolAgents CPU (SC)", lambda v: v.behavior.startswith("SC")),
        ]

        for cat_name, cat_filter in categories:
            cat_vms = [v for v in monitor.state_manager.vms.values() if cat_filter(v)]
            if cat_vms:
                cat_vms_sorted = sorted(cat_vms, key=lambda v: monitor._vm_sort_key(v.name))
                console.print(f"[cyan]{cat_name}[/cyan] ({len(cat_vms)} VMs):")
                for vm in cat_vms_sorted:
                    flavor = monitor._short_flavor(vm.flavor)
                    console.print(f"  - {vm.name} ({vm.behavior}) [{flavor}]")
                console.print()

        return 0

    monitor.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
