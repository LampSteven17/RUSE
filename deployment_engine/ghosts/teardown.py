"""GHOSTS NPC teardown — single deployment.

Direct OpenStack VM deletion (no Ansible playbook). After deletion,
finalize_teardown handles the shared epilogue.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..core import output
from ..core.openstack import OpenStack
from ..core.teardown_steps import finalize_teardown, make_dep_id


def run_ghosts_teardown(
    config_dir: Path, config_name: str, run_id: str, deploy_dir: Path,
) -> int:
    """Teardown a GHOSTS deployment."""
    run_dir = config_dir / "runs" / run_id

    output.banner(f"TEARDOWN: {config_name}/{run_id} (ghosts)")

    dep_id = make_dep_id(config_name, run_id)
    g_hash = hashlib.md5(dep_id.encode()).hexdigest()[:5]
    g_prefix = f"g-{g_hash}-"
    os_client = OpenStack()

    # Step 1: Delete VMs
    output.info("[1/1] Deleting GHOSTS VMs...")
    servers = os_client.server_list_with_ids(prefix=g_prefix)
    if servers:
        output.info(f"  Deleting {len(servers)} VMs...")
        for s in servers:
            os_client.server_delete(s["id"])
            output.info(f"    Deleted {s['name']}")
    else:
        output.info("  No GHOSTS VMs found")

    # Shared epilogue: ssh / volumes / phase / rmtree / feedback-dir.
    # poll_for_zero=True — direct OpenStack delete is async.
    ok = finalize_teardown(
        config_name, config_dir, run_id, run_dir,
        vm_prefix=g_prefix,
        feedback_marker="ghosts-feedback-",
        poll_for_zero=True,
    )
    if ok:
        output.info("")
        output.info("DONE: all GHOSTS VMs deleted")
    return 0 if ok else 1
