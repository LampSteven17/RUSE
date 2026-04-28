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
10. `src/common/network/whois.py`, `src/common/network/downloader.py` - Shared TCP/43 + HTTPS-stream helpers used by all 3 brains' feedback-only whois_lookup / download_files workflows
11. `src/brains/{smolagents,browseruse,mchp}/workflows/` - Per-brain workflow registries; `loader.py` gates `whois_lookup` + `download_files` on `is_feedback` (presence of behavior.json)

### Ansible Playbooks (SUP-specific)
4. `deployments/playbooks/provision-vms.yaml` - Create OpenStack VMs, wait ACTIVE, get IPs, write inventory + SSH config
5. `deployments/playbooks/install-sups.yaml` - SSH to VMs, install deps, INSTALL_SUP.sh stage1 → reboot → stage2 (skips C0)
6. `deployments/playbooks/distribute-behavior-configs.yaml` - Copy PHASE behavioral configs to VMs (baseline key derivation)

### PHASE Feedback Integration
7. `deployments/cli/commands/feedback.py` - Feedback source resolution, config generation, per-config-file CLI flags
8. `deployments/lib/register_experiment.py` - PHASE experiments.json registration

### Deployment Configs (feedback variants, auto-generated per-dataset)
9. `deployments/ruse-feedback-stdctrls-{sum24,fall24,spr25,axall,vt1g,vt50g}-all/config.yaml` - Feedback deploys created by `generate_feedback_config()`, one per PHASE dataset, not committed to git

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

# With PHASE feedback — batch is the default when no --target/--source given.
./deploy --ruse --feedback                 # BATCH: all discovered PHASE sources
./deploy --ruse --feedback --target sum24  # single dataset (autocompletes summer24)
./deploy --ruse --all-feedback             # alias for --feedback (still batch default)

# Granular feedback flags (RUSE-only, combine any) — also batch-by-default
./deploy --ruse --timing                   # BATCH: timing-only for all datasets
./deploy --ruse --timing --target sum24    # single dataset, timing-only
./deploy --ruse --timing --workflow        # BATCH: timing + workflow weights
./deploy --ruse --modifiers                # BATCH: behavior_modifiers.json
./deploy --ruse --sites                    # BATCH: site_config.json
./deploy --ruse --prompts                  # BATCH: prompt_augmentation.json
./deploy --ruse --activity                 # BATCH: activity_pattern.json
./deploy --ruse --diversity                # BATCH: diversity_injection.json
./deploy --ruse --variance                 # BATCH: variance_injection.json

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

Consolidated to a single `behavior.json` per SUP on 2026-04-16 (previously
8 separate JSONs). The distribute playbook (`distribute-behavior-configs.yaml`)
handles:
1. Deriving baseline config key from versioned key: `B2C.gemma → B0C.gemma`, `M2 → M1`
2. Source resolution: `{feedback_source}/{behavior_dir}/{baseline_config}/behavior.json`
   (e.g. `.../B.gemma/B0.gemma/behavior.json`)
3. Validates parseability with `python3 -m json.tool` on localhost before
   copying — a corrupt behavior.json aborts the deploy rather than shipping
   to the VM
4. Copying to `/opt/ruse/deployed_sups/{key}/behavioral_configurations/behavior.json`
5. Only runs for V2+ configs (V0/V1 are baselines with no behavioral configs)

The `*.json` wildcard used by the playbook's `find` task means legacy
multi-file sources would still copy, but PHASE emits one file.

### behavior.json schema (emitted by PHASE)

```json
{
  "_metadata": {"source": "...", "sup_config": "B0.gemma",
                "dataset": "axes-summer24", "current_score": 0.4,
                "target_score": 0.6, "generated_at": "..."},
  "timing": {
    "hourly_distribution": [24 floats summing to 1],
    "activity_probability_per_hour": [24 floats 0..1],
    "long_idle_probability": 0.05,
    "long_idle_duration_minutes": {"min": 30, "max": 120},
    "burst_percentiles": {
      "connections_per_burst":  {"5":,"25":,"50":,"75":,"95":,"max":},
      "idle_gap_minutes":       {"5":,"25":,"50":,"75":,"95":},
      "burst_duration_minutes": {"5":,"25":,"50":,"75":,"95":}
    },
    "variance": {
      "cluster_size_sigma": 0.5, "idle_gap_sigma": 0.5,
      "hourly_std_targets": {
        "volume":   {"hourly_std_target": [24 floats]},
        "duration": {"hourly_std_target": [24 floats]}
      }
    }
  },
  "content": {
    "workflow_weights": {"BrowseWeb": 0.3, "GoogleSearch": 0.22, ...},
    "site_categories":  {"lightweight": 0.55, "medium": 0.3, "heavy": 0.15}
  },
  "behavior": {
    "page_dwell":             {"min_seconds": 2, "max_seconds": 43},
    "navigation_clicks":      {"min": 10, "max": 30},
    "keep_alive_probability": 0.8,
    "max_steps":              10
  },
  "diversity": {
    "background_services": {
      "dns_per_hour":       [24 ints],
      "http_head_per_hour": [24 ints],
      "ntp_checks_per_day": 4
    },
    "workflow_rotation": {
      "max_consecutive_same": 2,
      "min_distinct_per_cluster": 3
    }
  },
  "prompt_content": "... optional free-form prompt guidance ..."
}
```

The loader (`src/common/behavioral_config.py::load_behavioral_config`)
slices these 5 sections into the 8 dataclass fields with no key
renaming or re-nesting — every downstream reader matches the shape
PHASE emits verbatim.

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
└── deployment_type          # Marker file containing "sup"
```

Feedback configs are NOT copied into the run dir. The distribute
playbook reads directly from `{behavior_source}` on localhost (the
PHASE feedback dir) and ships `behavior.json` straight to each VM
at `/opt/ruse/deployed_sups/{key}/behavioral_configurations/`.

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

## PHASE Feedback Runtime Consumption

RUSE runtime reads PHASE-generated feedback from a single `behavior.json`
per SUP (consolidated 2026-04-16 from 8 separate JSONs). Docs in
`docs/feedback-consumption-plan.md` and `docs/feedback-field-audit.md`
describe the pre-consolidation schema and are historical — the live
field paths are below.

### Section → BehavioralConfig field → consumer

| behavior.json path | BehavioralConfig field | Consumer |
|---|---|---|
| `timing.hourly_distribution` (flat `[24]`) | `timing_profile` | `CalibratedTimingConfig.hourly_fractions` |
| `timing.burst_percentiles.*` (flat dicts) | `timing_profile` | `CalibratedTimingConfig.{burst_duration,idle_gap,connections_per_burst}` |
| `timing.variance.cluster_size_sigma` | `variance_injection` | `get_cluster_size()` scalar lognormal noise |
| `timing.variance.idle_gap_sigma` | `variance_injection` | `get_cluster_delay()` scalar lognormal noise |
| `timing.variance.hourly_std_targets.{volume,duration}.hourly_std_target` | `variance_injection` | D1 per-hour sigma arrays in `_init_variance_targets` |
| `timing.activity_probability_per_hour` | `activity_pattern` | `should_skip_hour()` hourly rolldown |
| `timing.long_idle_probability` + `long_idle_duration_minutes` | `activity_pattern` | `should_take_long_idle()` |
| `content.workflow_weights` | `workflow_weights` | `build_workflow_weights()` for `random.choices()` |
| `content.site_categories` | `site_config` | SmolAgents BrowseWebWorkflow filters task pool by category (W3 wired 2026-04-27) |
| `content.download_url_pool` | `download_url_pool` | Smol/BU `DownloadFiles` workflow LLM picker (feedback-only) — falls back to `common.network.downloader.FALLBACK_URLS` when missing/empty |
| `content.whois_domain_pool` | `whois_domain_pool` | Smol/BU/MCHP `WhoisLookup` workflow (feedback-only) — falls back to `common.network.whois.FALLBACK_DOMAINS` when missing/empty |
| `content.download_size_pref` | (informational) | Schema marks as informational only — RUSE intentionally ignores |
| `behavior.page_dwell` / `navigation_clicks` | `behavior_modifiers` | MCHP `BrowseWeb.{min,max}_sleep_time`, `max_navigation_clicks`; BU `Agent(register_new_step_callback=...)` per-step uniform delay (wired 2026-04-27) |
| `behavior.keep_alive_probability` (flat) | `behavior_modifiers` | G2: MCHP `BrowseWeb.keep_alive_probability` |
| `behavior.max_steps` | `behavior_modifiers` | BrowserUse / SmolAgents per-workflow `max_steps` |
| `diversity.background_services.dns_per_hour` / `http_head_per_hour` / `ntp_checks_per_day` | `diversity_injection` | `BackgroundServiceGenerator` (D4) |
| `diversity.workflow_rotation.{max_consecutive_same,min_distinct_per_cluster}` | `diversity_injection` | D2 rotation enforcement in `emulation_loop` |
| `prompt_content` (top-level string) | `prompt_augmentation.prompt_content` | G1: BrowserUse + SmolAgents prompt prepend |

**G3 detection_hours was removed** — PHASE no longer emits it and
`should_skip_hour` no longer reads it. Activity suppression is now
driven solely by `activity_probability_per_hour`.

### Loader contract (2026-04-16)
- Reads `{behavioral_configurations}/behavior.json`.
- File missing → empty `BehavioralConfig` (baseline path, V0/V1).
- File present but malformed JSON → `JSONDecodeError` propagates
  (service crash-loops, audit NRestarts probe flags it).
- No translation, no re-keying — sections populate the 8 dataclass
  fields verbatim and downstream readers were updated to match.

### Fail-loud semantics + ablation gating (2026-04-17)
Every feature prints `[WARNING] {tag} DISABLED — {reason}` to
`systemd.log` when it can't activate, UNLESS PHASE marked the deploy
as ablation-gated via `_metadata.ablation_gate`. Then the tag
downgrades to `[INFO] ... (ablation-gated)` — a deliberate PHASE
omission, not a bug.

**When ablation-gated**: PHASE's feedback engine ran per-feature
ablation against the target detection model and found
|Δscore| < 0.10 for the section's knobs. Omitting it avoids setting
knobs the model ignores. For summer24 and vt-fall22, `timing` and
`behavior` are gated off entirely because those models key on
topology features (see topology-mimicry below).

Baselines: runtime short-circuits on `fc.is_empty()` before reaching
warning paths — silence is correct.
Feedback deploys: all-INFO expected when ablation-gated; all-silence
expected when PHASE ships complete sections.

`BehavioralConfig.is_ablation_gated()` reads the gate metadata.
Warning emitters in:
- `src/common/emulation_loop.py::_reload_behavioral_config` (D2/D4/G1/W4)
- `src/common/timing/phase_timing.py::CalibratedTiming` (D1/D3/G5, via
  `ablation_gated=True` constructor kwarg — must be threaded through
  the fallback path at line ~175 too, else warnings leak at WARNING
  level)
- `src/brains/mchp/agent.py::_apply_brain_specific_config` (B1/B2/G2)

`W3 site_config` is now wired (2026-04-27) — `SmolAgentLoop._apply_brain_specific_config`
propagates `content.site_categories` to `BrowseWebWorkflow.site_weights`.
The legacy `[INFO] W3 site_config UNUSED` line was removed in the same change.
BrowserUse + MCHP do not consume `site_config` yet; if wired later, place
the `[INFO]` guard in their respective `_apply_brain_specific_config`.

### Distribute playbook JSON validity gate (2026-04-16)
Before copying to the VM, each matched `behavior.json` is parsed with
`python3 -m json.tool` on localhost. A corrupt file aborts the deploy
with the source path in the error — it never reaches any VM.

## Workflow Set & Feedback-Only Workflows (2026-04-28)

Per-brain workflow registries gated on the presence of `behavior.json`
in `behavioral_configurations/` at workflow-load time. Loop's
`_select_workflow()` picks workflows from `self.workflows` per
`content.workflow_weights` — same code for all 3 brains. The LIST of
loaded workflows differs by deploy type.

| Brain | Controls (no behavior.json) | Feedback (behavior.json present) |
|---|---|---|
| **Smol** | BrowseWeb, WebSearch, BrowseYouTube (3) | + WhoisLookup, DownloadFiles (5 total) |
| **BU** | BrowseWeb, WebSearch, BrowseYouTube (3) | + WhoisLookup, DownloadFiles (5 total) |
| **MCHP** | 8 baseline workflows (download_files excluded) | 9 baseline + WhoisLookup + DownloadFiles |

`FEEDBACK_ONLY_WORKFLOWS` set lives in `brains/mchp/agent.py:33`. Smol +
BU loaders use an `is_feedback: bool = False` parameter on `load_workflows()`.
All 3 brain loops (`SmolAgentLoop`, `BrowserUseLoop`, `MCHPAgent`)
implement `_is_feedback_deploy()` checking
`Path(self._behavior_config_dir, "behavior.json").exists()`.

### Workflow internals (whois_lookup + download_files)

Architecture per brain:

  **Smol** — dedicated workflow. ONE `LiteLLMModel` picker call chooses
  domain/URL from PHASE-supplied pool; deterministic helper does TCP/43
  socket or `requests.get` stream. Bypasses CodeAgent's tool-decision
  loop; LLM picks content only.

  **BU** — dedicated workflow that BYPASSES `browser_use.Agent` entirely.
  ONE Ollama HTTP API picker call (loopback `127.0.0.1:11434`, invisible
  to Zeek); same deterministic helpers. Browser is never invoked.

  **MCHP** — `random.choice(pool)` no-LLM picker; same helpers.
  `WhoisLookup` is new (`mchp/app/workflows/whois_lookup.py`);
  `download_files.py` is the existing scripted xkcd/wiki/NIST workflow,
  now gated as feedback-only.

### Tool palette of existing 3 LLM workflows (Smol)

`BrowseWebWorkflow` / `WebSearchWorkflow` / `BrowseYouTubeWorkflow`
CodeAgent tool list = `[DuckDuckGoSearchTool(), VisitWebpageTool()]`
only. `WhoisLookupTool` and `DownloadFileTool` classes were **deleted**
(2026-04-28) — entire `src/brains/smolagents/tools/` directory removed.
The new whois/download workflows do not use CodeAgent at all; they
import the helpers directly from `common.network.{whois,downloader}`.

### Loud-failure semantics

LLM picker exceptions and off-pool selections print to stderr AND log
via `AgentLogger.warning`:
  `[ERROR] {Workflow} LLM picker failed: {ExcType}: {msg}`
  `[WARNING] {Workflow} LLM picked X not in pool — falling back...`
Audit's WARN_COUNT probe will surface these in the `Warn` column. Real
TCP/43 / HTTP failures return error strings from the helpers — workflow
logs `step_error` and continues without crashing the loop.

### PHASE schema knobs (consumed today)

```json
{
  "content": {
    "workflow_weights":   { ... },          // existing
    "site_categories":    { ... },          // existing (W3 wired 2026-04-27)
    "download_url_pool":  ["https://...", ...],  // NEW per-target
    "whois_domain_pool":  ["wikipedia.org", ...] // NEW per-target
  }
}
```

Empty list or missing key → `BehavioralConfig.{download_url_pool,
whois_domain_pool}` = None → workflow falls back to module-level
`FALLBACK_*` lists. Workflows propagate from fc to their per-instance
attributes via each brain loop's `_apply_brain_specific_config`.

### Schema fields RUSE intentionally ignores

- `content.download_size_pref` — schema marks as informational metadata only
- `content._controllability` — schema metadata

## Stage 3 Fail-Loud Assertions (2026-04-14)

`install-sups.yaml` now has explicit assertions (was previously
swallowing failures):
- **S3** — `stage2_result.rc != 0` → fail with stderr.
- **S4** — `systemctl is-active {behavior}.service` must return
  `active` (was using `|| true` which always returned rc=0).
- **S5** — `crontab -l | grep -cE 'mchp-(daily|weekly)'` must show
  both entries for M-series brains.

`spinup.py` now aborts if `_test_ssh_all()` finds fewer than 90%
reachable VMs (previously a warning and install continued against
unreachable hosts).

**M0 S4 exemption (2026-04-17)** — M0 is exempt from the S4 is-active
assertion. Upstream pyhuman crash-loops on Linux by design
(os.startfile is Windows-only, hardcoded `/root/Downloads/` paths,
flaky Chrome session). Without the exemption S4 races the
activating→active→fail→restart cycle and fails depending on when
the check fires. Mirrors audit.py's `EXPECTED (M0 upstream crashes
on Linux)` status.

**Install recap parsing (2026-04-17)** — if `install-sups.yaml`
exits rc!=0, `spinup.py::_parse_ansible_recap(log_path)` parses
PLAY RECAP to report which hosts failed which assertion, then
aborts with `return 1` before distributing configs or registering
in PHASE. Previously spinup.py continued and reported DONE despite
1+ failed VMs.

**Transient-flake retries** — `Update apt cache`, `Install prerequisites`,
and `Clone RUSE repo` have `retries: 3 delay: 30/15 until: succeeded`.
Survives single-VM apt-mirror and GitHub rate-limit flakes without
silencing real failures.

See `/deploy-context` for the full catalog of fail-loud assertions
across the deploy system.

## Topology Mimicry — neighborhood sidecar (2026-04-17, phase A)

For summer24 and vt-fall22 target models, PHASE ablation showed
behavioral knobs produce |Δscore| < 0.10 but five non-behavioral
topology features move the score by 0.27-1.00:

  local_orig, id.orig_p, id.resp_p, local_resp, conn_state

The model is detecting sandbox-VM environment, not behavior —
sandbox SUPs receive no inbound LAN traffic (no SMB polling,
LDAP, WSUS, IPMI, mDNS, printer polls) so every Zeek row has
`local_orig=1`, ephemeral `id.orig_p`, `conn_state=SF` dominant.
Workstations have the opposite distribution.

### Architecture (components 1 + 3 shipped)
- **1 neighborhood VM per feedback deploy** (FEEDBACK ONLY —
  controls get no neighborhood). Name:
  `r-{dep_id}-neighborhood-0`, flavor `v1.small` (1 vCPU / 2 GB),
  same `bot-desktop` keypair as SUPs.
- **Data-driven daemon** `common.network.neighborhood_traffic`
  reads `/etc/ruse-neighborhood/sups.json` and synthesizes real
  TCP/UDP probes at each SUP IP on the subnet. Strictly data-
  driven: empty/zero rates → zero probes. Daemon stays alive but
  idle (heartbeat only). This is what lets controls safely carry
  the same code without emitting traffic.
- **10 probe types** in `src/common/network/probes.py`:
  `inbound_smb_per_hour`, `inbound_ldap_per_hour`, `inbound_wsus_per_hour`,
  `inbound_ntp_receive_per_hour`, `inbound_printer_per_hour`,
  `inbound_ipmi_per_hour`, `inbound_winrm_per_hour`,
  `inbound_mdns_per_hour`, `inbound_ssdp_per_hour`,
  `inbound_scan_per_hour`. Produce mixed conn_state
  (SF / S0 / REJ / RSTO / unidir) on Zeek rows from the SUP.
- **PHASE contract (component 3)** — writes
  `diversity.topology_mimicry.inbound_*_per_hour` into each SUP's
  `behavior.json`. RUSE's `BehavioralConfig.topology_mimicry()`
  helper reads verbatim via `diversity_injection`.

### Deploy flow
`spinup.py` phase 2c runs AFTER `distribute-behavior-configs.yaml`:
1. `_synthesize_neighborhood_config(behavior_source, inventory_path,
   run_dir)` reads each SUP's `behavior.json` and collects
   `topology_mimicry` rates. Writes `neighborhood-sups.json` if any
   rate is non-zero; else returns None (→ skip sidecar entirely).
2. `_provision_and_install_neighborhood(...)` creates the VM via
   OpenStack CLI, writes `neighborhood-inventory.ini`, runs
   `install-neighborhood.yaml` (asserts `ruse-neighborhood`
   systemd service active + NRestarts ≤ 5).

Fail-loud: if any step fails, spinup.py aborts before PHASE
registration — a feedback deploy without the topology layer
would be experimentally worse than no deploy at all.

### Teardown
The neighborhood VM is named `r-{dep_id}-neighborhood-0` so the
existing `r-` prefix sweep in `teardown.yaml` / `teardown-all.yaml`
deletes it automatically. No special handling needed.

### Audit
`./audit` currently probes SUP VMs only. Neighborhood VMs are
excluded from the orphan check but their service status is NOT
audited by the main `./audit` command yet. Ad-hoc probe script
at `/tmp/audit_ghosts.py` has the right template; promote to
`./audit --neighborhoods` in phase B.

### Observed performance (2026-04-17 overnight)
7 neighborhood sidecars running 12+ hours: all `active`, 0
restarts, 2800-3300 probes emitted each. At 360/hr target that's
~235/hr observed (~65%) — scheduler's jitter-sleep accumulation
burns ~35% of each 60s tick. Not broken; rate still produces the
topology signal. Can tune later.

### Deferred (phase B)
- **Component 2** — SUP listening services (sshd/node-exporter/
  cockpit/http) for `id.orig_p` diversity.
- **Component 4** — subnet chatter (mDNS/SSDP/NetBIOS multicast)
  from SUPs for `local_orig=0` rows.
- **Phase C** — PHASE re-runs knob ablation with the topology
  layer live. Target: five topology features' max|Δ| drops below
  0.10 threshold.

Full design: `docs/topology-mimicry.md`.

After reading these files, provide a brief summary of the current state and any recent changes visible in the code.
