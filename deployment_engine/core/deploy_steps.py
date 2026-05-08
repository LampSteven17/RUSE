"""Shared deploy-step helpers used by every per-type spinup.

Three things every spinup does post-provision:
  1. SSH connectivity test — parallel probe across the new VMs
  2. PHASE registration — write to experiments.json
  3. SSH config snippet install — covered by core/ssh_config.py
     (caller-side because the VM list shape differs per type)
"""

from __future__ import annotations

import concurrent.futures
import os
import subprocess
import time
from pathlib import Path

from . import output


# Path to the register_experiment.py script, lives next to this module.
_REGISTER_SCRIPT = Path(__file__).resolve().parent / "register_experiment.py"


def ssh_connectivity_test(
    hosts: list[dict],
    *,
    key_path: Path | None = None,
    user: str = "ubuntu",
    max_retries: int = 30,
    timeout: int = 10,
    delay: int = 5,
    workers: int = 20,
) -> int:
    """Parallel SSH probe with real-time per-VM output. Returns count reachable.

    Each host dict needs `name` and `ip` keys. Default key is
    ~/.ssh/id_ed25519 (DECOY/GHOSTS); RAMPART/Linux endpoints can
    override via key_path. SSH_AUTH_SOCK is unset in subprocess env to
    avoid OpenStack VM auth-timeouts (see CLAUDE.md).
    """
    if key_path is None:
        key_path = Path.home() / ".ssh" / "id_ed25519"

    ok_count = 0

    def _probe_one(host: dict) -> bool:
        name = host["name"]
        ip = host["ip"]
        for attempt in range(1, max_retries + 1):
            ts = time.strftime("%H:%M:%S")
            try:
                result = subprocess.run(
                    [
                        "ssh",
                        "-i", str(key_path),
                        "-o", "IdentitiesOnly=yes",
                        "-o", "StrictHostKeyChecking=no",
                        "-o", "UserKnownHostsFile=/dev/null",
                        "-o", f"ConnectTimeout={timeout}",
                        "-o", "ConnectionAttempts=1",
                        "-o", "BatchMode=yes",
                        "-o", "LogLevel=ERROR",
                        f"{user}@{ip}", "echo ok",
                    ],
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

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_probe_one, h): h for h in hosts}
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                ok_count += 1

    return ok_count


def register_phase(
    snippet_path: Path,
    config_name: str,
    run_id: str,
    *,
    extra_ips: list[str] | None = None,
) -> bool:
    """Register in PHASE experiments.json via the canonical script.

    Returns True on rc=0; False on registration failure or missing inventory.
    Missing snippet_path returns True — that means an earlier stage already
    aborted, no point double-failing here.

    extra_ips is a list of `IP=HOSTNAME` pairs (DECOY uses this for the
    neighborhood sidecar VM so it shows up in experiments.json alongside
    the SUPs).
    """
    if not snippet_path.exists():
        output.error("  WARNING: ssh_config_snippet.txt missing — skipping PHASE registration")
        return True

    if not _REGISTER_SCRIPT.exists():
        output.error(
            f"  ERROR: register_experiment.py not found at {_REGISTER_SCRIPT}. "
            f"Engine layout broken — fail loud."
        )
        return False

    run_dir = snippet_path.parent
    inventory_path = run_dir / "inventory.ini"

    cmd = [
        "python3", str(_REGISTER_SCRIPT),
        "--name", config_name,
        "--snippet", str(snippet_path),
        "--inventory", str(inventory_path),
        "--run-id", run_id,
    ]
    for pair in extra_ips or []:
        cmd.extend(["--extra-ip", pair])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode == 0:
        output.dim("  Registered in PHASE experiments.json")
        return True

    err = (result.stderr or result.stdout or "").strip()[:400]
    output.error(f"  ERROR: PHASE registration FAILED (rc={result.returncode}): {err}")
    return False


def neighborhood_extra_ips(run_dir: Path) -> list[str]:
    """DECOY-specific helper. Scan neighborhood-inventory.ini for sidecar VMs.

    Returns a list of `IP=HOSTNAME` strings for --extra-ip. Empty list if
    no neighborhood inventory (controls + non-topology-gated feedback).
    """
    inv = run_dir / "neighborhood-inventory.ini"
    if not inv.exists():
        return []
    pairs = []
    import re
    for line in inv.read_text().splitlines():
        m = re.match(r"^(\S+)\s+ansible_host=(\S+)", line)
        if m:
            host, ip = m.group(1), m.group(2)
            pairs.append(f"{ip}={host}")
    return pairs
