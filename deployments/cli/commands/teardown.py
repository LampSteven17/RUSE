"""Teardown commands — specific deployment and teardown-all."""

from __future__ import annotations

import re
from pathlib import Path

from .. import output
from ..ansible_runner import AnsibleRunner, default_event_handler
from ..config import DeploymentConfig
from ..openstack import OpenStack
from ..ssh_config import remove_all_ruse_blocks, remove_ssh_config


def run_teardown(target: str, deploy_dir: Path) -> int:
    """Teardown a specific deployment run. Target format: name-MMDDYYHHMMSS."""
    match = re.match(r"^(.+)-(\d{12})$", target)
    if not match:
        output.error(f"ERROR: Invalid teardown target: {target} (expected: name-MMDDYYHHMMSS)")
        return 1

    config_name = match.group(1)
    run_id = match.group(2)
    config_dir = deploy_dir / config_name
    config_file = config_dir / "config.yaml"

    if not config_file.exists():
        output.error(f"ERROR: No config.yaml found for: {config_name}")
        return 1

    config = DeploymentConfig.load(config_file)

    if config.is_rampart():
        return _rampart_teardown(config_dir, config_name, run_id, config, deploy_dir)

    if config.is_ghosts():
        return _ghosts_teardown(config_dir, config_name, run_id, deploy_dir)

    return _sup_teardown(config_dir, config_name, run_id, deploy_dir)


def _sup_teardown(config_dir: Path, config_name: str, run_id: str, deploy_dir: Path) -> int:
    """Teardown a SUP deployment."""
    run_dir = config_dir / "runs" / run_id
    if not run_dir.is_dir():
        output.error(f"ERROR: No run directory found for: {config_name}/{run_id}")
        return 1

    output.banner(f"TEARDOWN: {config_name}/{run_id}")

    # Find hosts.ini
    hosts_ini = _find_hosts_ini(config_dir, deploy_dir)
    if not hosts_ini:
        output.info("ERROR: No hosts.ini found")
        return 1

    # Build dep_id
    dep_id = _make_dep_id(config_name, run_id)
    vm_prefix = f"r-{dep_id}-"

    runner = AnsibleRunner(deploy_dir / "playbooks", deploy_dir / "logs")

    result = runner.run_playbook(
        "teardown.yaml",
        hosts_ini,
        extra_vars={
            "deployment_dir": str(config_dir),
            "deployment_id": dep_id,
            "run_dir": str(run_dir),
        },
        on_event=default_event_handler,
    )

    remove_ssh_config(f"{config_name}/{run_id}")

    # Verify via OpenStack
    os_client = OpenStack()
    remaining = os_client.count_vms_with_prefix(vm_prefix)

    if remaining > 0:
        output.info("")
        output.info(f"WARNING: {remaining} VMs still exist on OpenStack (prefix: {vm_prefix})")
        output.info("Local state preserved. Re-run teardown or use --all.")
        return 1

    output.info("Verified: 0 VMs remaining on OpenStack")

    # Cleanup local state
    if config_name.startswith("ruse-feedback-"):
        _safe_rmtree(config_dir)
    elif run_dir.is_dir():
        for f in ("inventory.ini", "ssh_config_snippet.txt", "config.yaml"):
            (run_dir / f).unlink(missing_ok=True)
        try:
            run_dir.rmdir()
        except OSError:
            pass

    return result.rc


def _rampart_teardown(
    config_dir: Path, config_name: str, run_id: str, config: DeploymentConfig, deploy_dir: Path,
) -> int:
    """Teardown a RAMPART enterprise deployment."""
    import hashlib
    import json
    import time

    run_dir = config_dir / "runs" / run_id

    output.banner(f"TEARDOWN: {config_name}/{run_id} (rampart)")

    dep_id = _make_dep_id(config_name, run_id)
    ent_hash = hashlib.md5(dep_id.encode()).hexdigest()[:5]
    ent_vm_prefix = f"e-{ent_hash}-"
    os_client = OpenStack()

    # Step 1: Kill emulation
    output.info("[1/4] Stopping emulation...")
    pid_file = run_dir / "emulate.pid"
    if pid_file.exists():
        _kill_pid_file(pid_file)
    else:
        output.info("  No emulate.pid found -- skipping")

    # Step 2: Delete VMs by prefix
    output.info("[2/4] Deleting VMs...")
    servers = os_client.server_list_with_ids(prefix=ent_vm_prefix)
    if servers:
        output.info(f"  Deleting {len(servers)} VMs (prefix {ent_vm_prefix})...")
        for s in servers:
            os_client.server_delete(s["id"])
            output.info(f"    Deleted {s['name']}")
    else:
        output.info("  No RAMPART VMs found")

    # Step 3: DNS cleanup (scoped to this deployment's zone)
    output.info("[3/4] Cleaning up DNS zone...")
    zone_marker = run_dir / "dns_zone.txt"
    zone_deleted = False
    if zone_marker.exists():
        zone_name = zone_marker.read_text().strip()
        z = os_client.zone_find(zone_name)
        if z:
            os_client.zone_delete(z["id"])
            output.info(f"  Deleted DNS zone: {zone_name}")
            zone_deleted = True
        else:
            output.info(f"  Zone not found: {zone_name} (already deleted?)")
            zone_deleted = True
    else:
        # Fallback for deployments created before zone isolation:
        # match zones containing this deployment's hash
        for z in os_client.zone_list():
            zname = z.get("name", "")
            if ent_hash in zname:
                os_client.zone_delete(z["id"])
                output.info(f"  Deleted DNS zone: {zname}")
                zone_deleted = True
    if not zone_deleted:
        output.info("  No DNS zones found for this deployment")

    # Step 4: Verify
    output.info("[4/4] Verifying...")
    remove_ssh_config(f"{config_name}/{run_id}")

    for attempt in range(1, 21):
        os_client.invalidate_cache()
        remaining = os_client.count_vms_with_prefix(ent_vm_prefix)
        if remaining == 0:
            break
        output.info(f"  Waiting for {remaining} VMs to delete... ({attempt}/20)")
        time.sleep(5)

    os_client.invalidate_cache()
    remaining = os_client.count_vms_with_prefix(ent_vm_prefix)

    if remaining > 0:
        output.info("")
        output.info(f"WARNING: {remaining} enterprise VMs still exist on OpenStack")
        return 1

    if run_dir.is_dir():
        _safe_rmtree(run_dir)
        output.info("  Removed local run directory")

    output.info("")
    output.info("DONE: all RAMPART VMs deleted")
    return 0


def _ghosts_teardown(
    config_dir: Path, config_name: str, run_id: str, deploy_dir: Path,
) -> int:
    """Teardown a GHOSTS deployment."""
    import hashlib
    import time

    run_dir = config_dir / "runs" / run_id

    output.banner(f"TEARDOWN: {config_name}/{run_id} (ghosts)")

    dep_id = _make_dep_id(config_name, run_id)
    g_hash = hashlib.md5(dep_id.encode()).hexdigest()[:5]
    g_prefix = f"g-{g_hash}-"
    os_client = OpenStack()

    # Step 1: Delete VMs
    output.info("[1/2] Deleting GHOSTS VMs...")
    servers = os_client.server_list_with_ids(prefix=g_prefix)
    if servers:
        output.info(f"  Deleting {len(servers)} VMs...")
        for s in servers:
            os_client.server_delete(s["id"])
            output.info(f"    Deleted {s['name']}")
    else:
        output.info("  No GHOSTS VMs found")

    # Step 2: Verify
    output.info("[2/2] Verifying...")
    remove_ssh_config(f"{config_name}/{run_id}")

    for attempt in range(1, 21):
        os_client.invalidate_cache()
        remaining = os_client.count_vms_with_prefix(g_prefix)
        if remaining == 0:
            break
        output.info(f"  Waiting for {remaining} VMs to delete... ({attempt}/20)")
        time.sleep(5)

    os_client.invalidate_cache()
    remaining = os_client.count_vms_with_prefix(g_prefix)

    if remaining > 0:
        output.info("")
        output.info(f"WARNING: {remaining} GHOSTS VMs still exist on OpenStack")
        return 1

    # Cleanup local state
    if config_name.startswith("ghosts-feedback-"):
        _safe_rmtree(config_dir)
        output.info("  Removed feedback config directory")
    elif run_dir.is_dir():
        _safe_rmtree(run_dir)
        output.info("  Removed local run directory")

    output.info("")
    output.info("DONE: all GHOSTS VMs deleted")
    return 0


def run_teardown_all(deploy_dir: Path) -> int:
    """Delete ALL RUSE, Enterprise, and GHOSTS VMs."""
    output.banner("TEARDOWN ALL")
    output.info("This will DELETE ALL RUSE (r-*), Enterprise (e-*), and GHOSTS (g-*) servers and volumes!")
    output.info("This also catches legacy sup-* VMs.")
    output.info("")

    if not output.confirm_destructive("Confirm teardown-all?"):
        output.info("Teardown cancelled.")
        return 0

    hosts_ini = _find_hosts_ini(None, deploy_dir)
    if not hosts_ini:
        output.error("ERROR: No hosts.ini found")
        return 1

    runner = AnsibleRunner(deploy_dir / "playbooks", deploy_dir / "logs")
    output.info("")
    output.section("[Teardown]")

    result = runner.run_playbook(
        "teardown-all.yaml",
        hosts_ini,
        extra_vars={"deployment_dir": str(deploy_dir)},
        on_event=default_event_handler,
    )

    # Remove all RUSE SSH config blocks
    removed = remove_all_ruse_blocks()
    if removed:
        output.info(f"  Removed {len(removed)} SSH config blocks")

    # Clean up inventory files across all deployments
    for config_dir in deploy_dir.iterdir():
        if not config_dir.is_dir():
            continue
        runs_dir = config_dir / "runs"
        if runs_dir.is_dir():
            for run_dir in runs_dir.iterdir():
                for f in ("inventory.ini", "ssh_config_snippet.txt"):
                    (run_dir / f).unlink(missing_ok=True)

    output.info("")
    output.info("DONE.")
    return result.rc


# --- Helpers ---

def _find_hosts_ini(config_dir: Path | None, deploy_dir: Path) -> Path | None:
    """Find hosts.ini, checking config dir first, then deploy_dir root."""
    if config_dir and (config_dir / "hosts.ini").exists():
        return config_dir / "hosts.ini"
    if (deploy_dir / "hosts.ini").exists():
        return deploy_dir / "hosts.ini"
    # Search subdirectories
    for d in deploy_dir.iterdir():
        if d.is_dir() and (d / "hosts.ini").exists():
            return d / "hosts.ini"
    return None


def _make_dep_id(deployment_name: str, run_id: str) -> str:
    dep = deployment_name
    for prefix in ("ruse-", "sup-", "ghosts-", "rampart-", "enterprise-"):
        if dep.startswith(prefix):
            dep = dep[len(prefix):]
    dep = dep.replace("-", "")
    return f"{dep}{run_id}"


def _kill_pid_file(pid_file: Path) -> None:
    """Kill a process from a PID file."""
    import signal

    try:
        pid = int(pid_file.read_text().strip())
        try:
            import os
            os.kill(pid, signal.SIGTERM)
            output.dim(f"  Killed emulation PID {pid}")
        except ProcessLookupError:
            output.dim(f"  Emulation PID {pid} already stopped")
    except (ValueError, FileNotFoundError):
        output.dim("  Could not read emulate.pid")



def _safe_rmtree(path: Path) -> None:
    """Recursively remove a directory."""
    import shutil
    try:
        shutil.rmtree(path)
    except OSError:
        pass
