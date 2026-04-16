"""
Behavioral configuration loader.

Loads behavioral configs from a drop-in directory for hot-swappable
behavior adjustment. The PHASE Feedback Engine generates per-behavior JSON
files that adjust workflow weights, behavior modifiers, site config,
and prompt augmentation.

Directory auto-discovery (matches AgentLogger pattern):
1. RUSE_BEHAVIOR_CONFIG_DIR env var
2. /opt/ruse/deployed_sups/<config_key>/behavioral_configurations/  (production)
3. <project_root>/deployed_sups/<config_key>/behavioral_configurations/     (development)

Supports two file naming conventions:
  - New (per-behavior directory): bare filenames (workflow_weights.json, etc.)
  - Legacy (flat directory): prefixed filenames (<config_key>_workflow_weights.json, etc.)

Supports two directory layouts:
  - Behavior directory: --behavior-config-dir points directly at M/, B.llama/, etc.
  - Experiment directory: --behavior-config-dir points at parent (e.g., exp3_axes-fall24/)
    and the behavior subdir is auto-resolved from config_key.
"""
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List


@dataclass
class BehavioralConfig:
    """Behavioral configuration for a single SUP."""
    workflow_weights: Optional[dict] = None     # {"BrowseWeb": 0.45, ...}
    behavior_modifiers: Optional[dict] = None   # {"page_dwell": {...}, ...}
    site_config: Optional[dict] = None          # {"site_categories": {...}}
    prompt_augmentation: Optional[dict] = None  # {"prompt_content": "..."}
    timing_profile: Optional[dict] = None       # calibrated timing profile
    # Feedback engine v2 configs
    variance_injection: Optional[dict] = None   # volume/timing variance targets
    diversity_injection: Optional[dict] = None   # service entropy + workflow rotation
    activity_pattern: Optional[dict] = None      # daily activity shape

    def is_empty(self) -> bool:
        return all(v is None for v in
                   [self.workflow_weights, self.behavior_modifiers,
                    self.site_config, self.prompt_augmentation,
                    self.timing_profile,
                    self.variance_injection, self.diversity_injection,
                    self.activity_pattern])


def config_key_to_behavior_dir(config_key: str) -> str:
    """
    Map a SUP config key to its PHASE behavior directory name.

    M1-M4 -> 'M', B0-B4.llama -> 'B.llama', S0-S4.gemma -> 'S.gemma', etc.
    Controls (C0, M0) return the key unchanged.
    """
    m = re.match(r'^([A-Z])\d+(?:\.(\w+))?$', config_key)
    if not m:
        return config_key
    return f"{m.group(1)}.{m.group(2)}" if m.group(2) else m.group(1)


def _resolve_behavior_subdir(path: Path, config_key: str) -> Path:
    """
    If path is an experiment-level directory containing behavior subdirs,
    resolve to the appropriate behavior subdir for this config key.

    If path already contains bare config JSON files, return it as-is.
    """
    # If the dir already has config JSONs, it's a behavior dir -- use directly
    if any(path.glob("workflow_weights.json")) or any(path.glob(f"{config_key}_*.json")):
        return path

    # Check for behavior subdir
    behavior = config_key_to_behavior_dir(config_key)
    subdir = path / behavior
    if subdir.is_dir():
        return subdir

    # No subdir found -- return original (loader will find nothing, which is fine)
    return path


def resolve_behavioral_config_dir(config_key: str, override_dir: Optional[str] = None) -> Path:
    """
    Resolve the behavioral configurations directory path using auto-discovery.

    Supports pointing at either a behavior directory (has *.json files)
    or an experiment directory (has M/, B.llama/, etc. subdirs).

    Args:
        config_key: SUP config key (e.g., 'M3', 'B3.gemma')
        override_dir: Optional explicit path (from --behavior-config-dir CLI flag)

    Returns:
        Path to the behavioral configurations directory (created if it doesn't exist)
    """
    if override_dir:
        path = Path(override_dir).expanduser()
        resolved = _resolve_behavior_subdir(path, config_key)
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    # 1. Check env var
    env_dir = os.environ.get("RUSE_BEHAVIOR_CONFIG_DIR")
    if env_dir:
        path = Path(env_dir).expanduser()
        resolved = _resolve_behavior_subdir(path, config_key)
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    # 2. Check deployed path
    deployed_base = Path("/opt/ruse/deployed_sups")
    if deployed_base.exists():
        path = deployed_base / config_key / "behavioral_configurations"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # 3. Development fallback: relative to project root
    project_root = Path(__file__).parent.parent.parent
    path = project_root / "deployed_sups" / config_key / "behavioral_configurations"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_behavioral_config(config_dir: Path, config_key: str) -> BehavioralConfig:
    """
    Load behavioral config for a SUP.

    Prefers the consolidated `behavior.json` layout emitted by the PHASE
    Feedback Engine from 2026-04-16 onward. Falls back to the legacy 8-file
    layout (one JSON per BehavioralConfig field) if behavior.json is absent,
    so deployments generated before the cutover still load.

    The 8 BehavioralConfig dataclass fields are populated from behavior.json
    sections per the mapping documented in `_from_behavior_json()` below.

    Args:
        config_dir: Path to the behavioral configurations directory
        config_key: SUP config key (used for legacy filename prefix)

    Returns:
        BehavioralConfig with loaded data, or empty config if dir not found
    """
    if not config_dir.exists():
        return BehavioralConfig()

    # New consolidated layout: single behavior.json
    consolidated = config_dir / "behavior.json"
    if consolidated.exists():
        try:
            with open(consolidated, "r") as f:
                return _from_behavior_json(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[behavior] Warning: Failed to load {consolidated.name}: {e}")
            return BehavioralConfig()

    # Legacy 8-file fallback — kept for the transition window. Remove once
    # all live PHASE feedback sources have been regenerated in the new layout.
    return _load_legacy_8_file(config_dir, config_key)


def _from_behavior_json(data: dict) -> BehavioralConfig:
    """
    Unpack a consolidated behavior.json into the 8 BehavioralConfig fields.

    The loader translates the new schema into the exact shape downstream
    consumers already read — dataclass fields are unchanged, but a few
    sections are re-keyed/re-nested so CalibratedTiming, emulation_loop,
    BackgroundServiceGenerator, and the brain loops see the structure they
    were written against. Keeping the translation here is what lets
    runtime code stay untouched.

    Section mapping and shape translation:

      timing.hourly_distribution
        → timing_profile.hourly_distribution.mean_fraction
      timing.burst_percentiles.{connections_per_burst,idle_gap_minutes,burst_duration_minutes}
        → timing_profile.burst_characteristics.*.percentiles
      timing.variance.{cluster_size_sigma,idle_gap_sigma}
        → variance_injection.* (pass-through scalars)
      timing.variance.hourly_std_targets.{volume,duration}: [24]
        → variance_injection.feature_variance_targets.{volume,duration}.hourly_std_target: [24]
        (re-keyed: plural 'targets' → nested singular 'target' for phase_timing.py D1)
      timing.activity_probability_per_hour
        → activity_pattern.daily_shape.per_hour_activity_probability
      content.workflow_weights
        → workflow_weights (pass-through)
      content.site_categories
        → site_config.site_categories
      behavior.{page_dwell,navigation_clicks,max_steps,...}
        → behavior_modifiers.* (pass-through)
      behavior.keep_alive_probability
        → behavior_modifiers.connection_reuse.keep_alive_probability
        (re-nested for MCHP G2 consumer in brains/mchp/agent.py)
      diversity.background_services.dns_per_hour
        → diversity_injection.background_services.dns_queries_per_hour
        (re-keyed for background_services.py)
      diversity.background_services.{http_head_per_hour,ntp_checks_per_day}
        → diversity_injection.background_services.* (pass-through)
      diversity.workflow_rotation
        → diversity_injection.workflow_rotation (pass-through)
      prompt_content
        → prompt_augmentation.prompt_content

    Sections absent from the file leave the corresponding BehavioralConfig
    field as None (matches the per-file loader's "missing = skipped"
    semantics). The _metadata section is ignored at runtime.
    """
    config = BehavioralConfig()

    timing = data.get("timing") or {}
    if timing:
        # timing_profile — shape expected by build_calibrated_timing_config()
        hourly = timing.get("hourly_distribution")
        burst_pct = timing.get("burst_percentiles") or {}
        if hourly is not None or burst_pct:
            config.timing_profile = {
                "dataset": "feedback",
                "hourly_distribution": {"mean_fraction": hourly},
                "burst_characteristics": {
                    "connections_per_burst": {
                        "percentiles": burst_pct.get("connections_per_burst", {}),
                    },
                    "idle_gap_minutes": {
                        "percentiles": burst_pct.get("idle_gap_minutes", {}),
                    },
                    "burst_duration_minutes": {
                        "percentiles": burst_pct.get("burst_duration_minutes", {}),
                    },
                },
            }

        # variance_injection — scalar sigmas pass through; hourly arrays are
        # re-keyed into the feature_variance_targets shape that phase_timing.py
        # D1 already reads.
        variance = timing.get("variance")
        if variance is not None:
            translated: dict = {}
            if "cluster_size_sigma" in variance:
                translated["cluster_size_sigma"] = variance["cluster_size_sigma"]
            if "idle_gap_sigma" in variance:
                translated["idle_gap_sigma"] = variance["idle_gap_sigma"]
            hstd = variance.get("hourly_std_targets") or {}
            if hstd:
                fvt: dict = {}
                if "volume" in hstd:
                    fvt["volume"] = {"hourly_std_target": hstd["volume"]}
                if "duration" in hstd:
                    fvt["duration"] = {"hourly_std_target": hstd["duration"]}
                if fvt:
                    translated["feature_variance_targets"] = fvt
            config.variance_injection = translated

        # activity_pattern — wrap under daily_shape for phase_timing G3 consumer
        activity_probs = timing.get("activity_probability_per_hour")
        if activity_probs is not None:
            config.activity_pattern = {
                "daily_shape": {
                    "per_hour_activity_probability": activity_probs,
                },
            }

    content = data.get("content") or {}
    if content:
        ww = content.get("workflow_weights")
        if ww is not None:
            config.workflow_weights = ww
        site_cats = content.get("site_categories")
        if site_cats is not None:
            config.site_config = {"site_categories": site_cats}

    # behavior_modifiers — pass through most keys; re-nest keep_alive_probability
    # under connection_reuse so MCHP's BrowseWeb G2 wiring still picks it up.
    behavior = data.get("behavior")
    if behavior is not None:
        modifiers = dict(behavior)
        if "keep_alive_probability" in modifiers:
            kap = modifiers.pop("keep_alive_probability")
            conn_reuse = dict(modifiers.get("connection_reuse") or {})
            conn_reuse["keep_alive_probability"] = kap
            modifiers["connection_reuse"] = conn_reuse
        config.behavior_modifiers = modifiers

    # diversity_injection — re-key dns_per_hour → dns_queries_per_hour so
    # BackgroundServiceGenerator finds the hourly array without change.
    diversity = data.get("diversity")
    if diversity is not None:
        translated_div = dict(diversity)
        bg = diversity.get("background_services")
        if bg is not None:
            bg_translated = dict(bg)
            if "dns_per_hour" in bg_translated:
                bg_translated["dns_queries_per_hour"] = bg_translated.pop("dns_per_hour")
            translated_div["background_services"] = bg_translated
        config.diversity_injection = translated_div

    if "prompt_content" in data:
        config.prompt_augmentation = {"prompt_content": data.get("prompt_content", "")}

    return config


def _load_legacy_8_file(config_dir: Path, config_key: str) -> BehavioralConfig:
    """Legacy loader — one JSON file per BehavioralConfig field.

    Kept as a transition fallback. Each field supports both bare filenames
    (per-behavior directory layout) and prefixed filenames (flat layout).
    """
    config = BehavioralConfig()

    file_map = {
        "workflow_weights": f"{config_key}_workflow_weights.json",
        "behavior_modifiers": f"{config_key}_behavior_modifiers.json",
        "site_config": f"{config_key}_site_config.json",
        "prompt_augmentation": f"{config_key}_prompt_augmentation.json",
        "timing_profile": f"{config_key}_timing_profile.json",
        "variance_injection": f"{config_key}_variance_injection.json",
        "diversity_injection": f"{config_key}_diversity_injection.json",
        "activity_pattern": f"{config_key}_activity_pattern.json",
    }

    for attr, legacy_filename in file_map.items():
        # Try bare filename first (new per-behavior directory layout)
        filepath = config_dir / f"{attr}.json"
        if not filepath.exists():
            # Fallback: legacy prefixed filename
            filepath = config_dir / legacy_filename
        if filepath.exists():
            try:
                with open(filepath, "r") as f:
                    setattr(config, attr, json.load(f))
            except (json.JSONDecodeError, OSError) as e:
                print(f"[behavior] Warning: Failed to load {filepath.name}: {e}")

    return config


def build_workflow_weights(workflows, behavioral_config: BehavioralConfig) -> Optional[List[float]]:
    """
    Build a weights list parallel to workflows for random.choices().

    Args:
        workflows: List of workflow objects (must have .name attribute)
        behavioral_config: BehavioralConfig with workflow_weights dict

    Returns:
        List of floats parallel to workflows, or None if no weights configured
    """
    if not behavioral_config.workflow_weights:
        return None

    weights = behavioral_config.workflow_weights
    result = []
    for w in workflows:
        # Try workflow name first, then class name
        name = getattr(w, 'name', None) or w.__class__.__name__
        weight = weights.get(name, weights.get(w.__class__.__name__, 1.0))
        result.append(float(weight))

    # Only return if at least one non-default weight was found
    if any(name in weights for w in workflows
           for name in [getattr(w, 'name', ''), w.__class__.__name__]):
        return result
    return None


def _category_weight(categories: dict, category: str, default: float = 1.0) -> float:
    """Extract a numeric weight from a category entry.

    Handles both flat format (category: 0.5) and PHASE dict format
    (category: {"weight": 0.5, "description": "..."}).
    """
    val = categories.get(category)
    if val is None:
        return default
    if isinstance(val, dict):
        return float(val.get("weight", default))
    return float(val)


def build_site_weights(website_list: list, site_config: dict) -> Optional[List[float]]:
    """
    Build weights for website selection based on site_config categories.

    The site_config should have a "site_categories" dict mapping category
    names to weights, and optionally a "domain_categories" dict mapping
    domain substrings to categories.

    Args:
        website_list: List of website domain strings
        site_config: Site config dict from PHASE

    Returns:
        List of floats parallel to website_list, or None if no config
    """
    if not site_config:
        return None

    categories = site_config.get("site_categories", {})
    domain_map = site_config.get("domain_categories", {})

    if not categories:
        return None

    default_weight = _category_weight(categories, "default", 1.0)
    result = []

    for site in website_list:
        site_lower = site.strip().lower()
        weight = default_weight

        # Check domain_categories for explicit mapping
        for domain_substr, category in domain_map.items():
            if domain_substr.lower() in site_lower:
                weight = _category_weight(categories, category, default_weight)
                break

        result.append(weight)

    return result


def build_calibrated_timing_config(timing_profile: dict):
    """
    Build a CalibratedTimingConfig from a timing_profile dict.

    Mirrors the structure of load_calibration_profile() in phase_timing.py
    but accepts an already-loaded dict (from behavioral config JSON) instead of
    reading from a bundled profile file.

    Args:
        timing_profile: Dict with hourly_distribution, burst_characteristics keys

    Returns:
        CalibratedTimingConfig instance
    """
    from common.timing.phase_timing import CalibratedTimingConfig

    burst = timing_profile["burst_characteristics"]
    return CalibratedTimingConfig(
        dataset=timing_profile.get("dataset", "default"),
        hourly_fractions=timing_profile["hourly_distribution"]["mean_fraction"],
        burst_duration=burst["burst_duration_minutes"]["percentiles"],
        idle_gap=burst["idle_gap_minutes"]["percentiles"],
        connections_per_burst=burst["connections_per_burst"]["percentiles"],
    )


def build_task_weights(task_list: list, site_config: dict) -> Optional[List[float]]:
    """
    Build weights for task selection based on site_config categories.

    Maps task text to categories using keyword matching, then applies
    category weights from site_config.

    Args:
        task_list: List of task description strings
        site_config: Site config dict from PHASE

    Returns:
        List of floats parallel to task_list, or None if no config
    """
    if not site_config:
        return None

    categories = site_config.get("site_categories", {})
    task_categories = site_config.get("task_categories", {})

    if not categories:
        return None

    default_weight = _category_weight(categories, "default", 1.0)
    result = []

    for task in task_list:
        task_lower = task.lower()
        weight = default_weight

        # Check task_categories for keyword matching
        for keyword, category in task_categories.items():
            if keyword.lower() in task_lower:
                weight = _category_weight(categories, category, default_weight)
                break

        result.append(weight)

    return result
