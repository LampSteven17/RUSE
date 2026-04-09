# RUSE SUP Deployment - System Context

Load critical context about RUSE SUP (Synthetic User Persona) deployment before working on it. Read all the files listed below, then summarize the current state for the user.

## Instructions

Read the following files in order to understand the RUSE SUP deployment system:

### Core RUSE SUP Deploy Files
1. `deployments/cli/commands/spinup.py` - Main RUSE SUP deployment orchestration (provision → SSH test → install → distribute configs)
2. `deployments/cli/config.py` - DeploymentConfig with `is_sup()`, SUP helpers (behaviors list, VM count, etc.)
3. `deployments/ruse-controls/config.yaml` - LEAN baseline RUSE controls config (7 VMs: C0, M0, M1, B0/S0.gemma, B0C/S0C.gemma — gemma-only post 2026-04-08 cutover, no llama, no RTX)
4. `deployments/cli/commands/feedback.py::FEEDBACK_TEMPLATE` - Lean 5-VM feedback template (M2, B2/S2.gemma, B2C/S2C.gemma)
5. `INSTALL_SUP.sh` - Per-VM install script (cloned from github by install-sups.yaml)
6. `src/common/config/model_config.py` - MODELS dict (gemma → gemma4:26b, gemmac → gemma4:e2b) + get_num_ctx() tier-aware helper
7. `src/runners/run_config.py` - SUPConfig registry; CPU variants (B*C/S*C.gemma) use model="gemmac"
8. `src/brains/browseruse/agent.py` - BrowserUse wrapper with num_ctx injection + tuned Agent settings (use_vision=False, max_clickable_elements_length=8000, llm_timeout=300, etc.)
9. `src/brains/smolagents/agent.py` + workflows/ - SmolAgents with num_ctx in LiteLLM kwargs

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

VM Topology (ruse-controls baseline — LEAN, gemma-only post 2026-04-08):
  r-{hash}-C0-0          Bare Ubuntu control (no software installed)
  r-{hash}-M0-0          Upstream MITRE pyhuman (read-only control)
  r-{hash}-M1-0          MCHP baseline (no timing, no LLM)
  r-{hash}-B0-gemma-0    BrowserUse + gemma4:26b on V100
  r-{hash}-S0-gemma-0    SmolAgents  + gemma4:26b on V100
  r-{hash}-B0C-gemma-0   BrowserUse + gemma4:e2b on CPU
  r-{hash}-S0C-gemma-0   SmolAgents  + gemma4:e2b on CPU
  (7 VMs total — dropped llama variants and RTX tier 2026-04-07/08)

Feedback variant template (5 VMs per ./deploy --ruse --feedback):
  r-{hash}-M2-0          MCHP + PHASE timing
  r-{hash}-B2-gemma-0    BrowserUse + gemma4:26b + PHASE on V100
  r-{hash}-S2-gemma-0    SmolAgents  + gemma4:26b + PHASE on V100
  r-{hash}-B2C-gemma-0   BrowserUse + gemma4:e2b + PHASE on CPU
  r-{hash}-S2C-gemma-0   SmolAgents  + gemma4:e2b + PHASE on CPU
```

## CLI Usage

```bash
# Baseline controls (7 VMs per lean template)
./deploy --ruse                            # 7 VMs, no PHASE feedback

# With PHASE feedback (all config files) — 5 VMs per lean template
./deploy --ruse --feedback                 # auto-detects most recent PHASE source for ruse
./deploy --ruse --feedback --target sum24  # specific dataset (autocompletes summer24)
./deploy --ruse --all-feedback             # alias for --feedback

# Batch deploy: ALL available PHASE feedback configs in sequence
./deploy --ruse --feedback --batch         # discovers all axes-ruse-* PHASE dirs, deploys each
./deploy --ruse --timing --batch           # batch with granular flags works too

# Granular feedback flags (RUSE-only, combine any)
./deploy --ruse --timing                   # timing_profile.json only
./deploy --ruse --timing --workflow        # timing + workflow weights
./deploy --ruse --modifiers                # behavior_modifiers.json
./deploy --ruse --sites                    # site_config.json
./deploy --ruse --prompts                  # prompt_augmentation.json
./deploy --ruse --activity                 # activity_pattern.json
./deploy --ruse --diversity                # diversity_injection.json
./deploy --ruse --variance                 # variance_injection.json

# Explicit PHASE source
./deploy --ruse --feedback --source ~/PHASE/feedback_engine/configs/some-path

# Teardown — three forms
./teardown ruse-controls-MMDDYYHHMMSS                         # specific
./teardown --ruse --feedback                                  # all RUSE feedback deploys
./teardown --all                                              # nuclear (all types)

# Shrink an existing deployment in-place (no full teardown)
./shrink ruse-controls-MMDDYYHHMMSS                           # diffs run snapshot vs config.yaml

# Audit health of all deployments
./audit                                                        # all 14 checks across all VMs
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
4. **M0 special path**: Upstream pyhuman has its own install (m0.service)

**Service naming convention:** `{behavior_lowercase}.service` with dots → underscores.
For example:
- `M1` → `m1.service`
- `M0` → `m0.service`
- `B0.gemma` → `b0_gemma.service`
- `S0C.gemma` → `s0c_gemma.service`
- `B2C.gemma` → `b2c_gemma.service`

(NOT generic `mchp` / `bu` / `smol` — that documentation was stale.)

**MCHP maintenance cron** (auto-installed by install-sups.yaml for M-brain VMs):
- `0 3 * * * systemctl restart {svc}.service` — daily restart at 03:00 UTC, mitigates
  the slow Selenium/pyautogui memory leak documented in 2026-04-07 incident
- `0 4 * * 0 /sbin/reboot` — weekly full VM reboot Sunday at 04:00 UTC

**Critical gotcha — `deployed_sups/{behavior}/src/` is a COPY not a symlink.**
Each install copies `/opt/ruse/src/` → `/opt/ruse/deployed_sups/{behavior}/src/`. So
`git pull` in `/opt/ruse` does NOT propagate to running agents — you must either:
1. Teardown + redeploy (clean), or
2. Hot-patch: `git pull` then `cp` the changed files into the per-deploy `src/`,
   then `systemctl restart {svc}.service`

**Critical gotcha — `INSTALL_SUP.sh` and `src/*` are pulled from github at install time.**
Local edits on mlserv don't affect new deploys until committed and pushed. The clone
URL is in `playbooks/install-sups.yaml::ruse_repo` (defaults to LampSteven17/RUSE.git).

**Logs aren't in journald** — service redirects stdout/stderr to
`{deploy_dir}/logs/systemd.log` and `systemd_error.log`. Use `tail` on those files,
not `journalctl -u`, to see actual brain output.

## SSH Access

The deploy automatically installs the SSH config block in `~/.ssh/config`
(via `install_ssh_config()` in `cli/ssh_config.py`) so you can ssh by VM name
without copy-pasting.

```bash
# SSH to VMs (after a deploy installs the SSH config block)
ssh r-controls040826193122-M1-0
ssh r-controls040826193122-B0-gemma-0

# Check service (note per-behavior service name)
ssh r-controls040826193122-B0-gemma-0 "systemctl status b0_gemma"

# View brain output (NOT via journalctl — service writes to a file)
ssh r-controls040826193122-B0-gemma-0 \
  "sudo tail -f /opt/ruse/deployed_sups/B0.gemma/logs/systemd.log"

# View structured agent log (jsonl event stream)
ssh r-controls040826193122-B0-gemma-0 \
  "tail -f /opt/ruse/deployed_sups/B0.gemma/logs/latest.jsonl | jq ."
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

## LLM Models (post 2026-04-08 cutover)

| Alias | Ollama tag | Used for | Why |
|---|---|---|---|
| `gemma` | **`gemma4:26b`** | V100 32GB | MoE: 25.2B total / 3.8B active per token. Fits 89% VRAM, ~10 tok/s on real DOM prompts. Best capability-per-speed on V100. |
| `gemmac` | **`gemma4:e2b`** | CPU only | Edge-optimized 2.3B effective params. Works well for SmolAgents on CPU (~7 tok/s). Times out on BrowserUse on CPU due to large prompts (documented limitation). |
| `llama` | `llama3.1:8b` | (legacy) | Kept in MODELS dict for back-compat, no longer used in any deploy template. |

**Empirical reports** (committed under `docs/`):
- `docs/gemma_v100_benchmark.md` — raw benchmark data (6 models × 3 runs each)
  on a V100-PCIE-32GB. Pull time, disk size, peak VRAM, generation tok/s,
  prompt eval tok/s, run-to-run consistency.
- `docs/gemma_model_selection.md` — presentation-ready writeup of the same
  data with embedded matplotlib charts (`docs/images/*.png`), derived
  metrics (capability-throughput score, speed-per-parameter), and the
  rationale for picking gemma4:26b (V100) and gemma4:e2b (CPU).

Aliases live in two places that **must agree**:
- `INSTALL_SUP.sh::MODEL_NAMES` (install-time pull on the VM)
- `src/common/config/model_config.py::MODELS` (runtime resolution by `get_model()`)

The **runner argparse choices** (`run_browseruse.py`, `run_smolagents.py`, `run_mchp.py`)
also have a hardcoded `choices=[...]` list. When adding a new alias, ALL THREE places
must be updated (model_config, INSTALL_SUP, runner argparse) — see the gemmac fix from
2026-04-08 (commits e28759a + 15b68aa).

## Tier-aware num_ctx

`get_num_ctx()` in `src/common/config/model_config.py` detects nvidia-smi at runtime:
- **GPU detected** → `num_ctx=32768` (V100 32GB has VRAM headroom)
- **CPU only** → `num_ctx=16384` (fits in 28GB system RAM with KV cache)
- **Override**: `SUP_NUM_CTX` env var

Both brains call `get_num_ctx()` at construction time:
- **BrowserUse** (`brains/browseruse/agent.py`) — injected into Ollama client `chat()`
  options dict via the `create_logged_chat_ollama` wrapper. Must use
  `kwargs.get('options') or {}` (not `setdefault`) because browser_use sometimes passes
  `options=None` explicitly — see the 2026-04-08 NoneType crash incident.
- **SmolAgents** (`brains/smolagents/agent.py` + 3 workflow files) — passed as
  `num_ctx` in the `LiteLLMModel` constructor kwargs.

Why this matters: Ollama's default `num_ctx` is **4096 on CPU**, which silently
truncates BrowserUse's full-DOM prompts and breaks workflows. With explicit num_ctx,
the model receives the full context.

## BrowserUse Agent tuning (2026-04-08)

`brains/browseruse/agent.py` constructs the `Agent` with non-default settings to cap
token usage. These apply uniformly across V100 and CPU:

```python
Agent(
    task=full_prompt,
    llm=self._get_llm(),
    browser_session=browser_session,
    use_vision=False,                  # gemma is text-only — screenshots are waste
    use_judge=False,                   # skip extra LLM eval per step
    max_clickable_elements_length=8000,  # cap DOM dump (~2K tokens vs 40K default)
    max_history_items=5,               # bounded conversation memory
    include_attributes=[               # strip data-*/style/onclick noise
        "id", "class", "name", "type", "value",
        "placeholder", "aria-label", "role", "href",
        "title", "alt",
    ],
    llm_timeout=300,                   # CPU LLM calls can take 2-3 min
)
```

**Without these settings**, BrowserUse on CPU was sending 6-23K-token prompts to
gemma4:e2b at 0.5 tok/s and hitting browser_use's hardcoded 75-second LLM timeout
on every step. With them, V100 BrowserUse is fast (~8 tok/s on 8K-token prompts) and
CPU BrowserUse can at least make forward progress (slower, but no longer crashes).

## Key Constraints

- **M0 is read-only** — upstream MITRE pyhuman control, do not modify
- **C0 gets no software** — bare Ubuntu control, only provisioned
- **No LLM fallback** — LLM-augmented agents fail loudly if LLM fails (experiment validity)
- **MCHP has no LLM** — pure scripted automation
- **Models run locally** — via Ollama, installed by INSTALL_SUP.sh
- **SSH agent MUST be disabled** — `SSH_AUTH_SOCK=""` / `IdentitiesOnly=yes` (too many keys cause auth timeouts)
- **MCHP has a slow Selenium leak** — `m1.service` (and other M-brain services) hits
  memory pressure after ~4 days. Mitigated by daily cron restart at 03:00 UTC + weekly
  full reboot Sunday 04:00 UTC (auto-installed by `install-sups.yaml`).
- **Per-deploy `src/` is a copy not a symlink** — see "Critical gotcha" in the install
  flow section above. Hot-patches need `cp` into the per-deploy directory.
- **Stuff in `src/*` and `INSTALL_SUP.sh` must be in github** — these files are pulled
  via `git clone` during install. Local edits on mlserv don't reach VMs without a
  `git push` first.

After reading these files, provide a brief summary of the current state and any recent changes visible in the code.
