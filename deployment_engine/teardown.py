"""Teardown router — dispatches to per-type modules under decoy/, rampart/,
ghosts/. Also owns the `--all` nuke path (single playbook covering all 3
prefixes via teardown-all.yaml regex)."""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from .core import output
from .core.ansible_runner import AnsibleRunner, default_event_handler
from .core.config import DeploymentConfig
from .core.openstack import OpenStack
from .core.ssh_config import remove_all_managed_blocks
from .core.teardown_steps import find_hosts_ini, make_dep_id, safe_rmtree


def run_teardown(target: str, deploy_dir: Path) -> int:
    """Teardown a specific deployment run. Target format: name-MMDDYYHHMMSS.

    Dispatches by deploy type to the per-subsystem teardown.
    """
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
        from .rampart.teardown import run_rampart_teardown
        return run_rampart_teardown(config_dir, config_name, run_id, config, deploy_dir)

    if config.is_ghosts():
        from .ghosts.teardown import run_ghosts_teardown
        return run_ghosts_teardown(config_dir, config_name, run_id, deploy_dir)

    from .decoy.teardown import run_decoy_teardown
    return run_decoy_teardown(config_dir, config_name, run_id, deploy_dir)


def run_teardown_filtered(
    deploy_dir: Path,
    types: dict[str, bool],
    feedback_only: bool,
) -> int:
    """Teardown all active deployments matching the given filters.

    types: {"decoy": bool, "rampart": bool, "ghosts": bool} — only
    deployments matching any selected type get torn down. If all False,
    matches nothing (caller should prevent that).

    feedback_only: only target deployments named *-feedback-* (vs controls).
    """
    matches: list[tuple[str, str, Path]] = []  # (config_name, run_id, config_dir)

    for config_dir in sorted(deploy_dir.iterdir()):
        if not config_dir.is_dir():
            continue
        config_file = config_dir / "config.yaml"
        if not config_file.exists():
            continue

        try:
            config = DeploymentConfig.load(config_file)
        except Exception as e:
            output.error(f"  WARNING: skipping {config_dir.name}/config.yaml: {e}")
            continue

        # Type filter
        if config.is_rampart():
            if not types.get("rampart"):
                continue
        elif config.is_ghosts():
            if not types.get("ghosts"):
                continue
        else:
            if not types.get("decoy"):
                continue

        # Feedback filter
        if feedback_only and "-feedback-" not in config_dir.name:
            continue

        # Per-run iteration
        runs_dir = config_dir / "runs"
        if not runs_dir.is_dir():
            continue
        for run_dir in sorted(runs_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            if not _is_run_active(config_dir, run_dir, config_dir.name, run_dir.name, config):
                continue
            matches.append((config_dir.name, run_dir.name, config_dir))

    if not matches:
        output.info("No active deployments match the filter.")
        return 0

    output.banner(f"FILTERED TEARDOWN — {len(matches)} deployments")
    for cn, rid, _ in matches:
        output.info(f"  {cn}/{rid}")
    output.info("")

    if not output.confirm_destructive(f"Confirm teardown of {len(matches)} deployments?"):
        output.info("Teardown cancelled.")
        return 0

    failures = 0
    for i, (cn, rid, _) in enumerate(matches, 1):
        output.info("")
        output.info(f"[{i}/{len(matches)}] Tearing down {cn}-{rid}...")
        rc = run_teardown(f"{cn}-{rid}", deploy_dir)
        if rc != 0:
            failures += 1
            output.error(f"  FAILED: {cn}-{rid} (rc={rc})")

    output.info("")
    if failures:
        output.error(f"DONE: {len(matches) - failures}/{len(matches)} succeeded, {failures} failed")
        return 1
    output.info(f"DONE: all {len(matches)} torn down")
    return 0


def _is_run_active(
    config_dir: Path, run_dir: Path, config_name: str, run_id: str,
    config: DeploymentConfig,
) -> bool:
    """A run is "active" if any of its VMs still exist on OpenStack."""
    os_client = OpenStack()
    dep_id = make_dep_id(config_name, run_id)
    if config.is_rampart():
        ent_hash = hashlib.md5(dep_id.encode()).hexdigest()[:5]
        return os_client.has_vms_with_prefix(f"r-{ent_hash}-")
    elif config.is_ghosts():
        g_hash = hashlib.md5(dep_id.encode()).hexdigest()[:5]
        return os_client.has_vms_with_prefix(f"g-{g_hash}-")
    else:
        return os_client.has_vms_with_prefix(f"d-{dep_id}-")


def run_teardown_all(deploy_dir: Path) -> int:
    """Delete ALL DECOY (d-*), RAMPART (r-*), and GHOSTS (g-*) servers + volumes.

    Uses teardown-all.yaml which sweeps by regex — no per-type dispatch.
    Local run directories are cleaned up afterward by walking the deploy
    dir and removing any inventory.ini that points at the now-gone VMs.
    """
    output.banner("TEARDOWN ALL")
    output.info("This will DELETE ALL DECOY (d-*), RAMPART (r-*), and GHOSTS (g-*) servers and volumes!")
    output.info("")

    if not output.confirm_destructive("Confirm teardown-all?"):
        output.info("Teardown cancelled.")
        return 0

    hosts_ini = find_hosts_ini(None, deploy_dir)
    if not hosts_ini:
        output.error("ERROR: No hosts.ini found")
        return 1

    runner = AnsibleRunner(deploy_dir / "logs")
    output.info("")
    output.section("[Teardown]")

    result = runner.run_playbook(
        "teardown-all.yaml",
        hosts_ini,
        extra_vars={"deployment_dir": str(deploy_dir)},
        on_event=default_event_handler,
    )

    # Remove all managed SSH config blocks
    removed = remove_all_managed_blocks()
    if removed:
        output.info(f"  Removed {len(removed)} SSH config blocks")

    # Clean up inventory files + close PHASE experiments.json entries for
    # every deployment that had an active run.
    from .shared.teardown_helpers import close_phase_experiment
    for config_dir in deploy_dir.iterdir():
        if not config_dir.is_dir():
            continue
        runs_dir = config_dir / "runs"
        if not runs_dir.is_dir():
            continue
        had_active_runs = False
        for run_dir in runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            if (run_dir / "inventory.ini").exists():
                had_active_runs = True
                safe_rmtree(run_dir)
        if had_active_runs:
            close_phase_experiment(config_dir.name)
            # Drop empty feedback dirs
            if any(p in config_dir.name for p in ("decoy-feedback-", "rampart-feedback-", "ghosts-feedback-")):
                remaining = [d for d in runs_dir.iterdir() if d.is_dir()] if runs_dir.is_dir() else []
                if not remaining:
                    safe_rmtree(config_dir)

    return result.rc
