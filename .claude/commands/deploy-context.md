# RUSE Deploy CLI - System Context

Load critical context about the shared RUSE deployment CLI infrastructure before working on it. This covers the common CLI framework used by all three deployment types (RUSE SUPs, RAMPART Enterprise, GHOSTS NPCs). For type-specific context, use `/deploy-ruse`, `/deploy-rampart`, or `/deploy-ghosts`.

## Instructions

Read the following files in order to understand the shared deployment infrastructure:

### Python CLI (the orchestrator)
1. `deployments/cli/__main__.py` - Entry point, argparse, command routing (deploy/teardown/list/shrink/audit as separate scripts)
2. `deployments/cli/config.py` - DeploymentConfig dataclass (loads config.yaml, supports sup/rampart/ghosts types)
3. `deployments/cli/openstack.py` - OpenStack CLI wrapper with caching (subprocess to `openstack` CLI, sources ~/vxn3kr-bot-rc)
4. `deployments/cli/ansible_runner.py` - Runs Ansible playbooks, streams + parses output in main thread (no race), stateful line parser with task whitelist
5. `deployments/cli/output.py` - Terminal output helpers (monochrome, ASCII banners, timestamps)
6. `deployments/cli/ssh_config.py` - SSH config block management (~/.ssh/config with RUSE markers)

### Command modules (shared)
7. `deployments/cli/commands/teardown.py` - Teardown for all three types + filter flags (--ruse/--rampart/--ghosts [--feedback]) + teardown-all
8. `deployments/cli/commands/list_cmd.py` - List active deployments across all types
9. `deployments/cli/commands/feedback.py` - PHASE feedback source detection, config generation, per-config-file CLI flags, find_all_feedback_sources for batch deploy
10. `deployments/cli/commands/shrink.py` - In-place VM removal: diffs run snapshot vs config.yaml, deletes delta VMs from OpenStack + cleans inventory/SSH config/experiments.json
11. `deployments/cli/commands/audit.py` - Full health audit of all RUSE deployments: SSH/service/process/model/GPU/log/cron checks per VM + cross-deployment consistency, writes markdown report

### Supporting libraries (imported by all command modules)
10. `deployments/lib/vm_naming.py` - VM naming conventions, prefix generation, parsing, sorting
11. `deployments/lib/register_experiment.py` - PHASE experiments.json registration

### Shared Ansible playbooks
12. `deployments/playbooks/provision-vms.yaml` - Create OpenStack VMs, wait ACTIVE, get IPs, write inventory + SSH config
13. `deployments/playbooks/teardown.yaml` - Delete servers + volumes for a specific deployment prefix
14. `deployments/playbooks/teardown-all.yaml` - Delete ALL r-/e-/g-/sup- VMs + volumes + orphans

## Architecture

The deploy system is a Python CLI with five separate entry-point scripts:

```
deployments/
  deploy                    # #!/bin/bash → exec python3 -m cli deploy "$@"
  teardown                  # #!/bin/bash → exec python3 -m cli teardown "$@"
  list                      # #!/bin/bash → exec python3 -m cli list "$@"
  shrink                    # #!/bin/bash → exec python3 -m cli shrink "$@"
  audit                     # #!/bin/bash → exec python3 -m cli audit "$@"
  deploy.legacy             # Old bash script (preserved for reference)

  cli/                      # Python CLI package
    __main__.py             # argparse routing: deploy/teardown/list/shrink/audit
    config.py               # DeploymentConfig dataclass
    openstack.py            # OpenStack CLI wrapper
    ansible_runner.py       # Playbook runner + streaming parser (main thread)
    output.py               # Monochrome terminal output
    ssh_config.py           # SSH config management
    commands/
      spinup.py             # ./deploy --ruse        (see /deploy-ruse)
      rampart.py            # ./deploy --rampart     (see /deploy-rampart)
      ghosts.py             # ./deploy --ghosts      (see /deploy-ghosts)
      teardown.py           # ./teardown <target> | --all | --ruse|rampart|ghosts [--feedback]
      list_cmd.py           # ./list
      feedback.py           # PHASE feedback resolution + config generation + batch source discovery
      shrink.py             # ./shrink <target> — in-place VM removal
      audit.py              # ./audit — health check of all RUSE deployments

  playbooks/                # Ansible (infrastructure only, no display)
    provision-vms.yaml      # Create VMs, get IPs, write inventory
    install-sups.yaml       # Install SUP agents (see /deploy-ruse)
    distribute-behavior-configs.yaml  # Deploy PHASE configs (see /deploy-ruse)
    install-ghosts-api.yaml          # GHOSTS API (see /deploy-ghosts)
    install-ghosts-clients.yaml      # GHOSTS NPC clients (see /deploy-ghosts)
    install-rampart-emulation.yaml   # RAMPART emulation (see /deploy-rampart)
    teardown.yaml           # Per-deployment teardown
    teardown-all.yaml       # Nuclear teardown (all prefixes)

  lib/                      # Python utilities (imported by CLI)
    vm_naming.py            # VM naming: r-{dep_id}-{behavior}-{index}
    register_experiment.py  # PHASE experiments.json
    enterprise_ssh_config.py # Enterprise SSH config gen (see /deploy-rampart)
```

**Note:** `phase_to_timeline.py` and `phase_to_user_roles.py` were deleted in
Stage 2 (2026-04-09). PHASE's feedback engine now writes target-native formats
directly — RAMPART per-node `user-roles.json` and GHOSTS per-NPC `timeline.json`
— so RUSE no longer needs a reverse-translation layer. See `/deploy-rampart`
and `/deploy-ghosts` for the new read-direct flows.

## Three Deployment Types

| Type | Flag | Prefix | Config type | Skill |
|------|------|--------|-------------|-------|
| RUSE SUPs | `--ruse` | `r-` | `sup` | `/deploy-ruse` |
| RAMPART Enterprise | `--rampart` | `e-` | `rampart` | `/deploy-rampart` |
| GHOSTS NPCs | `--ghosts` | `g-` | `ghosts` | `/deploy-ghosts` |

## CLI Usage (common operations)

```bash
# Deploy (type-specific — see individual skills for details)
./deploy --ruse                              # SUP baseline
./deploy --rampart                           # Enterprise baseline
./deploy --ghosts                            # GHOSTS NPCs baseline

# Batch deploy: deploys ALL available PHASE feedback configs for a type
./deploy --ruse --feedback --batch           # all RUSE feedback variants
./deploy --rampart --feedback --batch        # all RAMPART feedback variants
./deploy --ghosts --feedback --batch         # all GHOSTS feedback variants
# (Discovers via find_all_feedback_sources() in feedback.py — scans
#  ~/PHASE/feedback_engine/configs/ for matching dirs, shows them,
#  prompts for confirmation, then deploys each in sequence with a final summary.)

# List all active deployments
./list

# Teardown — three forms
./teardown ruse-controls-032226210347        # Specific deployment by name+run_id
./teardown --ruse --feedback                 # Filter: all active RUSE feedback deploys
./teardown --rampart                         # Filter: all active RAMPART deploys
./teardown --ghosts --feedback               # Filter: all active GHOSTS feedback deploys
./teardown --all                             # Nuclear: everything (requires confirmation)

# Shrink a running deployment in place (no full teardown/redeploy)
./shrink ruse-controls-040226205037          # Diffs run snapshot vs config.yaml,
                                             # deletes surplus VMs, cleans inventory/SSH/PHASE

# Health audit of all RUSE deployments
./audit                                       # Per-VM checks: SSH, service, process, model
                                             # loaded, GPU loaded, log freshness, MCHP cron;
                                             # cross-deployment: orphan detection, PHASE
                                             # registration; writes markdown to logs/audit_*.md
```

## Key Design Decisions

- **Monochrome output** — no ANSI colors, ASCII `####` banners, `[HH:MM:SS]` wall-clock timestamps, `OK`/`FAIL`/`..` markers
- **Ansible for infrastructure only** — all display logic in Python, playbooks stripped of pause/display tasks
- **Stateful Ansible parser** — `_LineParser` tracks current task, only shows `changed:` for whitelisted tasks, suppresses internal Ansible noise
- **SSH agent disabled** — `SSH_AUTH_SOCK=""` + `IdentitiesOnly=yes` everywhere (agent offers too many keys causing auth timeouts)
- **Python SSH test** — replaced Ansible retry loop (which hangs silently) with Python `concurrent.futures` that prints each attempt in real time
- **No teardown confirmation** — if you run `./teardown`, you mean it (except `--all`)
- **Three separate scripts** — `./deploy`, `./teardown`, `./list` instead of subcommands under one script

## OpenStack / SSH Details

- All runs locally on mlserv (10.246.118.30), same network as OpenStack API
- Credentials: `~/vxn3kr-bot-rc` (OS_AUTH_URL, OS_PROJECT_ID, etc.)
- SSL: `~/openstack_vault_ca.pem` (custom CA)
- VM prefixes: `r-` (RUSE SUPs), `e-` (enterprise), `g-` (GHOSTS), `sup-` (legacy)
- VM naming: `r-{dep_id}-{behavior}-{index}` where dep_id = `{name_no_hyphens}{run_id}`
- Run IDs: `MMDDYYHHmmss` timestamps (second precision)
- SSH config: managed blocks in `~/.ssh/config` with `# BEGIN/END RUSE:` markers

### SSH Keys by Deployment Type

| Type | OpenStack Keypair | Local Key |
|------|-------------------|-----------|
| RUSE SUPs | `bot-desktop` | `~/.ssh/id_ed25519` |
| GHOSTS NPCs | `bot-desktop` | `~/.ssh/id_ed25519` |
| RAMPART Enterprise | `enterprise-key` | `~/.ssh/id_rsa` |

## Behavioral Config System (shared concepts)

### Unified feedback flag (all deployment types)
- `--feedback` → deploy with all PHASE behavioral configs

### PHASE feedback source
Auto-detected from `~/PHASE/feedback_engine/configs/` (most recent directory matching deploy type). Can target a specific dataset with `--source`.

### Deployment naming pattern
- `{type}-controls` — Baseline (no feedback) — committed to git
- `{type}-feedback-{preset}-{dataset}-{scope}` — Auto-generated feedback deployment,
  **NOT committed**. These dirs are created by the deploy CLI from `FEEDBACK_TEMPLATE`
  and matching PHASE source data. They live entirely on the local mlserv filesystem
  and are listed in `.gitignore`:
  ```
  deployments/ruse-feedback-stdctrls-*/
  deployments/ghosts-feedback-stdctrls-*/
  deployments/rampart-feedback-stdctrls-*/
  ```
- On teardown, `*-feedback-*` directories are cleaned up entirely (last run torn
  down → whole directory removed)

### Dataset targets (in `feedback.py`)
```python
DATASET_TARGETS = {
    "summer24": "summer24", "sum24": "summer24",
    "fall24": "fall24",
    "spring25": "spring25", "spr25": "spring25",
}
```

### PHASE feedback source layouts (post Stage 2, 2026-04-09)

PHASE's feedback engine now writes target-native formats directly — no
more reverse-translation at deploy time. Each experiment type has its
own file layout, identified by glob patterns instead of the old
`manifest.json` marker.

```
~/PHASE/feedback_engine/configs/
  axes-ruse-controls_{dataset}_{preset}/
    {behavior}/{sup}/behavior.json  # single consolidated per-SUP file
                                    # e.g. B.gemma/B0.gemma/behavior.json
                                    # validator: */*/behavior.json
                                    # (2026-04-16 consolidation — was 8 JSONs)

  axes-rampart-controls_{dataset}_{preset}/
    {bare_node}/user-roles.json  # self-contained pyhuman configs
                                 # 19 per-node files (linep2-10, winep1-10)
                                 # dc1-3 + linep1 absent (user: null)
                                 # validator: */user-roles.json

  axes-ghosts-controls_{dataset}_{preset}/
    npc-{N}/timeline.json        # 5 per-NPC tuned timelines
                                 # per-VM DelayAfter proportional to volume
                                 # api-0 absent (server VM, not NPC)
                                 # validator: npc-*/timeline.json
```

**Source directory naming**: `{experiment}_{dataset}_{preset}`, split on
underscore. E.g. `axes-ruse-controls_axes-summer24_std-ctrls` parses to
experiment=`axes-ruse-controls`, dataset=`axes-summer24`, preset=`std-ctrls`.
Parsing via `_parse_source_name()` in `feedback.py` replaces the old
manifest.json reads.

**manifest.json is gone**: Stage 2 also removed it from
`distribute-behavior-configs.yaml` excludes. A directory is a valid
feedback source if it matches its type's glob pattern (see
`_is_valid_feedback_source()` in `feedback.py`).

## Stage 3: Fail-loud deploy semantics (2026-04-14)

After discovering 161 RAMPART Windows endpoints had been silently failing
to deploy across 7 "successful" deploys (`_safe_parallel_call` swallowed
every per-VM auth failure as a WARNING and continued), the deploy system
was overhauled to fail loud at every silent-failure point. `DONE` now
means every VM is actually verified functional.

### Core principles
1. No broad `except Exception:` without logging the actual error.
2. No `ignore_errors: yes` or `failed_when: false` on readiness checks.
3. No aggregate metric (`5 VMs succeeded`) without a pass/fail threshold.
4. Deploys emit a success contract — complete = every VM in expected state.
5. Shell blocks use `set -euo pipefail` so mid-script failures don't silently
   skip subsequent steps.

### Canonical failure threshold
90% by default. Below that the deploy is not usable for experiments,
so the step aborts with a clear summary of failure patterns.

### Where the assertions live
- **rampart.py** `_deploy_windows_emulation._ssh_step`: every SSH subprocess.run
  checks returncode, raises with stderr. Aggregates error patterns at end
  (e.g. `19x Authentication failed` shown once, not 19 warnings buried
  in scrollback). Caller aborts at < 90%.
- **post-deploy.py** `_check_step_results`: after every parallel batch
  (`register_windows`, `join_domains`, `deploy_human`, Moodle steps,
  `setup_fileservers`). Counts error dicts, prints pattern summary,
  `sys.exit(1)` if > 10% fail. Prints `[step_name] OK — all N succeeded`
  on success so operators know each step actually completed.
- **spinup.py** (RUSE), **ghosts.py** (GHOSTS), **rampart.py** (RAMPART):
  SSH threshold 90% — abort if fewer VMs reachable than threshold.
- **provision-vms.yaml**: abort if < 90% VMs reach ACTIVE.
- **distribute-behavior-configs.yaml**: abort if behavior source missing,
  no config files matched, or any behavior.json fails to parse as JSON
  (previously silently degraded feedback deploy to baseline with no
  warning; corrupt JSON now fails at localhost before shipping to VMs).
- **install-sups.yaml**: explicit assert stage2 rc=0, service is-active
  assertion (replacing `|| true` swallow), cron-count assertion for M-series
  maintenance jobs.
- **install-ghosts-api.yaml**: `set -euo pipefail` on Docker install shell,
  Dockerfile exists check before sed patch, explicit `fail:` when API
  health check times out (removed `ignore_errors`), docker compose error
  detection in stdout.
- **install-ghosts-clients.yaml**: `set -euo pipefail` on dotnet publish,
  stat + assert `Ghosts.Client.Universal.dll` exists, systemctl is-active
  assertion (removed `ignore_errors`).
- **install-rampart-emulation.yaml**: systemctl is-active + NRestarts ≤ 10
  assertion (catches services "active" between rapid restart cycles — the
  exact pattern that masked the D5 arg mismatch crash loop).

### audit.py parallel upgrades
- **NRestarts probe** — `systemctl show -p NRestarts --value`. Service
  check reports `FAIL (crash-looping, N restarts)` when active but
  NRestarts > 10. Previously crash-looping services reported `active`
  between restart cycles and audit missed the failure entirely.
- **M0 expected-failure exception** — `M0` (unmodified upstream MITRE
  pyhuman) reports `EXPECTED (M0 upstream crashes on Linux)` instead of
  FAIL, recognizing that `os.startfile()` crash is the intentional
  baseline behavior.
- **Feedback feature probes** (Fdbk, Warn columns) — checks
  `/opt/ruse/deployed_sups/*/behavioral_configurations/` file count
  (post-2026-04-16: expect 1 file, `behavior.json`) and `[WARNING]`
  line count in `systemd.log` to surface feature activation state
  per VM. Baselines expected to emit warnings (no feedback); feedback
  deploys with complete PHASE configs should emit silence.

### Teardown improvements
- **Orphan volume cleanup** — `_cleanup_orphaned_volumes(os_client)` in
  every teardown path. Deletes nameless/200GB/available volumes left
  over from deleted servers (was leaking ~200GB per VM).
- **experiments.json closure** — `_close_phase_experiment(config_name)`
  sets `end_date` on the matching `/mnt/AXES2U1/experiments.json` entry
  so PHASE batch pipelines (`PHASE.py --ruse`, `--rampart`, `--ghosts`)
  don't pick up torn-down deploys as active. Historical registration
  preserved for analysis correlation; only `end_date` is set.

## Stage 3b: Operator observability (2026-04-14)

### Session log
Every `./deploy`, `./teardown`, `./list`, `./shrink`, `./audit` invocation
opens a log file at `deployments/logs/session-{command}-{timestamp}.log`.
Every `output.info/error/banner/table` call tees to it via
`output._write()`. The session log captures:
- All banners, warnings, errors
- Filtered Ansible output from `default_event_handler` (OK/FAIL lines)
- Batch deploy summaries
- Abort reasons with stderr detail

Raw unfiltered Ansible output stays in
`deployments/logs/ansible-{playbook}-{timestamp}.log` (unchanged).

**The log path is printed as the last line of every CLI invocation**,
even on abort, so operators can paste it directly for diagnosis.

### Grepping after a failure
```bash
# What aborted the deploy?
grep -E "FAIL|ABORTING|FAILURES" deployments/logs/session-deploy-*.log | tail -30

# What aggregate failure patterns did post-deploy.py see?
grep -E "FAILURES:|nodes:" deployments/*/runs/*/enterprise.log | tail -20

# What did Ansible actually say per-task?
grep -E "FAILED|fatal|UNREACHABLE" deployments/logs/ansible-*.log | tail -30
```

## Documentation
- `docs/silent-failures-audit.md` — 15-item CRITICAL/HIGH/MEDIUM/LOW
  catalog with specific fix plan (now mostly implemented). Useful as
  a reference when adding new deploy steps — check that no new silent
  failure patterns slip in.
- `docs/feedback-consumption-plan.md` — D1-D5, G1-G3 runtime consumption
  plan for PHASE feedback fields.
- `docs/feedback-field-audit.md` — per-file gap analysis of what PHASE
  generates vs what RUSE consumes, with pruning checklists.

After reading these files, provide a brief summary of the current state and any recent changes visible in the code.
