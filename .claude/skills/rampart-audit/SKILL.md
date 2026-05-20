---
name: rampart-audit
description: RAMPART audit — `./audit --rampart` runs per-VM SSH probes across every active RAMPART deployment (Linux: id_rsa + systemctl is-active + NRestarts + journalctl hour-gate + ExecStart sigma/activity wiring + realm list; Windows + DCs: sshpass with forest-leader admin pw + Get-ScheduledTask state, Get-Service NTDS/ADWS, Get-ADDomain.DNSRoot). Plus cross-deployment OpenStack cohort diff, experiments.json registration (start_date sanity), and DNS zone presence. Code in `deployment_engine/rampart/audit.py`. Outputs terminal table + markdown report at `deployments/logs/audit_rampart_*.md`.
---

# rampart-audit

`./audit --rampart` runs health probes across every active RAMPART
deployment and reports per-VM + cross-deployment status. Replaced the
stub on 2026-05-12 alongside the start_date contract fix.

| | |
|---|---|
| Entry point | `./audit --rampart` at RUSE root |
| Code | `deployment_engine/rampart/audit.py::run_rampart_audit` |
| Outputs | terminal table + `deployments/logs/audit_rampart_*.md` |
| Exit code | 0 clean; 1 if any per-VM check fails or cross-deployment issue |

## What it probes

Walks `deployments/rampart-*/runs/<latest>/` for every config dir
starting with `rampart`. For each deploy, parses
`enterprise-config-prefixed.json` (canonical VM list + roles + user
slot) and `deploy-output.json` (per-VM IPs + cloud-init admin
passwords). Builds per-VM probe jobs and fans out via
`concurrent.futures.ThreadPoolExecutor(max_workers=20)`.

### Linux endpoints (`ssh -i ~/.ssh/id_rsa ubuntu@<ip>`)

Single round-trip bash collects:

- `systemctl is-active rampart-human` (expected `active`)
- `systemctl show -p NRestarts` (≤ 10; crash-loop catch — this is the
  D5 arg-mismatch failure mode that previously masked 2000+ restarts
  as "healthy")
- `journalctl -u rampart-human | grep -c '[hour-gate]'` (UTC hour-gate
  wiring proof; > 0 means pyhuman is logging its per-day active window)
- ExecStart grep for `--clustersize-sigma|--taskinterval-sigma` (D5
  sigma actually on the wire)
- ExecStart grep for `--activity-daily-min-hours` (activity-window
  flags actually on the wire — see 2026-05-11 incident where Ansible's
  INI safe_eval converted the CSV string to a tuple, breaking pyhuman)
- `realm list` (domain join still intact)

### Windows VMs (DCs + endpoints; `sshpass` + UPN auth)

```
sshpass -p '<forest_leader_admin_pass>' ssh \
    -o PreferredAuthentications=password -o PubkeyAuthentication=no \
    Administrator@castle.{hash}.{project}.os@<ip> \
    'powershell -EncodedCommand <utf16-base64>'
```

Critical: the audit uses the **forest leader's** (`dc1`) cloud-init
password for ALL Windows VMs in the deploy, not per-VM cloud-init
passwords. Once endpoint Win VMs and follower DCs join the domain,
their own cloud-init passwords stop authenticating against the now-
promoted domain Administrator UPN — only dc1's password works.
Initial implementation used per-VM passwords and got 12/23 SSH
failures per deploy; corrected after first audit run.

PowerShell payload uses `-EncodedCommand` (UTF-16 LE base64) to
sidestep bash → cmd.exe → PowerShell quoting hell. Collects:

- `(Get-ScheduledTask -TaskName RampartHuman).State` (expected
  `Ready` or `Running`)
- DCs only: `Get-Service NTDS` + `Get-Service ADWS` (both `Running`),
  `(Get-ADDomain).DNSRoot` (non-empty)

### Cross-deployment

- OpenStack server list vs canonical `enterprise-config-prefixed.json`
  → MISSING (canonical but absent on OpenStack) + ORPHAN (live on
  OpenStack but not in config)
- `experiments.json` per-deploy entry:
  - exists (not registered → PHASE won't dredge logs)
  - `start_date` populated (null → PHASE.py dredges ALL eno2 history;
    triggered disk-fill 2026-05-12 against rampart-controls)
  - `end_date` is null on active deploy (set → stale entry from a
    prior teardown that didn't get cleared on re-register)
  - `ips` field overlaps canonical VMs (no overlap → stale entry from
    a prior deploy)
  - `baseline_user_roles` populated and resolves (missing → PHASE
    `feedback_engine.rampart_generator` raises `FileNotFoundError`
    when feedback generation runs; RAMPART-only field per PHASE
    4.2-rampart SKILL.md A6). Default canonical path:
    `~/uva-cs-workflow/user-roles/user-roles.json`.
- DNS zone in `run_dir/dns_zone.txt` exists in Designate

## Output

Terminal table — one line per VM with a 5-char status string:

```
. = pass   X = fail   - = N/A for this role
```

Per-row legend depends on role:
- Linux endpoint with user: `P H S A R` (pyhuman / hour-gate / sigma /
  activity / realm)
- DC: `N A D` (NTDS / ADWS / domain-resolves)
- linep1 shared (no user): only SSH probed

Markdown report tabulates each deploy's VMs and lists cross-deployment
issues. Written to `deployments/logs/audit_rampart_<ts>.md`.

## Skipped checks (out of scope)

The following were in the original TODO list but deferred for being
either marginal-value or expensive to probe:

- `Get-EventLog 'Directory Service'` (noisy; healthy DCs have lots of
  benign errors)
- `repadmin /replsummary` (only matters if NTDS already says it's not
  running — caught by the NTDS check)
- Moodle / Shibboleth reachability (no Moodle nodes in enterprise-med
  topology; would only matter if the topology grows)
- `logins.json` start_date tz-aware UTC sanity (the file is dead-code
  in production per /rampart-deploy; only `emulate-logins.py` reads
  it, and `_start_emulation` is unused)
- Workflow availability intersection (`browse_iis browse_shibboleth
  browse_web browse_youtube build_software download_files
  google_search moodle spawn_shell`) — PHASE always emits valid
  workflow sets, hasn't bit us yet

Easy to add back if a future incident makes them load-bearing.

## Related

- Deploy lifecycle: `/rampart-deploy`
- DECOY audit: `/decoy-audit`
- GHOSTS audit: `/ghosts-audit`
