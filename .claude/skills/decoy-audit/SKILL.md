---
name: decoy-audit
description: DECOY audit — `./audit --decoy` runs 11 per-VM SSH probes + 5 cross-deployment consistency checks across every active DECOY deployment. Code in `deployment_engine/decoy/audit.py`. Outputs terminal table + markdown report at `deployments/logs/audit_*.md`. The only audit currently implemented; --rampart and --ghosts are stubs (see /rampart-audit, /ghosts-audit). Per-VM behavior.json window-mode states, NRestarts crash-loop detection, Ollama+GPU IDLE-vs-FAIL, feature-warning grep, and orphan-VM/missing-inventory diff vs OpenStack. SSH probe inlined (not via Ansible) for parallel speed + per-VM real-time output.
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
| `NRESTARTS` | `systemctl show {svc} -p NRestarts` |
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
| Svc | systemd active + NRestarts ≤ 10 | `SVC` + `NRESTARTS` |
| Proc | brain process running | `PROC_COUNT >= 1` |
| Model | Ollama loaded matches `expected_model(behavior)` | `OLLAMA_MODEL` |
| GPU | V100 VRAM ≥ 5 GB | `VRAM_MIB` |
| Logs | latest jsonl < `LOG_FRESHNESS_SECS` (4h default) | `LOG_MTIME` |
| Cron | M-brains have 2 maintenance entries (daily restart + weekly reboot) | `CRON_COUNT` |
| Fdbk | exactly one `behavior.json` in `behavioral_configurations/` | `BC_FILES + BC_HAS_BEHAVIOR` |
| Warn | 0 `[WARNING]`s; ablation-gated `[INFO]`s reported separately | `WARN_COUNT + INFO_COUNT` |
| Win | window-mode contract — see below | `WIN_STATE` |
| Vol | median bg-conn/min during ON-windows ≥ 70% of target | `WIN_VOL_MEDIAN` vs `WIN_TARGET` |

## Window-mode column states (post 2026-05-08)

| `WIN_STATE` | rendered as | meaning |
|---|---|---|
| `FEEDBACK` | `OK feedback (N wins, Mm)` | `_metadata.mode == "feedback"` |
| `CONTROLS` | `OK controls (N wins, Mm)` | `_metadata.mode == "controls"` |
| `parse_error` | `FAIL (behavior.json parse error)` | malformed JSON |
| anything else | `FAIL (mode=X — contract violated)` | schema regression / version skew |

## Volume column (post 2026-05-08)

`bg-counter` log lines emitted by `decoys/common/background_services.py`
are scraped from systemd.log. Last 60 in-window samples → median. State:

- `OK ({median}/{target})` when ratio ≥ 0.7
- `WARN (..., ratio X)` when ≥ 0.4
- `FAIL (..., ratio X)` below
- `PENDING (no bg-counter samples)` for fresh VMs (~first hour after deploy)

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

1. Summary table (one row per deployment, 13 columns including run_id and Win+Vol counts)
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
| `LOG_FRESHNESS_SECS` | 14400 (4h) | catches stuck agents past inter-cluster sleep window |
| `EXPERIMENTS_JSON` | `/mnt/AXES2U1/experiments.json` | PHASE registration table |
| service NRestarts threshold | 10 | flips Service from OK to `crash-looping` |
| Vol OK / WARN ratios | 0.7 / 0.4 | median conn/min vs target_conn_per_minute_during_active |

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
- GHOSTS audit (stub): `/ghosts-audit`
- behavior.json schema + window-mode contract: `/decoy-deploy`
