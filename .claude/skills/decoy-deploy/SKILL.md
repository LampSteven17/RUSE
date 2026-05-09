---
name: decoy-deploy
description: DECOY SUP deployment — running ./deploy --decoy [scope flags], 5-phase spinup, behavior.json plumbing, audit semantics, hot-patch path. Inputs deployments/decoy-controls/config.yaml + /mnt/AXES2U1/feedback/decoy-controls/{controls,dataset}/. Outputs deployments/decoy-{controls,feedback-...}/runs/{run_id}/. Does NOT cover RAMPART AD enterprise (see /rampart-deploy) or GHOSTS NPC clients (see /ghosts-deploy). Cross-type CLI shape, fail-loud contract, and SSH key matrix live in CLAUDE.md.
type: skill
---

# decoy-deploy

DECOY = synthetic user persona VMs that simulate human computer use. Each
SUP is a brain (MCHP / BrowserUse / SmolAgents) optionally driven by a
PHASE-tuned `behavior.json`. Brain naming scheme `[Brain][Version].[Model]`
is in CLAUDE.md.

| | |
|---|---|
| Inputs | `deployments/decoy-controls/config.yaml`, `/mnt/AXES2U1/feedback/decoy-controls/controls/{behavior}/{sup}/behavior.json` (baseline), `/mnt/AXES2U1/feedback/decoy-controls/{dataset}/{behavior}/{sup}/behavior.json` (feedback), `INSTALL_SUP.sh` + `decoys/` cloned from github at install time |
| Outputs | `deployments/decoy-{controls,feedback-{preset}-{dataset}-{scope}}/runs/{run_id}/` (inventory.ini, ssh_config_snippet.txt, deployment_type), per-VM `/opt/ruse/deployed_sups/{key}/` |
| Manifest | `manifest.json` in PHASE source; loaded via `core/feedback.py::load_manifest`, validated against deploy type via `validate_manifest_target` |
| Upstream | PHASE feedback engine (`feedback_engine.baseline` writes `controls/`; `feedback_engine.decoy_generator` writes `{dataset}/`) |
| Downstream | PHASE Zeek pipeline (`PHASE.py --decoy`), reads `experiments.json` for active deploys |
| Narrow exceptions | C0 (no install — bare Ubuntu, only provisioned + SSH-tested); M0 (upstream MITRE pyhuman, crash-loops on Linux by design — `os.startfile()` Windows-only) |

## Topology

`decoy-controls/config.yaml` provisions 7 VMs (gemma-only, V100 + CPU pairs):

```
d-{dep_id}-C0-0          Bare Ubuntu control (no software)
d-{dep_id}-M0-0          Upstream MITRE pyhuman (read-only control)
d-{dep_id}-M1-0          MCHP baseline (no timing, no LLM)
d-{dep_id}-B0-gemma-0    BrowserUse + gemma4:26b on V100
d-{dep_id}-S0-gemma-0    SmolAgents  + gemma4:26b on V100
d-{dep_id}-B0C-gemma-0   BrowserUse + gemma4:e2b on CPU
d-{dep_id}-S0C-gemma-0   SmolAgents  + gemma4:e2b on CPU
```

Feedback template (5 VMs per `./deploy --decoy --feedback`):

```
d-{dep_id}-M2-0          MCHP + PHASE timing
d-{dep_id}-B2-gemma-0    BrowserUse + gemma + PHASE on V100
d-{dep_id}-S2-gemma-0    SmolAgents  + gemma + PHASE on V100
d-{dep_id}-B2C-gemma-0   BrowserUse + gemma + PHASE on CPU
d-{dep_id}-S2C-gemma-0   SmolAgents  + gemma + PHASE on CPU
```

Plus `d-{dep_id}-neighborhood-0` sidecar (feedback only, when any
`topology_mimicry` rate is non-zero).

`dep_id` = `{name_no_hyphens}{run_id}` where `run_id` is `MMDDYYHHmmss`.

## CLI scope flags

```bash
./deploy --decoy                           # controls + ALL feedback datasets (default)
./deploy --decoy --controls                # controls only
./deploy --decoy --feedback                # all feedback (no controls)
./deploy --decoy --feedback --target sum24 # single dataset
./deploy --decoy --feedback --source /path # explicit PHASE source
./deploy --decoy --controls --target sum24 # controls + single feedback
```

Granular per-config-file flags (`--timing`, `--workflow`, `--modifiers`,
`--sites`, `--prompts`, `--activity`, `--diversity`, `--variance`,
`--all-feedback`) were removed when PHASE consolidated to a single
`behavior.json` per SUP. There's no longer a per-file filter to apply.

Batch is the default when `--feedback` is given without a single-target
selector. CLI scans `/mnt/AXES2U1/feedback/decoy-controls/`, prompts
confirmation, deploys each in sequence.

Dataset target aliases (`core/feedback.py::DATASET_TARGETS`): `sum24` →
`summer24`, `spr25` → `spring25`, `vt1g` → `vt-fall22-1gb`, `vt50g` →
`vt-fall22-50gb`, `cptc8` → `cptc8-23`, `axall` → `axes-all`, `2025` →
`axes-2025`. Resolution is substring against
`/mnt/AXES2U1/feedback/decoy-controls/`.

## Spinup phases (`decoy/spinup.py`)

0. `_validate_behavior_source` — walk every non-C0/M0 SUP's expected
   `{behavior_dir}/{baseline_config}/behavior.json`, abort with
   missing-path list before any VM work
1. Provision VMs (`provision-vms.yaml`) — abort if < 90% reach ACTIVE
2. SSH connectivity test (Python `concurrent.futures`, 20 workers) —
   abort if < 90% reachable
3. Install (`install-sups.yaml`) — stage1 system deps → reboot (exit 100)
   → stage2 brain deps + systemd service. C0 skipped. M0 special path.
4. Distribute behavior configs (`distribute-behavior-configs.yaml`) —
   abort spinup if rc != 0
5. Neighborhood sidecar (feedback only, gated on non-zero
   `topology_mimicry`)
6. SSH config install (`install_ssh_config()` writes block to
   `~/.ssh/config`) + PHASE register (return False → abort with
   `return 1`)

## Service naming

`{behavior_lowercase}.service` with dots → underscores:

- `M1` → `m1.service`
- `B0.gemma` → `b0_gemma.service`
- `S2C.gemma` → `s2c_gemma.service`

Per-behavior service, NOT generic `mchp` / `bu` / `smol`. Logs redirect
to `/opt/ruse/deployed_sups/{key}/logs/systemd.log` and
`systemd_error.log` — use `tail`, not `journalctl -u`.

MCHP maintenance cron (auto-installed for M-brain VMs to mitigate
Selenium/pyautogui memleak):

- `0 3 * * * systemctl restart {svc}.service` — daily restart at 03:00 UTC
- `0 4 * * 0 /sbin/reboot` — weekly reboot Sunday 04:00 UTC

## SSH access

```bash
ssh d-controls050826193122-M1-0
ssh d-controls050826193122-B0-gemma-0 "systemctl status b0_gemma"

# Brain output (NOT journalctl)
ssh d-controls050826193122-B0-gemma-0 \
  "sudo tail -f /opt/ruse/deployed_sups/B0.gemma/logs/systemd.log"

# Structured agent log
ssh d-controls050826193122-B0-gemma-0 \
  "tail -f /opt/ruse/deployed_sups/B0.gemma/logs/latest.jsonl | jq ."
```

## behavior.json schema (PHASE-emitted)

`BehavioralConfig.load` slices the file into 9 dataclass fields, no key
renaming. See `decoys/common/behavioral_config.py` for the loader; consumers
match the shape PHASE emits verbatim.

```json
{
  "_metadata": {"source", "sup_config", "dataset", "current_score", "target_score",
                "generated_at", "mode", "ablation_gate", "timezone": "UTC"},
  "timing": {
    "hourly_distribution": [24 floats],
    "activity_probability_per_hour": [24 floats 0..1],
    "long_idle_probability": 0.05,
    "long_idle_duration_minutes": {"min": 30, "max": 120},
    "burst_percentiles": {
      "connections_per_burst":  {"5","25","50","75","95","max"},
      "idle_gap_minutes":       {"5","25","50","75","95"},
      "burst_duration_minutes": {"5","25","50","75","95"}
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
    "site_categories":  {"lightweight": 0.55, "medium": 0.3, "heavy": 0.15},
    "download_url_pool": ["https://...", ...],
    "whois_domain_pool": ["wikipedia.org", ...]
  },
  "behavior": {
    "page_dwell": {"min_seconds": 2, "max_seconds": 43},
    "navigation_clicks": {"min": 10, "max": 30},
    "keep_alive_probability": 0.8,
    "max_steps": 10,
    "enable_whois": true,
    "enable_download": true
  },
  "diversity": {
    "background_services": {
      "dns_per_hour": [24 ints], "http_head_per_hour": [24 ints],
      "ntp_checks_per_day": 4
    },
    "workflow_rotation": {"max_consecutive_same": 2, "min_distinct_per_cluster": 3},
    "topology_mimicry": {"inbound_smb_per_hour": ..., ...}
  },
  "prompt_content": "... optional free-form prompt guidance ..."
}
```

`_metadata.mode` ∈ `{baseline, dumb_baseline, None}`. Baseline mode emits
a degenerate timing schema; `emulation_loop._reload_behavioral_config`
detects via `fc.mode in {"baseline", "dumb_baseline"}` OR by schema sniff
(`burst_percentiles.burst_duration_minutes is not a dict`) and skips
CalibratedTiming/variance/activity setup. Workflow gating + content pools
still honored.

## Per-flag workflow gating

`behavior.behavior.{enable_whois, enable_download}` controls workflow
registration. PHASE `feedback_engine.baseline` emits both `false`
(controls = single-workflow degenerate mode); feedback proper emits both
`true` (or omits, defaulting `true`).

| Brain | Both flags False | Both flags True |
|---|---|---|
| Smol | BrowseWeb, WebSearch, BrowseYouTube (3) | + WhoisLookup, DownloadFiles (5) |
| BU | BrowseWeb, WebSearch, BrowseYouTube (3) | + WhoisLookup, DownloadFiles (5) |
| MCHP | 7 baseline (no whois, no download) | + WhoisLookup, DownloadFiles |

Mechanism:

- Smol/BU loaders — `load_workflows(enable_whois=, enable_download=)`
- MCHP — `BEHAVIOR_GATED_WORKFLOWS = {'download_files.py': 'enable_download', 'whois_lookup.py': 'enable_whois'}` map; `_load_workflows` skips files whose flag is False
- All 3 brains read flags via `common.behavioral_config.load_workflow_gates(config_dir)`

WhoisLookup + DownloadFiles bypass the Agent's tool-decision loop:

- Smol — dedicated workflow, ONE `LiteLLMModel` picker → domain/URL from PHASE pool
- BU — dedicated workflow, ONE Ollama HTTP picker (loopback `127.0.0.1:11434`, invisible to Zeek), browser never invoked
- MCHP — `random.choice(pool)` no-LLM picker

Helpers in `decoys/common/network/`: `whois.py`, `downloader.py`,
`probes.py`, `neighborhood_traffic.py`. Brain workflow files import
directly — no cross-brain imports.

## Distribute flow (`distribute-behavior-configs.yaml`)

1. Derive baseline config key from versioned key: `B2C.gemma → B0C.gemma`,
   `M2 → M1`
2. Resolve `{feedback_source}/{behavior_dir}/{baseline_config}/behavior.json`
3. `python3 -m json.tool` validate on localhost — corrupt aborts before
   shipping
4. Copy to `/opt/ruse/deployed_sups/{key}/behavioral_configurations/behavior.json`
5. Assert file on disk after copy

Runs for ALL non-C0/M0 SUPs — controls' `decoy-controls/config.yaml`
points `behavior_source` at `/mnt/AXES2U1/feedback/decoy-controls/controls`
so baselines flow through the same path as feedback.

The `controls/` slot is excluded from feedback dataset auto-discovery via
`core/feedback.py::BASELINE_DATASET_SLOTS = {"controls"}` in three call sites:
`find_all_feedback_sources`, `auto_detect_feedback_source`,
`find_feedback_by_target`. To force PHASE re-roll the baseline:
`rm -rf /mnt/AXES2U1/feedback/decoy-controls/controls/`.

## LLM models

| Alias | Ollama tag | Tier | Notes |
|---|---|---|---|
| `gemma` | `gemma4:26b` | V100 32GB | MoE 25.2B/3.8B active, fits 89% VRAM, ~10 tok/s |
| `gemmac` | `gemma4:e2b` | CPU only | Edge-optimized 2.3B, ~7 tok/s on Smol; BU on CPU times out on big prompts |
| `llama` | `llama3.1:8b` | (legacy) | Kept for back-compat, not in any deploy template |

Aliases must agree across three call sites:
`INSTALL_SUP.sh::MODEL_NAMES`, `decoys/common/config/model_config.py::MODELS`,
runner argparse `choices=[...]` in `run_browseruse.py` /
`run_smolagents.py` / `run_mchp.py`.

`get_num_ctx()` in `model_config.py` detects nvidia-smi at runtime: GPU
→ `num_ctx=32768`, CPU → `num_ctx=16384`. Override via `SUP_NUM_CTX`.
Ollama default is 4096 on CPU which silently truncates DOM/tool-use
prompts.

Wired in:
- BrowserUse (`brains/browseruse/agent.py`) — injected into Ollama client
  `chat()` options dict via `create_logged_chat_ollama` wrapper. Uses
  `kwargs.get('options') or {}` (browser_use sometimes passes
  `options=None`)
- SmolAgents (`brains/smolagents/agent.py` + 3 workflow files) — passed
  as `num_ctx` in `LiteLLMModel` constructor

## BrowserUse Agent tuning (`brains/browseruse/agent.py`)

Non-default settings cap token usage to keep CPU BU forward-progressing:

```python
Agent(
    task=full_prompt, llm=self._get_llm(), browser_session=...,
    use_vision=False,                   # gemma is text-only
    use_judge=False,                    # skip extra LLM eval per step
    max_clickable_elements_length=8000, # ~2K tokens vs 40K default
    max_history_items=5,
    include_attributes=["id", "class", "name", "type", "value",
        "placeholder", "aria-label", "role", "href", "title", "alt"],
    llm_timeout=300,                    # CPU LLM calls can take 2-3 min
)
```

Per-step uniform delay from `behavior.behavior.page_dwell` is wired via
`Agent(register_new_step_callback=...)`.

## PHASE feedback runtime consumption

Loader (`load_behavioral_config`) → consumers:

| behavior.json path | BehavioralConfig field | Consumer |
|---|---|---|
| `timing.hourly_distribution` | `timing_profile` | `CalibratedTimingConfig.hourly_fractions` |
| `timing.burst_percentiles.*` | `timing_profile` | `CalibratedTimingConfig.{burst_duration,idle_gap,connections_per_burst}` |
| `timing.variance.cluster_size_sigma` | `variance_injection` | `get_cluster_size()` lognormal noise |
| `timing.variance.idle_gap_sigma` | `variance_injection` | `get_cluster_delay()` lognormal noise |
| `timing.variance.hourly_std_targets.{volume,duration}.hourly_std_target` | `variance_injection` | D1 per-hour sigma in `_init_variance_targets` |
| `timing.activity_probability_per_hour` | `activity_pattern` | `should_skip_hour()` |
| `timing.long_idle_probability` + `long_idle_duration_minutes` | `activity_pattern` | `should_take_long_idle()` |
| `content.workflow_weights` | `workflow_weights` | `build_workflow_weights()` for `random.choices()` |
| `content.site_categories` | `site_config` | SmolAgents `BrowseWebWorkflow` task pool filter |
| `content.download_url_pool` | `download_url_pool` | Smol/BU `DownloadFiles` LLM picker (falls back to `FALLBACK_URLS`) |
| `content.whois_domain_pool` | `whois_domain_pool` | Smol/BU/MCHP `WhoisLookup` (falls back to `FALLBACK_DOMAINS`) |
| `behavior.page_dwell` / `navigation_clicks` | `behavior_modifiers` | MCHP `BrowseWeb.{min,max}_sleep_time`; BU per-step delay |
| `behavior.enable_whois` / `enable_download` | (read via `load_workflow_gates`) | Workflow registration |
| `behavior.keep_alive_probability` | `behavior_modifiers` | MCHP `BrowseWeb.keep_alive_probability` |
| `behavior.max_steps` | `behavior_modifiers` | BU/Smol per-workflow max_steps |
| `diversity.background_services.*` | `diversity_injection` | `BackgroundServiceGenerator` (D4) |
| `diversity.workflow_rotation.*` | `diversity_injection` | D2 rotation in `emulation_loop` |
| `diversity.topology_mimicry.inbound_*_per_hour` | `diversity_injection` | Neighborhood sidecar daemon |
| `_metadata.mode` | `mode` | Baseline short-circuit in `_reload_behavioral_config` |
| `_metadata.ablation_gate` | `ablation_gate` | `is_ablation_gated()` → `[WARNING]` → `[INFO]` downgrade |
| `prompt_content` | `prompt_augmentation.prompt_content` | G1: BU + Smol prompt prepend |

## Topology mimicry (neighborhood sidecar)

Feedback-only. 1 small VM per deploy (`d-{dep_id}-neighborhood-0`,
`v1.small`, `bot-desktop` keypair). Daemon
`common.network.neighborhood_traffic` reads
`/etc/ruse-neighborhood/sups.json` and synthesizes inbound TCP/UDP
probes at each SUP IP.

10 probe types in `decoys/common/network/probes.py`:
`inbound_{smb,ldap,wsus,ntp_receive,printer,ipmi,winrm,mdns,ssdp,scan}_per_hour`.
Produces mixed conn_state (SF / S0 / REJ / RSTO / unidir) on Zeek rows
from the SUP — fights `local_orig=1` / ephemeral-port-only / `conn_state=SF`
sandbox signal.

Deploy flow (`decoy/spinup.py` phase 5, after distribute):

1. `_synthesize_neighborhood_config` walks each SUP's `behavior.json`,
   collects `topology_mimicry` rates, writes
   `neighborhood-sups.json` if any non-zero (else returns None → skip)
2. `_provision_and_install_neighborhood` creates VM, writes
   `neighborhood-inventory.ini`, runs `install-neighborhood.yaml` (asserts
   `ruse-neighborhood` service active + NRestarts ≤ 5)

Audit excludes sidecars from orphan check (live in
`neighborhood-inventory.ini`, not `sup_hosts`). Service-status audit
not yet wired to main `./audit`.

## Hot-patch path

`/opt/ruse/deployed_sups/{key}/decoys/` is a **copy**, not a symlink. Each
install copies `/opt/ruse/decoys/` → that path. `git pull` in `/opt/ruse`
does NOT propagate. Hot-patch:

1. `git push` from mlserv (INSTALL_SUP.sh and `decoys/*` are pulled from
   github at install time — clone URL in
   `deployment_engine/playbooks/decoy/install-sups.yaml::ruse_repo`)
2. SSH the VM, `cp` changed files into per-deploy `decoys/`
3. `systemctl restart {svc}.service`

Or teardown + redeploy.

## Audit (`./audit`)

Per-VM checks across all DECOY VMs. Key columns:

- `Service` — `systemctl is-active` + NRestarts probe; `FAIL (crash-looping, N restarts)` when active but NRestarts > 10
- `M0` — reports `EXPECTED (M0 upstream crashes on Linux)`
- `Fdbk` — checks for exactly 1 `behavior.json` in `/opt/ruse/deployed_sups/*/behavioral_configurations/`
- `Warn` — counts `[WARNING]` vs `[INFO]` separately:
  - Baseline (`bc_has_behavior=0`): `n/a (baseline)` — runtime short-circuits
  - Feedback, 0 warn + N INFO: `OK (N ablation-gated)` — PHASE deliberately omitted sections
  - Feedback, N warn: `FAIL (N unexpected warnings)` — real bug

VM probe greps `/opt/ruse/deployed_sups/{key}/logs/systemd.log` for
`[WARNING]` and `[INFO].*ablation-gated`.

## Observability recipes

```bash
# What aborted the deploy?
grep -E "FAIL|ABORTING|FAILURES" deployments/logs/session-deploy-*.log | tail -30

# What did Ansible actually say per-task?
grep -E "FAILED|fatal|UNREACHABLE" deployments/logs/ansible-*.log | tail -30

# Per-VM behavior.json present?
./audit | grep Fdbk

# All behavior.json files PHASE wrote for a dataset
ls /mnt/AXES2U1/feedback/decoy-controls/sum24/*/*/behavior.json
```

## Constraints

- C0 no software, M0 read-only, no LLM fallback, MCHP no LLM (see CLAUDE.md)
- Models run locally via Ollama
- Per-deploy `decoys/` is a COPY (see hot-patch path above)
- `INSTALL_SUP.sh` + `decoys/*` pulled from github → push before deploy
- VMs set `America/New_York` for log readability; runtime hour reads use
  `datetime.now(timezone.utc).hour` (UTC contract in CLAUDE.md)
