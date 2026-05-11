---
name: decoy-audit
description: DECOY audit — `./audit --decoy` runs 11 per-VM SSH probes + 5 cross-deployment consistency checks across every active DECOY deployment. Code in `deployment_engine/decoy/audit.py`. Outputs terminal table + markdown report at `deployments/logs/audit_*.md`. GHOSTS audit is also implemented (see /ghosts-audit); --rampart is still a stub (see /rampart-audit). Per-VM behavior.json window-mode states, NRestarts crash-loop detection, Ollama+GPU IDLE-vs-FAIL, feature-warning grep, and orphan-VM/missing-inventory diff vs OpenStack. SSH probe inlined (not via Ansible) for parallel speed + per-VM real-time output.
---

# decoy-audit

`./audit --decoy` (or just `./audit`, default) runs full health audit
across every DECOY deployment with active state. Routes through
`deployment_engine/decoy/audit.py::run_audit(deploy_dir)`.

| | |
|---|---|
| Entry point | `./audit` (default `--decoy`) at RUSE root |
| Code | `deployment_engine/decoy/audit.py` |
| Inputs | `deployments/{decoy-*}/runs/{run_id}/inventory.ini`, OpenStack server list, `/mnt/AXES2U1/experiments.json`, per-VM SSH probes |
| Outputs | Terminal summary table + markdown report at `deployments/logs/audit_<timestamp>.md` |
| Exit code | 0 on no failures, 1 if any check fails |

## What gets audited

Discovery: walks `deployment_engine.core.config.DeploymentConfig` for
each `deployments/*/config.yaml`. Skips RAMPART (`is_rampart()`) and
GHOSTS (`is_ghosts()`) — DECOY-only at the discovery boundary. For each
DECOY deployment, iterates `runs/*/inventory.ini` and probes every VM
in parallel.

## Per-VM SSH probe (`_ssh_probe`)

Single SSH round trip per VM, executes a bash blob that emits `KEY=value`
lines for each check. Parsed locally into a probe dict. 20-worker
ThreadPoolExecutor; 30s timeout per VM.

Probe collects:

| key | meaning |
|---|---|
| `SVC` | `systemctl is-active {svc}` |
| `NRESTARTS` | `systemctl show {svc} -p NRestarts` (cumulative, never decays) |
| `SVC_UPTIME_S` | seconds since `ActiveEnterTimestampMonotonic` — used to ignore stale NRestarts from past crash-loops |
| `PROC_COUNT` | `pgrep -f 'runners.run_'` |
| `OLLAMA_MODEL` | `curl localhost:11434/api/ps` first model name |
| `VRAM_MIB` | `nvidia-smi --query-gpu=memory.used` |
| `GPU_NAME` | nvidia-smi name (V100 / RTX) |
| `LOG_MTIME` | newest jsonl mtime under `/opt/ruse/deployed_sups/*/logs/` |
| `CRON_COUNT` | `sudo crontab -l \| grep -c 'mchp-(daily\|weekly)'` |
| `BC_FILES` / `BC_HAS_BEHAVIOR` | behavior.json presence + total file count |
| `WARN_COUNT` / `INFO_COUNT` / `WARN_LINES` | grep `[WARNING]` and `[INFO].*ablation-gated` from systemd.log |
| `WIN_STATE` / `WIN_N` / `WIN_ON_MIN` / `WIN_TARGET` | window-mode contract from `behavior.json` |
| `WIN_VOL_MEDIAN` | median `[bg-counter]` conns/min during ON-windows over last 60 minutes |

## 11 columns in the terminal summary

| col | check | source |
|---|---|---|
| SSH | reachable | probe rc |
| Svc | systemd active AND (uptime ≥ 600s OR NRestarts ≤ 10) | `SVC` + `NRESTARTS` + `SVC_UPTIME_S` |
| Proc | brain process running | `PROC_COUNT >= 1` |
| Model | Ollama loaded matches `expected_model(behavior)` | `OLLAMA_MODEL` |
| GPU | V100 VRAM ≥ 5 GB | `VRAM_MIB` |
| Logs | latest jsonl < `LOG_FRESHNESS_SECS` (4h default) | `LOG_MTIME` |
| Cron | M-brains have 2 maintenance entries (daily restart + weekly reboot) | `CRON_COUNT` |
| Fdbk | exactly one `behavior.json` in `behavioral_configurations/` | `BC_FILES + BC_HAS_BEHAVIOR` |
| Warn | 0 `[WARNING]`s; ablation-gated `[INFO]`s reported separately | `WARN_COUNT + INFO_COUNT` |
| Win | window-mode contract — see below | `WIN_STATE` |
| BG | median D4-only bg-conn/min during ON-windows ≥ 30% of target (floor check — brain workflow conns NOT counted) | `WIN_VOL_MEDIAN` vs `WIN_TARGET` |

## Window-mode column states (post 2026-05-08)

| `WIN_STATE` | rendered as | meaning |
|---|---|---|
| `FEEDBACK` | `OK feedback (N wins, Mm)` | `_metadata.mode == "feedback"` |
| `CONTROLS` | `OK controls (N wins, Mm)` | `_metadata.mode == "controls"` |
| `parse_error` | `FAIL (behavior.json parse error)` | malformed JSON |
| anything else | `FAIL (mode=X — contract violated)` | schema regression / version skew |

## BG column (post 2026-05-10)

`bg-counter` log lines emitted by `decoys/common/background_services.py`
are scraped from systemd.log. Last 60 in-window samples → median. State:

- `OK ({median}/{target} D4-only)` when ratio ≥ 0.3
- `WARN (..., ratio X)` when ≥ 0.15
- `FAIL (..., ratio X)` below
- `PENDING (no bg-counter samples)` — D4 daemon not emitting
- `PENDING (N samples, no in-window yet)` — bg-counter running, just hasn't aligned with a window yet (typical for fresh deploys; sparse-window datasets can take 1-2h)
- `PENDING (N in-window samples, all conns=0)` — D4 ran in-window but every minute logged 0 conns

**Critical caveat — this is a D4-FLOOR check, NOT a total-network-rate
check.** The bg-counter ONLY tracks D4 background-service probes
(`background_services.py`: dns/http_head/ntp/smb/etc.). Brain workflow
connections (browse_web, google_search, etc.) are NOT counted.
Ground-truthed on 2026-05-10 with tcpdump: real total outbound was
~35 conn/min vs target 7 while bg-counter alone reported 2-3 on the
same SUP. Thresholds are deliberately loose because workflows dominate
the actual emitted traffic; this column only flags "D4 isn't running
at all." If `Win=OK` but `BG=FAIL`, the SUP is almost certainly fine.

## Neighborhood sidecar probe (post 2026-05-11)

Each feedback deploy has one neighborhood VM (`d-{dep_id}-neighborhood-0`)
listed in `runs/{rid}/neighborhood-inventory.ini` — separate from
sup_hosts. `_neighborhood_probe()` SSHes the sidecar and emits:

| key | meaning |
|---|---|
| `ACT` | `systemctl is-active ruse-neighborhood` |
| `NR` | NRestarts |
| `UPTIME_S` | seconds since service became active (stale-NR gate) |
| `PROBES_LAST_HR` | probe events in `/var/log/ruse-neighborhood.systemd.log` in last hour |
| `PROBES_TOTAL` | cumulative probes in `/var/log/ruse-neighborhood.jsonl` |
| `SUPS_HIT` | distinct sup names in last 400 probe events |
| `PROBE_TYPES` | distinct probe types (out of 10 configured) |
| `TARGETS` | sup count in `/etc/ruse-neighborhood/sups.json` |

**Why two log files**: daemon writes pure JSON (no timestamps) to
`/var/log/ruse-neighborhood.jsonl` and timestamped stdout to
`/var/log/ruse-neighborhood.systemd.log` (via systemd
`StandardOutput=append:`). Time-windowed counts MUST come from
`.systemd.log` (the jsonl is timestamp-less). Don't grep journalctl —
the unit redirects stdout to a file, journalctl only has systemd's
own start/stop messages.

**Timestamps are LOCAL time, not UTC**. The bracketed prefix
`[YYYY-MM-DD HH:MM:SS,microseconds]` is whatever the daemon process saw
from `time.strftime` — VM tz is `America/New_York` per
`install-neighborhood.yaml`. The probe's cutoff uses local `date`, not
`date -u`. Lifting that to UTC requires either tz-aware daemon output
or a tz-converting awk filter.

`_classify_neighborhood()` returns:

- `OK ({probes_hr}/hr, {sups_hit}/{targets} SUPs)`
- `WARN ({probes_hr}/hr, only {sups_hit}/{targets} SUPs hit)` — partial routing
- `FAIL (silent daemon — 0 probes/hr)` — service active but emitting nothing
- `FAIL (no jsonl — daemon never wrote)`
- `FAIL (crash-looping, N restarts, up Ns)` — NR>5 within first 600s
- `FAIL (service {state})` — not active

Rendered as a separate "Neighborhood sidecars" table after the main
13-column SUP summary. Sidecar failures become issues in the same
ISSUES list. Per-row marker in the live probe progress is `..nbhd...X`
or `..nbhd....` to visually distinguish from SUP rows.

## IDLE → OK / FAIL post-pass

Ollama unloads idle models after 5 min. V2+ calibrated agents sleep up
to 1h between clusters. So `Model=IDLE` and `GPU=IDLE` are normal mid-
quiet. The post-pass rule: if `Service=OK + Process=OK`, IDLE becomes
`OK (idle)`; otherwise `FAIL (not loaded)`.

## Cross-deployment checks

Run after every per-VM probe completes:

- **Orphan / missing**: diff inventory.ini hostnames against
  `openstack server list` filtered by `_dep_prefix(dep)` =
  `make_vm_prefix(make_run_dep_id(...))`. Neighborhood VMs (in
  `neighborhood-inventory.ini`, NOT `sup_hosts`) excluded from orphan
  check by suffix `-neighborhood-0`.
- **PHASE registration**: every inventory IP must appear in
  `experiments.json[deployment_name].vm_ips`. Reports IPs missing
  from registration.
- **Duplicate run_ids**: per config_dir, more than one `runs/*` is
  fine (history); but two ACTIVE runs (both with inventory.ini) is a
  bug.
- **Orphan boot volumes**: nameless 200 GB available volumes on
  OpenStack — leftover from incomplete teardowns.

## Markdown report

Written to `deployments/logs/audit_{YYYYMMDD-HHMMSS}.md`. Two sections:

1. Summary table (one row per deployment, 13 columns including run_id and Win+BG counts)
2. Per-deployment per-VM detail (every check verbatim)

The `_row_status()` helper emits a 11-char compact status string
(`. = pass, X = fail, W = warning, ? = unknown`) for terminal one-liners.

## Behavior helpers (shared with INSTALL_SUP.sh logic)

```python
expected_model("B0.gemma")  → "gemma4:26b"
expected_model("B0C.gemma") → "gemma4:e2b"   # CPU variant
expected_model("M1")        → None            # MCHP no LLM
expected_service("B0.gemma") → "b0_gemma.service"  # dot → underscore
needs_gpu("B0.gemma")       → True            # GPU-tier flavor
needs_mchp_cron("M1")       → True            # M-brains only
```

These mirror the resolution in `INSTALL_SUP.sh::MODEL_NAMES`. Drift here
will cause every audit to misreport — keep the two in sync when adding
new behaviors.

## Constants

| constant | value | purpose |
|---|---|---|
| `LOG_FRESHNESS_SECS` | 86400 (24h) | catches stuck agents past inter-cluster sleep window |
| `EXPERIMENTS_JSON` | `/mnt/AXES2U1/experiments.json` | PHASE registration table |
| service NRestarts threshold | 10 | only flips Service to `crash-looping` if uptime < 600s; high NRestarts on a stable service is reported as `OK (N restarts, stable Mm)` |
| `STABLE_UPTIME_S` | 600 | continuous-active gate that suppresses NRestarts noise from past crash-loops |
| BG OK / WARN ratios | 0.3 / 0.15 | median D4-only conn/min vs target_conn_per_minute_during_active (deliberately loose — D4 is one stochastic contributor; workflows dominate total traffic) |

## Common usage

```bash
./audit                                  # full report (default --decoy)
./audit --decoy                          # explicit
./audit | grep -E "FAIL|^Issues"         # just the fails
./audit | grep Fdbk                      # behavior.json status across all
ls -t deployments/logs/audit_*.md | head -1 | xargs less
```

To debug a specific VM:

```bash
ssh d-controls050826193122-B0-gemma-0 \
  "systemctl status b0_gemma; journalctl -u b0_gemma --no-pager | tail -30"
ssh d-controls050826193122-B0-gemma-0 \
  "tail -f /opt/ruse/deployed_sups/B0.gemma/logs/systemd.log"
```

## Related

- Full deploy lifecycle: `/decoy-deploy`
- RAMPART audit (stub): `/rampart-audit`
- GHOSTS audit (real implementation): `/ghosts-audit`
- behavior.json schema + window-mode contract: `/decoy-deploy`
