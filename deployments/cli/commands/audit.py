"""Full health audit of all active RUSE deployments.

Per-VM checks (via SSH probe):
  1. SSH reachable
  2. SUP service active
  3. Brain process running (pgrep runners.run_*)
  4. Ollama model loaded (and matches expected)
  5. GPU model loaded into VRAM (V100 VMs)
  6. Recent log activity (latest.jsonl mtime within threshold)
  7. MCHP maintenance cron entries (M VMs only)
  8. Behavioral config files present (V2+ feedback deploys)
  9. Feature warnings from runtime (D1-G3 [WARNING] lines in systemd.log)

Cross-deployment consistency:
  10. Inventory ↔ OpenStack orphan/missing detection
  11. PHASE experiments.json registration
  12. No duplicate run_ids per config
  13. Orphaned boot volumes (nameless 200GB available)
  14. Session log warnings from most recent deploy

Outputs:
  - Terminal summary table (9 check columns + 2 new: Fdbk, Warn)
  - Markdown report at deployments/logs/audit_<timestamp>.md
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

from .. import output
from ..config import DeploymentConfig
from ..openstack import OpenStack


EXPERIMENTS_JSON = Path("/mnt/AXES2U1/experiments.json")
# V2+ calibrated agents can sleep up to ~1h between activity clusters
# (CalibratedTiming.inter_cluster delay), so log freshness must allow for that
# plus headroom. 4 hours catches genuinely stuck agents while ignoring normal
# idle windows.
LOG_FRESHNESS_SECS = 14400  # 4 hours

# Expected ollama model per behavior (mirrors INSTALL_SUP.sh resolution)
def expected_model(behavior: str) -> str | None:
    """Return the ollama tag a SUP behavior should have loaded, or None."""
    if behavior in ("C0", "M0"):
        return None
    if behavior.startswith("M"):
        return None  # MCHP brains don't use LLMs
    if behavior.endswith(".llama"):
        return "llama3.1:8b"
    if behavior.endswith(".gemma"):
        # Detect CPU variant by middle char (B0C.gemma, S2C.gemma, etc.)
        # Pattern: brain_letter + version_digit + 'C' + .gemma
        if re.match(r"^[BS]\d+C(\..*)?$", behavior):
            return "gemma4:e2b"
        return "gemma4:26b"
    return None


def expected_service(behavior: str) -> str | None:
    """Return the systemd service name for a behavior, or None for bare control."""
    if behavior == "C0":
        return None
    return behavior.lower().replace(".", "_") + ".service"


def needs_gpu(behavior: str) -> bool:
    """Should this behavior use a GPU?"""
    if behavior in ("C0", "M0") or behavior.startswith("M"):
        return False
    # CPU variants (B0C.gemma, S2C.llama etc.) don't need GPU
    if re.match(r"^[BS]\d+C(\..*)?$", behavior):
        return False
    return True


def needs_mchp_cron(behavior: str) -> bool:
    """Should this VM have MCHP maintenance cron entries?"""
    return bool(re.match(r"^M\d", behavior))


# ── Per-VM SSH probe ────────────────────────────────────────────────────

def _ssh_probe(name: str, ip: str, behavior: str, key_path: str = "~/.ssh/id_ed25519") -> dict:
    """SSH to one VM and collect all health data in a single round trip."""
    svc = expected_service(behavior) or "none"
    # Build a single bash command that prints key=value lines for all checks
    bash = f"""
SVC=$(systemctl is-active {svc} 2>/dev/null || echo notfound)
echo "SVC=$SVC"
# H4: Restart counter — catches crash loops where service is "active" between
# rapid restarts. Today's RAMPART D5 arg-mismatch bug had services with
# NRestarts=2185 over 12hrs but audit reported them as healthy.
echo "NRESTARTS=$(systemctl show {svc} -p NRestarts --value 2>/dev/null || echo 0)"
echo "PROC_COUNT=$(pgrep -f 'runners.run_' 2>/dev/null | wc -l)"
OLLAMA=$(curl -s --max-time 5 http://localhost:11434/api/ps 2>/dev/null)
if [ -n "$OLLAMA" ]; then
  echo "OLLAMA_MODEL=$(echo "$OLLAMA" | python3 -c 'import sys,json
try:
  d=json.load(sys.stdin)
  m=d.get("models",[])
  print(m[0]["name"] if m else "none")
except: print("none")' 2>/dev/null)"
else
  echo "OLLAMA_MODEL=none"
fi
echo "VRAM_MIB=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 || echo 0)"
echo "GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo none)"
LATEST=$(ls -t /opt/ruse/deployed_sups/*/logs/*.jsonl 2>/dev/null | head -1)
if [ -n "$LATEST" ]; then
  echo "LOG_MTIME=$(stat -c %Y "$LATEST")"
  echo "LOG_PATH=$LATEST"
else
  echo "LOG_MTIME=0"
  echo "LOG_PATH=none"
fi
echo "CRON_COUNT=$(sudo crontab -l 2>/dev/null | grep -cE 'mchp-(daily|weekly)' || echo 0)"
echo "NOW=$(date +%s)"
# Feedback feature checks (D1-G3)
# Post-2026-04-16: PHASE emits exactly one file per SUP — behavior.json. Probe
# for that filename specifically plus total *.json count so audit can flag:
#   BC_HAS_BEHAVIOR=1, BC_FILES=1  → healthy feedback deploy
#   BC_HAS_BEHAVIOR=0, BC_FILES=0  → baseline (V0/V1, no feedback)
#   BC_HAS_BEHAVIOR=0, BC_FILES>0 → junk/legacy files (pre-consolidation)
#   BC_HAS_BEHAVIOR=1, BC_FILES>1 → stale legacy JSONs alongside new file
BC_DIR=$(ls -d /opt/ruse/deployed_sups/*/behavioral_configurations 2>/dev/null | head -1)
if [ -n "$BC_DIR" ]; then
  echo "BC_FILES=$(ls "$BC_DIR"/*.json 2>/dev/null | wc -l)"
  if [ -f "$BC_DIR/behavior.json" ]; then
    echo "BC_HAS_BEHAVIOR=1"
  else
    echo "BC_HAS_BEHAVIOR=0"
  fi
else
  echo "BC_FILES=0"
  echo "BC_HAS_BEHAVIOR=0"
fi
SYSLOG=$(ls -t /opt/ruse/deployed_sups/*/logs/systemd.log 2>/dev/null | head -1)
if [ -n "$SYSLOG" ]; then
  # Count only REAL warnings — ablation-gated INFO lines are intentional
  # and should not count against the VM. [INFO] tag explicitly excluded.
  echo "WARN_COUNT=$(grep -c '\\[WARNING\\]' "$SYSLOG" 2>/dev/null || echo 0)"
  echo "INFO_COUNT=$(grep -c '\\[INFO\\].*ablation-gated' "$SYSLOG" 2>/dev/null || echo 0)"
  echo "WARN_LINES=$(grep '\\[WARNING\\]' "$SYSLOG" 2>/dev/null | tail -10 | tr '\\n' '|')"
else
  echo "WARN_COUNT=0"
  echo "INFO_COUNT=0"
  echo "WARN_LINES="
fi
"""
    result = subprocess.run(
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
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "SSH_AUTH_SOCK": ""},
    )

    if result.returncode != 0:
        return {"ssh_ok": False, "ssh_error": result.stderr.strip()[:100]}

    data = {"ssh_ok": True}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            data[k] = v.strip()
    return data


def _classify_vm(vm: dict, probe: dict) -> dict:
    """Apply pass/fail rules per check. Returns dict of check → status."""
    behavior = vm["behavior"]
    checks = {}

    # 1. SSH
    checks["ssh"] = "OK" if probe.get("ssh_ok") else "FAIL"
    if not probe.get("ssh_ok"):
        # If SSH fails, all downstream checks unknown
        for k in ("service", "process", "model", "gpu", "log", "cron", "feedback", "warnings"):
            checks[k] = "?"
        return checks

    # 2. Service
    expected_svc = expected_service(behavior)
    if expected_svc is None:
        checks["service"] = "n/a"
    elif behavior == "M0":
        # M0 is unmodified upstream MITRE pyhuman. On Linux it crash-loops
        # because workflows like open_office_calc.py call os.startfile()
        # (Windows-only API). This is the EXPECTED baseline behavior of the
        # control and is not an issue worth flagging.
        checks["service"] = "EXPECTED (M0 upstream crashes on Linux)"
    else:
        svc_state = probe.get("SVC", "?")
        nrestarts = int(probe.get("NRESTARTS", "0") or "0")
        # H4: A service that's "active" right now but with a high restart count
        # is in a crash loop, not healthy. Flag if NRestarts > 10.
        if svc_state == "active" and nrestarts > 10:
            checks["service"] = f"FAIL (crash-looping, {nrestarts} restarts)"
        elif svc_state == "active":
            checks["service"] = "OK" if nrestarts == 0 else f"OK ({nrestarts} restarts)"
        else:
            checks["service"] = f"FAIL ({svc_state}, {nrestarts} restarts)"

    # 3. Process
    if behavior in ("C0", "M0"):
        checks["process"] = "n/a"
    else:
        proc_count = int(probe.get("PROC_COUNT", "0") or "0")
        checks["process"] = "OK" if proc_count > 0 else f"FAIL (0 procs)"

    # 4. Ollama model loaded matches expected
    # Note: Ollama unloads idle models after OLLAMA_KEEP_ALIVE (default 5m).
    # "Not loaded" is only a failure if the agent isn't otherwise healthy.
    expected = expected_model(behavior)
    actual = probe.get("OLLAMA_MODEL", "none")
    if expected is None:
        checks["model"] = "n/a"
    elif actual == expected:
        checks["model"] = "OK"
    elif actual == "none":
        checks["model"] = "IDLE"  # downgraded later if VM otherwise unhealthy
    else:
        checks["model"] = f"WRONG ({actual})"

    # 5. GPU model loaded (V100 should show >5GB VRAM if model is GPU-loaded)
    if not needs_gpu(behavior):
        checks["gpu"] = "n/a"
    else:
        vram = int(probe.get("VRAM_MIB", "0") or "0")
        gpu_name = probe.get("GPU_NAME", "")
        if "V100" not in gpu_name and "RTX" not in gpu_name:
            checks["gpu"] = f"FAIL (no GPU: {gpu_name})"
        elif vram < 5000:
            checks["gpu"] = "IDLE"  # downgraded later if VM otherwise unhealthy
        else:
            checks["gpu"] = f"OK ({vram // 1024} GB)"

    # 6. Recent log activity
    if behavior == "C0":
        checks["log"] = "n/a"
    else:
        log_mtime = int(probe.get("LOG_MTIME", "0") or "0")
        now = int(probe.get("NOW", str(int(time.time()))) or "0")
        if log_mtime == 0:
            checks["log"] = "FAIL (no log)"
        else:
            age = now - log_mtime
            if age < LOG_FRESHNESS_SECS:
                checks["log"] = f"OK ({age}s ago)"
            else:
                checks["log"] = f"STALE ({age // 60}m ago)"

    # 7. MCHP maintenance cron
    if not needs_mchp_cron(behavior):
        checks["cron"] = "n/a"
    else:
        cron_count = int(probe.get("CRON_COUNT", "0") or "0")
        if cron_count >= 2:
            checks["cron"] = "OK"
        elif cron_count == 1:
            checks["cron"] = "PARTIAL"
        else:
            checks["cron"] = "MISSING"

    # 8. Behavioral config files present (post-2026-04-16 consolidation)
    # Expected state:
    #   V0/V1 baseline: BC_FILES=0, BC_HAS_BEHAVIOR=0 → n/a
    #   V2+ feedback:   BC_FILES=1, BC_HAS_BEHAVIOR=1 → OK
    #   Anything else is a misconfiguration that was silently OK'd pre-fix.
    bc_files = int(probe.get("BC_FILES", "0") or "0")
    bc_has_behavior = probe.get("BC_HAS_BEHAVIOR", "0") == "1"
    is_v2_plus = any(c.isdigit() and int(c) >= 2 for c in behavior if c.isdigit())
    if behavior in ("C0", "M0"):
        checks["feedback"] = "n/a"
    elif is_v2_plus:
        if bc_files == 1 and bc_has_behavior:
            checks["feedback"] = "OK"
        elif bc_files == 0:
            checks["feedback"] = "FAIL (no configs)"
        elif not bc_has_behavior:
            checks["feedback"] = f"FAIL (no behavior.json, {bc_files} junk files)"
        else:
            checks["feedback"] = f"FAIL (stale: {bc_files} files incl. legacy)"
    else:
        # V0/V1 baseline — should have zero feedback files
        if bc_files == 0:
            checks["feedback"] = "n/a"
        else:
            checks["feedback"] = f"FAIL (baseline has {bc_files} unexpected configs)"

    # 9. Feature warnings from runtime.
    #
    # [WARNING] lines = unexpected; [INFO] ablation-gated lines = intentional
    # (PHASE's ablation engine deliberately omitted sections whose knobs don't
    # move the score on the target model). We count WARNING and INFO
    # separately so an operator can see "this deploy ran clean" vs
    # "this deploy had PHASE-intentional omissions" vs "this deploy has bugs".
    #
    # Baseline deploys: no feedback attempted, so runtime never reaches the
    # warning paths (fc.is_empty() short-circuits). Silence is correct.
    # Feedback deploys: warnings UNEXPECTED, INFOs expected iff ablation-gated.
    warn_count = int(probe.get("WARN_COUNT", "0") or "0")
    info_count = int(probe.get("INFO_COUNT", "0") or "0")
    if behavior in ("C0", "M0"):
        checks["warnings"] = "n/a"
    elif bc_has_behavior:
        if warn_count == 0 and info_count == 0:
            checks["warnings"] = "OK"
        elif warn_count == 0 and info_count > 0:
            checks["warnings"] = f"OK ({info_count} ablation-gated)"
        else:
            checks["warnings"] = f"FAIL ({warn_count} unexpected warnings)"
    else:
        if warn_count == 0:
            checks["warnings"] = "n/a (baseline)"
        else:
            checks["warnings"] = f"FAIL ({warn_count} warnings on baseline — unexpected)"

    # Post-pass: interpret IDLE correctly.
    # Ollama unloads idle models after OLLAMA_KEEP_ALIVE (default 5m).
    # V2+ calibrated agents also sleep ~1h between clusters via inter_cluster
    # timing delays. If service + process are alive, the agent is OK even if
    # the model is currently unloaded — it'll reload on next inference.
    agent_alive = (
        checks.get("service") == "OK"
        and checks.get("process") == "OK"
    )
    for k in ("model", "gpu"):
        if checks.get(k) == "IDLE":
            if agent_alive:
                checks[k] = "OK (idle)"
            else:
                checks[k] = "FAIL (not loaded)"

    return checks


# ── Discovery ────────────────────────────────────────────────────────────

def _discover_deployments(deploy_dir: Path) -> list[dict]:
    """Find all active RUSE SUP deployments. Returns list of {name, run_id, run_dir, vms}."""
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
        # RUSE SUPs only — skip rampart and ghosts
        if cfg.is_rampart() or cfg.is_ghosts():
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
            vms = _parse_inventory(inv)
            if not vms:
                continue
            deployments.append({
                "name": config_dir.name,
                "run_id": run_dir.name,
                "run_dir": run_dir,
                "vms": vms,
            })
    return deployments


def _parse_inventory(inv_path: Path) -> list[dict]:
    """Parse inventory.ini into list of {name, ip, behavior}."""
    vms = []
    for line in inv_path.read_text().splitlines():
        m = re.match(r"^(\S+)\s+ansible_host=(\S+)\s+sup_behavior=(\S+)", line)
        if m:
            vms.append({
                "name": m.group(1),
                "ip": m.group(2),
                "behavior": m.group(3),
            })
    return vms


# ── Main entry point ─────────────────────────────────────────────────────

def run_audit(deploy_dir: Path) -> int:
    """Run full audit. Returns 0 on no failures, 1 otherwise."""
    output.banner("RUSE AUDIT")
    output.info("")

    output.dim("  Discovering deployments...")
    deployments = _discover_deployments(deploy_dir)
    if not deployments:
        output.info("No active RUSE deployments found.")
        return 0

    total_vms = sum(len(d["vms"]) for d in deployments)
    output.info(f"  Found {len(deployments)} deployments, {total_vms} VMs")

    output.dim("  Querying OpenStack...")
    os_client = OpenStack()
    all_os_servers = set(os_client.server_list())

    output.dim("  Loading PHASE experiments.json...")
    exp_data = {}
    if EXPERIMENTS_JSON.exists():
        try:
            exp_data = json.loads(EXPERIMENTS_JSON.read_text())
        except Exception:
            pass

    output.info("")
    output.info(f"  Probing {total_vms} VMs in parallel...")

    # Probe all VMs in parallel
    all_results = []  # list of (deployment, vm, probe, checks)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        futures = {}
        for dep in deployments:
            for vm in dep["vms"]:
                fut = pool.submit(_ssh_probe, vm["name"], vm["ip"], vm["behavior"])
                futures[fut] = (dep, vm)

        done = 0
        for fut in concurrent.futures.as_completed(futures):
            dep, vm = futures[fut]
            try:
                probe = fut.result()
            except Exception as e:
                probe = {"ssh_ok": False, "ssh_error": str(e)[:100]}
            checks = _classify_vm(vm, probe)
            all_results.append((dep, vm, probe, checks))
            done += 1
            ts = time.strftime("%H:%M:%S")
            status = _row_status(checks)
            output.info(f"  [{ts}]  [{done}/{total_vms}]  {status}  {vm['name']}")

    output.info("")

    # Cross-deployment checks
    issues = []

    # Check 10: orphan/missing VMs vs OpenStack
    for dep in deployments:
        inv_names = {vm["name"] for vm in dep["vms"]}
        os_for_dep = {n for n in all_os_servers if n.startswith(_dep_prefix(dep))}
        orphans = os_for_dep - inv_names
        missing = inv_names - os_for_dep
        for vm_name in orphans:
            issues.append(f"{dep['name']}-{dep['run_id']}: ORPHAN on OpenStack: {vm_name}")
        for vm_name in missing:
            issues.append(f"{dep['name']}-{dep['run_id']}: MISSING on OpenStack: {vm_name}")

    # Check 9 & 11: PHASE experiments.json registration
    for dep in deployments:
        entry = exp_data.get(dep["name"], {})
        registered_ips = set(entry.get("ips", {}).keys())
        inv_ips = {vm["ip"] for vm in dep["vms"]}
        for ip in inv_ips - registered_ips:
            issues.append(f"{dep['name']}-{dep['run_id']}: NOT in experiments.json: {ip}")

    # Check 12: duplicate run_ids per config name
    by_name = defaultdict(list)
    for dep in deployments:
        by_name[dep["name"]].append(dep["run_id"])
    for name, run_ids in by_name.items():
        if len(run_ids) > 1:
            issues.append(f"{name}: multiple active runs: {', '.join(run_ids)}")

    # Check 15: orphaned volumes
    orphan_vols = os_client.find_orphaned_volumes(size=200)
    if orphan_vols:
        issues.append(f"ORPHANED VOLUMES: {len(orphan_vols)} nameless 200GB volumes (run ./teardown --all or delete manually)")

    # Check 16: session log warnings from most recent deploy
    session_logs = sorted((deploy_dir / "logs").glob("session-deploy-*.log"), reverse=True)
    if session_logs:
        latest_session = session_logs[0]
        try:
            session_text = latest_session.read_text()
            session_warnings = [l.strip() for l in session_text.splitlines() if "[WARNING]" in l]
            if session_warnings:
                issues.append(f"SESSION LOG WARNINGS ({latest_session.name}): {len(session_warnings)} warnings")
                for w in session_warnings[:10]:
                    issues.append(f"  {w}")
        except OSError:
            pass

    # Per-VM check failures → issues
    for dep, vm, probe, checks in all_results:
        for check_name, status in checks.items():
            if (status != "?"
                and not status.startswith("OK")
                and not status.startswith("n/a")
                and not status.startswith("EXPECTED")):
                issues.append(f"{dep['name']}-{dep['run_id']}/{vm['name']}: {check_name}={status}")
        # Surface warning details from runtime logs
        warn_lines = probe.get("WARN_LINES", "")
        if warn_lines:
            for w in warn_lines.split("|"):
                w = w.strip()
                if w:
                    issues.append(f"{dep['name']}-{dep['run_id']}/{vm['name']}: {w}")

    # Per-deployment summary
    output.info("")
    output.banner("AUDIT SUMMARY")
    output.info("")

    by_dep = defaultdict(list)
    for dep, vm, probe, checks in all_results:
        by_dep[(dep["name"], dep["run_id"])].append((vm, probe, checks))

    headers = ["Deployment", "VMs", "SSH", "Svc", "Proc", "Model", "GPU", "Logs", "Cron", "Fdbk", "Warn"]
    rows = []

    def _ok(v: str) -> bool:
        # "OK", "OK (idle)", "OK (24 GB)", "OK (12s ago)", "n/a", "EXPECTED ..." all count as pass
        return v.startswith("n/a") or v.startswith("OK") or v.startswith("EXPECTED")

    for (name, rid), entries in sorted(by_dep.items()):
        n = len(entries)
        ssh_ok = sum(1 for _, _, c in entries if _ok(c.get("ssh", "")))
        svc_ok = sum(1 for _, _, c in entries if _ok(c.get("service", "")))
        proc_ok = sum(1 for _, _, c in entries if _ok(c.get("process", "")))
        model_ok = sum(1 for _, _, c in entries if _ok(c.get("model", "")))
        gpu_ok = sum(1 for _, _, c in entries if _ok(c.get("gpu", "")))
        log_ok = sum(1 for _, _, c in entries if _ok(c.get("log", "")))
        cron_ok = sum(1 for _, _, c in entries if _ok(c.get("cron", "")))
        fdbk_ok = sum(1 for _, _, c in entries if _ok(c.get("feedback", "")))
        warn_ok = sum(1 for _, _, c in entries if _ok(c.get("warnings", "")))
        rows.append([
            f"{name}-{rid}", str(n),
            f"{ssh_ok}/{n}", f"{svc_ok}/{n}", f"{proc_ok}/{n}",
            f"{model_ok}/{n}", f"{gpu_ok}/{n}", f"{log_ok}/{n}", f"{cron_ok}/{n}",
            f"{fdbk_ok}/{n}", f"{warn_ok}/{n}",
        ])
    output.table(headers, rows)
    output.info("")

    if issues:
        output.info(f"ISSUES ({len(issues)}):")
        for i in issues:
            output.info(f"  • {i}")
        output.info("")
    else:
        output.info("No issues found.")
        output.info("")

    # Write markdown report
    log_dir = deploy_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    report_path = log_dir / f"audit_{time.strftime('%Y%m%d-%H%M%S')}.md"
    _write_markdown(report_path, deployments, all_results, issues, by_dep)
    output.info(f"  Full report: {report_path}")

    return 1 if issues else 0


def _row_status(checks: dict) -> str:
    """Compact one-char-per-check status string for terminal."""
    parts = []
    for k in ("ssh", "service", "process", "model", "gpu", "log", "cron", "feedback", "warnings"):
        v = checks.get(k, "?")
        if v.startswith("OK") or v.startswith("n/a") or v.startswith("EXPECTED"):
            parts.append(".")
        elif v == "?":
            parts.append("?")
        elif v.startswith("WARN"):
            parts.append("W")
        else:
            parts.append("X")
    return "".join(parts)


def _dep_prefix(dep: dict) -> str:
    """Build the OpenStack VM prefix for a deployment (r-{dep_id}-)."""
    name = dep["name"]
    for p in ("ruse-", "sup-"):
        if name.startswith(p):
            name = name[len(p):]
    return f"r-{name.replace('-', '')}{dep['run_id']}-"


# ── Markdown report ──────────────────────────────────────────────────────

def _write_markdown(
    path: Path,
    deployments: list[dict],
    all_results: list[tuple],
    issues: list[str],
    by_dep: dict,
) -> None:
    lines = []
    lines.append(f"# RUSE Audit Report")
    lines.append("")
    lines.append(f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    lines.append(f"**Deployments scanned:** {len(deployments)}")
    lines.append(f"**Total VMs:** {sum(len(d['vms']) for d in deployments)}")
    lines.append(f"**Issues found:** {len(issues)}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Deployment | VMs | SSH | Service | Process | Model | GPU | Logs | Cron | Feedback | Warnings |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")

    def _ok(v: str) -> bool:
        return v.startswith("n/a") or v.startswith("OK")

    for (name, rid), entries in sorted(by_dep.items()):
        n = len(entries)
        ssh_ok = sum(1 for _, _, c in entries if _ok(c.get("ssh", "")))
        svc_ok = sum(1 for _, _, c in entries if _ok(c.get("service", "")))
        proc_ok = sum(1 for _, _, c in entries if _ok(c.get("process", "")))
        model_ok = sum(1 for _, _, c in entries if _ok(c.get("model", "")))
        gpu_ok = sum(1 for _, _, c in entries if _ok(c.get("gpu", "")))
        log_ok = sum(1 for _, _, c in entries if _ok(c.get("log", "")))
        cron_ok = sum(1 for _, _, c in entries if _ok(c.get("cron", "")))
        fdbk_ok = sum(1 for _, _, c in entries if _ok(c.get("feedback", "")))
        warn_ok = sum(1 for _, _, c in entries if _ok(c.get("warnings", "")))
        lines.append(
            f"| `{name}-{rid}` | {n} | {ssh_ok}/{n} | {svc_ok}/{n} | {proc_ok}/{n} | "
            f"{model_ok}/{n} | {gpu_ok}/{n} | {log_ok}/{n} | {cron_ok}/{n} | "
            f"{fdbk_ok}/{n} | {warn_ok}/{n} |"
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
        lines.append(f"### `{name}-{rid}`")
        lines.append("")
        lines.append("| VM | Behavior | IP | SSH | Service | Process | Model | GPU | Logs | Cron | Feedback | Warnings |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for vm, probe, checks in sorted(entries, key=lambda e: e[0]["name"]):
            lines.append(
                f"| `{vm['name']}` | {vm['behavior']} | {vm['ip']} | "
                f"{checks.get('ssh', '?')} | {checks.get('service', '?')} | "
                f"{checks.get('process', '?')} | {checks.get('model', '?')} | "
                f"{checks.get('gpu', '?')} | {checks.get('log', '?')} | "
                f"{checks.get('cron', '?')} | {checks.get('feedback', '?')} | "
                f"{checks.get('warnings', '?')} |"
            )
        lines.append("")

    lines.append("## Legend")
    lines.append("")
    lines.append("- **OK** — check passed")
    lines.append("- **n/a** — check doesn't apply (e.g. C0 has no service)")
    lines.append("- **FAIL / WRONG / STALE / MISSING** — investigate")
    lines.append("- **?** — could not determine (usually SSH failed)")
    lines.append("")
    lines.append("Compact terminal status: each VM gets 9 chars (ssh/service/process/model/gpu/log/cron/feedback/warnings). "
                 "`.` = pass, `X` = fail, `W` = warnings present, `?` = unknown.")

    path.write_text("\n".join(lines))
