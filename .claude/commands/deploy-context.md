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
    phase_to_timeline.py    # GHOSTS timeline gen (see /deploy-ghosts)
    phase_to_user_roles.py  # RAMPART user roles gen (see /deploy-rampart)
```

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

After reading these files, provide a brief summary of the current state and any recent changes visible in the code.
