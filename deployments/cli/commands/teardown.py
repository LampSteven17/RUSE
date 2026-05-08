"""Teardown commands — specific deployment, filtered batch, and teardown-all."""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from .. import output
from ..ansible_runner import AnsibleRunner, default_event_handler
from ..config import DeploymentConfig
from ..openstack import OpenStack
from ..ssh_config import remove_all_managed_blocks, remove_ssh_config


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


def run_teardown_filtered(
    deploy_dir: Path,
    types: dict[str, bool],
    feedback_only: bool,
) -> int:
    """Teardown all active deployments matching the given filters.

    types: {"decoy": bool, "rampart": bool, "ghosts": bool}
    feedback_only: if True, only tear down feedback-enabled deployments.
    """
    # If no type flags given but --feedback was used, match all types
    any_type = any(types.values())

    os_client = OpenStack()
    targets: list[tuple[str, str, str]] = []  # (config_name, run_id, group)

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

        # Determine group
        if config.is_rampart():
            group = "rampart"
        elif config.is_ghosts():
            group = "ghosts"
        else:
            group = "decoy"

        # Apply type filter
        if any_type and not types.get(group, False):
            continue

        # Apply feedback filter
        is_feedback = name.startswith(f"{group}-feedback-")
        if feedback_only and not is_feedback:
            continue

        # Find active runs
        for run_dir in sorted(runs_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            rid = run_dir.name
            if not re.match(r"^\d{12}$", rid):
                continue
            if _is_run_active(run_dir, name, rid, config, os_client, deploy_dir):
                targets.append((name, rid, group))

    if not targets:
        output.info("No active deployments match the given filters.")
        return 0

    # Show what will be torn down
    filter_desc = []
    if any_type:
        filter_desc.extend(k for k, v in types.items() if v)
    else:
        filter_desc.append("all types")
    if feedback_only:
        filter_desc.append("feedback only")

    output.banner(f"BATCH TEARDOWN ({', '.join(filter_desc)})")
    output.info("")
    for name, rid, group in targets:
        output.info(f"  {name}-{rid}  ({group})")
    output.info("")

    if not output.confirm(f"Teardown these {len(targets)} deployment(s)?"):
        output.info("Cancelled.")
        return 0

    # Tear down each one
    failed = 0
    for i, (name, rid, group) in enumerate(targets, 1):
        output.info("")
        output.info(f"[{i}/{len(targets)}] Tearing down {name}-{rid}...")
        rc = run_teardown(f"{name}-{rid}", deploy_dir)
        if rc != 0:
            output.error(f"  FAILED: {name}-{rid} (rc={rc})")
            failed += 1

    output.info("")
    if failed:
        output.info(f"DONE: {len(targets) - failed}/{len(targets)} succeeded, {failed} failed")
        return 1

    output.info(f"DONE: all {len(targets)} deployment(s) torn down")
    return 0


def _is_run_active(
    run_dir: Path, name: str, rid: str, config: DeploymentConfig,
    os_client: OpenStack, deploy_dir: Path,
) -> bool:
    """Check if a run is still active (mirrors list_cmd._check_active)."""
    if (run_dir / "inventory.ini").exists():
        return True
    if (run_dir / "deployment_type").exists():
        return True

    dep_id = _make_dep_id(name, rid)
    if config.is_rampart():
        ent_hash = hashlib.md5(dep_id.encode()).hexdigest()[:5]
        return os_client.has_vms_with_prefix(f"e-{ent_hash}-")
    elif config.is_ghosts():
        g_hash = hashlib.md5(dep_id.encode()).hexdigest()[:5]
        return os_client.has_vms_with_prefix(f"g-{g_hash}-")
    else:
        return os_client.has_vms_with_prefix(f"d-{dep_id}-")


def _sup_teardown(config_dir: Path, config_name: str, run_id: str, deploy_dir: Path) -> int:
    """Teardown a DECOY SUP deployment."""
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
    vm_prefix = f"d-{dep_id}-"

    runner = AnsibleRunner(deploy_dir / "playbooks", deploy_dir / "logs")

    result = runner.run_playbook(
        "teardown.yaml",
        hosts_ini,
        extra_vars={
            "deployment_dir": str(config_dir),
            "deployment_id": dep_id,
            "run_dir": str(run_dir),
            # Pass vm_prefix explicitly. The playbook's own default was
            # `r-{deployment_id}-` (legacy from the bash-deploy era), which
            # silently matched zero VMs since DECOYs are `d-{...}-`. Result:
            # listing returned empty, all delete tasks skipped, inventory got
            # removed, and the CLI post-check found the still-alive VMs.
            "vm_prefix": vm_prefix,
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
    _cleanup_orphaned_volumes(os_client)
    _close_phase_experiment(config_name)

    # Cleanup local state — remove this run, then remove feedback config dir if empty.
    # run_dir contains both main inventory.ini AND neighborhood-inventory.ini /
    # neighborhood-ssh-snippet.txt when a sidecar was provisioned, so rmtree
    # clears everything. VMs themselves (including d-{dep_id}-neighborhood-0)
    # are already deleted above by the teardown.yaml d-{prefix} sweep.
    if run_dir.is_dir():
        _safe_rmtree(run_dir)

    if config_name.startswith("decoy-feedback-"):
        runs_dir = config_dir / "runs"
        remaining_runs = [d for d in runs_dir.iterdir() if d.is_dir()] if runs_dir.is_dir() else []
        if not remaining_runs:
            _safe_rmtree(config_dir)

    return result.rc


def _rampart_teardown(
    config_dir: Path, config_name: str, run_id: str, config: DeploymentConfig, deploy_dir: Path,
) -> int:
    """Teardown a RAMPART enterprise deployment."""
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

    output.info("  Verified: 0 VMs remaining")
    _cleanup_orphaned_volumes(os_client)
    _close_phase_experiment(config_name)

    if run_dir.is_dir():
        _safe_rmtree(run_dir)
        output.info("  Removed local run directory")

    if config_name.startswith("rampart-feedback-"):
        runs_dir = config_dir / "runs"
        remaining_runs = [d for d in runs_dir.iterdir() if d.is_dir()] if runs_dir.is_dir() else []
        if not remaining_runs:
            _safe_rmtree(config_dir)
            output.info("  Removed feedback config directory")

    output.info("")
    output.info("DONE: all RAMPART VMs deleted")
    return 0


def _ghosts_teardown(
    config_dir: Path, config_name: str, run_id: str, deploy_dir: Path,
) -> int:
    """Teardown a GHOSTS deployment."""
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

    output.info("  Verified: 0 VMs remaining")
    _cleanup_orphaned_volumes(os_client)
    _close_phase_experiment(config_name)

    # Cleanup local state — remove this run, then remove feedback config dir if empty
    if run_dir.is_dir():
        _safe_rmtree(run_dir)
        output.info("  Removed local run directory")

    if config_name.startswith("ghosts-feedback-"):
        runs_dir = config_dir / "runs"
        remaining_runs = [d for d in runs_dir.iterdir() if d.is_dir()] if runs_dir.is_dir() else []
        if not remaining_runs:
            _safe_rmtree(config_dir)
            output.info("  Removed feedback config directory")

    output.info("")
    output.info("DONE: all GHOSTS VMs deleted")
    return 0


def run_teardown_all(deploy_dir: Path) -> int:
    """Delete ALL DECOY, Enterprise, and GHOSTS VMs."""
    output.banner("TEARDOWN ALL")
    output.info("This will DELETE ALL DECOY (d-*), Enterprise (e-*), and GHOSTS (g-*) servers and volumes!")
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

    # Remove all managed SSH config blocks
    removed = remove_all_managed_blocks()
    if removed:
        output.info(f"  Removed {len(removed)} SSH config blocks")

    # Clean up inventory files + close PHASE experiments.json entries for
    # every deployment that had an active run.
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
            had_active_runs = True
            for f in ("inventory.ini", "ssh_config_snippet.txt"):
                (run_dir / f).unlink(missing_ok=True)
        if had_active_runs:
            _close_phase_experiment(config_dir.name)

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
    for prefix in ("decoy-", "ghosts-", "rampart-", "enterprise-"):
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



def _cleanup_orphaned_volumes(os_client: OpenStack) -> int:
    """Delete orphaned boot volumes (nameless, 200GB, available). Returns count deleted."""
    orphans = os_client.find_orphaned_volumes(size=200)
    if not orphans:
        return 0
    deleted = 0
    for v in orphans:
        vid = v.get("ID", v.get("id", ""))
        if vid and os_client.volume_delete(vid):
            deleted += 1
    if deleted:
        output.info(f"  Cleaned up {deleted} orphaned boot volumes")
    return deleted


EXPERIMENTS_JSON = Path("/mnt/AXES2U1/experiments.json")


def _close_phase_experiment(config_name: str) -> None:
    """Set end_date on the PHASE experiments.json entry for this deployment.

    Previously teardown left entries with end_date=None, so PHASE batch
    pipelines (e.g. PHASE.py --decoy) would still pick up torn-down deploys
    as if they were active and try to dredge their (now-deleted) VM IPs.
    Setting end_date marks the deploy as ended without deleting the
    historical registration record.

    Uses fcntl-locked read-modify-write + atomic rename so concurrent
    teardowns and deploy registrations can't clobber each other. A race
    on 2026-04-17 wiped 12 entries before the lock went in.
    """
    import datetime
    import fcntl
    import json
    import os
    import tempfile
    if not EXPERIMENTS_JSON.exists():
        return

    lock_path = EXPERIMENTS_JSON.with_suffix(EXPERIMENTS_JSON.suffix + ".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    lock_fd = None
    try:
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        try:
            data = json.loads(EXPERIMENTS_JSON.read_text())
        except (OSError, json.JSONDecodeError) as e:
            output.error(f"  WARNING: cannot read experiments.json: {e}")
            return

        entry = data.get(config_name)
        if not entry:
            return  # nothing to close
        if entry.get("end_date"):
            return  # already closed

        # Yesterday, not today: teardown-day Zeek captures are partial
        # (VMs stop emitting traffic mid-day, some rows may be truncated
        # by the teardown sequence). Using the day before gives PHASE a
        # clean last-full-day boundary for log dredging.
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        entry["end_date"] = yesterday

        # Atomic replace — write to temp in same dir, fsync, rename.
        try:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", dir=str(EXPERIMENTS_JSON.parent),
                prefix=f".{EXPERIMENTS_JSON.name}.", suffix=".tmp",
                delete=False,
            )
            tmp.write(json.dumps(data, indent=4) + "\n")
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp.name, EXPERIMENTS_JSON)
            output.info(f"  Closed experiments.json entry: {config_name} end_date={yesterday}")
        except OSError as e:
            output.error(f"  WARNING: cannot write experiments.json: {e}")
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)


def _safe_rmtree(path: Path) -> None:
    """Recursively remove a directory."""
    import shutil
    try:
        shutil.rmtree(path)
    except OSError:
        pass
