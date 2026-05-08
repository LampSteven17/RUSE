"""RAMPART enterprise teardown — single deployment.

RAMPART teardown is direct OpenStack iteration (no Ansible playbook for
deletion). Pre-step kills emulate.pid; mid-step deletes the per-deploy
DNS zone. After VM deletion, finalize_teardown handles the shared
epilogue (ssh / volumes / phase / rmtree / feedback-dir).
"""

from __future__ import annotations

import hashlib
import os
import signal
from pathlib import Path

from ... import output
from ...config import DeploymentConfig
from ...openstack import OpenStack
from ..shared.teardown_helpers import finalize_teardown, make_dep_id


def run_rampart_teardown(
    config_dir: Path, config_name: str, run_id: str,
    config: DeploymentConfig, deploy_dir: Path,
) -> int:
    """Teardown a RAMPART enterprise deployment."""
    run_dir = config_dir / "runs" / run_id

    output.banner(f"TEARDOWN: {config_name}/{run_id} (rampart)")

    dep_id = make_dep_id(config_name, run_id)
    ent_hash = hashlib.md5(dep_id.encode()).hexdigest()[:5]
    ent_vm_prefix = f"r-{ent_hash}-"
    os_client = OpenStack()

    # Step 1: Kill emulation
    output.info("[1/3] Stopping emulation...")
    pid_file = run_dir / "emulate.pid"
    if pid_file.exists():
        _kill_pid_file(pid_file)
    else:
        output.info("  No emulate.pid found -- skipping")

    # Step 2: Delete VMs by prefix
    output.info("[2/3] Deleting VMs...")
    servers = os_client.server_list_with_ids(prefix=ent_vm_prefix)
    if servers:
        output.info(f"  Deleting {len(servers)} VMs (prefix {ent_vm_prefix})...")
        for s in servers:
            os_client.server_delete(s["id"])
            output.info(f"    Deleted {s['name']}")
    else:
        output.info("  No RAMPART VMs found")

    # Step 3: DNS cleanup (scoped to this deployment's zone)
    output.info("[3/3] Cleaning up DNS zone...")
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
        # Fallback for deployments created before zone isolation: match
        # zones containing this deployment's hash
        for z in os_client.zone_list():
            zname = z.get("name", "")
            if ent_hash in zname:
                os_client.zone_delete(z["id"])
                output.info(f"  Deleted DNS zone: {zname}")
                zone_deleted = True
    if not zone_deleted:
        output.info("  No DNS zones found for this deployment")

    # Shared epilogue: ssh / volumes / phase / rmtree / feedback-dir.
    # poll_for_zero=True because direct OpenStack delete is async — we
    # need to wait for it to complete before declaring success.
    ok = finalize_teardown(
        config_name, config_dir, run_id, run_dir,
        vm_prefix=ent_vm_prefix,
        feedback_marker="rampart-feedback-",
        poll_for_zero=True,
    )
    if ok:
        output.info("")
        output.info("DONE: all RAMPART VMs deleted")
    return 0 if ok else 1


def _kill_pid_file(pid_file: Path) -> None:
    """Kill a process from a PID file. RAMPART-only — emulate.pid pattern."""
    try:
        pid = int(pid_file.read_text().strip())
        try:
            os.kill(pid, signal.SIGTERM)
            output.dim(f"  Killed emulation PID {pid}")
        except ProcessLookupError:
            output.dim(f"  Emulation PID {pid} already stopped")
    except (ValueError, FileNotFoundError):
        output.dim("  Could not read emulate.pid")
