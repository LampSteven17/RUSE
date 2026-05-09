---
name: rampart-audit
description: RAMPART audit — `./audit --rampart`. STUB only at the moment. Routes to `deployment_engine/rampart/audit.py::run_rampart_audit` which prints a TODO list and returns rc=1. RAMPART check semantics differ enough from DECOY (AD health on dc1-3, pyhuman scheduled-task state across Win+Linux endpoints, DNS zone freshness, Moodle reachability, simulate-logins seed sanity) that the DECOY probe doesn't transfer. Skill exists so the dispatch shape is symmetric with /decoy-audit and /ghosts-audit; see /rampart-deploy for what an implementation would need to probe. Implementation deferred until we have a deploy lifecycle stable enough to need it.
---

# rampart-audit

`./audit --rampart` — currently a placeholder. Returns rc=1 with a TODO
list of what a real implementation would check.

| | |
|---|---|
| Entry point | `./audit --rampart` at RUSE root |
| Code | `deployment_engine/rampart/audit.py::run_rampart_audit` |
| Status | **stub only** — exits 1 with `RAMPART audit not yet implemented` |

## Why no implementation yet

DECOY's audit logic doesn't transfer cleanly. DECOY checks:

- Per-behavior systemd service (e.g., `b0_gemma.service`)
- Ollama-loaded model + V100 VRAM
- jsonl log freshness
- behavior.json window-mode contract

None of those map to RAMPART's mix of:

- AD forest health (NTDS, ADWS, DNS replication across dc1/dc2/dc3)
- pyhuman delivery: systemd `rampart-human.service` on Linux endpoints
  vs. scheduled task `RampartHuman` on Windows endpoints
- DNS zone freshness scoped to `r-{md5(dep_id)[:5]}-` per-deploy isolation
- Moodle / Shibboleth reachability
- `simulate-logins.py` `logins.json` seed sanity / hour-gate fields
- pyhuman `--clustersize-sigma` / `--taskinterval-sigma` per-host arg
  presence (D5 sigma flow)
- per-node `user-roles.json` `_phase_metadata.mode ∈ {feedback,controls}`
  — same FATAL gate as DECOY's window-mode contract

Implementing for real means a separate probe shape (`sshpass` for
Windows, plus `Get-ScheduledTask` PowerShell on Win, plus AD-aware
health queries) that doesn't reuse much of `decoy/audit.py`.

## What the implementation would check (TODO)

Per-VM (via SSH probe; sshpass + PubkeyAuthentication=no for Windows):

1. SSH reachable (Linux: id_rsa; Windows: sshpass + admin pass)
2. Domain join: `realm list` on Linux endpoints; `(Get-ADDomain).DNSRoot`
   on Windows endpoints
3. pyhuman delivery:
   - Linux: `systemctl is-active rampart-human` + NRestarts ≤ 10
   - Windows: `(Get-ScheduledTask -TaskName RampartHuman).State == Ready/Running`
4. AD service health on dc1/dc2/dc3:
   - `Get-Service NTDS, ADWS` State == Running
   - `Get-EventLog -LogName 'Directory Service' -EntryType Error -Newest 5`
   - DNS replication: `repadmin /replsummary`
5. Hour-gating wiring on endpoints:
   - Linux: `journalctl -u rampart-human | grep '\[hour-gate\]'` last 24h
   - Windows: read scheduled-task action for `--day-start-hour-min`/etc args
6. user-roles.json mode contract:
   - Pull `enterprise-config-feedback.json` from run_dir, walk per-node
     roles, for each non-null user node verify `_phase_metadata.mode ∈
     {feedback, controls}`. Anything else → FATAL.
7. logins.json seed: parse run_dir's `logins.json`, verify
   `start_date` is tz-aware UTC and within `duration_days` of today.
8. Workflow availability: each role's `workflows` array intersected
   with the available pyhuman workflow set
   (`browse_iis browse_shibboleth browse_web browse_youtube
   build_software download_files google_search moodle spawn_shell`).

Cross-deployment:

- Orphan / missing diff: `r-{md5(dep_id)[:5]}-` prefix on OpenStack vs.
  `enterprise-config-prefixed.json::nodes[]`
- PHASE registration in `experiments.json`
- DNS zone present + matches `dns_zone.txt` per run
- D5 sigma values (`clustersize_sigma` / `taskinterval_sigma`) actually
  on the wire — grep ExecStart line in
  `/etc/systemd/system/rampart-human.service`
- pyhuman + workflows.zip version skew (the
  `workflows.zip` was mentioned in /rampart-deploy as needing to roll
  with playbook changes)

## Until then

To eyeball RAMPART health, see the manual recipes in `/rampart-deploy`:

```bash
# Linux endpoints
for ip in <linep_ips>; do
  ssh -i ~/.ssh/id_rsa ubuntu@$ip "systemctl is-active rampart-human"
done

# Windows endpoints
for ip in <winep_ips>; do
  SSH_AUTH_SOCK="" sshpass -p '<admin_pass>' ssh \
    -o PreferredAuthentications=password -o PubkeyAuthentication=no \
    Administrator@castle.{hash}.{project}.os@$ip \
    "powershell -Command (Get-ScheduledTask -TaskName RampartHuman).State"
done
```

## Related

- Deploy lifecycle: `/rampart-deploy`
- DECOY audit (real implementation): `/decoy-audit`
- GHOSTS audit (also stub): `/ghosts-audit`
