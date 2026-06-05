---
name: teardown
description: Teardown CLI — `./teardown` removes deployed VMs + zones + local run state across DECOY (d-*), RAMPART (r-*), and GHOSTS (g-*). Single-target positional form (`name-MMDDYYHHMMSS`, type auto-detected), filter form (`--rampart`/`--decoy`/`--ghosts` [+ `--feedback`]), `--failed` filter (only runs stamped failed in deploy_status.json), and `--all` nuke. Code in `deployment_engine/teardown.py` + per-type `*/teardown.py`. Knows the filter-vs-positional footgun and the `--failed` stamping contract.
---

# teardown

`./teardown` removes deployed VMs, their DNS zone, the SSH config block,
the PHASE experiments.json entry (end_date closed), and the local
`runs/{run_id}/` state for one or many deployments.

| | |
|---|---|
| Entry point | `./teardown ...` at RUSE root |
| Router | `deployment_engine/teardown.py` |
| Per-type | `decoy/teardown.py`, `rampart/teardown.py`, `ghosts/teardown.py` |
| Dispatch in router | `__main__.py::_cmd_teardown` |
| Outputs | `deployments/logs/session-teardown-*.log` (+ `teardown-parallel-*.log` per child in filter mode) |

## Forms

```bash
# Single deploy — bare positional, type auto-detected from its config.yaml.
./teardown rampart-feedback-stdctrls-cptc9-all-053026171155
./teardown decoy-controls-040226205037

# Filter (batch) — all active runs of a type, parallel fan-out.
./teardown --rampart                  # ALL rampart deploys
./teardown --rampart --feedback       # ALL rampart feedback deploys (skips controls)
./teardown --decoy --feedback
./teardown --ghosts

# Failed-only — runs stamped failed (see below). Composes with type/--feedback.
./teardown --failed --rampart         # only broken rampart runs
./teardown --failed                   # only broken runs, all types

# Nuke — every d-*, r-*, g-* VM in the project (two-step confirm).
./teardown --all
```

Target format for the positional form is `{config_name}-{MMDDYYHHMMSS}` —
the config dir name under `deployments/` concatenated with the run-id
subdir under its `runs/`.

## FOOTGUN — filter + positional silently drops the positional

`_cmd_teardown` computes `has_filter = decoy or rampart or ghosts or
feedback or failed` **first**. If any filter flag is set it calls
`run_teardown_filtered` and **the positional `target` is ignored**. So:

```bash
./teardown --rampart rampart-feedback-...-cptc9-all-053026171155
```

tears down **ALL** rampart deploys, not the one named. Single-target is
**always** a bare positional with no filter flags. Don't pass `--rampart`
alongside a specific target.

## `--failed` — stamped-outcome filter (added 2026-05-30)

`--failed` targets only runs whose deploy outcome was recorded as failed.
Outcome lives in `runs/{run_id}/deploy_status.json` (`core/run_status.py`):

- spinup stamps **`failed`** the moment the run dir is created, then flips
  to **`ok`** only if it reaches its final return. Any early return,
  exception, or hard kill leaves it `failed` — the safe default for a
  destructive filter.
- A run with **no stamp** reads as `unknown` and is **never** matched by
  `--failed` (covers in-flight deploys and any pre-instrumentation run).

So `--failed` deletes only deploys known-broken, never an in-progress or
unclassifiable one. Verify the match set before confirming — the filter
prints the full list and a single y/N:

```bash
echo n | ./teardown --failed --rampart    # dry-run: prints matches, cancels
```

**Instrumentation status:** **rampart** (`rampart/spinup.py`) and **decoy**
(`decoy/spinup.py`, wired 2026-06-05) spinups stamp — FAILED right after
`run_dir.mkdir`, OK only on the final clean return (decoy gates the OK flip on
`install_result.rc == 0`). **GHOSTS is still unwired** → ghosts runs read
`unknown` and `--failed` won't surface their broken runs; wire it the same way
(same two `run_status.write_run_status` calls) and update this skill in the same
change. Runs that predate a type's wiring also read `unknown` (e.g. decoy deploys
from before 2026-06-05) — backfill those (below) or use the positional teardown.

Backfill for runs that predate stamping: classify each run dir and write
`deploy_status.json`. For rampart, presence of the terminal artifact
`post-deploy-output.json` is a reliable ok/failed proxy (a run that
provisioned VMs but died at a later step lacks it).

## What a teardown does (per deploy)

`run_teardown` → per-type `run_*_teardown`:

1. openstack-delete every VM under the deploy's prefix (`make_vm_prefix` /
   `make_ent_vm_prefix` / `make_ghosts_vm_prefix`), wait for zero.
2. Delete the DNS zone (rampart/ghosts: from `run_dir/dns_zone.txt`).
3. Remove the managed SSH config block.
4. Close the PHASE `experiments.json` entry — sets `end_date` under an
   fcntl lock (`core/teardown_steps.py::close_phase_experiment`).
5. `safe_rmtree` the local `runs/{run_id}/` state.

VMs already gone is handled gracefully ("No VMs found" → proceeds to local
cleanup), so a run whose VMs failed to provision still gets its local
state + experiments.json entry cleaned.

## Filter mode internals

`run_teardown_filtered` enumerates **every** `runs/*` dir of a matching
config (not just ones with live OpenStack VMs) so zombie local state from
interrupted deploys/teardowns still gets reaped. It fans out one
subprocess per match via `ThreadPoolExecutor` (each child = its own
OpenStack auth + session log; concurrency-safe via fcntl locks on
`~/.ssh/config` and `experiments.json`). One y/N confirm for the whole
batch; children run with `CI=1` so they don't re-prompt.

`--all` (`run_teardown_all`) is a separate path: one `teardown-all.yaml`
regex sweep over all three prefixes + a two-step destructive confirm,
then local-state + experiments.json cleanup for every config with runs.

## DNS zone quota note

RAMPART (and GHOSTS) give each deploy its own Designate zone for
AD/DNS isolation. The project zone quota (observed: **10**, incl. the
parent `{project}.os.` zone) caps how many can coexist — a full
`./deploy --rampart` wants 14 deploy zones and hits `OverQuota` partway.
Tearing down completed/failed deploys frees zones; raising the quota is
admin-only (`vxn3kr-bot` is non-admin). `./teardown --failed` is the quick
way to reclaim zones held by broken partial deploys.

## Related

- Deploy lifecycle: `/decoy-deploy`, `/rampart-deploy`, `/ghosts-deploy`
- Health probes: `/decoy-audit`, `/rampart-audit`, `/ghosts-audit`
