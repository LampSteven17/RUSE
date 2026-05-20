"""Health audit of all active GHOSTS NPC deployments.

Per-VM checks (via SSH probe). Two roles, two probe shapes:

  API VM (g-{hash}-api-0):
    1. SSH reachable
    2. Docker stack: 5 containers (ghosts-{api,postgres,frontend,n8n,grafana})
       all 'Up' — n8n in particular has a tendency to restart-loop in the wild
    3. /api/machines healthcheck — distinct-name count covers expected NPCs

  NPC VMs (g-{hash}-npc-N):
    1. SSH reachable
    2. ghosts-client.service active
    3. NRestarts within mode-aware threshold:
         controls: NRestarts == 0 expected (pure upstream, no cap)
         feedback: NRestarts in [0, 50] healthy (cgroup OOM cycle every ~2h
                   from upstream memleak; ~12 cycles/24h is normal; much
                   higher means MemoryMax cap is too tight)
    4. Memory cap drop-in present iff feedback (controls keep pure upstream)
    5. RSS within cap (informational; near-cap = next OOM imminent)
    6. /opt/ghosts-client/config/timeline.json present + parseable +
       Status=Run + handlers > 0 + has top-level Id (the .NET client adds
       a registration GUID on first run; missing Id ⇒ never started OK)
    7. Mode contract — read run_dir/timelines/{vm_name}.json LOCALLY (the
       reference we shipped), not the deployed copy: the .NET client
       rewrites timeline.json at startup (drops _phase_metadata, normalizes
       JSON), so the on-VM file can't be trusted for mode validation.
       _phase_metadata.mode must match deployment type
       (ghosts-controls/ → mode=controls; ghosts-feedback-*/ → mode=feedback;
       anything else FATAL — same shape as DECOY's window-mode FATAL gate).

Cross-deployment:
  - OpenStack vs inventory orphan/missing diff (g-{hash}- prefix)
  - PHASE experiments.json registration
  - Duplicate run_ids per config name
  - Orphaned 200GB volumes
  - Session log warnings from latest deploy
  - API machine-set covers every healthy NPC in [ghosts_clients]

Outputs:
  - Terminal summary table per deployment
  - Markdown report at deployments/logs/audit_ghosts_<timestamp>.md
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import subprocess
import time
from collections import defaultdict
from pathlib import Path

from ..core import output
from ..core.config import DeploymentConfig
from ..core.openstack import OpenStack
from ..core.vm_naming import make_ghosts_vm_prefix, make_run_dep_id


EXPERIMENTS_JSON = Path("/mnt/AXES2U1/experiments.json")

# 5 containers expected on the API VM (see install-ghosts-api.yaml docker compose).
EXPECTED_CONTAINERS = {
    "ghosts-api",
    "ghosts-postgres",
    "ghosts-frontend",
    "ghosts-n8n",
    "ghosts-grafana",
}

# Memcap drop-in path on NPC VMs (set by install-ghosts-clients.yaml when
# is_feedback=true — feedback gets the .NET memleak mitigation, controls
# stay on the pure upstream unit).
DROPIN_PATH = "/etc/systemd/system/ghosts-client.service.d/memcap.conf"

# Restart thresholds. NRestarts is cumulative — never decays. The pre-cap
# leak fires every ~2h on a 28GB VM, so 24h × 0.5 = ~12 cycles is the
# steady-state on feedback. Threshold leaves headroom for slightly leaky
# timelines that exceed the cap faster (e.g. 50 = ~one cycle/30min over
# a day) without flagging genuinely runaway clients.
FEEDBACK_RESTART_HEALTHY = 50
# Continuous active-uptime past this value treats the service as
# "stabilized after early failures" (matches DECOY's STABLE_UPTIME_S
# pattern — early-deploy crash bursts shouldn't dirty the audit
# permanently).
STABLE_UPTIME_S = 600


# ── Per-VM SSH probe ────────────────────────────────────────────────────

def _ssh_run(ip: str, bash: str, key_path: str = "~/.ssh/id_ed25519",
             timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "ssh",
            "-i", os.path.expanduser(key_path),
            "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            "-o", "ConnectionAttempts=1",
            "-o", "BatchMode=yes",
            "-o", "LogLevel=ERROR",
            f"ubuntu@{ip}",
            bash,
        ],
        capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "SSH_AUTH_SOCK": ""},
    )


def _ssh_probe_api(ip: str) -> dict:
    """Probe the GHOSTS API VM. Single round-trip."""
    bash = r"""
echo "NOW=$(date +%s)"
# Docker container state. PS_LINE format: name|status (one container per row,
# joined with ';'). EXPECTED_CONTAINERS in the classifier checks set membership.
echo "DOCKER_PS=$(sudo docker ps --format '{{.Names}}|{{.Status}}' 2>/dev/null | tr '\n' ';' )"
# /api/machines healthcheck. Returns array of machine objects; we extract
# distinct .name values so re-registrations (same VM, multiple rows) collapse.
RESP=$(curl -sf --max-time 10 http://localhost:5000/api/machines 2>/dev/null)
if [ -n "$RESP" ]; then
  echo "API_HEALTH=ok"
  echo "API_DISTINCT_NAMES=$(echo "$RESP" | python3 -c 'import sys,json
try:
  d=json.load(sys.stdin)
  names=sorted({m.get("name","") for m in d if m.get("name")})
  print(",".join(names))
except: print("")' 2>/dev/null)"
  echo "API_RAW_COUNT=$(echo "$RESP" | python3 -c 'import sys,json
try: print(len(json.load(sys.stdin)))
except: print(0)' 2>/dev/null)"
else
  echo "API_HEALTH=fail"
  echo "API_DISTINCT_NAMES="
  echo "API_RAW_COUNT=0"
fi
"""
    try:
        result = _ssh_run(ip, bash)
    except subprocess.TimeoutExpired:
        return {"ssh_ok": False, "ssh_error": "ssh timeout"}
    if result.returncode != 0:
        return {"ssh_ok": False, "ssh_error": result.stderr.strip()[:120]}
    data = {"ssh_ok": True}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            data[k] = v.strip()
    return data


def _ssh_probe_npc(ip: str) -> dict:
    """Probe a GHOSTS NPC client VM. Single round-trip."""
    bash = r"""
echo "NOW=$(date +%s)"
SVC=$(systemctl is-active ghosts-client 2>/dev/null || echo notfound)
echo "SVC=$SVC"
echo "NRESTARTS=$(systemctl show ghosts-client -p NRestarts --value 2>/dev/null || echo 0)"
echo "MEM_CURRENT=$(systemctl show ghosts-client -p MemoryCurrent --value 2>/dev/null || echo 0)"
echo "MEM_MAX=$(systemctl show ghosts-client -p MemoryMax --value 2>/dev/null || echo 0)"
ACTIVE_ENTER=$(systemctl show ghosts-client -p ActiveEnterTimestampMonotonic --value 2>/dev/null || echo 0)
NOW_MONO=$(awk '{print int($1)}' /proc/uptime)
ACTIVE_ENTER_SEC=$(( ACTIVE_ENTER / 1000000 ))
echo "SVC_UPTIME_S=$(( NOW_MONO - ACTIVE_ENTER_SEC ))"
# Memcap drop-in. Presence is a binary signal — file exists iff
# install-ghosts-clients.yaml ran with is_feedback=true.
if [ -f /etc/systemd/system/ghosts-client.service.d/memcap.conf ]; then
  echo "DROPIN=1"
else
  echo "DROPIN=0"
fi
TLINE=/opt/ghosts-client/config/timeline.json
# The .NET GHOSTS client rewrites timeline.json at startup (adds an Id
# registration GUID, drops _phase_metadata, normalizes JSON formatting).
# So we don't trust the on-disk file for mode validation or SHA parity —
# both are checked locally against the run_dir copy. Here we only verify
# the runtime artifact looks healthy: parses, Status=Run, handlers>0,
# and the Id field is present (means the client successfully bound and
# wrote at least once — missing Id ⇒ never started OK).
if [ -f "$TLINE" ]; then
  sudo python3 - "$TLINE" <<'PYEOF'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("TLINE_PARSE=fail")
    print("TLINE_STATUS=?")
    print("TLINE_HANDLERS=0")
    print("TLINE_HAS_ID=0")
    sys.exit(0)
print("TLINE_PARSE=ok")
print("TLINE_STATUS=%s" % (d.get("Status") or "?"))
print("TLINE_HANDLERS=%d" % len(d.get("TimeLineHandlers") or []))
print("TLINE_HAS_ID=%d" % (1 if d.get("Id") else 0))
PYEOF
else
  echo "TLINE_PARSE=missing"
  echo "TLINE_STATUS=?"
  echo "TLINE_HANDLERS=0"
  echo "TLINE_HAS_ID=0"
fi
"""
    try:
        result = _ssh_run(ip, bash)
    except subprocess.TimeoutExpired:
        return {"ssh_ok": False, "ssh_error": "ssh timeout"}
    if result.returncode != 0:
        return {"ssh_ok": False, "ssh_error": result.stderr.strip()[:120]}
    data = {"ssh_ok": True}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            data[k] = v.strip()
    return data


# ── Classification ──────────────────────────────────────────────────────

def _local_timeline_mode(local_path: Path | None) -> str:
    """Read _phase_metadata.mode from the run_dir copy of the timeline.

    Why local, not on-VM: the .NET ghosts-client rewrites timeline.json at
    startup (adds an Id GUID, strips _phase_metadata). Mode validation has
    to read the reference we shipped, not what the client left behind.

    Returns one of: "feedback", "controls", "missing_metadata",
    "parse_error", "missing_file".
    """
    if not local_path or not local_path.exists():
        return "missing_file"
    try:
        d = json.loads(local_path.read_text())
    except Exception:
        return "parse_error"
    meta = d.get("_phase_metadata") or {}
    mode = meta.get("mode")
    if not mode:
        return "missing_metadata"
    return mode


def _classify_api(vm: dict, probe: dict, expected_clients: int,
                  healthy_npc_names: set[str]) -> dict:
    """Apply pass/fail rules to API VM probe."""
    checks = {}
    checks["ssh"] = "OK" if probe.get("ssh_ok") else "FAIL"
    if not probe.get("ssh_ok"):
        for k in ("stack", "registration"):
            checks[k] = "?"
        return checks

    # Container stack — every expected name must appear and be 'Up'.
    docker_ps = probe.get("DOCKER_PS", "")
    container_states = {}
    for entry in docker_ps.split(";"):
        if not entry or "|" not in entry:
            continue
        name, status = entry.split("|", 1)
        container_states[name.strip()] = status.strip()

    missing = EXPECTED_CONTAINERS - container_states.keys()
    not_up = {n for n, s in container_states.items()
              if n in EXPECTED_CONTAINERS and not s.startswith("Up")}
    if missing:
        checks["stack"] = f"FAIL (missing: {','.join(sorted(missing))})"
    elif not_up:
        details = ", ".join(f"{n}={container_states[n]}" for n in sorted(not_up))
        checks["stack"] = f"FAIL ({details})"
    else:
        checks["stack"] = f"OK ({len(EXPECTED_CONTAINERS)}/5)"

    # /api/machines registration — distinct names must cover the healthy NPCs.
    # /api/machines lists every registration ever, including re-registers
    # (same machine across cgroup-OOM respawns), so raw count routinely
    # exceeds expected. Distinct-by-name dedupes that.
    if probe.get("API_HEALTH") != "ok":
        checks["registration"] = "FAIL (API down)"
    else:
        names_csv = probe.get("API_DISTINCT_NAMES", "")
        registered = {n for n in names_csv.split(",") if n}
        # Healthy NPCs that haven't registered = problem. NPCs that aren't
        # healthy are expected to be missing — they're checked elsewhere.
        unregistered = healthy_npc_names - registered
        if unregistered and healthy_npc_names:
            checks["registration"] = (
                f"FAIL ({len(registered)}/{len(healthy_npc_names)} healthy NPCs registered, "
                f"missing: {','.join(sorted(unregistered))})"
            )
        elif not healthy_npc_names:
            checks["registration"] = f"OK ({len(registered)} machines, no healthy NPCs to verify)"
        else:
            checks["registration"] = f"OK ({len(registered)}/{len(healthy_npc_names)} NPCs)"

    # NPC-only checks: n/a on the API VM.
    for k in ("service", "mode", "cap", "restart", "memory", "timeline"):
        checks[k] = "n/a"
    return checks


def _classify_npc(vm: dict, probe: dict, dep_is_feedback: bool,
                  local_mode: str) -> dict:
    """Apply pass/fail rules to one NPC VM probe.

    `local_mode` comes from _local_timeline_mode() — read from the
    run_dir copy of the timeline because the on-VM file is rewritten by
    the .NET client at startup.
    """
    checks = {}
    checks["ssh"] = "OK" if probe.get("ssh_ok") else "FAIL"
    if not probe.get("ssh_ok"):
        for k in ("service", "mode", "cap", "restart", "memory", "timeline"):
            checks[k] = "?"
        # API-only checks: n/a.
        for k in ("stack", "registration"):
            checks[k] = "n/a"
        return checks

    # Service active. Cumulative NRestarts can be high on feedback (cgroup
    # OOM cycle is healthy) — service-state check just asks "is it active
    # right now."
    svc = probe.get("SVC", "?")
    nrestarts = int(probe.get("NRESTARTS", "0") or "0")
    svc_uptime = int(probe.get("SVC_UPTIME_S", "0") or "0")
    if svc == "active":
        checks["service"] = "OK"
    else:
        checks["service"] = f"FAIL ({svc})"

    # Mode contract. Read from the run_dir reference (local), not the
    # deployed file — the .NET client rewrites timeline.json at startup
    # and strips _phase_metadata. The deployment type tells us the
    # expected mode; anything else is FATAL — same shape as DECOY's
    # window-mode contract.
    expected_mode = "feedback" if dep_is_feedback else "controls"
    if local_mode == expected_mode:
        checks["mode"] = f"OK ({local_mode})"
    elif local_mode in ("missing_file", "parse_error"):
        checks["mode"] = f"FAIL (run_dir copy: {local_mode})"
    elif local_mode == "missing_metadata":
        checks["mode"] = "FAIL (run_dir copy: no _phase_metadata.mode)"
    else:
        checks["mode"] = (
            f"FATAL (mode={local_mode}, expected {expected_mode})"
        )

    # Memcap drop-in. Must be present iff feedback. Mismatch is a deploy
    # bug — install-ghosts-clients.yaml's `when: is_feedback` decided wrong.
    dropin = probe.get("DROPIN") == "1"
    if dep_is_feedback and dropin:
        checks["cap"] = "OK (memcap drop-in)"
    elif not dep_is_feedback and not dropin:
        checks["cap"] = "OK (pure upstream)"
    elif dep_is_feedback and not dropin:
        checks["cap"] = "FAIL (feedback missing memcap drop-in)"
    else:
        checks["cap"] = "FAIL (controls has memcap drop-in)"

    # Restart count — bimodal threshold. Pre-cap, feedback NPCs went
    # SSH-fail entirely after ~2h because the upstream memleak OOM-killed
    # sshd. Post-cap, kernel kills only the .NET process inside its cgroup
    # and Restart=always respawns within RestartSec=10s. So feedback can
    # legitimately have NRestarts>0 — that's the cap doing its job.
    if svc != "active":
        # Service down already flagged above; restart count is
        # secondary information, not a separate failure.
        checks["restart"] = f"? ({nrestarts}, svc {svc})"
    elif svc_uptime >= STABLE_UPTIME_S and nrestarts == 0:
        checks["restart"] = "OK"
    elif dep_is_feedback:
        if nrestarts <= FEEDBACK_RESTART_HEALTHY:
            checks["restart"] = f"OK ({nrestarts} cycles)"
        else:
            checks["restart"] = (
                f"WARN ({nrestarts} cycles > {FEEDBACK_RESTART_HEALTHY}; "
                f"cap may be too tight)"
            )
    else:
        # Controls — pure upstream, no cap. NRestarts > 0 means the .NET
        # client crashed at least once. The leak is upstream-baked-in so
        # this can happen, but it's worth flagging.
        if nrestarts == 0:
            checks["restart"] = "OK"
        elif svc_uptime >= STABLE_UPTIME_S:
            checks["restart"] = f"OK ({nrestarts} restarts, stable {svc_uptime//60}m)"
        else:
            checks["restart"] = f"WARN ({nrestarts} restarts, up {svc_uptime}s)"

    # Memory vs cap. Informational on controls (no cap = MEM_MAX is
    # systemd's "infinity" sentinel). On feedback, RSS approaching cap
    # means OOM is imminent — useful as a leading indicator.
    mem_current = int(probe.get("MEM_CURRENT", "0") or "0")
    mem_max = probe.get("MEM_MAX", "0")
    try:
        mem_max_int = int(mem_max)
    except ValueError:
        mem_max_int = 0
    if mem_current == 0:
        checks["memory"] = "?"
    elif mem_max_int == 0 or mem_max_int >= 2**62:
        # No cap (controls) or systemd's infinity sentinel. Just report RSS.
        checks["memory"] = f"OK ({mem_current // (1024**3)} GB)"
    else:
        ratio = mem_current / mem_max_int
        if ratio >= 0.95:
            checks["memory"] = (
                f"WARN ({mem_current // (1024**3)}/{mem_max_int // (1024**3)} GB, "
                f"{ratio:.0%}) — OOM imminent"
            )
        else:
            checks["memory"] = (
                f"OK ({mem_current // (1024**3)}/{mem_max_int // (1024**3)} GB, "
                f"{ratio:.0%})"
            )

    # Timeline runtime artifact (the on-disk file the client owns):
    #   1. file present + parseable
    #   2. Status == "Run" (case-insensitive)
    #   3. handlers > 0
    #   4. Id field present — .NET client adds a registration GUID on
    #      first successful start; missing Id ⇒ never started OK
    # SHA parity with run_dir is intentionally NOT checked: the client
    # rewrites the file (drops _phase_metadata, normalizes JSON), so a
    # mismatch is the expected steady state.
    tline_parse = probe.get("TLINE_PARSE", "?")
    tline_status = probe.get("TLINE_STATUS", "?")
    tline_handlers = int(probe.get("TLINE_HANDLERS", "0") or "0")
    tline_has_id = probe.get("TLINE_HAS_ID") == "1"
    if tline_parse == "missing":
        checks["timeline"] = "FAIL (no timeline.json)"
    elif tline_parse == "fail":
        checks["timeline"] = "FAIL (parse error)"
    elif tline_status.lower() != "run":
        checks["timeline"] = f"FAIL (Status={tline_status})"
    elif tline_handlers == 0:
        checks["timeline"] = "FAIL (no handlers)"
    elif not tline_has_id:
        checks["timeline"] = "FAIL (no Id — client never registered)"
    else:
        checks["timeline"] = f"OK ({tline_handlers} handlers)"

    # API-only checks: n/a on NPC.
    for k in ("stack", "registration"):
        checks[k] = "n/a"
    return checks


# ── Discovery ────────────────────────────────────────────────────────────

def _discover_deployments(deploy_dir: Path) -> list[dict]:
    """Find all active GHOSTS deployments. Returns list of dicts."""
    deployments = []
    for config_dir in sorted(deploy_dir.iterdir()):
        if not config_dir.is_dir():
            continue
        config_file = config_dir / "config.yaml"
        if not config_file.exists():
            continue
        try:
            cfg = DeploymentConfig.load(config_file)
        except Exception as e:
            output.error(f"  WARNING: skipping {config_dir.name}/config.yaml: "
                         f"{type(e).__name__}: {e}")
            continue
        # GHOSTS only — skip DECOY and RAMPART.
        if not cfg.is_ghosts():
            continue

        runs_dir = config_dir / "runs"
        if not runs_dir.is_dir():
            continue

        for run_dir in sorted(runs_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            inv = run_dir / "inventory.ini"
            if not inv.exists():
                continue
            api_vm, client_vms = _parse_inventory(inv)
            if not api_vm and not client_vms:
                continue
            deployments.append({
                "name": config_dir.name,
                "run_id": run_dir.name,
                "run_dir": run_dir,
                "api_vm": api_vm,
                "client_vms": client_vms,
                "expected_clients": cfg.ghosts_client_count(),
                # ghosts-feedback-* gets the memcap drop-in; ghosts-controls
                # stays on pure upstream. Mirrors install-ghosts-clients.yaml's
                # is_feedback gate (set from config_name.startswith).
                "is_feedback": config_dir.name.startswith("ghosts-feedback-"),
            })
    return deployments


def _parse_inventory(inv_path: Path) -> tuple[dict | None, list[dict]]:
    """Parse a GHOSTS inventory.ini into (api_vm, [client_vms]).

    Two host groups: [ghosts_api] (single VM, name+ip only) and
    [ghosts_clients] (per-host ghosts_api_ip + optional ghosts_timeline_file).
    """
    api_vm: dict | None = None
    client_vms: list[dict] = []
    section = None
    for line in inv_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section == "ghosts_api":
            m = re.match(r"^(\S+)\s+ansible_host=(\S+)", line)
            if m:
                api_vm = {"name": m.group(1), "ip": m.group(2), "role": "api"}
        elif section == "ghosts_clients":
            m = re.match(r"^(\S+)\s+ansible_host=(\S+)", line)
            if m:
                vm = {"name": m.group(1), "ip": m.group(2), "role": "npc"}
                # Pull per-host ghosts_timeline_file for parity check.
                tl_match = re.search(r"ghosts_timeline_file=(\S+)", line)
                if tl_match:
                    vm["timeline_file"] = tl_match.group(1)
                client_vms.append(vm)
    return api_vm, client_vms


# ── Main entry ───────────────────────────────────────────────────────────

def run_ghosts_audit(deploy_dir: Path) -> int:
    """Run full GHOSTS audit. Returns 0 on no failures, 1 otherwise."""
    output.banner("GHOSTS AUDIT")
    output.info("")

    output.dim("  Discovering deployments...")
    deployments = _discover_deployments(deploy_dir)
    if not deployments:
        output.info("No active GHOSTS deployments found.")
        return 0

    total_vms = sum(
        (1 if d["api_vm"] else 0) + len(d["client_vms"]) for d in deployments
    )
    output.info(f"  Found {len(deployments)} deployments, {total_vms} VMs")

    output.dim("  Querying OpenStack...")
    os_client = OpenStack()
    all_os_servers = set(os_client.server_list())

    output.dim("  Loading PHASE experiments.json...")
    exp_data: dict = {}
    if EXPERIMENTS_JSON.exists():
        try:
            exp_data = json.loads(EXPERIMENTS_JSON.read_text())
        except Exception:
            pass

    output.info("")
    output.info(f"  Probing {total_vms} VMs in parallel...")

    # Probe phase 1: NPCs in parallel. We need NPC health to compute the
    # expected machine-set for the API's registration check, so NPCs go
    # first; APIs are probed in phase 2 with that information in hand.
    npc_results = []  # list of (dep, vm, probe, checks)
    api_jobs = []     # collected for phase 2

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        # Phase 1: every NPC across every deployment
        npc_futures = {}
        for dep in deployments:
            for vm in dep["client_vms"]:
                fut = pool.submit(_ssh_probe_npc, vm["ip"])
                npc_futures[fut] = (dep, vm)

        done = 0
        for fut in concurrent.futures.as_completed(npc_futures):
            dep, vm = npc_futures[fut]
            try:
                probe = fut.result()
            except Exception as e:
                probe = {"ssh_ok": False, "ssh_error": str(e)[:100]}
            local_mode = _local_timeline_mode(
                Path(vm["timeline_file"]) if vm.get("timeline_file") else None
            )
            checks = _classify_npc(vm, probe, dep["is_feedback"], local_mode)
            npc_results.append((dep, vm, probe, checks))
            done += 1
            ts = time.strftime("%H:%M:%S")
            status = _row_status(checks)
            output.info(f"  [{ts}]  [{done}/{total_vms}]  {status}  {vm['name']}")

        # Compute per-deployment healthy-NPC set for API registration check.
        healthy_npcs_by_dep: dict[tuple, set[str]] = defaultdict(set)
        for dep, vm, probe, checks in npc_results:
            if checks.get("service") == "OK":
                healthy_npcs_by_dep[(dep["name"], dep["run_id"])].add(vm["name"])

        # Phase 2: API VMs in parallel, given the healthy-NPC sets.
        api_futures = {}
        for dep in deployments:
            if dep["api_vm"]:
                fut = pool.submit(_ssh_probe_api, dep["api_vm"]["ip"])
                api_futures[fut] = dep

        api_results = []
        for fut in concurrent.futures.as_completed(api_futures):
            dep = api_futures[fut]
            try:
                probe = fut.result()
            except Exception as e:
                probe = {"ssh_ok": False, "ssh_error": str(e)[:100]}
            healthy = healthy_npcs_by_dep.get((dep["name"], dep["run_id"]), set())
            checks = _classify_api(
                dep["api_vm"], probe, dep["expected_clients"], healthy,
            )
            api_results.append((dep, dep["api_vm"], probe, checks))
            done += 1
            ts = time.strftime("%H:%M:%S")
            status = _row_status(checks)
            output.info(f"  [{ts}]  [{done}/{total_vms}]  {status}  {dep['api_vm']['name']}")

    all_results = npc_results + api_results

    # ── Cross-deployment checks ──────────────────────────────────────────
    issues: list[str] = []

    # Orphan / missing diff vs OpenStack (g-{hash}- prefix per deployment)
    for dep in deployments:
        prefix = _dep_prefix(dep)
        inv_names = set()
        if dep["api_vm"]:
            inv_names.add(dep["api_vm"]["name"])
        inv_names.update(vm["name"] for vm in dep["client_vms"])
        os_for_dep = {n for n in all_os_servers if n.startswith(prefix)}
        for vm_name in os_for_dep - inv_names:
            issues.append(
                f"{dep['name']}-{dep['run_id']}: ORPHAN on OpenStack: {vm_name}"
            )
        for vm_name in inv_names - os_for_dep:
            issues.append(
                f"{dep['name']}-{dep['run_id']}: MISSING on OpenStack: {vm_name}"
            )

    # PHASE experiments.json registration
    for dep in deployments:
        entry = exp_data.get(dep["name"], {})
        registered_ips = set(entry.get("ips", {}).keys())
        inv_ips = set()
        if dep["api_vm"]:
            inv_ips.add(dep["api_vm"]["ip"])
        inv_ips.update(vm["ip"] for vm in dep["client_vms"])
        for ip in inv_ips - registered_ips:
            issues.append(
                f"{dep['name']}-{dep['run_id']}: NOT in experiments.json: {ip}"
            )

    # Duplicate run_ids per config name (should never happen, but if it
    # does, teardown gets confused — same trap as DECOY).
    by_name = defaultdict(list)
    for dep in deployments:
        by_name[dep["name"]].append(dep["run_id"])
    for name, run_ids in by_name.items():
        if len(run_ids) > 1:
            issues.append(f"{name}: multiple active runs: {', '.join(run_ids)}")

    # Orphaned 200GB volumes (shared with DECOY/RAMPART teardown leaks)
    orphan_vols = os_client.find_orphaned_volumes(size=200)
    if orphan_vols:
        issues.append(
            f"ORPHANED VOLUMES: {len(orphan_vols)} nameless 200GB volumes"
        )

    # Session log warnings from latest deploy session
    session_logs = sorted(
        (deploy_dir / "logs").glob("session-deploy-*.log"), reverse=True
    )
    if session_logs:
        try:
            session_text = session_logs[0].read_text()
            warns = [l.strip() for l in session_text.splitlines() if "[WARNING]" in l]
            if warns:
                issues.append(
                    f"SESSION LOG WARNINGS ({session_logs[0].name}): {len(warns)}"
                )
                for w in warns[:10]:
                    issues.append(f"  {w}")
        except OSError:
            pass

    # Per-VM check failures → issues
    for dep, vm, probe, checks in all_results:
        for check_name, status in checks.items():
            if (status != "?"
                    and not status.startswith("OK")
                    and not status.startswith("n/a")):
                issues.append(
                    f"{dep['name']}-{dep['run_id']}/{vm['name']}: "
                    f"{check_name}={status}"
                )

    # ── Summary table ────────────────────────────────────────────────────
    output.info("")
    output.banner("AUDIT SUMMARY")
    output.info("")

    by_dep = defaultdict(list)
    for dep, vm, probe, checks in all_results:
        by_dep[(dep["name"], dep["run_id"])].append((vm, probe, checks))

    headers = ["Deployment", "VMs", "SSH", "Stack", "Reg",
               "Svc", "Mode", "Cap", "Restart", "Tline"]
    rows = []

    def _ok(v: str) -> bool:
        return v.startswith("n/a") or v.startswith("OK")

    def _count(entries, key, role_filter=None):
        n_total = 0
        n_ok = 0
        for vm, _, c in entries:
            if role_filter and vm.get("role") != role_filter:
                continue
            n_total += 1
            if _ok(c.get(key, "")):
                n_ok += 1
        return n_ok, n_total

    for (name, rid), entries in sorted(by_dep.items()):
        api_count = sum(1 for vm, _, _ in entries if vm.get("role") == "api")
        npc_count = sum(1 for vm, _, _ in entries if vm.get("role") == "npc")
        ssh_ok, ssh_n = _count(entries, "ssh")
        # Stack/Reg are API-only — n_total is the API count (1 typically).
        stack_ok, stack_n = _count(entries, "stack", role_filter="api")
        reg_ok, reg_n = _count(entries, "registration", role_filter="api")
        # Svc/Mode/Cap/Restart/Tline are NPC-only.
        svc_ok, svc_n = _count(entries, "service", role_filter="npc")
        mode_ok, mode_n = _count(entries, "mode", role_filter="npc")
        cap_ok, cap_n = _count(entries, "cap", role_filter="npc")
        rst_ok, rst_n = _count(entries, "restart", role_filter="npc")
        tl_ok, tl_n = _count(entries, "timeline", role_filter="npc")
        rows.append([
            f"{name}-{rid}",
            f"{api_count}A+{npc_count}N",
            f"{ssh_ok}/{ssh_n}",
            f"{stack_ok}/{stack_n}",
            f"{reg_ok}/{reg_n}",
            f"{svc_ok}/{svc_n}",
            f"{mode_ok}/{mode_n}",
            f"{cap_ok}/{cap_n}",
            f"{rst_ok}/{rst_n}",
            f"{tl_ok}/{tl_n}",
        ])
    output.table(headers, rows)
    output.info("")

    if issues:
        output.info(f"ISSUES ({len(issues)}):")
        for i in issues:
            output.info(f"  - {i}")
        output.info("")
    else:
        output.info("No issues found.")
        output.info("")

    # Markdown report
    log_dir = deploy_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    report_path = log_dir / f"audit_ghosts_{time.strftime('%Y%m%d-%H%M%S')}.md"
    _write_markdown(report_path, deployments, all_results, issues, by_dep)
    output.info(f"  Full report: {report_path}")

    return 1 if issues else 0


def _row_status(checks: dict) -> str:
    """Compact one-char-per-check status string for terminal."""
    parts = []
    for k in ("ssh", "stack", "registration",
              "service", "mode", "cap", "restart", "memory", "timeline"):
        v = checks.get(k, "?")
        if v.startswith("OK") or v.startswith("n/a"):
            parts.append(".")
        elif v == "?":
            parts.append("?")
        elif v.startswith("WARN"):
            parts.append("W")
        else:
            parts.append("X")
    return "".join(parts)


def _dep_prefix(dep: dict) -> str:
    """Build the OpenStack VM prefix for a GHOSTS deployment ('g-{hash}-')."""
    return make_ghosts_vm_prefix(make_run_dep_id(dep["name"], dep["run_id"]))


# ── Markdown report ──────────────────────────────────────────────────────

def _write_markdown(
    path: Path,
    deployments: list[dict],
    all_results: list[tuple],
    issues: list[str],
    by_dep: dict,
) -> None:
    lines = []
    lines.append("# GHOSTS Audit Report")
    lines.append("")
    lines.append(f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    lines.append(f"**Deployments scanned:** {len(deployments)}")
    total_vms = sum(
        (1 if d['api_vm'] else 0) + len(d['client_vms']) for d in deployments
    )
    lines.append(f"**Total VMs:** {total_vms}")
    lines.append(f"**Issues found:** {len(issues)}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(
        "| Deployment | Mode | VMs | SSH | Stack | Reg | Svc | Mode | Cap | "
        "Restart | Timeline |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")

    def _ok(v: str) -> bool:
        return v.startswith("n/a") or v.startswith("OK")

    dep_lookup = {(d["name"], d["run_id"]): d for d in deployments}

    for (name, rid), entries in sorted(by_dep.items()):
        d = dep_lookup[(name, rid)]
        mode = "feedback" if d["is_feedback"] else "controls"
        api_count = sum(1 for vm, _, _ in entries if vm.get("role") == "api")
        npc_count = sum(1 for vm, _, _ in entries if vm.get("role") == "npc")

        def cnt(key, role=None):
            t = ok = 0
            for vm, _, c in entries:
                if role and vm.get("role") != role:
                    continue
                t += 1
                if _ok(c.get(key, "")):
                    ok += 1
            return ok, t

        ssh = cnt("ssh")
        stack = cnt("stack", "api")
        reg = cnt("registration", "api")
        svc = cnt("service", "npc")
        md = cnt("mode", "npc")
        cap = cnt("cap", "npc")
        rst = cnt("restart", "npc")
        tl = cnt("timeline", "npc")
        lines.append(
            f"| `{name}-{rid}` | {mode} | {api_count}A+{npc_count}N | "
            f"{ssh[0]}/{ssh[1]} | {stack[0]}/{stack[1]} | {reg[0]}/{reg[1]} | "
            f"{svc[0]}/{svc[1]} | {md[0]}/{md[1]} | {cap[0]}/{cap[1]} | "
            f"{rst[0]}/{rst[1]} | {tl[0]}/{tl[1]} |"
        )
    lines.append("")

    if issues:
        lines.append("## Issues")
        lines.append("")
        for i in issues:
            lines.append(f"- {i}")
        lines.append("")
    else:
        lines.append("## Issues")
        lines.append("")
        lines.append("**None.** All checks passed.")
        lines.append("")

    lines.append("## Per-Deployment Details")
    lines.append("")
    for (name, rid), entries in sorted(by_dep.items()):
        d = dep_lookup[(name, rid)]
        mode = "feedback" if d["is_feedback"] else "controls"
        lines.append(f"### `{name}-{rid}` ({mode})")
        lines.append("")
        lines.append(
            "| VM | Role | IP | SSH | Stack | Reg | Svc | Mode | Cap | "
            "Restart | Memory | Timeline |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for vm, probe, checks in sorted(entries, key=lambda e: e[0]["name"]):
            lines.append(
                f"| `{vm['name']}` | {vm.get('role','?')} | {vm['ip']} | "
                f"{checks.get('ssh','?')} | {checks.get('stack','?')} | "
                f"{checks.get('registration','?')} | "
                f"{checks.get('service','?')} | {checks.get('mode','?')} | "
                f"{checks.get('cap','?')} | {checks.get('restart','?')} | "
                f"{checks.get('memory','?')} | {checks.get('timeline','?')} |"
            )
        lines.append("")

    lines.append("## Legend")
    lines.append("")
    lines.append("- **OK** — check passed")
    lines.append("- **n/a** — check doesn't apply to this role (api vs npc)")
    lines.append("- **WARN** — survivable but worth investigating")
    lines.append("- **FAIL / FATAL** — investigate; FATAL = schema/contract violation")
    lines.append("- **?** — could not determine (usually SSH failed)")
    lines.append("")
    lines.append(
        "Compact terminal status: 9 chars per VM — "
        "ssh/stack/reg/service/mode/cap/restart/memory/timeline. "
        "`.` = pass, `X` = fail, `W` = warn, `?` = unknown."
    )

    path.write_text("\n".join(lines))
