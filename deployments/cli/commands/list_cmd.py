"""List active deployments."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .. import output
from ..config import DeploymentConfig
from ..openstack import OpenStack


def run_list(deploy_dir: Path) -> int:
    """Display all active deployments."""
    output.info("")
    output.banner("ACTIVE DEPLOYMENTS")
    output.info("")

    output.dim("  Querying OpenStack...")
    os_client = OpenStack()

    rows = []

    for config_dir in sorted(deploy_dir.iterdir()):
        config_file = config_dir / "config.yaml"
        if not config_file.exists() or not config_dir.is_dir():
            continue

        name = config_dir.name
        runs_dir = config_dir / "runs"
        if not runs_dir.is_dir():
            continue

        try:
            config = DeploymentConfig.load(config_file)
        except Exception:
            continue

        for run_dir in sorted(runs_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            rid = run_dir.name

            is_active = _check_active(
                run_dir, name, rid, config, os_client, deploy_dir,
            )
            if not is_active:
                continue

            vm_summary = _get_vm_summary(run_dir, config)
            date_col = _format_run_date(rid)
            target = f"{name}-{rid}"

            rows.append([target, vm_summary, date_col])

    if not rows:
        output.dim("  No active deployments.")
        output.info("")
        return 0

    output.table(["Target", "VMs", "Date"], rows)
    output.info("")
    return 0


def _check_active(
    run_dir: Path,
    name: str,
    rid: str,
    config: DeploymentConfig,
    os_client: OpenStack,
    deploy_dir: Path,
) -> bool:
    """Check if a run is still active."""
    # Has inventory or deployment_type marker → definitely active
    if (run_dir / "inventory.ini").exists():
        return True
    if (run_dir / "deployment_type").exists():
        return True

    # Check OpenStack for VMs with matching prefix
    dep_id = _make_dep_id(name, rid)
    if config.is_rampart():
        ent_hash = hashlib.md5(dep_id.encode()).hexdigest()[:5]
        return os_client.has_vms_with_prefix(f"e-{ent_hash}-")
    elif config.is_ghosts():
        g_hash = hashlib.md5(dep_id.encode()).hexdigest()[:5]
        return os_client.has_vms_with_prefix(f"g-{g_hash}-")
    else:
        return os_client.has_vms_with_prefix(f"r-{dep_id}-")


def _get_vm_summary(run_dir: Path, config: DeploymentConfig) -> str:
    """Get VM count summary from inventory or config."""
    if config.is_rampart():
        return _get_enterprise_vm_count(run_dir)

    if config.is_ghosts():
        client_count = config.ghosts_client_count()
        return f"{1 + client_count} ghosts (1 api + {client_count} npc)"

    # Try inventory.ini first (actual deployed)
    inv_path = run_dir / "inventory.ini"
    if inv_path.exists():
        return _count_brains_from_inventory(inv_path)

    # Fall back to config
    return config.brain_summary()


def _count_brains_from_inventory(inv_path: Path) -> str:
    """Count brains from inventory.ini sup_behavior= fields."""
    counts = {"C": 0, "M": 0, "B": 0, "S": 0, "total": 0}
    for line in inv_path.read_text().splitlines():
        match = re.search(r"sup_behavior=(\S+)", line)
        if match:
            b = match.group(1)
            counts["total"] += 1
            if b.startswith("C"):
                counts["C"] += 1
            elif b.startswith("M"):
                counts["M"] += 1
            elif b.startswith("B"):
                counts["B"] += 1
            elif b.startswith("S"):
                counts["S"] += 1

    parts = []
    if counts["C"]:
        parts.append(f"{counts['C']}c")
    if counts["M"]:
        parts.append(f"{counts['M']}m")
    if counts["B"]:
        parts.append(f"{counts['B']}b")
    if counts["S"]:
        parts.append(f"{counts['S']}s")

    if parts:
        return f"{counts['total']} ({' '.join(parts)})"
    return str(counts["total"])


def _get_enterprise_vm_count(run_dir: Path) -> str:
    """Get VM count from enterprise deploy-output.json."""
    import json

    nodes = None
    for fname in ("enterprise-config-prefixed.json", "deploy-output.json"):
        fpath = run_dir / fname
        if not fpath.exists():
            continue
        try:
            data = json.loads(fpath.read_text())
            if isinstance(data, list):
                nodes = data
            elif isinstance(data, dict):
                # enterprise-config-prefixed.json: {"nodes": [...]}
                # deploy-output.json: {"enterprise_built": {"deployed": {"nodes": [...]}}}
                nodes = data.get("nodes")
                if nodes is None:
                    nodes = (
                        data.get("enterprise_built", {})
                        .get("deployed", {})
                        .get("nodes", [])
                    )
            if nodes:
                break
        except (json.JSONDecodeError, TypeError):
            continue

    if not nodes:
        return "?"

    total = len(nodes)
    endpoints = sum(
        1 for n in nodes
        if "endpoint" in n.get("roles", [])
    )
    infra = total - endpoints
    return f"{total} ({infra} infra + {endpoints} ep)"


def _make_dep_id(deployment_name: str, run_id: str) -> str:
    """Build dep_id from deployment name + run_id."""
    # Strip common prefixes
    dep = deployment_name
    for prefix in ("ruse-", "sup-", "ghosts-", "rampart-", "enterprise-"):
        if dep.startswith(prefix):
            dep = dep[len(prefix):]
    dep = dep.replace("-", "")
    return f"{dep}{run_id}"


def _format_run_date(rid: str) -> str:
    """Format a run ID (MMDDYYHHmmss) into readable date."""
    if len(rid) >= 12:
        return f"{rid[0:2]}/{rid[2:4]} {rid[6:8]}:{rid[8:10]}"
    return "-"
