# RUSE SUP Deployment - System Context

Load critical context about RUSE SUP (Synthetic User Persona) deployment before working on it. Read all the files listed below, then summarize the current state for the user.

## Instructions

Read the following files in order to understand the RUSE SUP deployment system:

### Core RUSE SUP Deploy Files
1. `deployments/cli/commands/spinup.py` - Main RUSE SUP deployment orchestration (provision → SSH test → install → distribute configs)
2. `deployments/cli/config.py` - DeploymentConfig with `is_sup()`, SUP helpers (behaviors list, VM count, etc.)
3. `deployments/ruse-controls/config.yaml` - Baseline RUSE controls config (15 VMs: C0, M0, M1, B0/S0, B0C/S0C, B0R/S0R)

### Ansible Playbooks (SUP-specific)
4. `deployments/playbooks/provision-vms.yaml` - Create OpenStack VMs, wait ACTIVE, get IPs, write inventory + SSH config
5. `deployments/playbooks/install-sups.yaml` - SSH to VMs, install deps, INSTALL_SUP.sh stage1 → reboot → stage2 (skips C0)
6. `deployments/playbooks/distribute-behavior-configs.yaml` - Copy PHASE behavioral configs to VMs (baseline key derivation)

### PHASE Feedback Integration
7. `deployments/cli/commands/feedback.py` - Feedback source resolution, config generation, per-config-file CLI flags
8. `deployments/lib/register_experiment.py` - PHASE experiments.json registration

### Deployment Configs (feedback variants)
9. `deployments/ruse-feedback-stdctrls-sum24-all/config.yaml` - Example feedback deployment config (if present)
10. `deployments/ruse-feedback-stdctrls-sum24-timing/config.yaml` - Example timing-only feedback config (if present)

## Architecture

RUSE SUPs are the primary deployment type (`type: sup`, prefix: `r-`).

```
┌─────────────────────────────────────────────────────────────────────────┐
│  ./deploy --ruse [--feedback|--timing|--workflow|...] [--source path]  │
│  Python CLI: deployments/cli/__main__.py → commands/spinup.py          │
├─────────────────────────────────────────────────────────────────────────┤
│  spinup.py orchestrator                                                 │
│  [1/5] Provision VMs (Ansible: provision-vms.yaml)                     │
│  [2/5] Test SSH connectivity (Python concurrent.futures, 20 workers)   │
│  [3/5] Install SUP agents (Ansible: install-sups.yaml, stage1→reboot→stage2) │
│  [4/5] Distribute behavioral configs (Ansible: distribute-behavior-configs.yaml) │
│  [5/5] Finalize (SSH config, PHASE registration)                       │
└─────────────────────────────────────────────────────────────────────────┘

VM Topology (ruse-controls baseline):
  r-{hash}-C0-0         Bare Ubuntu control (no software installed)
  r-{hash}-M0-0         Upstream MITRE pyhuman (read-only control)
  r-{hash}-M1-0         MCHP baseline (no timing, no LLM)
  r-{hash}-B0-llama-0   BrowserUse + llama baseline
  r-{hash}-S0-llama-0   SmolAgents + llama baseline
  r-{hash}-B0-gemma-0   BrowserUse + gemma baseline
  r-{hash}-S0-gemma-0   SmolAgents + gemma baseline
  ... (15 total in controls)
```

## CLI Usage

```bash
# Baseline controls
./deploy --ruse                          # 15 VMs, no behavioral configs

# With PHASE feedback (all config files)
./deploy --ruse --feedback               # → ruse-feedback-stdctrls-sum24-all
./deploy --ruse --all-feedback           # Same as --feedback

# Granular feedback flags (RUSE-only, combine any)
./deploy --ruse --timing                 # timing_profile.json only
./deploy --ruse --timing --workflow      # timing + workflow weights
./deploy --ruse --modifiers              # behavior_modifiers.json
./deploy --ruse --sites                  # site_config.json
./deploy --ruse --prompts                # prompt_augmentation.json
./deploy --ruse --activity               # activity_pattern.json
./deploy --ruse --diversity              # diversity_injection.json
./deploy --ruse --variance               # variance_injection.json

# Explicit PHASE source
./deploy --ruse --feedback --source ~/PHASE/feedback_engine/configs/some-path

# Teardown
./teardown ruse-controls-MMDDYYHHMMSS
./teardown ruse-feedback-stdctrls-sum24-all-MMDDYYHHMMSS
```

## VM Naming

- Prefix: `r-{hash}-` where hash = MD5(dep_id)[:5]
- Pattern: `r-{hash}-{behavior}-{index}` (e.g., `r-a1b2c-M1-0`, `r-a1b2c-B2-llama-0`)
- dep_id: `{name_no_hyphens}{run_id}` where run_id = `MMDDYYHHmmss`
- `teardown-all.yaml` catches all prefixes: `(r-|e-|g-|sup-)`

## Behavioral Config Distribution

The distribute playbook (`distribute-behavior-configs.yaml`) handles:
1. Deriving baseline config key from versioned key: `B2C.gemma → B0C.gemma`, `M2 → M1`
2. Copying configs to `/opt/ruse/deployed_sups/{key}/behavioral_configurations/` on each VM
3. Only runs for V2+ configs (V0/V1 are baselines with no behavioral configs)

Config files distributed:
- `timing_profile.json` - Hourly activity distribution, burst/idle characteristics
- `workflow_weights.json` - Per-workflow selection probabilities
- `behavior_modifiers.json` - Max steps, dwell times, navigation limits
- `site_config.json` - Site category weights for task selection
- `prompt_augmentation.json` - Additional prompt content for LLM-based brains
- `activity_pattern.json` - Active hour ranges
- `diversity_injection.json` - Background service diversity
- `variance_injection.json` - Behavioral variance parameters

## Feedback Config Generation

When `./deploy --ruse --feedback` (or any granular flag) is used with `ruse-controls`, the CLI auto-generates a `ruse-feedback-*` deployment directory via `generate_feedback_config()` in `feedback.py`:
- Deployment name: `ruse-feedback-{preset}-{dataset}-{scope}` (e.g., `ruse-feedback-stdctrls-sum24-all`)
- Config is a copy of `ruse-controls/config.yaml` with the same behaviors list
- On teardown, `ruse-feedback-*` directories are cleaned up entirely

## Install Flow (install-sups.yaml)

Two-stage install with reboot:
1. **Stage 1**: Install system deps (Chrome, Ollama, Python, etc.) → reboot VM
2. **Stage 2**: `INSTALL_SUP.sh --{behavior}` installs brain-specific deps + creates systemd service
3. **C0 skipped**: Bare Ubuntu control, only provisioned and SSH-tested
4. **M0 skipped for stage2**: Upstream pyhuman has its own install path

Service names by brain:
- `mchp` — MCHP brain (M1-M4)
- `bu` — BrowserUse (B0-B4)
- `smol` — SmolAgents (S0-S4)

## SSH Access

```bash
# After deployment, copy SSH config
cat deployments/ruse-controls/runs/<run_id>/ssh_config_snippet.txt >> ~/.ssh/config

# SSH to VMs
ssh sup-M1-0                          # MCHP baseline
ssh sup-B2-llama-0                    # BrowserUse + llama

# Check service
ssh sup-B2-llama-0 "systemctl status bu"

# View logs
ssh sup-B2-llama-0 "tail -f /opt/ruse/deployed_sups/B2.llama/logs/*.jsonl"
```

SSH key: `~/.ssh/id_ed25519` (matches OpenStack keypair `bot-desktop`)

## Run Directory Contents

```
deployments/ruse-controls/runs/<run_id>/
├── config.yaml              # Snapshot of deployment config
├── inventory.ini            # [sups] host group
├── ssh_config_snippet.txt   # SSH access for all VMs
├── deployment_type          # Marker file containing "sup"
└── behavioral_configs/      # PHASE configs (if feedback enabled)
    ├── M2/
    │   ├── timing_profile.json
    │   ├── workflow_weights.json
    │   └── ...
    ├── B2.llama/
    │   └── ...
    └── ...
```

## PHASE Registration

`_register_phase()` in spinup.py calls `register_experiment.py` with:
- `--name ruse-controls` — experiment name
- `--snippet ssh_config_snippet.txt` — SSH config for all VMs
- `--run-id MMDDYYHHMMSS` — deployment timestamp

## Key Constraints

- **M0 is read-only** — upstream MITRE pyhuman control, do not modify
- **C0 gets no software** — bare Ubuntu control, only provisioned
- **No LLM fallback** — LLM-augmented agents fail loudly if LLM fails (experiment validity)
- **MCHP has no LLM** — pure scripted automation
- **Models run locally** — via Ollama, installed by INSTALL_SUP.sh
- **SSH agent MUST be disabled** — `SSH_AUTH_SOCK=""` / `IdentitiesOnly=yes` (too many keys cause auth timeouts)

After reading these files, provide a brief summary of the current state and any recent changes visible in the code.
