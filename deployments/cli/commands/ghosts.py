"""GHOSTS NPC traffic generator deployment command."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .. import output
from ..ansible_runner import AnsibleRunner, default_event_handler
from ..config import DeploymentConfig
from ..openstack import OpenStack
from ..ssh_config import install_ssh_config


def run_ghosts_spinup(
    config_name: str | None,
    deploy_dir: Path,
    behavior_source: str | None = None,
    configs_spec: str | None = None,
) -> int:
    """Deploy GHOSTS API + NPC client VMs."""
    # If feedback args given but config is ghosts-controls, generate feedback config
    config_name = config_name or "ghosts-controls"
    if behavior_source and config_name == "ghosts-controls":
        from .feedback import generate_ghosts_feedback_config
        config_name = generate_ghosts_feedback_config(
            Path(behavior_source), configs_spec or "all", deploy_dir,
        )

    config_dir = _find_ghosts_config(config_name, deploy_dir)
    if not config_dir:
        output.error("ERROR: No GHOSTS deployment config found")
        return 1

    deployment = config_dir.name
    config = DeploymentConfig.load(config_dir / "config.yaml")

    client_count = config.ghosts_client_count()
    total_vms = 1 + client_count  # 1 API + N clients

    run_id = time.strftime("%m%d%y%H%M%S")
    run_dir = config_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    dep_id = _make_dep_id(deployment, run_id)
    g_hash = hashlib.md5(dep_id.encode()).hexdigest()[:5]
    g_prefix = f"g-{g_hash}-"

    # Header
    output.banner(f"DEPLOY: GHOSTS ({deployment})")
    output.info(f"  VMs:       {total_vms} (1 api + {client_count} npc)")
    output.info(f"  Run ID:    {run_id}")
    output.info(f"  VM prefix: {g_prefix}*")
    output.info(f"  Repo:      {config.ghosts_repo()} ({config.ghosts_branch()})")
    if behavior_source:
        output.info(f"  Feedback:  {behavior_source}")
        if configs_spec:
            output.info(f"  Configs:   {configs_spec}")
    output.info("")

    # Snapshot config
    shutil.copy2(config_dir / "config.yaml", run_dir / "config.yaml")

    os_client = OpenStack()

    # [1/5] Provision VMs
    output.info("[1/5] Provisioning VMs...")
    vms = _provision_vms(os_client, config, g_prefix, run_dir)
    if not vms:
        output.error("  FAILED: Could not provision VMs")
        return 1

    api_vm = vms["api"]
    client_vms = vms["clients"]
    all_vms = [api_vm] + client_vms

    output.info(f"  {len(all_vms)}/{total_vms} VMs provisioned")

    # Write inventory and SSH config
    _write_inventory(api_vm, client_vms, run_dir, deployment)
    _write_ssh_config(all_vms, run_dir, deployment)

    # [2/5] Test SSH
    output.info("")
    output.info("[2/5] Testing SSH connectivity...")
    ssh_ok = _test_ssh_all(all_vms)
    if ssh_ok < len(all_vms):
        output.info(f"  WARNING: SSH reachable on {ssh_ok}/{len(all_vms)} VMs")
    else:
        output.info(f"  All {ssh_ok} VMs reachable via SSH")

    inventory_path = run_dir / "inventory.ini"
    runner = AnsibleRunner(deploy_dir / "playbooks", deploy_dir / "logs")

    # [3/5] Install GHOSTS API
    output.info("")
    output.info("[3/5] Installing GHOSTS API stack...")
    api_result = runner.run_playbook(
        "install-ghosts-api.yaml",
        inventory_path,
        extra_vars={
            "ghosts_repo": config.ghosts_repo(),
            "ghosts_branch": config.ghosts_branch(),
        },
        on_event=default_event_handler,
    )
    if api_result.rc != 0:
        output.error("  FAILED: GHOSTS API installation failed")
        output.info(f"  Log: {api_result.log_path}")
        # Continue anyway — clients might still be useful to install

    # Generate PHASE-informed timeline if feedback source provided
    client_extra_vars: dict[str, str] = {}
    if behavior_source:
        output.info("")
        output.info("  Generating PHASE-informed GHOSTS timeline...")
        timeline_path = _generate_feedback_timeline(behavior_source, run_dir)
        if timeline_path:
            client_extra_vars["ghosts_timeline_file"] = str(timeline_path)
            output.info(f"  Timeline written: {timeline_path.name}")
        else:
            output.info("  WARNING: Could not generate timeline, using default")

    # [4/5] Install GHOSTS clients
    output.info("")
    output.info(f"[4/5] Installing GHOSTS clients ({client_count} VMs)...")
    client_result = runner.run_playbook(
        "install-ghosts-clients.yaml",
        inventory_path,
        extra_vars=client_extra_vars if client_extra_vars else None,
        on_event=default_event_handler,
    )

    # [5/5] Finalize
    output.info("")
    output.info("[5/5] Finalizing...")

    (run_dir / "deployment_type").write_text("ghosts")

    snippet_path = run_dir / "ssh_config_snippet.txt"
    if snippet_path.exists():
        install_ssh_config(snippet_path, f"{deployment}/{run_id}")

    # Register in PHASE experiments.json
    _register_phase(snippet_path, deployment, run_id, deploy_dir)

    output.info("")
    output.info(f"DONE: GHOSTS deployment {deployment}/{run_id}")
    output.info(f"  API:      ssh {api_vm['name']} (http://{api_vm['ip']}:5000)")
    output.info(f"  Frontend: http://{api_vm['ip']}:4200")
    output.info(f"  Grafana:  http://{api_vm['ip']}:3000")
    output.info(f"  Clients:  {len(client_vms)} NPCs")

    return client_result.rc if api_result.rc == 0 else api_result.rc


# --- VM Provisioning ---

def _provision_vms(
    os_client: OpenStack,
    config: DeploymentConfig,
    g_prefix: str,
    run_dir: Path,
) -> dict | None:
    """Provision API + client VMs on OpenStack. Returns {api: {...}, clients: [...]}."""
    rc_file = Path.home() / "vxn3kr-bot-rc"
    os_image = "noble-amd64"
    os_network = "ext_net"
    os_keypair = "bot-desktop"
    os_security_group = "default"

    # Build VM list: 1 API + N clients
    vm_specs = []
    vm_specs.append({
        "name": f"{g_prefix}api-0",
        "flavor": config.ghosts_api_flavor(),
        "role": "api",
    })
    for i in range(config.ghosts_client_count()):
        vm_specs.append({
            "name": f"{g_prefix}npc-{i}",
            "flavor": config.ghosts_client_flavor(),
            "role": "client",
        })

    # Create VMs
    for spec in vm_specs:
        ts = time.strftime("%H:%M:%S")
        result = _openstack_cmd(
            rc_file,
            "server", "create",
            "--flavor", spec["flavor"],
            "--image", os_image,
            "--boot-from-volume", "200",
            "--network", os_network,
            "--key-name", os_keypair,
            "--security-group", os_security_group,
            "-f", "value", "-c", "id",
            spec["name"],
        )
        if result.returncode == 0:
            output.info(f"  [{ts}]    OK  {spec['name']}")
        else:
            output.info(f"  [{ts}]    FAIL  {spec['name']}  {result.stderr[:80]}")

    # Wait for ACTIVE
    output.info("")
    output.info("  Waiting for VMs to reach ACTIVE state...")
    for spec in vm_specs:
        for attempt in range(60):
            result = _openstack_cmd(
                rc_file,
                "server", "show", spec["name"],
                "-f", "value", "-c", "status",
            )
            status = result.stdout.strip()
            if status == "ACTIVE":
                break
            elif status == "ERROR":
                output.info(f"  [{time.strftime('%H:%M:%S')}]    FAIL  {spec['name']} (ERROR state)")
                break
            time.sleep(5)

    # Get IPs
    vms_with_ips = []
    for spec in vm_specs:
        result = _openstack_cmd(
            rc_file,
            "server", "show", spec["name"],
            "-f", "value", "-c", "addresses",
        )
        ip = ""
        if result.returncode == 0:
            # Parse "ext_net=10.x.x.x" format
            for part in result.stdout.strip().split(","):
                part = part.strip()
                if "=" in part:
                    ip = part.split("=", 1)[1].strip()
                    break
                # Fallback: just grab an IP-looking string
                import re
                ip_match = re.search(r"(\d+\.\d+\.\d+\.\d+)", part)
                if ip_match:
                    ip = ip_match.group(1)
                    break

        if ip:
            output.info(f"  {spec['name']} => {ip}")
            vms_with_ips.append({**spec, "ip": ip})
        else:
            output.info(f"  {spec['name']} => NO IP")

    if not vms_with_ips:
        return None

    # Split into API and clients
    api_vm = None
    client_vms = []
    for vm in vms_with_ips:
        if vm["role"] == "api":
            api_vm = vm
        else:
            client_vms.append(vm)

    if not api_vm:
        output.error("  API VM not found or has no IP")
        return None

    return {"api": api_vm, "clients": client_vms}


def _openstack_cmd(rc_file: Path, *args: str) -> subprocess.CompletedProcess:
    """Run an OpenStack CLI command with sourced credentials."""
    import shlex
    cmd = f"source {shlex.quote(str(rc_file))} && openstack {shlex.join(args)}"
    return subprocess.run(
        ["bash", "-c", cmd],
        capture_output=True,
        text=True,
    )


# --- Inventory & SSH Config ---

def _write_inventory(api_vm: dict, client_vms: list[dict], run_dir: Path, deployment_name: str) -> None:
    """Write Ansible inventory with two host groups."""
    lines = [
        f"# Auto-generated inventory for {deployment_name} (GHOSTS)",
        f"# Generated: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
        "",
        "[ghosts_api]",
        f"{api_vm['name']} ansible_host={api_vm['ip']}",
        "",
        "[ghosts_clients]",
    ]

    api_ip = api_vm["ip"]
    for vm in client_vms:
        lines.append(
            f"{vm['name']} ansible_host={vm['ip']} ghosts_api_ip={api_ip}"
        )

    lines.extend([
        "",
        "[all:vars]",
        "ansible_user=ubuntu",
        "ansible_python_interpreter=/usr/bin/python3",
        "ansible_ssh_common_args=-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null",
        "",
    ])

    (run_dir / "inventory.ini").write_text("\n".join(lines))


def _write_ssh_config(all_vms: list[dict], run_dir: Path, deployment_name: str) -> None:
    """Write SSH config snippet."""
    lines = [
        f"############# GHOSTS - {deployment_name} #############",
        "",
        "Host g-*",
        "    User ubuntu",
        "    PreferredAuthentications publickey",
        "    IdentityFile ~/.ssh/id_ed25519",
        "    IdentitiesOnly yes",
        "    StrictHostKeyChecking no",
        "    UserKnownHostsFile /dev/null",
        "    ServerAliveInterval 120",
        "",
    ]

    for vm in all_vms:
        lines.append(f"Host {vm['name']}")
        lines.append(f"    HostName {vm['ip']}")
        lines.append("")

    lines.append("#############################################")

    (run_dir / "ssh_config_snippet.txt").write_text("\n".join(lines))


# --- SSH Testing ---

def _test_ssh_all(vms: list[dict], max_retries: int = 30, timeout: int = 10, delay: int = 5) -> int:
    """Test SSH to all VMs with real-time per-VM output. Returns count of reachable."""
    ok_count = 0

    def _test_one(vm: dict) -> bool:
        name = vm["name"]
        ip = vm["ip"]
        for attempt in range(1, max_retries + 1):
            ts = time.strftime("%H:%M:%S")
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
            time.sleep(delay)

        ts = time.strftime("%H:%M:%S")
        output.info(f"  [{ts}]    FAIL  {name} ({ip})  unreachable after {max_retries} attempts")
        return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(_test_one, vm): vm for vm in vms}
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                ok_count += 1

    return ok_count


# --- Helpers ---

def _register_phase(
    snippet_path: Path, config_name: str, run_id: str, deploy_dir: Path,
) -> None:
    """Register GHOSTS deployment in PHASE experiments.json."""
    if not snippet_path.exists():
        return

    lib_dir = deploy_dir / "lib"
    register_script = lib_dir / "register_experiment.py"
    if not register_script.exists():
        return

    inventory_path = snippet_path.parent / "inventory.ini"

    try:
        result = subprocess.run(
            [
                "python3", str(register_script),
                "--name", config_name,
                "--snippet", str(snippet_path),
                "--inventory", str(inventory_path),
                "--run-id", run_id,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            output.info("  Registered in PHASE experiments.json")
        else:
            output.info(f"  PHASE registration skipped: {result.stderr[:80]}")
    except Exception:
        pass  # Non-critical


def _find_ghosts_config(config_name: str | None, deploy_dir: Path) -> Path | None:
    """Find GHOSTS config directory."""
    if config_name:
        d = deploy_dir / config_name
        if (d / "config.yaml").exists():
            return d
        return None

    # Auto-detect first ghosts config
    for d in sorted(deploy_dir.iterdir()):
        if d.is_dir() and (d / "config.yaml").exists():
            try:
                cfg = DeploymentConfig.load(d / "config.yaml")
                if cfg.is_ghosts():
                    return d
            except Exception:
                continue
    return None


def _make_dep_id(deployment_name: str, run_id: str) -> str:
    """Build dep_id from deployment name + run_id."""
    dep = deployment_name
    for prefix in ("ruse-", "sup-", "ghosts-"):
        if dep.startswith(prefix):
            dep = dep[len(prefix):]
    dep = dep.replace("-", "")
    return f"{dep}{run_id}"


def _generate_feedback_timeline(behavior_source: str, run_dir: Path) -> Path | None:
    """Generate a GHOSTS timeline.json from PHASE feedback configs."""
    import importlib.util

    source_path = Path(behavior_source)
    feedback_dir = _find_feedback_subdir(source_path)
    if not feedback_dir:
        return None

    # Import the translator from lib/
    lib_dir = Path(__file__).resolve().parent.parent.parent / "lib"
    spec = importlib.util.spec_from_file_location(
        "phase_to_timeline", lib_dir / "phase_to_timeline.py",
    )
    if not spec or not spec.loader:
        return None

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    timeline = mod.generate_timeline(feedback_dir)
    timeline_path = run_dir / "timeline.json"
    timeline_path.write_text(json.dumps(timeline, indent=2) + "\n")
    return timeline_path


def _find_feedback_subdir(source_path: Path) -> Path | None:
    """Find a behavior config subdir with JSON files.

    Prefers M/M1 (broadest RUSE config set, no LLM-specific fields).
    Then tries GHOSTS-specific paths (npc/npc).
    Falls back to first subdir containing activity_pattern.json or timing_profile.json.
    """
    # RUSE brain paths
    for name in ["M/M1", "M/M2", "B.llama/B0.llama", "S.llama/S0.llama"]:
        candidate = source_path / name
        if candidate.is_dir() and any(candidate.glob("*.json")):
            return candidate

    # GHOSTS NPC path (double-nested)
    for name in ["npc/npc", "api/api"]:
        candidate = source_path / name
        if candidate.is_dir() and any(candidate.glob("*.json")):
            return candidate

    # Fallback: first subdir with activity_pattern.json or timing_profile.json
    for config_file in ["activity_pattern.json", "timing_profile.json"]:
        for p in source_path.rglob(config_file):
            return p.parent

    return None
