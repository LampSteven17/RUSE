"""RAMPART audit — health probe across all active RAMPART deployments.

Walks `deployments/rampart-*/runs/<latest>/`, parses each deploy's
`enterprise-config-prefixed.json` (canonical VM cohort + roles) and
`deploy-output.json` (per-VM IP + Administrator password), then probes
every endpoint in parallel:

  Linux endpoints (`ssh -i ~/.ssh/id_rsa ubuntu@<ip>`):
    - `systemctl is-active rampart-human` + NRestarts (crash-loop catch)
    - journalctl `[hour-gate]` line count (UTC hour-gate wiring)
    - ExecStart has --clustersize-sigma + --activity-daily-min-hours
      (D5 sigma + activity-window flags actually on the wire)
    - `realm list` (domain join)

  Windows endpoints + DCs (sshpass + Administrator@<domain>@<ip>,
  PreferredAuthentications=password, PubkeyAuthentication=no):
    - `(Get-ScheduledTask RampartHuman).State` (endpoints)
    - `Get-Service NTDS, ADWS` + `Get-ADDomain.DNSRoot` (DCs)

Cross-deployment:
  - OpenStack VM cohort vs canonical (orphan + missing)
  - experiments.json registration (entry exists, has start_date, no
    stale end_date, IPs overlap canonical)
  - DNS zone exists and matches dns_zone.txt

Outputs: terminal table + markdown report at
`deployments/logs/audit_rampart_*.md`. Exit 0 if no issues; 1 otherwise.
"""

from __future__ import annotations

import base64
import concurrent.futures
import json
import os
import re
import subprocess
import time
from pathlib import Path

from ..core import output
from ..core.openstack import OpenStack
from ..core.teardown_steps import EXPERIMENTS_JSON


_SSH_KEY = str(Path.home() / ".ssh" / "id_rsa")
_SSH_TIMEOUT_S = 25


def _is_dc(roles: list) -> bool:
    return any(r in roles for r in ("domain_controller", "domain_controller_leader"))


def _is_windows(roles: list) -> bool:
    return "windows" in roles


def _discover(deploy_dir: Path) -> list[dict]:
    """Walk deployments/rampart-*/runs/ and return one entry per latest run.

    Returns: [{name, run_id, run_dir, zone, vm_prefix, domain_fqdn, vms}]
    where vms = [{name, ip, roles, is_dc, is_windows, user, admin_pass}].
    """
    out = []
    for cdir in sorted(deploy_dir.iterdir()):
        if not cdir.is_dir() or not cdir.name.startswith("rampart"):
            continue
        runs = cdir / "runs"
        if not runs.is_dir():
            continue
        # Most recent run only
        run_dirs = sorted(
            [r for r in runs.iterdir() if r.is_dir()],
            key=lambda p: p.name, reverse=True,
        )
        if not run_dirs:
            continue
        run_dir = run_dirs[0]

        ent_path = run_dir / "enterprise-config-prefixed.json"
        out_path = run_dir / "deploy-output.json"
        if not ent_path.exists() or not out_path.exists():
            output.dim(f"  skip {cdir.name}/{run_dir.name}: missing config/output")
            continue
        try:
            ent = json.loads(ent_path.read_text())
            dout = json.loads(out_path.read_text())
        except Exception as e:
            output.error(f"  skip {cdir.name}/{run_dir.name}: parse error: {e}")
            continue

        # name -> {ip, password} from deploy-output.json
        ip_map = {}
        for node in dout.get("enterprise_built", {}).get("deployed", {}).get("nodes", []):
            name = node.get("name")
            ip = None
            for a in node.get("addresses", []):
                if isinstance(a, dict) and a.get("addr"):
                    ip = a["addr"]
                    break
            if name and ip:
                ip_map[name] = {"ip": ip, "password": node.get("password")}

        # Canonical VM list from enterprise-config
        vms = []
        for node in ent.get("nodes", []):
            roles = node.get("roles", []) or []
            name = node.get("name")
            info = ip_map.get(name, {})
            vms.append({
                "name": name,
                "ip": info.get("ip"),
                "roles": roles,
                "is_dc": _is_dc(roles),
                "is_windows": _is_windows(roles) or _is_dc(roles),
                "user": node.get("user"),
                "admin_pass": info.get("password"),
            })

        zone = ""
        zone_marker = run_dir / "dns_zone.txt"
        if zone_marker.exists():
            zone = zone_marker.read_text().strip()

        # vm_prefix = "r-{hash}-" — derived from a VM name
        vm_prefix = ""
        for v in vms:
            if v["name"]:
                m = re.match(r"(r-[a-f0-9]+-)", v["name"])
                if m:
                    vm_prefix = m.group(1)
                    break

        # Domain FQDN for sshpass user: castle.{hash}.{project}.os
        domain_fqdn = ""
        if zone and vm_prefix:
            hash_part = vm_prefix[2:-1]
            domain_fqdn = f"castle.{zone}"
            if not zone.startswith(hash_part):
                domain_fqdn = f"castle.{hash_part}.vxn3kr-bot-project.os"

        # Domain admin password = forest leader's cloud-init password. Once
        # follower DCs and endpoint Windows VMs join the domain, their own
        # cloud-init passwords stop working for the `Administrator@<fqdn>@<ip>`
        # UPN login path — only the leader's password authenticates against
        # the now-promoted domain admin account.
        domain_admin_pass = ""
        for v in vms:
            if "domain_controller_leader" in v["roles"] and v["admin_pass"]:
                domain_admin_pass = v["admin_pass"]
                break

        out.append({
            "name": cdir.name,
            "run_id": run_dir.name,
            "run_dir": run_dir,
            "zone": zone,
            "vm_prefix": vm_prefix,
            "domain_fqdn": domain_fqdn,
            "domain_admin_pass": domain_admin_pass,
            "vms": vms,
        })
    return out


def _ssh_linux(ip: str) -> dict:
    """Probe a Linux endpoint. Single round trip, returns parsed key=value."""
    bash = (
        "ACTIVE=$(systemctl is-active rampart-human 2>/dev/null || echo notfound); "
        'echo "ACTIVE=$ACTIVE"; '
        'echo "NRESTARTS=$(systemctl show rampart-human -p NRestarts --value 2>/dev/null || echo 0)"; '
        "HG=$(journalctl -u rampart-human --no-pager 2>/dev/null | grep -c '\\[hour-gate\\]'); "
        'echo "HOUR_GATE_LINES=$HG"; '
        "EXEC=$(systemctl cat rampart-human 2>/dev/null | grep -E '^ExecStart=' | head -1); "
        'echo "HAS_SIGMA=$(echo "$EXEC" | grep -cE -- \'--clustersize-sigma|--taskinterval-sigma\')"; '
        'echo "HAS_ACTIVITY=$(echo "$EXEC" | grep -cE -- \'--activity-daily-min-hours\')"; '
        'echo "REALM=$(realm list 2>/dev/null | head -1 || echo none)"'
    )
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        "-o", "IdentitiesOnly=yes",
        "-i", _SSH_KEY,
        f"ubuntu@{ip}",
        bash,
    ]
    try:
        env = {**os.environ, "SSH_AUTH_SOCK": ""}
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=_SSH_TIMEOUT_S, env=env)
        if r.returncode != 0:
            return {"ssh_ok": False, "err": (r.stderr or r.stdout or "").strip()[:120]}
        kv = {"ssh_ok": True}
        for line in r.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                kv[k.strip()] = v.strip()
        return kv
    except subprocess.TimeoutExpired:
        return {"ssh_ok": False, "err": "timeout"}
    except Exception as e:
        return {"ssh_ok": False, "err": str(e)[:120]}


def _ssh_windows(ip: str, domain_fqdn: str, admin_pass: str, is_dc: bool) -> dict:
    """Probe a Windows VM via sshpass + password auth.

    Uses PowerShell -EncodedCommand (UTF-16 LE base64) to sidestep all
    quoting issues between bash → cmd → powershell.
    """
    if not admin_pass:
        return {"ssh_ok": False, "err": "no admin password in deploy-output.json"}
    if not domain_fqdn:
        return {"ssh_ok": False, "err": "no domain_fqdn"}

    ps_lines = [
        "$ErrorActionPreference='SilentlyContinue'",
        "$t = Get-ScheduledTask -TaskName RampartHuman",
        'Write-Output ("TASK_STATE=" + $t.State)',
    ]
    if is_dc:
        ps_lines += [
            "$ntds = Get-Service NTDS",
            'Write-Output ("NTDS=" + $ntds.Status)',
            "$adws = Get-Service ADWS",
            'Write-Output ("ADWS=" + $adws.Status)',
            "$d = Get-ADDomain",
            'Write-Output ("DOMAIN=" + $d.DNSRoot)',
        ]
    ps_script = "; ".join(ps_lines)
    encoded = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")

    cmd = [
        "sshpass", "-p", admin_pass,
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "PreferredAuthentications=password",
        "-o", "PubkeyAuthentication=no",
        "-o", "ConnectTimeout=15",
        f"Administrator@{domain_fqdn}@{ip}",
        f"powershell -EncodedCommand {encoded}",
    ]
    try:
        env = {**os.environ, "SSH_AUTH_SOCK": ""}
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=_SSH_TIMEOUT_S + 10, env=env)
        if r.returncode != 0:
            return {"ssh_ok": False, "err": (r.stderr or r.stdout or "").strip()[:120]}
        kv = {"ssh_ok": True}
        for line in r.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                kv[k.strip()] = v.strip()
        return kv
    except subprocess.TimeoutExpired:
        return {"ssh_ok": False, "err": "timeout"}
    except Exception as e:
        return {"ssh_ok": False, "err": str(e)[:120]}


def _classify(vm: dict, probe: dict) -> dict:
    """Apply per-VM pass/fail checks. Returns {check_name: bool}.

    Skips checks that aren't relevant for the VM role (e.g. AD service
    checks on endpoints; pyhuman checks on user=None nodes).
    """
    checks = {"ssh": bool(probe.get("ssh_ok"))}
    if not checks["ssh"]:
        return checks

    if vm["is_dc"]:
        checks["ntds"] = probe.get("NTDS") == "Running"
        checks["adws"] = probe.get("ADWS") == "Running"
        checks["domain_resolves"] = bool((probe.get("DOMAIN") or "").strip())
        return checks

    if vm["user"] is None:
        # linep1 shared, no user, no emulation. SSH-up is enough.
        return checks

    # Endpoint with user — pyhuman expected
    if vm["is_windows"]:
        state = (probe.get("TASK_STATE") or "").strip()
        checks["pyhuman"] = state in ("Ready", "Running")
    else:
        active = (probe.get("ACTIVE") or "").strip()
        try:
            nrestarts = int(probe.get("NRESTARTS") or "0")
        except ValueError:
            nrestarts = 9999
        checks["pyhuman"] = active == "active" and nrestarts <= 10
        # Hour-gate sanity (Linux only — Windows scheduled task path doesn't
        # emit journalctl entries we can grep for cheaply).
        try:
            hg = int(probe.get("HOUR_GATE_LINES") or "0")
        except ValueError:
            hg = 0
        checks["hour_gate"] = hg > 0
        # D5 sigma + activity flags on the wire
        checks["sigma_wired"] = probe.get("HAS_SIGMA") == "1"
        checks["activity_wired"] = probe.get("HAS_ACTIVITY") == "1"
        checks["realm_joined"] = "castle" in (probe.get("REALM") or "")
    return checks


def _row_status(vm: dict, checks: dict) -> str:
    """One-line per-VM status string for the running probe output."""
    if not checks.get("ssh"):
        return "X.........."
    if vm["is_dc"]:
        return (
            ("." if checks.get("ntds") else "X")
            + ("." if checks.get("adws") else "X")
            + ("." if checks.get("domain_resolves") else "X")
            + "        "
        )
    if vm["user"] is None:
        return ".          "  # SSH OK, no other checks expected
    cells = "." if checks.get("pyhuman") else "X"
    if vm["is_windows"]:
        return cells + "         "
    cells += "." if checks.get("hour_gate") else "X"
    cells += "." if checks.get("sigma_wired") else "X"
    cells += "." if checks.get("activity_wired") else "X"
    cells += "." if checks.get("realm_joined") else "X"
    return cells + "     "


def _cross_check(
    deploys: list[dict], os_servers: set[str],
    exp_data: dict, zones: dict,
) -> list[str]:
    """Cross-deployment consistency. Returns list of issue strings."""
    issues = []
    for dep in deploys:
        prefix = dep["vm_prefix"]
        canonical = {v["name"] for v in dep["vms"] if v["name"]}
        os_for_dep = {n for n in os_servers if n.startswith(prefix)}

        for missing in sorted(canonical - os_for_dep):
            issues.append(f"{dep['name']}/{dep['run_id']}: MISSING on OpenStack: {missing}")
        for orphan in sorted(os_for_dep - canonical):
            issues.append(f"{dep['name']}/{dep['run_id']}: ORPHAN on OpenStack: {orphan}")

        # experiments.json
        e = exp_data.get(dep["name"])
        if not e:
            issues.append(f"{dep['name']}: NOT in experiments.json (PHASE won't dredge logs)")
        elif not isinstance(e, dict):
            issues.append(f"{dep['name']}: experiments.json entry is not a dict")
        else:
            if e.get("end_date"):
                issues.append(
                    f"{dep['name']}: experiments.json end_date={e['end_date']} "
                    f"despite active deploy (deploy didn't re-register)"
                )
            if not e.get("start_date"):
                issues.append(
                    f"{dep['name']}: experiments.json missing start_date "
                    f"(PHASE will dredge unscoped Zeek logs)"
                )
            e_hosts = set((e.get("ips") or {}).values())
            if canonical and not (e_hosts & canonical):
                issues.append(
                    f"{dep['name']}: experiments.json IPs don't match active VMs (stale entry)"
                )
            # PHASE 4.2-rampart SKILL.md A6: baseline_user_roles must be a
            # resolvable file. Missing or unresolvable → PHASE's
            # rampart_generator._baseline_path raises FileNotFoundError when
            # feedback generation runs.
            bur = e.get("baseline_user_roles")
            if not bur:
                issues.append(
                    f"{dep['name']}: experiments.json missing baseline_user_roles "
                    f"(PHASE feedback_engine will abort)"
                )
            elif not Path(bur).expanduser().exists():
                issues.append(
                    f"{dep['name']}: baseline_user_roles does not resolve: {bur}"
                )

        # DNS zone
        if dep["zone"]:
            if dep["zone"] not in zones and (dep["zone"] + ".") not in zones:
                issues.append(f"{dep['name']}/{dep['run_id']}: DNS zone missing: {dep['zone']}")
    return issues


def _write_markdown(
    path: Path, deploys: list[dict],
    results: list[tuple], issues: list[str],
) -> None:
    lines = [
        "# RAMPART Audit Report", "",
        f"_Generated: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}_", "",
        f"{len(deploys)} active deployment(s), {len(results)} VMs probed.", "",
    ]

    by_dep: dict[str, list] = {}
    for dep, vm, probe, c in results:
        by_dep.setdefault(f"{dep['name']}/{dep['run_id']}", []).append((vm, probe, c))

    for key in sorted(by_dep):
        lines.append(f"## {key}")
        lines.append("")
        lines.append("| VM | role | SSH | pyhuman | hour-gate | sigma | realm | NTDS | ADWS | err |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for vm, probe, c in sorted(by_dep[key], key=lambda x: x[0]["name"] or ""):
            role = "DC" if vm["is_dc"] else ("win" if vm["is_windows"] else "lin")
            cell = lambda v, applicable: ("✓" if v else "✗") if applicable else "—"
            row = [
                vm["name"] or "?",
                role,
                cell(c.get("ssh"), True),
                cell(c.get("pyhuman"), bool(vm.get("user"))),
                cell(c.get("hour_gate"),
                     bool(vm.get("user")) and not vm["is_windows"]),
                cell(c.get("sigma_wired"),
                     bool(vm.get("user")) and not vm["is_windows"]),
                cell(c.get("realm_joined"),
                     bool(vm.get("user")) and not vm["is_windows"]),
                cell(c.get("ntds"), vm["is_dc"]),
                cell(c.get("adws"), vm["is_dc"]),
                (probe.get("err") or "")[:60],
            ]
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    if issues:
        lines.append("## Cross-deployment issues")
        lines.append("")
        for issue in issues:
            lines.append(f"- {issue}")
        lines.append("")
    else:
        lines.append("## Cross-deployment issues")
        lines.append("")
        lines.append("_none_")
        lines.append("")

    path.write_text("\n".join(lines))


def run_rampart_audit(deploy_dir: Path) -> int:
    """RAMPART audit entry point. Returns 0 on clean, 1 if any issue found."""
    output.banner("RAMPART AUDIT")
    output.info("")

    output.dim("  Discovering deployments...")
    deploys = _discover(deploy_dir)
    if not deploys:
        output.info("No active RAMPART deployments found.")
        return 0

    total_vms = sum(len(d["vms"]) for d in deploys)
    output.info(f"  Found {len(deploys)} deployments, {total_vms} VMs")

    output.dim("  Querying OpenStack...")
    os_client = OpenStack()
    os_servers = set(os_client.server_list())
    zones = {z.get("name", "").rstrip("."): z for z in os_client.zone_list()}

    output.dim("  Loading experiments.json...")
    exp_data: dict = {}
    if EXPERIMENTS_JSON.exists():
        try:
            exp_data = json.loads(EXPERIMENTS_JSON.read_text())
        except Exception:
            pass

    output.info("")
    output.info(f"  Probing {total_vms} VMs in parallel (max 20 workers)...")
    output.info("  Legend (Linux endpoint):  P=pyhuman H=hour-gate S=sigma A=activity R=realm")
    output.info("  Legend (DC):              N=NTDS  A=ADWS  D=domain-resolves")
    output.info("")

    results: list[tuple] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        futs = {}
        for dep in deploys:
            for vm in dep["vms"]:
                if not vm["ip"]:
                    continue
                if vm["is_windows"]:
                    # All Win VMs (DCs + endpoints) auth against domain admin,
                    # which is the FOREST LEADER's cloud-init pw — not the
                    # per-VM cloud-init pw (which stops working post-join).
                    fut = pool.submit(
                        _ssh_windows, vm["ip"], dep["domain_fqdn"],
                        dep["domain_admin_pass"], vm["is_dc"],
                    )
                else:
                    fut = pool.submit(_ssh_linux, vm["ip"])
                futs[fut] = (dep, vm)

        done = 0
        for fut in concurrent.futures.as_completed(futs):
            dep, vm = futs[fut]
            try:
                probe = fut.result()
            except Exception as e:
                probe = {"ssh_ok": False, "err": str(e)[:120]}
            c = _classify(vm, probe)
            results.append((dep, vm, probe, c))
            done += 1
            ts = time.strftime("%H:%M:%S")
            output.info(f"  [{ts}]  [{done}/{total_vms}]  {_row_status(vm, c)}  {vm['name']}")

    output.info("")

    issues = _cross_check(deploys, os_servers, exp_data, zones)

    # Summary
    output.banner("SUMMARY")
    by_dep: dict[tuple, list] = {}
    for dep, vm, probe, c in results:
        by_dep.setdefault((dep["name"], dep["run_id"]), []).append((vm, c))

    per_vm_fails = 0
    for (name, run_id), vrs in sorted(by_dep.items()):
        ssh_ok = sum(1 for _, c in vrs if c.get("ssh"))
        n = len(vrs)
        bad = [(v, c) for v, c in vrs if not all(c.values())]
        per_vm_fails += len(bad)
        marker = "OK" if not bad else f"{len(bad)} VM issue(s)"
        output.info(f"  {name}/{run_id}: {ssh_ok}/{n} SSH OK — {marker}")

    if issues:
        output.info("")
        output.error(f"Cross-deployment issues: {len(issues)}")
        for issue in issues[:40]:
            output.error(f"  {issue}")
        if len(issues) > 40:
            output.error(f"  ... +{len(issues) - 40} more (see markdown)")

    logs_dir = deploy_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    md_path = logs_dir / f"audit_rampart_{time.strftime('%Y%m%d-%H%M%S')}.md"
    _write_markdown(md_path, deploys, results, issues)
    output.info("")
    output.info(f"  Report: {md_path}")

    return 1 if (per_vm_fails > 0 or issues) else 0
