"""Shrink a running deployment in-place to match its top-level config.yaml.

Diffs the run's config snapshot against the top-level (desired) config,
deletes the delta VMs from OpenStack, and cleans up:
  - inventory.ini
  - ssh_config_snippet.txt + ~/.ssh/config block (reinstalled)
  - PHASE experiments.json (removes the IPs)
  - run config.yaml snapshot (overwritten with desired)

Usage:
    ./shrink ruse-controls-040226205037

Surviving VMs keep running with their existing behavioral configs — no
reboot, no reinstall.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import yaml

from .. import output
from ..openstack import OpenStack
from ..ssh_config import install_ssh_config


EXPERIMENTS_JSON = Path("/mnt/AXES2U1/experiments.json")


def run_shrink(target: str, deploy_dir: Path) -> int:
    """Shrink a deployment to match its top-level config.yaml."""
    match = re.match(r"^(.+)-(\d{12})$", target)
    if not match:
        output.error(f"ERROR: Invalid target: {target} (expected: name-MMDDYYHHMMSS)")
        return 1

    config_name = match.group(1)
    run_id = match.group(2)
    config_dir = deploy_dir / config_name
    run_dir = config_dir / "runs" / run_id

    if not run_dir.is_dir():
        output.error(f"ERROR: Run directory not found: {run_dir}")
        return 1

    snapshot_path = run_dir / "config.yaml"
    desired_path = config_dir / "config.yaml"

    if not snapshot_path.exists():
        output.error(f"ERROR: No snapshot config: {snapshot_path}")
        return 1
    if not desired_path.exists():
        output.error(f"ERROR: No top-level config: {desired_path}")
        return 1

    snapshot = yaml.safe_load(snapshot_path.read_text())
    desired = yaml.safe_load(desired_path.read_text())

    snap_counts = _behavior_counts(snapshot.get("deployments", []))
    want_counts = _behavior_counts(desired.get("deployments", []))

    # Diff: behaviors with surplus VMs (current > desired) get trimmed.
    # Behaviors only in desired (current < desired) need ./deploy, not shrink.
    to_remove: dict[str, int] = {}
    to_add: dict[str, int] = {}
    for beh, snap_n in snap_counts.items():
        want_n = want_counts.get(beh, 0)
        if snap_n > want_n:
            to_remove[beh] = snap_n - want_n
    for beh, want_n in want_counts.items():
        snap_n = snap_counts.get(beh, 0)
        if want_n > snap_n:
            to_add[beh] = want_n - snap_n

    if not to_remove and not to_add:
        output.info("Nothing to do — snapshot already matches desired config.")
        return 0

    # Map behavior → list of {name, ip} from inventory
    inventory_path = run_dir / "inventory.ini"
    if not inventory_path.exists():
        output.error(f"ERROR: No inventory.ini at {inventory_path}")
        return 1

    vm_by_behavior: dict[str, list[dict]] = {}
    for line in inventory_path.read_text().splitlines():
        m = re.match(r"^(\S+)\s+ansible_host=(\S+)\s+sup_behavior=(\S+)", line)
        if m:
            vm_by_behavior.setdefault(m.group(3), []).append({
                "name": m.group(1),
                "ip": m.group(2),
            })

    # Build VM list to remove (highest-index first for stability)
    vms_to_remove: list[dict] = []
    for beh, n in to_remove.items():
        existing = sorted(vm_by_behavior.get(beh, []), key=lambda v: v["name"], reverse=True)
        vms_to_remove.extend(existing[:n])

    # Plan
    output.banner(f"SHRINK: {config_name}/{run_id}")
    output.info("")
    if to_add:
        output.info("WARNING: desired config has behaviors not in snapshot — use ./deploy to add:")
        for beh, n in sorted(to_add.items()):
            output.info(f"  + {beh} x{n}")
        output.info("")

    if not vms_to_remove:
        output.info("Nothing to remove (only additions requested).")
        return 0

    output.info(f"Will remove {len(vms_to_remove)} VMs:")
    for vm in vms_to_remove:
        output.info(f"  - {vm['name']}  ({vm['ip']})")
    output.info("")

    total_before = sum(snap_counts.values())
    total_after = total_before - len(vms_to_remove)
    output.info(f"VM count: {total_before} -> {total_after}")
    output.info("")

    if not output.confirm("Proceed with shrink?"):
        output.info("Cancelled.")
        return 0

    remove_names = {vm["name"] for vm in vms_to_remove}
    remove_ips = {vm["ip"] for vm in vms_to_remove}

    # [1/5] Delete VMs from OpenStack
    output.info("")
    output.info("[1/5] Deleting VMs from OpenStack...")
    os_client = OpenStack()
    all_servers = os_client.server_list_with_ids()
    name_to_id = {s["name"]: s["id"] for s in all_servers}
    failed_deletes: list[str] = []
    for vm in vms_to_remove:
        sid = name_to_id.get(vm["name"])
        if not sid:
            output.info(f"  -- {vm['name']}  (not found on OpenStack, already gone?)")
            continue
        if os_client.server_delete(sid):
            output.info(f"  OK {vm['name']}")
        else:
            output.info(f"  FAIL {vm['name']}")
            failed_deletes.append(vm["name"])

    # [2/5] Clean inventory.ini
    output.info("")
    output.info("[2/5] Cleaning inventory.ini...")
    new_lines = []
    removed = 0
    for line in inventory_path.read_text().splitlines():
        m = re.match(r"^(\S+)\s+ansible_host=", line)
        if m and m.group(1) in remove_names:
            removed += 1
            continue
        new_lines.append(line)
    inventory_path.write_text("\n".join(new_lines) + "\n")
    output.info(f"  Removed {removed} entries")

    # [3/5] Clean SSH config snippet + reinstall ~/.ssh/config block
    output.info("")
    output.info("[3/5] Cleaning SSH config snippet + reinstalling block...")
    snippet_path = run_dir / "ssh_config_snippet.txt"
    if snippet_path.exists():
        text = snippet_path.read_text()
        for name in remove_names:
            text = re.sub(
                rf"Host {re.escape(name)}\n\s+HostName \S+\n\n?",
                "",
                text,
            )
        snippet_path.write_text(text)
        install_ssh_config(snippet_path, f"{config_name}/{run_id}")
    else:
        output.info("  No snippet file (skipped)")

    # [4/5] Clean PHASE experiments.json
    output.info("")
    output.info("[4/5] Cleaning experiments.json...")
    if EXPERIMENTS_JSON.exists():
        try:
            data = json.loads(EXPERIMENTS_JSON.read_text())
            entry = data.get(config_name, {})
            ips_dict = entry.get("ips", {})
            removed_ips = 0
            for ip in list(ips_dict.keys()):
                if ip in remove_ips:
                    del ips_dict[ip]
                    removed_ips += 1
            EXPERIMENTS_JSON.write_text(json.dumps(data, indent=4) + "\n")
            output.info(f"  Removed {removed_ips} IPs ({len(ips_dict)} remaining)")
        except (json.JSONDecodeError, OSError) as e:
            output.info(f"  WARNING: failed to update experiments.json: {e}")
    else:
        output.info(f"  No experiments.json at {EXPERIMENTS_JSON} (skipped)")

    # [5/5] Update run config.yaml snapshot
    output.info("")
    output.info("[5/5] Updating run config.yaml snapshot...")
    shutil.copy2(desired_path, snapshot_path)
    output.info(f"  Snapshot updated from {desired_path}")

    # Summary
    succeeded = len(vms_to_remove) - len(failed_deletes)
    output.info("")
    output.banner("SHRINK COMPLETE")
    output.info(f"  Removed: {succeeded}/{len(vms_to_remove)} VMs")
    if failed_deletes:
        output.info(f"  Failed:  {len(failed_deletes)}")
        for name in failed_deletes:
            output.info(f"    {name}")
    output.info(f"  Surviving: {total_before - succeeded} VMs in {config_name}/{run_id}")
    output.info("")

    return 1 if failed_deletes else 0


def _behavior_counts(deployments: list[dict]) -> dict[str, int]:
    """Aggregate behavior counts from a deployments list."""
    counts: dict[str, int] = {}
    for d in deployments:
        beh = d.get("behavior")
        if beh:
            counts[beh] = counts.get(beh, 0) + d.get("count", 1)
    return counts
