---
name: decoy-deploy
description: DECOY SUP deployment — running ./deploy --decoy [scope flags], 5-phase spinup, behavior.json plumbing, audit semantics, hot-patch path. Three GPU tiers via --gpu {v100,rtx,rtx-a} (V100 → gemma4:26b, RTX 2080 Ti non-A pool → gemma4:e4b on B2R/S2R, RTX 2080 Ti A-pool → same model, separate physical cards). Inputs deployments/decoy-controls/config.yaml + /mnt/AXES2U1/feedback/decoy-controls/{controls (un-namespaced), {preset}_v{version}/{dataset} (feedback, needs --preset)}/. Outputs deployments/decoy-{controls,feedback-...}/runs/{run_id}/. Does NOT cover RAMPART AD enterprise (see /rampart-deploy) or GHOSTS NPC clients (see /ghosts-deploy). Cross-type CLI shape, fail-loud contract, and SSH key matrix live in CLAUDE.md.
type: skill
---

# decoy-deploy

DECOY = synthetic user persona VMs that simulate human computer use. Each
SUP is a brain (MCHP / BrowserUse / SmolAgents) optionally driven by a
PHASE-tuned `behavior.json`. Brain naming scheme `[Brain][Version].[Model]`
is in CLAUDE.md.

| | |
|---|---|
| Inputs | `deployments/decoy-controls/config.yaml`, `/mnt/AXES2U1/feedback/decoy-controls/controls/{behavior}/{sup}/behavior.json` (baseline, **un-namespaced**), `/mnt/AXES2U1/feedback/decoy-controls/{preset}_v{version}/{dataset}/{behavior}/{sup}/behavior.json` (feedback, namespaced 2026-06 — needs `--preset`), `INSTALL_SUP.sh` + `decoys/` cloned from github at install time |
| Outputs | `deployments/decoy-{controls,feedback-{preset}-{dataset}-{scope}}/runs/{run_id}/` (inventory.ini, ssh_config_snippet.txt, deployment_type), per-VM `/opt/ruse/deployed_sups/{key}/`. `{preset}` = sanitized full-ns token incl. version (`stdctrlsv712`), so different lineages/versions don't collide |
| Manifest | `manifest.json` in PHASE source; loaded via `core/feedback.py::load_manifest`, validated against deploy type via `validate_manifest_target` |
| Upstream | PHASE feedback engine (`feedback_engine.baseline` writes `controls/`; `feedback_engine.decoy_generator` writes `{dataset}/`) |
| Downstream | PHASE Zeek pipeline (`PHASE.py --decoy`), reads `experiments.json` for active deploys (carries `dataset`/`scope`/`gpu_tier` descriptive fields since 2026-05-22, + `preset` sanitized-namespace token since 2026-06; see CLAUDE.md "experiments.json schema") |
| Narrow exceptions | C0 (no install — bare Ubuntu, only provisioned + SSH-tested); M0 (upstream MITRE pyhuman, crash-loops on Linux by design — `os.startfile()` Windows-only) |

## Topology

`decoy-controls/config.yaml` provisions 9 VMs (gemma-only; V100 + RTX + CPU pairs):

```
d-{dep_id}-C0-0          Bare Ubuntu control (no software)
d-{dep_id}-M0-0          Upstream MITRE pyhuman (read-only control)
d-{dep_id}-M1-0          MCHP baseline (no timing, no LLM)
d-{dep_id}-B0-gemma-0    BrowserUse + gemma4:26b on V100
d-{dep_id}-S0-gemma-0    SmolAgents  + gemma4:26b on V100
d-{dep_id}-B0R-gemma-0   BrowserUse + gemma4:e4b on RTX 2080 Ti (flavor rtx2080ti-A-1gpu.14vcpu.28g)
d-{dep_id}-S0R-gemma-0   SmolAgents  + gemma4:e4b on RTX 2080 Ti (flavor rtx2080ti-A-1gpu.14vcpu.28g)
d-{dep_id}-B0C-gemma-0   BrowserUse + gemma4:e2b on CPU
d-{dep_id}-S0C-gemma-0   SmolAgents  + gemma4:e2b on CPU
```

B0R/S0R baseline the RTX feedback tiers; they reuse the V100 `.gemma`
baseline behavior.json (R stripped in `_derive_behavior_paths`, so
`B0R.gemma → B.gemma/B0.gemma`), only runtime model (gemma4:e4b) + flavor
differ. Added 2026-05-25; placed on the **rtx-a** pool (`rtx2080ti-A-1gpu`)
2026-05-26 because the non-A `rtx` pool was full with sum25+vt1g feedback —
the axyear rtx-a feedback deploy was dropped to make room (net-zero on rtx-a).

Feedback template (5 VMs per `./deploy --decoy --feedback`). Shape varies
by `--gpu` tier:

**V100 tier** (default, `--gpu v100`):
```
d-{dep_id}-M2-0          MCHP + PHASE timing
d-{dep_id}-B2-gemma-0    BrowserUse + gemma4:26b on V100  (flavor v100-1gpu.14vcpu.28g)
d-{dep_id}-S2-gemma-0    SmolAgents  + gemma4:26b on V100  (flavor v100-1gpu.14vcpu.28g)
d-{dep_id}-B2C-gemma-0   BrowserUse + gemma4:e2b on CPU
d-{dep_id}-S2C-gemma-0   SmolAgents  + gemma4:e2b on CPU
```

**RTX tier** (`--gpu rtx`, dep_name suffix `-rtx`):
```
d-{dep_id}-M2-0           MCHP + PHASE timing
d-{dep_id}-B2R.gemma-0    BrowserUse + gemma4:e4b on RTX 2080 Ti  (flavor rtx2080ti-1gpu.14vcpu.28g, PCI alias rtx2080ti:1)
d-{dep_id}-S2R.gemma-0    SmolAgents  + gemma4:e4b on RTX 2080 Ti  (flavor rtx2080ti-1gpu.14vcpu.28g, PCI alias rtx2080ti:1)
d-{dep_id}-B2C-gemma-0    BrowserUse + gemma4:e2b on CPU
d-{dep_id}-S2C-gemma-0    SmolAgents  + gemma4:e2b on CPU
```

**RTX A-pool tier** (`--gpu rtx-a`, dep_name suffix `-rtx-a`):
```
d-{dep_id}-M2-0           MCHP + PHASE timing
d-{dep_id}-B2R.gemma-0    BrowserUse + gemma4:e4b on RTX 2080 Ti  (flavor rtx2080ti-A-1gpu.14vcpu.28g, PCI alias 2080ti-rtx-a:1)
d-{dep_id}-S2R.gemma-0    SmolAgents  + gemma4:e4b on RTX 2080 Ti  (flavor rtx2080ti-A-1gpu.14vcpu.28g, PCI alias 2080ti-rtx-a:1)
d-{dep_id}-B2C-gemma-0    BrowserUse + gemma4:e2b on CPU
d-{dep_id}-S2C-gemma-0    SmolAgents  + gemma4:e2b on CPU
```

RTX and RTX-A use identical B2R.gemma / S2R.gemma behavior keys and the
same gemma4:e4b runtime model. Only the OpenStack flavor differs — they
map to two distinct physical card pools (separate PCI aliases). The
`-rtx` vs `-rtx-a` deployment-name suffix lets the OpenStack provision
calls land on either pool without VM-name collision, so you can fan
across pools when one is exhausted. Each tier deploy is its own
independent experiments.json entry — no automatic linkage. If you want
to swap sum25 from V100 to RTX-A, run `./teardown` on the V100
deployment first, then deploy the RTX-A one; both stay registered
independently for as long as their VMs exist.

Plus `d-{dep_id}-neighborhood-0` sidecar (feedback only, when any
`topology_mimicry` rate is non-zero).

`dep_id` = `{name_no_hyphens}{run_id}` where `run_id` is `MMDDYYHHmmss`.

## CLI scope flags

```bash
# --preset {preset}_v{version} REQUIRED whenever feedback is in scope (2026-06).
./deploy --decoy --preset std-ctrls_v7.1.2                          # controls + ALL feedback in that ns
./deploy --decoy --controls                                        # controls only (no --preset needed)
./deploy --decoy --feedback --preset std-ctrls_v7.1.2              # all feedback in ns (no controls)
./deploy --decoy --feedback --preset std-ctrls_v7.1.2 --target sum24   # single dataset
./deploy --decoy --feedback --preset std-ctrls_v7.1.2 --target sum24,axyear,vt50g  # batch on one tier
./deploy --decoy --feedback --source /path                        # explicit PHASE source (path encodes ns; no --preset)
./deploy --decoy --controls --preset std-ctrls_v7.1.2 --target sum24    # controls + single feedback
./deploy --decoy --feedback --preset std-ctrls_v7.1.2 --gpu rtx --target sum24   # RTX (PCI alias rtx2080ti:1)
./deploy --decoy --feedback --preset exp-ctrls_v7.1.6 --gpu rtx-a --target axall # other lineage + A-pool
```

**`--preset` (2026-06 namespace):** feedback datasets live under
`{type}-controls/{preset}_v{version}/{dataset}/`. `--preset` is REQUIRED for any
feedback deploy; missing/not-found aborts fail-loud (lists available namespaces).
Folded into `core/feedback.py::_type_root` → transparent downstream. `controls/`
stays un-namespaced (config.yaml `behavior_source`). Spinup lineage-asserts the
config's stamped `_metadata.model_preset`/`model_version` == the deployed ns.
Hard cutover with PHASE write-side. Full detail: CLAUDE.md "Feedback namespace".

**Collision-safety:** the deploy NAME stamps the FULL ns incl. version (sanitized
`[a-z0-9]` via `_ns_preset_token` from `source_dir.parent.name`) — e.g.
`std-ctrls_v7.1.2` → `decoy-feedback-stdctrlsv712-{ds}-{scope}` vs `_v7.1.5` →
`…stdctrlsv715…`. So two lineages OR two versions of the same dataset get distinct
`deployment_name → run_dir → VM prefix (dep_id) → experiments.json key` and coexist
(no idempotent-refresh teardown clash). `experiments.json` carries a `preset` attr
(the sanitized token) per entry.

GPU tier selection via `--gpu {v100,rtx,rtx-a}` (default v100). RTX
tiers swap B2.gemma/S2.gemma → B2R.gemma/S2R.gemma and the V100 flavor
→ RTX 2080 Ti flavor; M2 + B2C.gemma + S2C.gemma stay identical across
tiers. The two RTX tiers target distinct physical card pools — when
one pool is exhausted (`No valid host was found` on B2R/S2R provision),
switch to the other. PHASE feedback is portable across gemma4 variants
so the same `.gemma/` source ships behavior.json for V100, RTX, and
RTX-A deploys with no re-roll.

Granular per-config-file flags (`--timing`, `--workflow`, `--modifiers`,
`--sites`, `--prompts`, `--activity`, `--diversity`, `--variance`,
`--all-feedback`) were removed when PHASE consolidated to a single
`behavior.json` per SUP. There's no longer a per-file filter to apply.

Batch is the default when `--feedback` is given without a single-target
selector. CLI scans `/mnt/AXES2U1/feedback/decoy-controls/`, prompts
confirmation, then deploys each task sequentially. No cross-deploy
parallel fan-out — `--parallel` was removed 2026-05-11 (operator
preference: clean inline output and easier debugging beat the
wall-time win).

Dataset target aliases (`core/feedback.py::DATASET_TARGETS`): `sum24` →
`summer24`, `spr25` → `spring25`, `vt1g` → `vt-fall22-1gb`, `vt50g` →
`vt-fall22-50gb`, `cptc8` → `cptc8-23`, `axall` → `axes-all`, `2025` →
`axes-2025`. Resolution is substring against
`/mnt/AXES2U1/feedback/decoy-controls/`.

## Spinup phases (`decoy/spinup.py`)

0. `_validate_behavior_source` — walk every non-C0/M0 SUP's expected
   `{behavior_dir}/{baseline_config}/behavior.json`, abort with
   missing-path list before any VM work
0.5. `_teardown_matching_prior_runs` — for each `runs/{old_rid}/` whose
   saved `config.yaml` has SAME `gpu_tier` AND SAME `deployments[]` list as
   the new config, openstack-delete its VMs (`wait_until_zero`) and
   `safe_rmtree` the prior run_dir. Makes `./deploy` idempotent against
   the same logical deploy; orphan accumulation across reruns goes
   away. Mismatching prior runs are left intact (operator can ./teardown).
1. Provision VMs (`provision-vms.yaml`) — abort if < 90% reach ACTIVE
2. SSH connectivity test (Python `concurrent.futures`, 20 workers) —
   abort if < 90% reachable
3. Install (`install-sups.yaml`) — stage1 system deps → reboot (exit 100)
   → stage2 brain deps + systemd service. INSTALL_SUP.sh runs with
   `RUSE_NO_SERVICE_START=1` so the service is enabled but NOT started —
   distribute starts it (next phase) once behavior.json is on disk. M0 is
   started here (it skips distribute, expected to crash on Linux). C0 skipped.
4. Distribute behavior configs (`distribute-behavior-configs.yaml`) —
   copy + JSON-validate + on-VM stat assert, then `systemd state=started`,
   poll up to 30s for `state=active` AND `NRestarts ≤ 5`, abort if either
   fails. With this ordering NRestarts stays at 0 on a clean deploy
   (pre-fix it sat at 60-100 from crash-loops in the install→distribute gap).
   C0/M0 skip via `meta: end_host`.
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
- `B2R.gemma` → `b2r_gemma.service`  (RTX, both pools)
- `S2R.gemma` → `s2r_gemma.service`  (RTX, both pools)

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
                "generated_at", "mode", "ablation_gate", "timezone": "UTC",
                "seed": int},  // optional; PHASE-emitted, overrides CLI --seed default
                               // via peek_seed() in sup/__main__.py
  "timing": {
    "active_minute_windows": [[start_min, end_min), ...],   // hard 0/1 schedule
    "target_conn_per_minute_during_active": 7.0,
    "min_window_minutes": 15,
    "hard_fence_seconds": 90,
    "burst_percentiles": {
      "connections_per_burst":  {"5","25","50","75","95","max"},
      "idle_gap_minutes":       {"5","25","50","75","95"},
      "burst_duration_minutes": {"5","25","50","75","95"}
    },
    // hourly_distribution / activity_probability_per_hour / long_idle_*
    // were the pre-window soft schedule. Window-mode (2026-05-08)
    // replaced them with active_minute_windows + per-minute target rate.
    // PHASE no longer emits them. RUSE defaults hourly_fractions to
    // uniform [1/24]*24 if absent — windows gate the real schedule.
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
| `gemma` | `gemma4:26b` | V100 32GB | MoE 25.2B/3.8B active, fits 89% VRAM, ~10 tok/s. Used by B2.gemma / S2.gemma (V100 feedback) and B0.gemma / S0.gemma (V100 controls). |
| `gemmar` | `gemma4:e4b` | RTX 2080 Ti 11GB | Edge 4B variant (~3 GB int4 weights, ~10 GB loaded with KV cache). Used by B2R.gemma / S2R.gemma on both `--gpu rtx` and `--gpu rtx-a` deploys. Same model across both pools — only the underlying flavor / PCI alias differs. |
| `gemmac` | `gemma4:e2b` | CPU only | Edge-optimized 2.3B, ~7 tok/s on Smol; BU on CPU times out on big prompts. Used by B2C.gemma / S2C.gemma + B0C.gemma / S0C.gemma. |
| `llama` | `llama3.1:8b` | (legacy) | Kept for back-compat, not in any deploy template |

Three gemma4 tiers (V100 / RTX / CPU) keep results structurally
comparable — same family, different VRAM-fit variants. PHASE-shipped
`.gemma/` feedback is portable across all three.

**Brain framework versions are PINNED** (`INSTALL_SUP.sh`):
`browser-use==0.12.7`, `smolagents==1.25.0`. The step-action log parser is
keyed to each version's action/tool vocabulary —
`_BU_ACTION_MAP` (`brains/browseruse/agent.py`) and `_SMOL_ACTION_PATTERNS`
(`common/logging/llm_callbacks.py`). These libs rename actions between
versions (browser-use's pre-0.12 `go_to_url`/`click_element` → 0.12
`navigate`/`click`), so an unpinned bump silently zeroes out per-step
logging (confirmed 2026-05-25: ~99% of BU steps dropped). When bumping a
pin, re-derive the maps from a live VM's emitted action names and update
both in lockstep. A `[parser-drift]` `[WARNING]` (caught by `./audit`'s
Warn column) fires if N consecutive responses parse but map to nothing.

Aliases must agree across **four** call sites:
`INSTALL_SUP.sh::MODEL_NAMES`, `decoys/common/config/model_config.py::MODELS`,
runner argparse `choices=[...]` in **all three** of `run_browseruse.py`,
`run_smolagents.py`, `run_mchp.py`. Adding a new alias and missing any
runner argparse silently crashes the SUP at startup — INSTALL_SUP.sh
generates `run_agent.sh` with `--model={alias}`, the runner rejects it
with `argument --model: invalid choice`, the service crash-loops, and
NRestarts blows past the install-time 30s grace before
distribute-behavior-configs.yaml's service-active assertion catches
it (observed when `gemmar` was added to MODEL_NAMES + model_config.py
but missed in the runners — commit `f2ad12a` is the fix; original miss
was in `755fc0c`).

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
| `timing.active_minute_windows` + `target_conn_per_minute_during_active` + `min_window_minutes` + `hard_fence_seconds` | `timing_profile` | `phase_timing.update_window_contract` → window gate in `emulation_loop` + D4 deficit-burst in `background_services` |
| `timing.burst_percentiles.*` | `timing_profile` | `CalibratedTimingConfig.{burst_duration,idle_gap,connections_per_burst}` |
| `timing.hourly_distribution` (legacy, vestigial) | `timing_profile` | `CalibratedTimingConfig.hourly_fractions` — defaults uniform when absent; windows gate the real schedule |
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
| `_metadata.seed` | `seed` | `sup/__main__.py` peeks before `random.seed()`; overrides CLI `--seed`. Also propagated into `neighborhood-sups.json` top-level `seed` field for sidecar RNG anchor. `AgentLogger.session_id` derives from this via separate `Random()` instance (no global RNG consumption) |
| `prompt_content` | `prompt_augmentation.prompt_content` | G1: BU + Smol prompt prepend |

## Logging output (jsonl)

Each SUP writes events to
`/opt/ruse/deployed_sups/{key}/logs/session_{YYYY-MM-DD_HH-MM-SS}_{session_id}.jsonl`
(+ a `latest.jsonl` symlink). Envelope on every line: `timestamp` (naive
**local** ISO; runtime hour-gating uses UTC separately — see CLAUDE.md UTC
contract), `session_id` (8 hex, seed-derived → deterministic across replays),
`agent_type` (config key), `event_type`, optional `workflow`, `details`.
None values omitted.

**17 event types**: `session_{start,success,fail,end}`,
`workflow_{start,end}`, `step_{start,success,error}`,
`llm_{request,response,error}`, `decision`, `timing_delay`, `warning`, `info`,
`network_sample`. PHASE-side consumers and the DuckDB collection
(`/mnt/AXES2U1/SUP_LOGS/sup-logs-<exp>.duckdb`) read these directly.

`network_sample` (2026-06-01) is the **representative traffic signal** — emitted
~per-minute by `background_services.py` via `OutboundConnSampler`
(`common/network/conn_sampler.py`). Workflow/step COUNTS are honest but are NOT a
traffic proxy (a BU `navigate` step = a full page-load with dozens of sub-resource
conns; an MCHP step = one local micro-action — ground-truthed 2026-06-01: on the
wire BU ~18 conn/min ≫ MCHP ~1 ≫ Smol ~0.27, the inverse of the workflow-count
ranking). `details`: `active_opens` (real outbound TCP conns opened in the window,
incl. short-lived; from `/proc/net/snmp` `Tcp:ActiveOpens` delta; minor loopback
noise), `distinct_hosts` (loopback-excluded external peers from `/proc/net/tcp{,6}`),
`d4_synthetic` (legacy D4-only count, = the `[bg-counter]` `conns=` floor), `window_s`.
The `[bg-counter]` systemd.log line gained matching `active_opens=`/`hosts=` fields.
Cadence follows the inter-task `maybe_generate` call, so for slow BU it's per-workflow
(minutes), not strictly per-minute — `window_s` carries the true interval and
`active_opens` is a delta, so volume is still complete.

BU `llm_error` now also fires on `cancelled/timeout` (2026-06-01): CPU-slow LLM
calls were cancelled mid-flight (`CancelledError`, a `BaseException`) and vanished
silently (`llm_request` ≫ `llm_response`, `llm_error=0`). The wrapper now logs them
(`fatal=False`) so the request/response gap is reconcilable.

### Canonical `workflow` field (2026-05-25)

The `workflow` top-level field carries `workflow.name` — the harmonized
cross-brain identifier (`BrowseWeb`, `BrowseYouTube`, `WebSearch`,
`WhoisLookup`, `DownloadFiles`, `DocumentEditor`, `SpreadsheetEditor`,
`ExecuteCommand`, `ListFiles`, `MicrosoftPaint`). These match exactly the
keys `feedback_engine.decoy_generator` emits in `content.workflow_weights`,
so log events join to weights directly. Human task text moved to
`params.description`; `workflow_class` was REMOVED (zero PHASE consumers
used it). Workflow names DIVERGE from Python class names in MCHP
(`google_search.py` class `GoogleSearch` → name `WebSearch`;
`browse_web.py` class `WebBrowse` → name `BrowseWeb`) — the `.name` is the
deliberately harmonized join key; class names stay legacy.

### Real per-step outcomes + durations (2026-05-25)

`step_success`/`step_error` and `duration_ms` reflect actual execution from
authoritative sources per brain:

| Brain | Step source | Timing |
|---|---|---|
| **BrowserUse** | walks `AgentHistoryList` returned by `agent.run()` (`_log_bu_steps` in `brains/browseruse/agent.py`); pairs `model_output.action` with `ActionResult.error` per step | **batched at workflow-end** |
| **SmolAgents** | `CodeAgent(step_callbacks=[make_smol_step_callback(logger)])` over each `ActionStep` (`code_action`/`error`/`timing` in `common/logging/llm_callbacks.py`) | streamed per step |
| **MCHP** | hand-instrumented `logger.step_start/success/error` in each workflow file | streamed |

⚠️ **BU batching caveat for inter-step timing**: BU `step_start` timestamps
cluster at workflow-end (since the history is walked once after
`agent.run()` returns), so they're NOT meaningful for inter-step gap
analysis (`feedback_engine/knob_investigation/inter_step_timing.py`). Use
`llm_request`/`llm_response` timestamps (still streamed via the chat
wrapper) for BU inter-step timing. Smol and MCHP stream normally.

### Action / step vocabulary (version-coupled — see `project_brain_lib_pin_parser_coupling` memory)

- **`_BU_ACTION_MAP`** (`brains/browseruse/agent.py`) maps the **full
  browser-use 0.12.7 `Tools.registry`** (24 actions: navigate, click, input,
  scroll, search, search_page, extract, find_elements, find_text,
  screenshot, evaluate, dropdown_options, select_dropdown, read_file,
  write_file, replace_file, save_as_pdf, upload_file, go_back, switch,
  close, send_keys, wait; `done` intentionally skipped). **Derive from the
  registry** (`python -c "from browser_use.tools.service import Tools;
  print(sorted(Tools().registry.registry.actions))"`), NOT sampled logs —
  sampling missed half on 2026-05-25 (drift guard caught `read_file`).
- **`_SMOL_ACTION_PATTERNS`** (`common/logging/llm_callbacks.py`) is
  bounded by what we register: `web_search`/`duckduckgo`/
  `DuckDuckGoSearchTool` → search, `visit_webpage` → navigate,
  `requests.get`/`urllib`/`fetch` → navigate, `print` → scroll;
  `final_answer` skipped. Complete by construction.
- **MCHP**: step names hardcoded in workflow files (`open_application`,
  `edit_content`, `save_document`, `download_file`, `whois_lookup`, etc.).
  No version-coupled vocabulary.

### Parser-drift guard

Both BU (`_log_bu_steps` → `_bu_note_drift`) and Smol
(`_smol_code_unmatched`) count consecutive unmapped action names / unmatched
code turns. At threshold (BU=10, Smol=25) they print one
`[WARNING] [parser-drift] ...` to stdout → systemd.log → caught by
`./audit`'s Warn column. Validated 2026-05-27: caught `read_file` (an
action the original observed-sample map missed). Pinned versions
(`browser-use==0.12.7`, `smolagents==1.25.0`) are in `INSTALL_SUP.sh` so a
silent bump can't break the maps unnoticed.

### DownloadFiles / WhoisLookup detail fields (2026-05-26 / -27)

The dedicated workflows now carry rich detail in step_success/_error
(previously discarded on success):

- **`download_file`** details: `{url, outcome, host, bytes, content_type,
  elapsed_ms}` + real `duration_ms`. MCHP variant:
  `{source, bytes}` from a `~/Downloads` scandir-delta snapshot
  (no common downloader for MCHP).
- **`whois_lookup`**: `message` = trimmed IANA referral
  (non-`%`-comment lines joined: refer / domain / organisation),
  `details = {domain}`, real `duration_ms` (the TCP/43 call time).

### Schedule-idle ≠ stuck

Outside `behavior.json` `active_minute_windows`, the SUP emits an `info`
event and sleeps without firing a workflow:
- Feedback: `[window] outside windows — sleeping Nmin until next start`
- Controls: `[controls] outside windows — sleeping 5.0min`

A SUP with `workflows=0` AND these info lines AND `svc=active` (recent
file mtime) is correctly idle per schedule — NOT hung. Different datasets
have different windows, so simultaneous on-window/off-window splits across
the fleet are normal (2026-05-27 redeploy audit: 35 on-window logging,
27 off-window idle, all healthy).

### DuckDB collection

Periodic SSH-collection from `/opt/ruse/deployed_sups/.../logs/*.jsonl`
into `/mnt/AXES2U1/SUP_LOGS/sup-logs-<experiment>.duckdb` `events` table.
First-class extracted columns (queryable without JSON path): `timestamp,
session_id, agent_type, event_type, workflow, duration_ms, success,
error_message, model, action, category, step_name, status,
{input,output,total}_tokens, llm_output`. The newer `details` payload
fields (`bytes`, `content_type`, `outcome`, `host`, `domain`, `description`)
live inside the `details` JSON column → query via JSON path, e.g.
`details->>'bytes'`.

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

- `Service` — `systemctl is-active` + NRestarts + uptime probe. NRestarts is cumulative and never decays, so a service with high restart count from past crash-loops is still treated as `OK (N restarts, stable Mm)` if it's been continuously active ≥ 600s. Only services active < 600s with NRestarts > 10 are flagged `FAIL (crash-looping)`.
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
