---
name: ghosts-audit
description: GHOSTS audit — `./audit --ghosts` runs role-aware SSH probes (API VM Docker-stack health + /api/machines distinct-name registration; NPC ghosts-client systemd state, NRestarts, RSS-vs-cap, timeline.json runtime sanity + Id-field check) across every active GHOSTS deployment, plus cross-deployment OpenStack/PHASE/run-id consistency. _phase_metadata.mode FATAL gate is checked LOCALLY against the run_dir copy of the timeline (the .NET client rewrites the on-VM file at startup, stripping _phase_metadata). Mode-aware thresholds: controls=pure upstream (NRestarts>0 suspicious, no memcap drop-in); feedback=cgroup memcap (NRestarts≤50 healthy, drop-in present). Code in `deployment_engine/ghosts/audit.py`. Outputs terminal table + markdown at `deployments/logs/audit_ghosts_*.md`. All three audits are now implemented (see /rampart-audit, /decoy-audit).
---

# ghosts-audit

`./audit --ghosts` — health audit of every active GHOSTS deployment.
Mode-aware: controls (pure upstream cmu-sei/GHOSTS) and feedback (cgroup
memcap drop-in) get different healthy thresholds.

| | |
|---|---|
| Entry point | `./audit --ghosts` at RUSE root |
| Code | `deployment_engine/ghosts/audit.py::run_ghosts_audit` |
| Outputs | Terminal summary table + `deployments/logs/audit_ghosts_<ts>.md` |
| Exit code | 0 if no issues, 1 otherwise |

## Per-VM probe (role-aware)

SSH probe over `bot-desktop` keypair (`~/.ssh/id_ed25519`),
`SSH_AUTH_SOCK=""`, parallel up to 20 workers. NPCs probe first so the
API's `/api/machines` registration check has the healthy-NPC set ready.

API VM (`g-{hash}-api-0`):

1. SSH reachable
2. Docker stack — every container in `{ghosts-api, ghosts-postgres,
   ghosts-frontend, ghosts-n8n, ghosts-grafana}` must be present and
   `Up*` (n8n in particular has a real-world tendency to crash-loop)
3. `/api/machines` distinct-name set covers every healthy NPC in the
   deployment (raw count routinely exceeds NPC count due to
   re-registrations across cgroup-OOM respawns; dedupe by `.name`)

NPC VMs (`g-{hash}-npc-N`):

1. SSH reachable
2. `systemctl is-active ghosts-client`
3. NRestarts within mode-aware threshold:
    - controls: 0 expected, `>0` flagged WARN
    - feedback: 0..50 healthy (cgroup OOM cycle every ~2h from upstream
      memleak; ~12 cycles/24h is steady-state); `>50` = cap too tight
4. Memcap drop-in (`/etc/systemd/system/ghosts-client.service.d/memcap.conf`)
   present iff feedback (mismatch = `is_feedback` Ansible gate fired wrong)
5. RSS vs `MemoryMax` — informational; ≥95% on feedback = OOM imminent
6. `/opt/ghosts-client/config/timeline.json`: present, parseable,
   `Status=Run` (case-insensitive), handlers > 0, top-level `Id` field
   present (the .NET client adds a registration GUID on first start;
   missing Id ⇒ never started OK)
7. Mode contract — `_phase_metadata.mode` read LOCALLY from
   `run_dir/timelines/{vm_name}.json`, not the deployed file. The
   .NET client rewrites timeline.json at startup (drops `_phase_metadata`,
   normalizes JSON), so the on-VM copy can't be trusted for contract
   validation. Must match deployment type (`controls`/`feedback`);
   anything else FATAL.

SHA parity between VM and run_dir is **intentionally not checked**: the
client mutating the file is the expected steady state.

## Cross-deployment

- OpenStack VM list vs inventory diff (per `g-{hash}-` prefix from
  `make_ghosts_vm_prefix(make_run_dep_id(name, run_id))`)
- PHASE `experiments.json` registration (every inventory IP)
- Duplicate `run_ids` per config name
- Orphan 200GB volumes (shared with DECOY/RAMPART teardown leak)
- Latest `session-deploy-*.log` `[WARNING]` lines surfaced

## Output shape

Terminal:

```
GHOSTS AUDIT
  Found 2 deployments, 12 VMs
  Probing 12 VMs in parallel...
  [16:42:11]  [1/12]  ........  g-1c8e0-npc-0
  ...
AUDIT SUMMARY
Deployment                         VMs    SSH   Stack  Reg   Svc  Mode Cap   Restart Tline
ghosts-controls-050926155703       1A+5N  6/6   1/1    1/1   5/5  5/5  5/5   5/5     5/5
ghosts-feedback-...-fall24-...     1A+5N  6/6   1/1    1/1   5/5  5/5  5/5   5/5     5/5
```

9-char compact status legend: ssh / stack / reg / service / mode / cap /
restart / memory / timeline. `.` pass, `X` fail, `W` warn, `?` unknown.

Markdown at `deployments/logs/audit_ghosts_<YYYYMMDD-HHMMSS>.md` with
per-deployment full check tables.

## Why role-aware (vs DECOY's single-shape probe)

DECOY VMs are uniform (each runs a SUP brain). GHOSTS deploys have two
shapes that share almost no checks: the API VM only matters for Docker +
HTTP, the NPCs only matter for systemd + .NET state. A single bash
template would n/a-out half the columns on every row. Splitting saves
one round trip's worth of irrelevant probing per VM and keeps each
classifier focused.

## Mode FATAL gate

The `_phase_metadata.mode` contract is enforced in two directions:

1. The mode value itself must be `controls` or `feedback`. Anything else
   (legacy `phase_test`, `dataset-only`, parse error) → FATAL.
2. The mode must match the deployment type. A `ghosts-feedback-*/` deploy
   carrying timelines with `mode=controls` (or vice versa) → FATAL —
   indicates PHASE wrote into the wrong slot or the deploy picked the
   wrong `behavior_source`.

Same shape as DECOY's `_metadata.mode ∈ {feedback, controls}` window-mode
gate, applied to GHOSTS's `_phase_metadata.mode`.

## Manual recipes (still useful)

```bash
ssh g-1c8e0-api-0 "sudo docker ps"
ssh g-1c8e0-api-0 "curl -s localhost:5000/api/machines | jq 'length'"
ssh g-1c8e0-npc-0 "systemctl status ghosts-client"
ssh g-1c8e0-npc-0 "journalctl -u ghosts-client --no-pager -n 100"
ssh g-1c8e0-npc-0 "ls /etc/systemd/system/ghosts-client.service.d/"
```

## Related

- Deploy lifecycle: `/ghosts-deploy`
- DECOY audit: `/decoy-audit`
- RAMPART audit: `/rampart-audit`
