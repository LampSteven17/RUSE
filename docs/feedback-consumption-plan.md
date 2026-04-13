# PHASE Feedback Consumption Plan

**Date:** 2026-04-13
**Status:** Planning
**Scope:** RUSE runtime + RAMPART pyhuman + PHASE feedback engine cleanup

This document captures the complete audit of PHASE-generated behavioral config fields
vs what RUSE/RAMPART runtime code actually consumes, and the implementation plan to
close every gap.

---

## Table of Contents

1. [Background](#background)
2. [Audit Methodology](#audit-methodology)
3. [Complete Field Gap Analysis](#complete-field-gap-analysis)
   - [timing_profile.json](#timing_profilejson)
   - [variance_injection.json](#variance_injectionjson)
   - [diversity_injection.json](#diversity_injectionjson)
   - [activity_pattern.json](#activity_patternjson)
   - [workflow_weights.json](#workflow_weightsjson)
   - [behavior_modifiers.json](#behavior_modifiersjson)
   - [site_config.json](#site_configjson)
   - [prompt_augmentation.json](#prompt_augmentationjson)
4. [Implementation Items](#implementation-items)
   - [D1: Per-Hour Sigma in CalibratedTiming](#d1-per-hour-sigma-in-calibratedtiming)
   - [D2: min_distinct_per_cluster Consumption](#d2-min_distinct_per_cluster-consumption)
   - [D3: Max-Capping Per Hour](#d3-max-capping-per-hour)
   - [D4: Per-Hour Entropy Verification](#d4-per-hour-entropy-verification)
   - [D5: Pyhuman Variance Support (RAMPART)](#d5-pyhuman-variance-support-rampart)
   - [G1: prompt_augmentation Injection](#g1-prompt_augmentation-injection)
   - [G2: connection_reuse.keep_alive_probability](#g2-connection_reusekeep_alive_probability)
   - [G3: detection_hours Suppression](#g3-detection_hours-suppression)
   - [P1: Dead Payload Cleanup (PHASE-side)](#p1-dead-payload-cleanup-phase-side)
   - [P2: RAMPART Generator Variance Targeting](#p2-rampart-generator-variance-targeting)
5. [Implementation Phases](#implementation-phases)
6. [File Index](#file-index)
7. [Testing Strategy](#testing-strategy)
8. [Risk Assessment](#risk-assessment)

---

## Background

PHASE's feedback engine generates 8 behavioral config JSON files per SUP configuration.
These are deployed to VMs at `/opt/ruse/deployed_sups/{behavior}/behavioral_configurations/`
and hot-reloaded at cluster boundaries by `BehavioralConfig` in
`src/common/behavioral_config.py`.

A code audit on 2026-04-13 revealed that **approximately 30-40% of the fields PHASE
generates are never read by RUSE runtime code**. Some of these are diagnostic/informational
(acceptable), but several contain actionable targeting data that should be consumed to
improve realism scores. Additionally, one entire config file (`prompt_augmentation.json`)
is generated but its primary field is never wired into any brain's prompt system.

The original D1-D5 improvement proposals were validated against the codebase. All claims
checked out. Three additional gaps (G1-G3) were discovered during the audit.

---

## Audit Methodology

1. Read every PHASE-generated config file from a representative feedback directory
   (`~/PHASE/feedback_engine/configs/axes-ruse-controls_axes-all_std-ctrls/`)
2. Cataloged every field at every nesting level across all 8 JSON files
3. Searched `src/` exhaustively (grep) for every field name to find consumption points
4. Mapped each field to one of: LIVE (consumed at runtime), DEAD (never loaded),
   or DIAGNOSTIC (loaded/logged but doesn't affect behavior)
5. Cross-referenced with RAMPART emulation code in `deployments/cli/commands/rampart.py`
   and `src/brains/mchp/human.py`

---

## Complete Field Gap Analysis

### timing_profile.json

```
Fields PHASE generates → RUSE consumption status
```

| Field Path | Type | RUSE Status | Notes |
|------------|------|-------------|-------|
| `metadata.*` | object | DEAD | Informational provenance. OK to keep. |
| `hourly_distribution.mean_fraction` | float[24] | **LIVE** | `phase_timing.py:518` — CalibratedTimingConfig.hourly_fractions |
| `burst_characteristics.burst_duration_minutes.percentiles` | dict{5,25,50,75,95} | **LIVE** | `phase_timing.py:514` — sampled via `_sample_percentile()` |
| `burst_characteristics.idle_gap_minutes.percentiles` | dict{5,25,50,75,95} | **LIVE** | `phase_timing.py:515` |
| `burst_characteristics.connections_per_burst.percentiles` | dict{5,25,50,75,95} | **LIVE** | `phase_timing.py:516` |
| `burst_characteristics.burst_duration_minutes.{count,mean,median,min,max,std}` | float | DEAD | Summary stats. Never indexed. |
| `burst_characteristics.idle_gap_minutes.{count,mean,median,min,max,std}` | float | DEAD | Summary stats. Never indexed. |
| `burst_characteristics.connections_per_burst.{count,mean,median,min,max,std}` | float | DEAD | Summary stats. D3 proposes consuming `max`. |
| `per_minute_volume.mean` | float[1440] | DEAD | **Largest dead payload: 1440 floats.** |
| `per_minute_volume.median` | float[1440] | DEAD | 1440 floats. |
| `per_minute_volume.p25` | float[1440] | DEAD | 1440 floats. |
| `per_minute_volume.p75` | float[1440] | DEAD | 1440 floats. |
| `per_minute_volume.normalized_shape` | float[1440] | DEAD | 1440 floats. |
| `volume_distribution.{count,mean,median,min,max,std,percentiles}` | float/dict | DEAD | Never read. |
| `volume_distribution.{histogram_bins,histogram_counts}` | float[] | DEAD | Never read. |

**Dead payload size:** ~7,200 floats in `per_minute_volume` alone, plus `volume_distribution`
histograms. These account for the majority of file size in timing_profile.json.

---

### variance_injection.json

| Field Path | Type | RUSE Status | Notes |
|------------|------|-------------|-------|
| `metadata.*` | object | DEAD | Provenance. OK to keep. |
| `volume_variance.cluster_size_sigma` | float | **LIVE** | `phase_timing.py:410` — lognormal noise scalar |
| `volume_variance.idle_gap_sigma` | float | **LIVE** | `phase_timing.py:430` — lognormal noise scalar |
| `volume_variance.cluster_size_cv_raw` | float | DEAD | Diagnostic — raw CV before sigma conversion |
| `volume_variance.idle_gap_cv_raw` | float | DEAD | Diagnostic |
| `volume_variance.per_hour_multiplier_range` | float[2] | **DEAD — D1 could consume** | Range of hourly multipliers for sigma scaling |
| `volume_variance.noise_distribution` | string | DEAD | Informational ("lognormal") |
| `volume_variance.human_volume_cv` | float | DEAD | Diagnostic — human baseline CV |
| `volume_variance.sup_volume_cv` | float | DEAD | Diagnostic — current SUP CV |
| `feature_variance_targets.volume.hourly_std_target` | float[24] | **DEAD — D1 should consume** | Per-hour std targets for connection volume |
| `feature_variance_targets.volume.hourly_std_current` | float[24] | DEAD | Diagnostic — current per-hour std |
| `feature_variance_targets.volume.correction_weight_per_hour` | float[24] | **DEAD — D1 could consume** | How much correction each hour needs |
| `feature_variance_targets.duration.hourly_std_target` | float[24] | **DEAD — could consume** | Per-hour std targets for connection duration |
| `feature_variance_targets.duration.hourly_std_current` | float[24] | DEAD | Diagnostic |
| `feature_variance_targets.duration.correction_weight_per_hour` | float[24] | DEAD | Diagnostic |
| `feature_variance_targets.orig_bytes.hourly_std_target` | float[24] | DEAD | Per-hour std targets for bytes |
| `feature_variance_targets.orig_bytes.hourly_std_current` | float[24] | DEAD | Diagnostic |
| `feature_variance_targets.orig_bytes.correction_weight_per_hour` | float[24] | DEAD | Diagnostic |
| `feature_variance_targets.orig_ip_bytes.hourly_std_target` | float[24] | DEAD | Per-hour std targets for IP bytes |
| `feature_variance_targets.orig_ip_bytes.hourly_std_current` | float[24] | DEAD | Diagnostic |
| `feature_variance_targets.orig_ip_bytes.correction_weight_per_hour` | float[24] | DEAD | Diagnostic |

**Key insight:** `feature_variance_targets.volume.hourly_std_target` is the single most
impactful dead field. It contains exactly the per-hour sigma values that D1 needs.
PHASE already generates it — RUSE just never reads it.

---

### diversity_injection.json

| Field Path | Type | RUSE Status | Notes |
|------------|------|-------------|-------|
| `metadata.*` | object | DEAD | Provenance. |
| `workflow_rotation.max_consecutive_same` | int | **LIVE** | `emulation_loop.py:209` |
| `workflow_rotation.min_distinct_per_cluster` | int | **DEAD — D2 should consume** | Never read, no enforcement |
| `background_services.enabled` | bool | **LIVE** | `background_services.py:37` |
| `background_services.dns_queries_per_hour` | int[24] | **LIVE** | `background_services.py:38,72` — correctly indexed by hour |
| `background_services.ntp_checks_per_day` | int | **LIVE** | `background_services.py:39` |
| `background_services.http_head_per_hour` | int[24] | **LIVE** | `background_services.py:40,81` — correctly indexed by hour |
| `service_diversity.target_entropy_per_hour` | float[24] | DEAD | Per-hour entropy targets |
| `service_diversity.current_entropy_per_hour` | float[24] | DEAD | Diagnostic |
| `service_diversity.target_n_unique_per_hour` | int[24] | DEAD | Target unique service count per hour |
| `service_diversity.current_n_unique_per_hour` | int[24] | DEAD | Diagnostic |
| `service_diversity.min_entropy` | float | DEAD | Minimum acceptable entropy |
| `service_diversity.target_service_distribution` | dict | DEAD | Weight per service name |
| `service_diversity.correction_weight_per_hour` | float[24] | DEAD | Diagnostic |

**Note:** `service_diversity` fields are informational targets. They describe what the
*network-level* entropy should look like, but RUSE can only influence this indirectly
via workflow selection and background service generation. These are not directly
actionable at the application layer — they're verification targets for PHASE analysis.
Keeping them in the config for documentation is reasonable; they don't need runtime
consumption.

---

### activity_pattern.json

| Field Path | Type | RUSE Status | Notes |
|------------|------|-------------|-------|
| `metadata.*` | object | DEAD | Provenance. |
| `daily_shape.per_hour_activity_probability` | float[24] | **LIVE** | `phase_timing.py:469` — `should_skip_hour()` |
| `daily_shape.target_active_hours` | float | DEAD | Informational |
| `daily_shape.current_active_hours` | float | DEAD | Diagnostic |
| `daily_shape.target_active_minutes` | float | DEAD | Informational |
| `daily_shape.target_volume_cv` | float | DEAD | Informational |
| `daily_shape.current_volume_cv` | float | DEAD | Diagnostic |
| `daily_shape.correction_weight_per_hour` | float[24] | DEAD | Diagnostic |
| `daily_shape.unweighted_per_hour_probability` | float[24] | DEAD | Diagnostic — pre-correction probs |
| `daily_shape.active_hour_range` | int[2] | DEAD | Start/end of active window |
| `daily_shape.detection_hours` | float[24] | **DEAD — G3 should consume** | Hours flagged as high-detection risk |
| `idle_behavior.long_idle_probability` | float | **LIVE** | `phase_timing.py:484` |
| `idle_behavior.long_idle_duration_minutes.{min,max}` | int | **LIVE** | `phase_timing.py:486` |

---

### workflow_weights.json

| Field Path | Type | RUSE Status | Notes |
|------------|------|-------------|-------|
| `metadata.*` | object | DEAD | Provenance. |
| `workflow_weights` | dict{name: float} | **LIVE** | `behavioral_config.py:188` — `build_workflow_weights()` |
| `rationale.service_over` | dict | DEAD | Diagnostic — which services are over-represented |
| `rationale.service_under` | dict | DEAD | Diagnostic |
| `rationale.proto_over` | dict | DEAD | Diagnostic |
| `rationale.orig_bytes_direction` | string | DEAD | Diagnostic |

**`rationale` is purely diagnostic** — it explains why PHASE chose the weights it did.
Not actionable at runtime. Fine to keep for human review.

---

### behavior_modifiers.json

| Field Path | Type | RUSE Status | Notes |
|------------|------|-------------|-------|
| `metadata.*` | object | DEAD | Provenance. |
| `page_dwell.min_seconds` | int | **LIVE** (MCHP) | `mchp/agent.py:130` |
| `page_dwell.max_seconds` | int | **LIVE** (MCHP) | `mchp/agent.py:134` |
| `page_dwell.note` | string | DEAD | Informational |
| `navigation_clicks.min` | int | **LIVE** (MCHP) | `mchp/agent.py:135` |
| `navigation_clicks.max` | int | **LIVE** (MCHP) | `mchp/agent.py:137` |
| `connection_reuse.keep_alive_probability` | float | **DEAD — G2 could consume** | Tab/connection reuse probability |
| `max_steps` | int | **LIVE** (BU/Smol) | `browseruse/loop.py:130`, `smolagents/loop.py:122` |
| `direction` | string | DEAD | Informational ("OVER"/"UNDER") |
| `magnitude` | float | DEAD | Informational |
| `target.{p75,p90}` | float | DEAD | Diagnostic — target byte percentiles |

---

### site_config.json

| Field Path | Type | RUSE Status | Notes |
|------------|------|-------------|-------|
| `metadata.*` | object | DEAD | Provenance. |
| `site_categories` | dict | **LIVE** | `behavioral_config.py:235,302` |
| `domain_categories` | dict | **LIVE** | `behavioral_config.py:236` |
| `task_categories` | dict | **LIVE** | `behavioral_config.py:303` |
| `target_profiles.{resp_bytes,resp_ip_bytes,resp_pkts}.{p75,p90}` | float | DEAD | Response-side byte targets |
| `current_profiles.*` | dict | DEAD | Diagnostic |
| `adjustment_ratios.*` | float | DEAD | Diagnostic |

**`target_profiles` describes what the response-side network stats should look like.**
RUSE can't directly control response bytes (that's server-side), so these are
verification targets, not actionable at runtime. Fine to keep for documentation.

---

### prompt_augmentation.json

| Field Path | Type | RUSE Status | Notes |
|------------|------|-------------|-------|
| `metadata.*` | object | DEAD | Provenance. |
| `brain_type` | string | DEAD | Identifies target brain |
| `prompt_content` | string | **DEAD — G1 should consume** | Natural language behavioral guidance for LLM |
| `deviation_basis` | array[object] | DEAD | Per-feature deviation data backing the prompt |

**This is the most significant wiring gap.** `prompt_content` contains text like:

> "Your browsing should be slower and more deliberate, with longer page dwell times.
> Focus on fewer sites per session. Avoid rapid sequential page loads."

This is generated specifically for each SUP config and brain type. BrowserUse and
SmolAgents both have prompt systems (`BUPrompts.get_task_prompt()`,
`SMOLPrompts.get_task_prompt()`) that already accept augmentation content. The field
is declared on `BehavioralConfig` but never accessed by any brain. The entire feedback
loop for LLM-driven behavioral adjustment is broken at the last mile.

---

## Implementation Items

### D1: Per-Hour Sigma in CalibratedTiming

**Signal addressed:** 38% (std 21.9% + CV 16.2%)
**Estimated impact:** +0.05-0.10
**Effort:** Medium (~20 lines)

**Current behavior:**
`CalibratedTiming.get_cluster_size()` at `phase_timing.py:404-413` applies a single
`cluster_size_sigma` scalar uniformly across all 24 hours:
```python
sigma = vol_var.get("cluster_size_sigma", vol_var.get("cluster_size_cv", 0))
if sigma > 0:
    scaled *= random.lognormvariate(0, sigma)
```

**Problem:** Human traffic has different variance at different hours. Night hours have
higher relative variance (fewer connections, more sporadic). Peak hours have lower
relative variance (steady stream). A uniform sigma misses this.

**PHASE already generates the fix data:**
`variance_injection.json` → `feature_variance_targets.volume.hourly_std_target` is a
24-element array with per-hour standard deviation targets. It's been generated since
the feedback engine was built but never consumed.

**Implementation:**

File: `src/common/timing/phase_timing.py`

1. In `CalibratedTiming.__init__()` (~line 367), extract the per-hour sigma array:
```python
# Per-hour sigma from feature_variance_targets (D1)
fvt = self._variance_config.get("feature_variance_targets", {})
volume_targets = fvt.get("volume", {})
self._hourly_std_target = volume_targets.get("hourly_std_target", [])
```

2. In `get_cluster_size()` (~line 404), index by current hour with scalar fallback:
```python
def get_cluster_size(self) -> int:
    raw = self._sample_percentile(self.config.connections_per_burst)
    scaled = raw * self._get_hourly_scale()

    vol_var = self._variance_config.get("volume_variance", {})
    hour = datetime.now().hour

    # D1: Per-hour sigma from PHASE feature_variance_targets
    if self._hourly_std_target and hour < len(self._hourly_std_target):
        sigma = self._hourly_std_target[hour]
    else:
        # Fallback to scalar sigma (backward compat with older configs)
        sigma = vol_var.get("cluster_size_sigma", vol_var.get("cluster_size_cv", 0))

    if sigma > 0:
        scaled *= random.lognormvariate(0, sigma)
    return max(1, min(int(scaled), self._hourly_max(hour)))  # D3 max cap
```

3. Same treatment for `get_cluster_delay()` (~line 422) using
   `feature_variance_targets.duration.hourly_std_target` for idle gap sigma.

**Backward compatibility:** Falls back to scalar sigma when `hourly_std_target` is
absent or empty, so existing configs without `feature_variance_targets` work unchanged.

---

### D2: min_distinct_per_cluster Consumption

**Signal addressed:** 8.6% (n_unique)
**Estimated impact:** +0.02-0.03
**Effort:** Easy (~15 lines)

**Current behavior:**
`diversity_injection.workflow_rotation.min_distinct_per_cluster` is generated by PHASE
but has zero references in the codebase. The only rotation constraint is
`max_consecutive_same` (prevents N identical picks in a row). There is no per-cluster
distinct-workflow enforcement.

**Problem:** Human users tend to do several different things in a session, not repeat
the same workflow. Bots that randomly pick from weighted workflows can accidentally
produce low-diversity clusters (e.g., 5 consecutive BrowseWeb picks).

**Implementation:**

File: `src/common/emulation_loop.py`

1. Add cluster-scoped tracking. In the main cluster loop (~line 271), reset a set:
```python
# At cluster start
self._cluster_distinct = set()
```

2. Modify `_select_workflow_with_rotation()` (~line 206):
```python
def _select_workflow_with_rotation(self):
    rotation = (self._diversity_config or {}).get("workflow_rotation", {})
    max_consec = rotation.get("max_consecutive_same", 99)
    min_distinct = rotation.get("min_distinct_per_cluster", 0)  # D2

    weights = list(self._workflow_weights) if self._workflow_weights else [1.0] * len(self.workflows)

    # Existing: penalize consecutive same
    if len(self._recent_workflows) >= max_consec:
        last_name = self._recent_workflows[-1]
        if all(w == last_name for w in self._recent_workflows[-max_consec:]):
            for i, w in enumerate(self.workflows):
                if getattr(w, 'name', '') == last_name:
                    weights[i] *= 0.1

    # D2: Near cluster end, force diversity if below minimum
    if min_distinct > 0 and hasattr(self, '_cluster_remaining'):
        needed = min_distinct - len(self._cluster_distinct)
        if needed > 0 and self._cluster_remaining <= needed:
            # Must pick something not yet seen this cluster
            for i, w in enumerate(self.workflows):
                if getattr(w, 'name', '') in self._cluster_distinct:
                    weights[i] *= 0.01  # Strong penalty, not zero (graceful)

    workflow = random.choices(self.workflows, weights=weights, k=1)[0]
    name = getattr(workflow, 'name', '')
    self._recent_workflows.append(name)
    if len(self._recent_workflows) > 10:
        self._recent_workflows.pop(0)
    self._cluster_distinct.add(name)
    return workflow
```

3. In the cluster loop, set `self._cluster_remaining` as a countdown and reset
   `self._cluster_distinct` at the start of each cluster.

**Edge case:** If `min_distinct` exceeds available workflow count, the penalty still
allows selection (0.01 weight, not 0), so it degrades gracefully.

---

### D3: Max-Capping Per Hour

**Signal addressed:** 7.6% (max)
**Estimated impact:** +0.02-0.04
**Effort:** Easy (~8 lines)

**Current behavior:**
`get_cluster_size()` at `phase_timing.py:413` caps at hardcoded `200`:
```python
return max(1, min(int(scaled), 200))
```

**Problem:** Human traffic has natural per-hour maximums. A burst of 200 connections
at 3 AM is an obvious outlier that triggers max-based detection features.

**PHASE already generates the data:** `connections_per_burst.max` exists in the timing
profile (global max), and `feature_variance_targets.volume.hourly_std_target` combined
with `hourly_distribution.mean_fraction` can derive per-hour caps.

**Implementation:**

File: `src/common/timing/phase_timing.py`

1. In `__init__()`, compute per-hour max from profile data:
```python
# D3: Per-hour max cap (mean + 3*std per hour, or profile global max)
profile_max = self.config.connections_per_burst.get("max", 200)
self._per_hour_max = [200] * 24  # Default
if self._hourly_std_target and self.config.hourly_fractions:
    peak_fraction = max(self.config.hourly_fractions)
    for h in range(24):
        if peak_fraction > 0:
            hour_mean = profile_max * (self.config.hourly_fractions[h] / peak_fraction)
            hour_std = self._hourly_std_target[h] if h < len(self._hourly_std_target) else 0
            self._per_hour_max[h] = int(min(hour_mean + 3 * hour_std, profile_max))
        else:
            self._per_hour_max[h] = int(profile_max)
```

2. In `get_cluster_size()`, replace hardcoded 200:
```python
hour = datetime.now().hour
cap = self._per_hour_max[hour] if hasattr(self, '_per_hour_max') else 200
return max(1, min(int(scaled), cap))
```

**Backward compatibility:** Defaults to `[200]*24` if no per-hour data is available.

---

### D4: Per-Hour Entropy Verification

**Signal addressed:** 18.6% (entropy/diversity)
**Status: NO ACTION NEEDED**

**Verified working correctly.** `BackgroundServiceGenerator` at `background_services.py:72`
indexes `dns_queries_per_hour[hour]` directly using `datetime.now().hour`. No averaging.
Hourly counters reset properly via `_reset_hourly()`.

The entropy/diversity signal is addressed by `background_services` (live) and
`workflow_rotation.max_consecutive_same` (live). The `service_diversity` fields
(target_entropy_per_hour, etc.) are verification targets, not directly actionable.

---

### D5: Pyhuman Variance Support (RAMPART)

**Signal addressed:** 21.9% (std)
**Estimated impact:** +0.05-0.15
**Effort:** Medium (~30 lines across 4 files)

**Current behavior:**
pyhuman's `emulation_loop()` in `src/brains/mchp/human.py` runs:
```python
for c in range(clustersize):  # Exact, no jitter
```
The argparser only accepts `--clustersize <int>`. RAMPART passes fixed values from
`logins.json` with zero variance. The emulation service definition in
`install-rampart-emulation.yaml` passes literal integers.

**Problem:** Every login session produces exactly the same cluster size. Real humans
vary: sometimes they do 3 things, sometimes 8. A perfectly constant cluster size is
a trivial detection feature.

**Implementation:**

**File 1: `src/brains/mchp/human.py`**

Add sigma arguments to argparse:
```python
parser.add_argument('--clustersize-sigma', type=float, default=0.0,
                    help='Lognormal sigma for clustersize jitter (0=exact)')
parser.add_argument('--taskinterval-sigma', type=float, default=0.0,
                    help='Lognormal sigma for taskinterval jitter (0=exact)')
```

Modify `emulation_loop()`:
```python
def emulation_loop(workflows, clustersize, taskinterval, taskgroupinterval, extra,
                   clustersize_sigma=0.0, taskinterval_sigma=0.0):
    while True:
        # D5: Jitter clustersize per cluster
        if clustersize_sigma > 0:
            effective_cs = max(1, int(clustersize * random.lognormvariate(0, clustersize_sigma)))
        else:
            effective_cs = clustersize

        for c in range(effective_cs):
            # D5: Jitter taskinterval per task
            if taskinterval_sigma > 0:
                effective_ti = max(1, int(taskinterval * random.lognormvariate(0, taskinterval_sigma)))
            else:
                effective_ti = taskinterval
            sleep(random.randrange(effective_ti))
            index = random.randrange(len(workflows))
            print(workflows[index].display)
            workflows[index].action(extra)
        sleep(random.randrange(taskgroupinterval))
```

**File 2: `deployments/playbooks/install-rampart-emulation.yaml`**

Add sigma args to ExecStart (line 44):
```
ExecStart=... --clustersize {{ rampart_clustersize | default(5) }} \
  --clustersize-sigma {{ rampart_clustersize_sigma | default(0) }} \
  --taskinterval {{ rampart_taskinterval | default(10) }} \
  --taskinterval-sigma {{ rampart_taskinterval_sigma | default(0) }} \
  ...
```

**File 3: `deployments/cli/commands/rampart.py`**

In `_generate_emulation_inventory()` (~line 480), extract sigma from login_profile:
```python
"clustersize_sigma": user["login_profile"].get("clustersize_sigma", "0"),
"taskinterval_sigma": user["login_profile"].get("taskinterval_sigma", "0"),
```

Add to inventory host vars (~line 547):
```python
f"rampart_clustersize_sigma={user['clustersize_sigma']} "
f"rampart_taskinterval_sigma={user['taskinterval_sigma']}"
```

Same additions in `_deploy_windows_emulation()` for the PowerShell script generation
(~line 693).

**File 4: PHASE feedback engine** (outside RUSE repo)

The RAMPART feedback generator needs to emit `clustersize_sigma` and
`taskinterval_sigma` in each per-node `user-roles.json` file. This is P2.

**Critical:** `human.py` is pulled from github at install time. These changes must be
committed and pushed before deploying.

---

### G1: prompt_augmentation Injection

**Signal addressed:** Indirect — improves LLM behavioral compliance
**Estimated impact:** Potentially high for BU/Smol
**Effort:** Easy (~10 lines per brain)

**Current behavior:**
`prompt_augmentation` is declared on `BehavioralConfig` (`behavioral_config.py:37`) and
loaded from `prompt_augmentation.json`. But no brain ever accesses
`fc.prompt_augmentation`. The field contains `prompt_content` — a natural language
string generated per-SUP that describes how the LLM should adjust its behavior.

Example generated content:
> "Browse more slowly. Spend more time reading each page before navigating. Prefer
> lightweight sites (text-heavy, minimal media). Limit the number of pages visited per
> task to 3-5 rather than rapid sequential loads."

**Problem:** BrowserUse and SmolAgents receive zero behavioral feedback signal from
PHASE. Their prompts are static. The entire feedback loop for LLM-driven behavioral
adjustment is broken at the last mile.

**Implementation:**

**File 1: `src/brains/browseruse/loop.py`**

In the behavioral config application section (around line 125-140), after applying
other configs, read and inject the prompt augmentation:
```python
# G1: Inject PHASE behavioral guidance into BrowserUse prompts
if fc.prompt_augmentation:
    augmentation_text = fc.prompt_augmentation.get("prompt_content", "")
    if augmentation_text:
        self._prompt_augmentation = augmentation_text
        self.logger.info("[behavior] Applied prompt augmentation",
                         details={"length": len(augmentation_text)})
```

Then in the task prompt construction (where `BUPrompts.get_task_prompt()` is called),
append the augmentation:
```python
prompt = BUPrompts.get_task_prompt(task, content)
if hasattr(self, '_prompt_augmentation') and self._prompt_augmentation:
    prompt += f"\n\nBehavioral guidance:\n{self._prompt_augmentation}"
```

**File 2: `src/brains/smolagents/loop.py`**

Same pattern — inject into `SMOLPrompts.get_task_prompt()` output.

**Note:** The prompt content is already sanitized by PHASE (no injection risks — it's
generated from structured deviation data, not user input). But the content should be
appended as a system-level instruction, not as part of the user task, to maintain
prompt hierarchy.

---

### G2: connection_reuse.keep_alive_probability

**Signal addressed:** Connection pattern realism
**Estimated impact:** Low-medium
**Effort:** Easy for MCHP (~10 lines), medium for BU/Smol

**Current behavior:**
`behavior_modifiers.connection_reuse.keep_alive_probability` is generated by PHASE but
never read. Humans tend to reuse browser tabs/connections more than bots, which open
fresh connections for each action.

**Implementation (MCHP only — lowest risk):**

File: `src/brains/mchp/agent.py`

In workflows that open URLs, use `keep_alive_probability` to decide whether to reuse
the existing browser tab or open a new one:
```python
keep_alive = (self._behavior_modifiers or {}).get("connection_reuse", {}).get(
    "keep_alive_probability", 0.5)
if random.random() < keep_alive and self.driver.current_url != "about:blank":
    # Reuse existing tab — navigate in current tab
    self.driver.get(url)
else:
    # Open new tab (current behavior)
    self.driver.execute_script("window.open('');")
    self.driver.switch_to.window(self.driver.window_handles[-1])
    self.driver.get(url)
```

**BU/Smol:** These brains manage their own browser sessions. Tab reuse would require
deeper integration with the browser_use/smolagents frameworks. Defer to a later phase.

---

### G3: detection_hours Suppression

**Signal addressed:** Per-hour detection risk
**Estimated impact:** Medium
**Effort:** Easy (~8 lines)

**Current behavior:**
`activity_pattern.daily_shape.detection_hours` is a 24-element array generated by PHASE
that marks which hours triggered detection in the classification model. It's never read.
`should_skip_hour()` only uses `per_hour_activity_probability`.

**Problem:** Some hours may have adequate activity probability but still produce traffic
patterns that are easily classified as non-human. A SUP active at those hours is at
higher detection risk.

**Implementation:**

File: `src/common/timing/phase_timing.py`

In `__init__()`, load detection hours:
```python
# G3: Detection risk per hour
daily_shape = self._activity_config.get("daily_shape", {})
self._detection_hours = daily_shape.get("detection_hours", [])
```

In `should_skip_hour()` (~line 467), factor in detection risk:
```python
def should_skip_hour(self) -> bool:
    hour = datetime.now().hour
    probs = self._activity_config.get("daily_shape", {}).get(
        "per_hour_activity_probability", [])
    if probs and hour < len(probs):
        prob = probs[hour]

        # G3: Reduce activity during high-detection hours
        if self._detection_hours and hour < len(self._detection_hours):
            detection_risk = self._detection_hours[hour]
            # Scale down probability based on detection risk
            # detection_risk of 1.0 = high risk → halve the probability
            # detection_risk of 0.0 = no risk → no change
            prob *= (1.0 - 0.5 * detection_risk)

        if random.random() > prob:
            return True
    return False
```

**Tuning:** The `0.5` scaling factor is conservative — it halves activity at max-risk
hours rather than eliminating it. This can be adjusted based on PHASE evaluation results.

---

### P1: Dead Payload Cleanup (PHASE-side)

**Scope:** PHASE feedback engine (~/PHASE/feedback_engine/)
**Impact:** Clarity + smaller config files (especially timing_profile.json)
**Effort:** Medium

**Strategy:** Two-tier approach:
1. **Remove from deployed configs** — strip dead fields before writing to output dir
2. **Keep in PHASE internal data** — the analysis pipeline may use these for diagnostics

**Fields to remove from deployed configs (confirmed dead and NOT consumed by D1-G3):**

From `timing_profile.json`:
- `per_minute_volume` (entire object — 7,200 floats)
- `volume_distribution` (entire object)
- `burst_characteristics.*.{count,mean,median,min,std}` (keep only `percentiles` and `max`)

From `variance_injection.json`:
- `volume_variance.{cluster_size_cv_raw, idle_gap_cv_raw, noise_distribution, human_volume_cv, sup_volume_cv}`
- `feature_variance_targets.*.{hourly_std_current, correction_weight_per_hour}` (keep only `hourly_std_target` — consumed by D1)

From `diversity_injection.json`:
- `service_diversity.{current_entropy_per_hour, current_n_unique_per_hour, correction_weight_per_hour}`
- (Keep `target_entropy_per_hour`, `target_n_unique_per_hour`, `min_entropy`, `target_service_distribution` as verification targets)

From `activity_pattern.json`:
- `daily_shape.{current_active_hours, current_volume_cv, correction_weight_per_hour, unweighted_per_hour_probability}`
- (Keep `target_active_hours`, `target_active_minutes`, `active_hour_range` as informational)

From `workflow_weights.json`:
- `rationale` (entire object)

From `behavior_modifiers.json`:
- `page_dwell.note`
- `direction`, `magnitude`, `target`

From `site_config.json`:
- `current_profiles`, `adjustment_ratios`

From `prompt_augmentation.json`:
- `deviation_basis` (array) — only keep `prompt_content` and `brain_type`

**Wait until after D1-G3 implementation** to execute this cleanup, then re-audit to
confirm which fields are now live.

---

### P2: RAMPART Generator Variance Targeting

**Scope:** PHASE feedback engine — RAMPART config generator
**Impact:** HIGH — addresses the biggest untargeted signal for RAMPART (21.9% std)
**Effort:** Medium (PHASE-side)

**Current state:** PHASE's RAMPART generator reads `variance_stats.human.mean` for
activity metrics but ignores `std`, `cv`, and `max`. The per-node `user-roles.json`
files it generates have fixed `clustersize`, `taskinterval`, `taskgroupinterval` values
with no variance parameters.

**Required changes (PHASE-side):**

1. Read `variance_stats.human.{std, cv}` from the analysis pipeline output
2. Compute `clustersize_sigma` and `taskinterval_sigma` per node based on observed
   human variance (analogous to how RUSE's feedback engine computes
   `cluster_size_sigma` from volume CV)
3. Emit these fields in each per-node `user-roles.json`:
```json
{
  "roles": [
    {
      "name": "linep9_user",
      "clustersize": 5,
      "clustersize_sigma": 0.3,
      "taskinterval": 10,
      "taskinterval_sigma": 0.4,
      "taskgroupinterval": 500,
      ...
    }
  ]
}
```

4. `rampart.py::_generate_feedback_user_roles()` already reads these files and passes
   through all fields. No RUSE-side changes needed beyond D5 (which adds the runtime
   consumption).

**Dependency:** D5 must be implemented first (pyhuman `--clustersize-sigma` support).

---

## Implementation Phases

### Phase 1: Quick Wins (D2, D3, G2)

**Estimated effort:** 1-2 hours
**Risk:** Low — additive changes with fallback defaults
**Files changed:** 3

| Item | File | Lines | Description |
|------|------|-------|-------------|
| D2 | `src/common/emulation_loop.py` | ~15 | Consume `min_distinct_per_cluster` in workflow rotation |
| D3 | `src/common/timing/phase_timing.py` | ~8 | Replace hardcoded 200 cap with per-hour max |
| G2 | `src/brains/mchp/agent.py` | ~10 | Read `keep_alive_probability` for tab reuse |

**Test:** Deploy a feedback config to a single VM, verify log output shows distinct
workflow enforcement and cluster size capping.

### Phase 2: Per-Hour Sigma + Prompt Injection (D1, G1, G3)

**Estimated effort:** 2-3 hours
**Risk:** Medium — changes timing distribution shape
**Files changed:** 4

| Item | File | Lines | Description |
|------|------|-------|-------------|
| D1 | `src/common/timing/phase_timing.py` | ~20 | Per-hour sigma from `feature_variance_targets` |
| G1 | `src/brains/browseruse/loop.py` | ~10 | Inject `prompt_content` into BU prompts |
| G1 | `src/brains/smolagents/loop.py` | ~10 | Inject `prompt_content` into Smol prompts |
| G3 | `src/common/timing/phase_timing.py` | ~8 | Detection hours suppression in `should_skip_hour()` |

**Test:** Deploy feedback config, verify per-hour sigma variation in timing logs and
prompt augmentation text in LLM request logs.

### Phase 3: Pyhuman Variance (D5 + P2)

**Estimated effort:** 3-4 hours (RUSE) + PHASE-side work
**Risk:** Medium — modifies pyhuman interface (requires git push before deploy)
**Files changed:** 4 (RUSE) + PHASE generator

| Item | File | Lines | Description |
|------|------|-------|-------------|
| D5 | `src/brains/mchp/human.py` | ~15 | Add `--clustersize-sigma`, `--taskinterval-sigma` |
| D5 | `deployments/playbooks/install-rampart-emulation.yaml` | ~2 | Pass sigma args |
| D5 | `deployments/cli/commands/rampart.py` | ~12 | Extract/pass sigma from login_profile |
| D5 | `deployments/cli/commands/rampart.py` | ~6 | Windows emulation sigma args |
| P2 | PHASE feedback engine | ~30 | Generate per-node sigma values |

**Test:** Deploy RAMPART with feedback, verify `journalctl -u rampart-human` shows
varying cluster sizes across login sessions.

### Phase 4: PHASE Cleanup (P1)

**Estimated effort:** 2-3 hours (PHASE-side)
**Risk:** Low — only removes dead fields
**Prerequisite:** Phases 1-3 complete, re-audit confirms field status
**Files changed:** PHASE feedback engine generators

Execute after D1-G3 are verified working, to avoid removing fields that turn out to be
needed.

---

## File Index

All RUSE files modified by this plan:

| File | Items | Changes |
|------|-------|---------|
| `src/common/timing/phase_timing.py` | D1, D3, G3 | Per-hour sigma, max cap, detection hours |
| `src/common/emulation_loop.py` | D2 | min_distinct_per_cluster enforcement |
| `src/common/background_services.py` | (none) | Already working correctly (D4 verified) |
| `src/brains/mchp/agent.py` | G2 | keep_alive_probability for tab reuse |
| `src/brains/mchp/human.py` | D5 | clustersize-sigma, taskinterval-sigma args |
| `src/brains/browseruse/loop.py` | G1 | Prompt augmentation injection |
| `src/brains/smolagents/loop.py` | G1 | Prompt augmentation injection |
| `deployments/playbooks/install-rampart-emulation.yaml` | D5 | Pass sigma args to systemd |
| `deployments/cli/commands/rampart.py` | D5 | Extract/pass sigma from login_profile |

PHASE-side files (outside RUSE repo):
- RAMPART feedback generator (P2)
- All feedback generators (P1 — dead field cleanup)

---

## Testing Strategy

### Unit-Level Verification

Each item can be tested by deploying a single VM with a known feedback config and
checking the logs:

- **D1:** `grep "cluster_size" logs/*.jsonl` — verify per-hour sigma variation
  (night hours should show more variance than peak hours)
- **D2:** `grep "workflow" logs/*.jsonl` — verify cluster diversity
  (count distinct workflows per cluster, should meet minimum)
- **D3:** `grep "cluster_size" logs/*.jsonl` — verify max values per hour
  (no cluster at 3 AM should exceed the per-hour cap)
- **D5:** `journalctl -u rampart-human` — verify varying cluster sizes
- **G1:** `grep "prompt" logs/*.jsonl` — verify augmentation text present in LLM calls
- **G2:** Monitor Zeek conn.log — verify connection reuse patterns
- **G3:** Compare activity levels during detection-flagged hours before/after

### Integration Verification

Deploy a full feedback config (`./deploy --ruse --feedback`) and run PHASE inference
to measure detection score improvement:
```bash
cd ~/PHASE
poetry run python LAUNCH_INFERENCE.py --model <latest> --eval-data <post-deploy-data>
```

Compare pre/post detection rates on the features each item targets (std, n_unique, max,
entropy).

---

## Risk Assessment

| Item | Risk | Mitigation |
|------|------|------------|
| D1 | Per-hour sigma could be too aggressive → unrealistic bursts at night | Clamp sigma to [0, 1.5] range; D3 max cap prevents outliers |
| D2 | min_distinct near cluster end → forced non-optimal workflow picks | Use soft penalty (0.01) not hard exclusion; graceful degradation |
| D3 | Per-hour max too low → suppresses legitimate peak-hour activity | Use mean + 3*std as cap; conservative by definition |
| D5 | pyhuman sigma → very small clusters (1 task) feel unnatural | Clamp effective_cs to max(2, ...) for minimum cluster size |
| G1 | Prompt augmentation → LLM ignores or misinterprets guidance | The content is clear behavioral instructions; LLMs follow these well. Monitor via step logs. |
| G3 | Detection hours suppression → too little activity during work hours | Conservative 0.5 scaling factor; never eliminates activity entirely |
| P1 | Removing fields breaks future PHASE analysis | Only remove from deployed configs; keep in PHASE internal pipeline |
