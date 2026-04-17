"""RUSE SUP deployment command."""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from .. import output
from ..ansible_runner import AnsibleRunner, AnsibleEvent, default_event_handler
from ..config import DeploymentConfig
from ..openstack import OpenStack
from ..ssh_config import install_ssh_config
from .feedback import generate_feedback_config


def run_ruse_spinup(
    config_name: str,
    deploy_dir: Path,
    behavior_source: str | None = None,
    configs_spec: str | None = None,
) -> int:
    """Deploy RUSE SUP agents."""
    # If feedback args given but config is ruse-controls, generate feedback config
    if behavior_source and config_name == "ruse-controls":
        config_name = generate_feedback_config(
            Path(behavior_source), configs_spec or "all", deploy_dir,
        )

    config_dir = deploy_dir / config_name
    config_file = config_dir / "config.yaml"

    if not config_file.exists():
        output.error(f"ERROR: No config.yaml found for: {config_name}")
        return 1

    config = DeploymentConfig.load(config_file)

    # Find hosts.ini
    hosts_ini = _find_hosts_ini(config_dir, deploy_dir)
    if not hosts_ini:
        output.error(f"ERROR: No hosts.ini found for: {config_name}")
        return 1

    # Generate run ID and paths
    run_id = time.strftime("%m%d%y%H%M%S")
    run_dir = config_dir / "runs" / run_id
    dep_id = _make_run_dep_id(config_name, run_id)
    vm_prefix = f"r-{dep_id}-"
    vm_count = config.vm_count()

    # Display header
    output.banner(f"DEPLOY: {config_name}")
    output.info(f"  VMs:       {config.brain_summary()}")
    output.info(f"  Run ID:    {run_id}")
    output.info(f"  VM prefix: {vm_prefix}*")
    if behavior_source:
        output.info(f"  Feedback:  {behavior_source}")
    output.info("")

    # Create run directory
    run_dir.mkdir(parents=True, exist_ok=True)
    _copy_file(config_file, run_dir / "config.yaml")

    runner = AnsibleRunner(deploy_dir / "playbooks", deploy_dir / "logs")

    # Phase 1: Provision
    output.info("")
    output.info(f"--- Provisioning {vm_count} VMs ---")

    provision_result = runner.run_playbook(
        "provision-vms.yaml",
        hosts_ini,
        extra_vars={
            "deployment_dir": str(config_dir),
            "deployment_id": dep_id,
            "run_dir": str(run_dir),
        },
        on_event=default_event_handler,
    )

    inventory_path = run_dir / "inventory.ini"
    if not inventory_path.exists():
        output.info("")
        output.error("Provisioning failed -- no VMs were created successfully.")
        output.dim(f"  Log: {provision_result.log_path}")
        return 1

    if provision_result.rc != 0:
        output.info("")
        output.info("WARNING: Provisioning completed with failures. Continuing install for successful VMs.")

    # Count provisioned VMs and extract host info
    provisioned_hosts = _parse_inventory(inventory_path)
    provisioned = len(provisioned_hosts)
    output.info(f"  {provisioned}/{vm_count} VMs provisioned")

    # Test SSH connectivity (done in Python for real-time output)
    output.info("")
    output.info("--- Testing SSH connectivity ---")
    ssh_ok = _test_ssh_all(provisioned_hosts)
    # S1: Fail-loud if too many VMs unreachable. Previously this was a warning
    # and install proceeded against unreachable hosts, eventually "succeeding"
    # while the SUP services on unreachable VMs never got configured.
    ssh_threshold = 0.9
    if ssh_ok < provisioned * ssh_threshold:
        output.error(f"  FAIL: SSH reachable on only {ssh_ok}/{provisioned} VMs "
                     f"(threshold {int(ssh_threshold*100)}%). Aborting.")
        return 1
    elif ssh_ok < provisioned:
        output.info(f"  WARNING: SSH reachable on {ssh_ok}/{provisioned} VMs (threshold met)")
    else:
        output.info(f"  All {ssh_ok} VMs reachable via SSH")

    # Phase 2: Install
    output.info("")
    output.info(f"--- Installing on {provisioned} VMs ---")

    install_playbook = "install-sups.yaml"
    extra_vars = {
        "deployment_dir": str(config_dir),
        "deployment_id": dep_id,
        "run_dir": str(run_dir),
    }

    # Override behavior_source if provided via CLI
    if behavior_source:
        extra_vars["behavior_source"] = behavior_source

    install_result = runner.run_playbook(
        install_playbook,
        inventory_path,
        extra_vars=extra_vars,
        on_event=default_event_handler,
    )

    # I1: Fail-loud on install failures. Ansible exits rc=2 when any host
    # fails a task — in our install-sups.yaml that means an S3/S4/S5
    # assertion tripped (stage2 rc, service is-active, MCHP cron count).
    # Previously spinup.py kept going, distributed configs to every VM
    # (including the failed ones), registered in PHASE, and printed
    # "DONE: 7/7 VMs deployed" even when one or more VMs never got a
    # working service. Abort here so the operator sees the failure
    # immediately and can diagnose from the Ansible log.
    if install_result.rc != 0:
        failed_hosts, succeeded_hosts = _parse_ansible_recap(install_result.log_path)
        total = len(failed_hosts) + len(succeeded_hosts)
        output.error("")
        output.error(f"ABORTING: install-sups.yaml exited with rc={install_result.rc}")
        if total > 0:
            output.error(f"  {len(succeeded_hosts)}/{total} VMs passed install assertions")
            if failed_hosts:
                output.error(f"  Failed: {', '.join(sorted(failed_hosts))}")
        output.error(f"  Log: {install_result.log_path}")
        output.error("  Tear down with: ./teardown " + f"{config_name}-{run_id}")
        return 1

    # Phase 2b: Distribute behavioral configs (if applicable)
    effective_source = behavior_source or config.behavior_source
    if effective_source:
        output.info("")
        output.info("--- Distributing behavioral configs ---")
        dist_vars = {
            "deployment_dir": str(config_dir),
            "deployment_id": dep_id,
            "run_dir": str(run_dir),
            "config_source": effective_source,
        }
        if configs_spec and configs_spec != "all":
            dist_vars["behavior_configs"] = configs_spec

        runner.run_playbook(
            "distribute-behavior-configs.yaml",
            inventory_path,
            extra_vars=dist_vars,
            on_event=default_event_handler,
        )

    # Post-deploy: SSH config + PHASE registration
    snippet_path = run_dir / "ssh_config_snippet.txt"
    if snippet_path.exists():
        output.info("")
        install_ssh_config(snippet_path, f"{config_name}/{run_id}")

    # P1: PHASE registration is fail-loud. Previously a registration failure
    # printed a WARNING and the deploy continued, leaving VMs running but
    # invisible to PHASE inference — logs collected but never analyzed. DONE
    # must mean "every VM functional AND registered" per the fail-loud
    # contract.
    phase_ok = _register_phase(snippet_path, config_name, run_id, deploy_dir)
    if not phase_ok:
        output.error("")
        output.error("ABORTING: PHASE experiments.json registration failed.")
        output.error("VMs are running but logs won't be picked up by PHASE inference.")
        output.error("Tear down and fix register_experiment.py, or register manually.")
        return 1

    # Final summary
    output.info("")
    output.info(f"DONE: {provisioned}/{vm_count} VMs deployed")
    output.info(f"  Log: {install_result.log_path}")

    return install_result.rc


# --- Helpers ---

def _parse_ansible_recap(log_path: Path) -> tuple[set[str], set[str]]:
    """Parse PLAY RECAP from an Ansible log. Returns (failed_hosts, succeeded_hosts).

    PLAY RECAP format:
        hostname : ok=N  changed=N  unreachable=N  failed=N  skipped=N  rescued=N  ignored=N

    A host is considered failed if failed>0 or unreachable>0. Otherwise succeeded.
    Returns empty sets if log can't be read — caller still aborts on rc != 0.
    """
    failed: set[str] = set()
    succeeded: set[str] = set()
    if not log_path.exists():
        return failed, succeeded
    try:
        text = log_path.read_text()
    except OSError:
        return failed, succeeded

    # Find the PLAY RECAP section — everything after the last "PLAY RECAP"
    recap_idx = text.rfind("PLAY RECAP")
    if recap_idx == -1:
        return failed, succeeded

    recap = text[recap_idx:]
    pat = re.compile(
        r"^(\S+)\s*:\s*"
        r"ok=(\d+)\s+"
        r"changed=\d+\s+"
        r"unreachable=(\d+)\s+"
        r"failed=(\d+)",
        re.MULTILINE,
    )
    for match in pat.finditer(recap):
        host = match.group(1)
        unreachable = int(match.group(3))
        fails = int(match.group(4))
        if unreachable > 0 or fails > 0:
            failed.add(host)
        else:
            succeeded.add(host)
    return failed, succeeded


def _find_hosts_ini(config_dir: Path, deploy_dir: Path) -> Path | None:
    if (config_dir / "hosts.ini").exists():
        return config_dir / "hosts.ini"
    if (deploy_dir / "hosts.ini").exists():
        return deploy_dir / "hosts.ini"
    return None


def _make_run_dep_id(deployment_name: str, run_id: str) -> str:
    """Build run dep_id: strip prefixes, remove hyphens, append run_id."""
    dep = deployment_name
    for prefix in ("ruse-", "sup-"):
        if dep.startswith(prefix):
            dep = dep[len(prefix):]
    dep = dep.replace("-", "")
    return f"{dep}{run_id}"


def _copy_file(src: Path, dst: Path) -> None:
    import shutil
    shutil.copy2(src, dst)


def _parse_inventory(inventory_path: Path) -> list[dict]:
    """Parse inventory.ini into list of {name, ip, behavior}."""
    import re
    hosts = []
    for line in inventory_path.read_text().splitlines():
        match = re.match(r"^(\S+)\s+ansible_host=(\S+)\s+sup_behavior=(\S+)", line)
        if match:
            hosts.append({
                "name": match.group(1),
                "ip": match.group(2),
                "behavior": match.group(3),
            })
    return hosts


def _test_ssh_all(hosts: list[dict], max_retries: int = 30, timeout: int = 10, delay: int = 5) -> int:
    """Test SSH to all hosts with real-time per-VM output. Returns count of reachable hosts."""
    import subprocess
    import concurrent.futures
    import time as _time

    ok_count = 0

    def _test_one(host: dict) -> bool:
        name = host["name"]
        ip = host["ip"]
        for attempt in range(1, max_retries + 1):
            ts = _time.strftime("%H:%M:%S")
            try:
                result = subprocess.run(
                    ["ssh",
                     "-i", str(Path.home() / ".ssh" / "id_ed25519"),
                     "-o", "IdentitiesOnly=yes",
                     "-o", "StrictHostKeyChecking=no",
                     "-o", "UserKnownHostsFile=/dev/null",
                     "-o", f"ConnectTimeout={timeout}",
                     "-o", "ConnectionAttempts=1",
                     "-o", "BatchMode=yes",
                     "-o", "LogLevel=ERROR",
                     f"ubuntu@{ip}", "echo ok"],
                    capture_output=True, timeout=timeout + 5,
                    env={**os.environ, "SSH_AUTH_SOCK": ""},
                )
                if result.returncode == 0:
                    output.info(f"  [{ts}]    OK  {name} ({ip})")
                    return True
            except subprocess.TimeoutExpired:
                pass

            output.info(f"  [{ts}]    ..  {name} ({ip})  attempt {attempt}/{max_retries}")
            _time.sleep(delay)

        ts = _time.strftime("%H:%M:%S")
        output.info(f"  [{ts}]    FAIL  {name} ({ip})  unreachable after {max_retries} attempts")
        return False

    # Run SSH tests with limited concurrency (like Ansible throttle: 20)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(_test_one, h): h for h in hosts}
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                ok_count += 1

    return ok_count


def _register_phase(snippet_path: Path, config_name: str, run_id: str, deploy_dir: Path) -> bool:
    """Register in PHASE experiments.json. Returns True on success, False on failure.

    Returns True when the script is absent or the snippet is missing (no
    registration was attempted or possible — not a failure). Returns False
    only when registration was attempted and actually failed.
    """
    if not snippet_path.exists():
        output.error("  WARNING: ssh_config_snippet.txt missing — skipping PHASE registration")
        return True  # no snippet = earlier stage already aborted, don't double-fail

    lib_dir = deploy_dir / "lib"
    register_script = lib_dir / "register_experiment.py"
    if not register_script.exists():
        output.error(f"  WARNING: {register_script} not found — skipping PHASE registration")
        return True  # missing script = dev environment, not a deploy failure

    run_dir = snippet_path.parent
    inventory_path = run_dir / "inventory.ini"

    # Prefer subprocess — it's the canonical path and surfaces rc directly.
    # The previous import-based path silently swallowed ImportErrors and fell
    # through to subprocess, hiding which path actually ran.
    return _register_phase_subprocess(snippet_path, config_name, run_id, deploy_dir)


def _register_phase_subprocess(
    snippet_path: Path, config_name: str, run_id: str, deploy_dir: Path,
) -> bool:
    """Register via subprocess. Returns True on rc=0, False otherwise."""
    import subprocess

    lib_dir = deploy_dir / "lib"
    run_dir = snippet_path.parent
    inventory_path = run_dir / "inventory.ini"

    try:
        result = subprocess.run(
            [
                "python3", str(lib_dir / "register_experiment.py"),
                "--name", config_name,
                "--snippet", str(snippet_path),
                "--inventory", str(inventory_path),
                "--run-id", run_id,
            ],
            capture_output=True, text=True,
            timeout=30,
        )
        if result.returncode == 0:
            output.dim("  Registered in PHASE experiments.json")
            return True
        err = (result.stderr or result.stdout or "").strip()[:400]
        output.error(f"  ERROR: PHASE registration FAILED (rc={result.returncode}): {err}")
        return False
    except Exception as e:
        output.error(f"  ERROR: PHASE registration crashed ({type(e).__name__}): {e}")
        return False
