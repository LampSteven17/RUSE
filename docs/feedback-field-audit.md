# PHASE Feedback Field Audit — Consumed vs Dead

**Date:** 2026-04-13
**Purpose:** Definitive reference for pruning dead fields from PHASE feedback generation.
**Methodology:** grep of every field name across `/home/ubuntu/RUSE/src/`, cross-referenced
with runtime `.get()` calls in phase_timing.py, emulation_loop.py, background_services.py,
behavioral_config.py, and all three brain implementations.

**Legend:**
- **LIVE** — consumed by runtime code, affects SUP behavior
- **LIVE (planned)** — not yet consumed, but D1-G3 plan will consume (do NOT prune yet)
- **DEAD** — never loaded, never affects behavior, safe to prune
- **DIAGNOSTIC** — never affects behavior but useful for human review / PHASE-side analysis
- **METADATA** — provenance fields; informational, standard across all files

> **Rule of thumb:** prune DEAD fields. Keep DIAGNOSTIC fields only if PHASE analysis
> pipelines read them. METADATA is cheap and useful — keep it.

---

## timing_profile.json

| Field Path | Status | Consumed At | Notes |
|------------|--------|-------------|-------|
| `metadata` | METADATA | — | Provenance. Keep. |
| `hourly_distribution.mean_fraction` | **LIVE** | `phase_timing.py:518` | 24-element array, CalibratedTimingConfig.hourly_fractions |
| `burst_characteristics.burst_duration_minutes.percentiles` | **LIVE** | `phase_timing.py:514` | Keys: 5,25,50,75,95. Sampled by `_sample_percentile()` |
| `burst_characteristics.idle_gap_minutes.percentiles` | **LIVE** | `phase_timing.py:515` | Same |
| `burst_characteristics.connections_per_burst.percentiles` | **LIVE** | `phase_timing.py:516` | Same |
| `burst_characteristics.connections_per_burst.max` | **LIVE (planned)** | — | D3 will use for per-hour max cap |
| `burst_characteristics.burst_duration_minutes.count` | DEAD | — | Summary stat |
| `burst_characteristics.burst_duration_minutes.mean` | DEAD | — | Summary stat |
| `burst_characteristics.burst_duration_minutes.median` | DEAD | — | Summary stat |
| `burst_characteristics.burst_duration_minutes.min` | DEAD | — | Summary stat |
| `burst_characteristics.burst_duration_minutes.max` | DEAD | — | Summary stat (burst duration max not used) |
| `burst_characteristics.burst_duration_minutes.std` | DEAD | — | Summary stat |
| `burst_characteristics.idle_gap_minutes.count` | DEAD | — | Summary stat |
| `burst_characteristics.idle_gap_minutes.mean` | DEAD | — | Summary stat |
| `burst_characteristics.idle_gap_minutes.median` | DEAD | — | Summary stat |
| `burst_characteristics.idle_gap_minutes.min` | DEAD | — | Summary stat |
| `burst_characteristics.idle_gap_minutes.max` | DEAD | — | Summary stat |
| `burst_characteristics.idle_gap_minutes.std` | DEAD | — | Summary stat |
| `burst_characteristics.connections_per_burst.count` | DEAD | — | Summary stat |
| `burst_characteristics.connections_per_burst.mean` | DEAD | — | Summary stat |
| `burst_characteristics.connections_per_burst.median` | DEAD | — | Summary stat |
| `burst_characteristics.connections_per_burst.std` | DEAD | — | Summary stat |
| `per_minute_volume` | DEAD | — | **Largest dead payload.** 5 arrays x 1440 floats = 7,200 values. Never read. |
| `per_minute_volume.mean` | DEAD | — | 1440 floats |
| `per_minute_volume.median` | DEAD | — | 1440 floats |
| `per_minute_volume.p25` | DEAD | — | 1440 floats |
| `per_minute_volume.p75` | DEAD | — | 1440 floats |
| `per_minute_volume.normalized_shape` | DEAD | — | 1440 floats |
| `volume_distribution` | DEAD | — | Entire object never read |
| `volume_distribution.count` | DEAD | — | |
| `volume_distribution.mean` | DEAD | — | |
| `volume_distribution.median` | DEAD | — | |
| `volume_distribution.min` | DEAD | — | |
| `volume_distribution.max` | DEAD | — | |
| `volume_distribution.std` | DEAD | — | |
| `volume_distribution.percentiles` | DEAD | — | |
| `volume_distribution.histogram_bins` | DEAD | — | |
| `volume_distribution.histogram_counts` | DEAD | — | |

### timing_profile.json — Pruning Summary

**Keep:**
```
metadata
hourly_distribution.mean_fraction
burst_characteristics.burst_duration_minutes.percentiles
burst_characteristics.idle_gap_minutes.percentiles
burst_characteristics.connections_per_burst.percentiles
burst_characteristics.connections_per_burst.max          ← D3 will consume
```

**Prune (safe now):**
```
per_minute_volume                                        ← entire object (7,200 floats)
volume_distribution                                      ← entire object
burst_characteristics.*.count
burst_characteristics.*.mean
burst_characteristics.*.median
burst_characteristics.*.min
burst_characteristics.*.std
burst_characteristics.burst_duration_minutes.max         ← (only cpb.max is planned)
burst_characteristics.idle_gap_minutes.max
```

---

## variance_injection.json

| Field Path | Status | Consumed At | Notes |
|------------|--------|-------------|-------|
| `metadata` | METADATA | — | Keep |
| `volume_variance.cluster_size_sigma` | **LIVE** | `phase_timing.py:410` | Lognormal noise scalar for cluster size |
| `volume_variance.idle_gap_sigma` | **LIVE** | `phase_timing.py:430` | Lognormal noise scalar for idle gap |
| `volume_variance.cluster_size_cv_raw` | DEAD | — | Raw CV before sigma conversion |
| `volume_variance.idle_gap_cv_raw` | DEAD | — | Raw CV |
| `volume_variance.per_hour_multiplier_range` | DEAD | — | 2-element range, never read |
| `volume_variance.noise_distribution` | DEAD | — | String "lognormal", informational |
| `volume_variance.human_volume_cv` | DEAD | — | Diagnostic: human baseline CV |
| `volume_variance.sup_volume_cv` | DEAD | — | Diagnostic: current SUP CV |
| `feature_variance_targets.volume.hourly_std_target` | **LIVE (planned)** | — | D1 will consume: per-hour sigma for cluster size |
| `feature_variance_targets.volume.hourly_std_current` | DIAGNOSTIC | — | Current per-hour std (PHASE analysis) |
| `feature_variance_targets.volume.correction_weight_per_hour` | DEAD | — | Pre-baked into sigma |
| `feature_variance_targets.duration.hourly_std_target` | **LIVE (planned)** | — | D1 will consume: per-hour sigma for idle gap |
| `feature_variance_targets.duration.hourly_std_current` | DIAGNOSTIC | — | Current per-hour std |
| `feature_variance_targets.duration.correction_weight_per_hour` | DEAD | — | Pre-baked |
| `feature_variance_targets.orig_bytes.hourly_std_target` | DEAD | — | RUSE can't control byte sizes directly |
| `feature_variance_targets.orig_bytes.hourly_std_current` | DEAD | — | |
| `feature_variance_targets.orig_bytes.correction_weight_per_hour` | DEAD | — | |
| `feature_variance_targets.orig_ip_bytes.hourly_std_target` | DEAD | — | RUSE can't control IP byte sizes |
| `feature_variance_targets.orig_ip_bytes.hourly_std_current` | DEAD | — | |
| `feature_variance_targets.orig_ip_bytes.correction_weight_per_hour` | DEAD | — | |

### variance_injection.json — Pruning Summary

**Keep:**
```
metadata
volume_variance.cluster_size_sigma
volume_variance.idle_gap_sigma
feature_variance_targets.volume.hourly_std_target        ← D1 will consume
feature_variance_targets.duration.hourly_std_target      ← D1 will consume
```

**Prune (safe now):**
```
volume_variance.cluster_size_cv_raw
volume_variance.idle_gap_cv_raw
volume_variance.per_hour_multiplier_range
volume_variance.noise_distribution
volume_variance.human_volume_cv
volume_variance.sup_volume_cv
feature_variance_targets.volume.correction_weight_per_hour
feature_variance_targets.duration.correction_weight_per_hour
feature_variance_targets.orig_bytes                       ← entire object
feature_variance_targets.orig_ip_bytes                    ← entire object
```

**Prune after D1 ships (currently DIAGNOSTIC, used by PHASE analysis):**
```
feature_variance_targets.volume.hourly_std_current
feature_variance_targets.duration.hourly_std_current
```

---

## diversity_injection.json

| Field Path | Status | Consumed At | Notes |
|------------|--------|-------------|-------|
| `metadata` | METADATA | — | Keep |
| `workflow_rotation.max_consecutive_same` | **LIVE** | `emulation_loop.py:209` | Rotation enforcement |
| `workflow_rotation.min_distinct_per_cluster` | **LIVE (planned)** | — | D2 will consume |
| `background_services.enabled` | **LIVE** | `background_services.py:37` | |
| `background_services.dns_queries_per_hour` | **LIVE** | `background_services.py:38,72` | 24-element, indexed by hour |
| `background_services.ntp_checks_per_day` | **LIVE** | `background_services.py:39` | |
| `background_services.http_head_per_hour` | **LIVE** | `background_services.py:40,81` | 24-element, indexed by hour |
| `service_diversity.target_entropy_per_hour` | DIAGNOSTIC | — | Verification target (not actionable at app layer) |
| `service_diversity.current_entropy_per_hour` | DIAGNOSTIC | — | Diagnostic |
| `service_diversity.target_n_unique_per_hour` | DIAGNOSTIC | — | Verification target |
| `service_diversity.current_n_unique_per_hour` | DIAGNOSTIC | — | Diagnostic |
| `service_diversity.min_entropy` | DIAGNOSTIC | — | Verification target |
| `service_diversity.target_service_distribution` | DIAGNOSTIC | — | Verification target (network-level) |
| `service_diversity.correction_weight_per_hour` | DEAD | — | Pre-baked into other values |

### diversity_injection.json — Pruning Summary

**Keep:**
```
metadata
workflow_rotation.max_consecutive_same
workflow_rotation.min_distinct_per_cluster                ← D2 will consume
background_services.enabled
background_services.dns_queries_per_hour
background_services.ntp_checks_per_day
background_services.http_head_per_hour
```

**Prune (safe now):**
```
service_diversity.correction_weight_per_hour
```

**Prune if PHASE analysis doesn't need them:**
```
service_diversity.target_entropy_per_hour                 ← DIAGNOSTIC
service_diversity.current_entropy_per_hour                ← DIAGNOSTIC
service_diversity.target_n_unique_per_hour                ← DIAGNOSTIC
service_diversity.current_n_unique_per_hour               ← DIAGNOSTIC
service_diversity.min_entropy                             ← DIAGNOSTIC
service_diversity.target_service_distribution             ← DIAGNOSTIC
```

---

## activity_pattern.json

| Field Path | Status | Consumed At | Notes |
|------------|--------|-------------|-------|
| `metadata` | METADATA | — | Keep |
| `daily_shape.per_hour_activity_probability` | **LIVE** | `phase_timing.py:469` | `should_skip_hour()` |
| `daily_shape.detection_hours` | **LIVE (planned)** | — | G3 will consume: suppression during high-detection hours |
| `daily_shape.target_active_hours` | DEAD | — | Informational |
| `daily_shape.current_active_hours` | DEAD | — | Diagnostic |
| `daily_shape.target_active_minutes` | DEAD | — | Informational |
| `daily_shape.target_volume_cv` | DEAD | — | Informational |
| `daily_shape.current_volume_cv` | DEAD | — | Diagnostic |
| `daily_shape.correction_weight_per_hour` | DEAD | — | Pre-baked into probabilities |
| `daily_shape.unweighted_per_hour_probability` | DEAD | — | Diagnostic: pre-correction values |
| `daily_shape.active_hour_range` | DEAD | — | Informational |
| `idle_behavior.long_idle_probability` | **LIVE** | `phase_timing.py:484` | |
| `idle_behavior.long_idle_duration_minutes.min` | **LIVE** | `phase_timing.py:486` | |
| `idle_behavior.long_idle_duration_minutes.max` | **LIVE** | `phase_timing.py:486` | |

### activity_pattern.json — Pruning Summary

**Keep:**
```
metadata
daily_shape.per_hour_activity_probability
daily_shape.detection_hours                              ← G3 will consume
idle_behavior.long_idle_probability
idle_behavior.long_idle_duration_minutes.min
idle_behavior.long_idle_duration_minutes.max
```

**Prune (safe now):**
```
daily_shape.target_active_hours
daily_shape.current_active_hours
daily_shape.target_active_minutes
daily_shape.target_volume_cv
daily_shape.current_volume_cv
daily_shape.correction_weight_per_hour
daily_shape.unweighted_per_hour_probability
daily_shape.active_hour_range
```

---

## workflow_weights.json

| Field Path | Status | Consumed At | Notes |
|------------|--------|-------------|-------|
| `metadata` | METADATA | — | Keep |
| `workflow_weights` | **LIVE** | `behavioral_config.py:188` | Dict of {name: float} |
| `rationale` | DEAD | — | Entire object: diagnostic explanation |
| `rationale.service_over` | DEAD | — | |
| `rationale.service_under` | DEAD | — | |
| `rationale.proto_over` | DEAD | — | |
| `rationale.orig_bytes_direction` | DEAD | — | |

### workflow_weights.json — Pruning Summary

**Keep:**
```
metadata
workflow_weights
```

**Prune (safe now):**
```
rationale                                                ← entire object
```

---

## behavior_modifiers.json

| Field Path | Status | Consumed At | Notes |
|------------|--------|-------------|-------|
| `metadata` | METADATA | — | Keep |
| `page_dwell.min_seconds` | **LIVE** (MCHP) | `mchp/agent.py:130` | |
| `page_dwell.max_seconds` | **LIVE** (MCHP) | `mchp/agent.py:134` | |
| `page_dwell.note` | DEAD | — | Informational string |
| `navigation_clicks.min` | **LIVE** (MCHP) | `mchp/agent.py:135` | |
| `navigation_clicks.max` | **LIVE** (MCHP) | `mchp/agent.py:137` | |
| `connection_reuse.keep_alive_probability` | **LIVE (planned)** | — | G2 will consume |
| `max_steps` | **LIVE** (BU/Smol) | `browseruse/loop.py:130`, `smolagents/loop.py:122` | |
| `direction` | DEAD | — | Informational ("OVER"/"UNDER") |
| `magnitude` | DEAD | — | Informational |
| `target.p75` | DEAD | — | Diagnostic |
| `target.p90` | DEAD | — | Diagnostic |

### behavior_modifiers.json — Pruning Summary

**Keep:**
```
metadata
page_dwell.min_seconds
page_dwell.max_seconds
navigation_clicks.min
navigation_clicks.max
connection_reuse.keep_alive_probability                  ← G2 will consume
max_steps
```

**Prune (safe now):**
```
page_dwell.note
direction
magnitude
target                                                   ← entire object
```

---

## site_config.json

| Field Path | Status | Consumed At | Notes |
|------------|--------|-------------|-------|
| `metadata` | METADATA | — | Keep |
| `site_categories` | **LIVE** | `behavioral_config.py:235,302` | Weight per category |
| `domain_categories` | **LIVE** | `behavioral_config.py:236` | Domain → category mapping |
| `task_categories` | **LIVE** | `behavioral_config.py:303` | Task → category mapping |
| `target_profiles.resp_bytes.p75` | DEAD | — | Response-side (server controls this) |
| `target_profiles.resp_bytes.p90` | DEAD | — | |
| `target_profiles.resp_ip_bytes.p75` | DEAD | — | |
| `target_profiles.resp_ip_bytes.p90` | DEAD | — | |
| `target_profiles.resp_pkts.p75` | DEAD | — | |
| `target_profiles.resp_pkts.p90` | DEAD | — | |
| `current_profiles` | DEAD | — | Entire object: diagnostic |
| `adjustment_ratios` | DEAD | — | Entire object: diagnostic |

### site_config.json — Pruning Summary

**Keep:**
```
metadata
site_categories
domain_categories
task_categories
```

**Prune (safe now):**
```
target_profiles                                          ← entire object (response-side, not actionable)
current_profiles                                         ← entire object
adjustment_ratios                                        ← entire object
```

---

## prompt_augmentation.json

| Field Path | Status | Consumed At | Notes |
|------------|--------|-------------|-------|
| `metadata` | METADATA | — | Keep |
| `brain_type` | **LIVE (planned)** | — | G1 uses to validate brain match |
| `prompt_content` | **LIVE (planned)** | — | G1 will inject into LLM prompts |
| `deviation_basis` | DEAD | — | Per-feature deviation data backing the prompt |

### prompt_augmentation.json — Pruning Summary

**Keep:**
```
metadata
brain_type                                               ← G1 will consume
prompt_content                                           ← G1 will consume
```

**Prune (safe now):**
```
deviation_basis                                          ← entire array
```

---

## Combined Pruning Checklist

Quick-reference for the PHASE-side cleanup. Sorted by file, with estimated bytes saved.

### Safe to Prune Now (no RUSE code reads these)

| File | Field | Est. Size |
|------|-------|-----------|
| timing_profile.json | `per_minute_volume` (entire) | **~60 KB** (7,200 floats) |
| timing_profile.json | `volume_distribution` (entire) | ~2 KB |
| timing_profile.json | `burst_characteristics.*.{count,mean,median,min,std}` | ~500 B |
| timing_profile.json | `burst_characteristics.burst_duration_minutes.max` | ~20 B |
| timing_profile.json | `burst_characteristics.idle_gap_minutes.max` | ~20 B |
| variance_injection.json | `volume_variance.{cluster_size_cv_raw,idle_gap_cv_raw}` | ~50 B |
| variance_injection.json | `volume_variance.{per_hour_multiplier_range,noise_distribution}` | ~80 B |
| variance_injection.json | `volume_variance.{human_volume_cv,sup_volume_cv}` | ~40 B |
| variance_injection.json | `feature_variance_targets.orig_bytes` (entire) | ~1 KB |
| variance_injection.json | `feature_variance_targets.orig_ip_bytes` (entire) | ~1 KB |
| variance_injection.json | `feature_variance_targets.*.correction_weight_per_hour` | ~500 B |
| diversity_injection.json | `service_diversity.correction_weight_per_hour` | ~250 B |
| activity_pattern.json | `daily_shape.{target_active_hours,current_active_hours}` | ~30 B |
| activity_pattern.json | `daily_shape.{target_active_minutes,target_volume_cv,current_volume_cv}` | ~50 B |
| activity_pattern.json | `daily_shape.correction_weight_per_hour` | ~250 B |
| activity_pattern.json | `daily_shape.unweighted_per_hour_probability` | ~250 B |
| activity_pattern.json | `daily_shape.active_hour_range` | ~20 B |
| workflow_weights.json | `rationale` (entire) | ~500 B |
| behavior_modifiers.json | `page_dwell.note` | ~100 B |
| behavior_modifiers.json | `direction`, `magnitude` | ~30 B |
| behavior_modifiers.json | `target` (entire) | ~50 B |
| site_config.json | `target_profiles` (entire) | ~300 B |
| site_config.json | `current_profiles` (entire) | ~300 B |
| site_config.json | `adjustment_ratios` (entire) | ~100 B |
| prompt_augmentation.json | `deviation_basis` (entire) | ~1 KB |

**Total estimated savings: ~68 KB per SUP config** (dominated by `per_minute_volume`).
Across a typical 5-SUP feedback deployment with 8 configs each: **~2.7 MB** of dead JSON eliminated.

### Do NOT Prune Yet (planned for consumption by D1-G3)

| File | Field | Planned Consumer |
|------|-------|------------------|
| timing_profile.json | `burst_characteristics.connections_per_burst.max` | D3 |
| variance_injection.json | `feature_variance_targets.volume.hourly_std_target` | D1 |
| variance_injection.json | `feature_variance_targets.duration.hourly_std_target` | D1 |
| diversity_injection.json | `workflow_rotation.min_distinct_per_cluster` | D2 |
| activity_pattern.json | `daily_shape.detection_hours` | G3 |
| behavior_modifiers.json | `connection_reuse.keep_alive_probability` | G2 |
| prompt_augmentation.json | `brain_type` | G1 |
| prompt_augmentation.json | `prompt_content` | G1 |

### Optional Prune (DIAGNOSTIC — only if PHASE analysis doesn't read them)

| File | Field | Used By |
|------|-------|---------|
| variance_injection.json | `feature_variance_targets.volume.hourly_std_current` | PHASE iteration comparison |
| variance_injection.json | `feature_variance_targets.duration.hourly_std_current` | PHASE iteration comparison |
| diversity_injection.json | `service_diversity.target_entropy_per_hour` | PHASE verification |
| diversity_injection.json | `service_diversity.current_entropy_per_hour` | PHASE verification |
| diversity_injection.json | `service_diversity.target_n_unique_per_hour` | PHASE verification |
| diversity_injection.json | `service_diversity.current_n_unique_per_hour` | PHASE verification |
| diversity_injection.json | `service_diversity.min_entropy` | PHASE verification |
| diversity_injection.json | `service_diversity.target_service_distribution` | PHASE verification |

> Check whether PHASE's iteration comparison or verification pipelines read these
> before pruning. If they only exist in the deployed config (not read back by PHASE),
> they're safe to remove.
