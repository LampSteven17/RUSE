"""
Behavioral configuration loader.

Loads a single behavior.json per SUP for hot-swappable behavior adjustment.
PHASE's Feedback Engine emits one file per SUP config at:

  {feedback_source}/{behavior_dir}/{config_key}/behavior.json

e.g. axes-ruse-controls_axes-summer24_std-ctrls/B.gemma/B0.gemma/behavior.json

At runtime the deploy playbook copies it into:

  /opt/ruse/deployed_sups/<config_key>/behavioral_configurations/behavior.json

Directory auto-discovery:
  1. RUSE_BEHAVIOR_CONFIG_DIR env var
  2. /opt/ruse/deployed_sups/<config_key>/behavioral_configurations/  (prod)
  3. <project_root>/deployed_sups/<config_key>/behavioral_configurations/ (dev)

If --behavior-config-dir points at an experiment-level dir (containing
M/, B.gemma/, S.gemma/ subdirs), the behavior subdir is auto-resolved
from config_key. Pointing at a SUP-level dir is also supported.
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
    site_config: Optional[dict] = None          # {category: weight} — flat from content.site_categories
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
    """Resolve an experiment-level dir to the SUP-level subdir, or return as-is.

    If `path` contains behavior.json it's already a SUP dir — use directly.
    Otherwise drill one level via config_key_to_behavior_dir() / config_key.
    """
    if (path / "behavior.json").exists():
        return path

    behavior = config_key_to_behavior_dir(config_key)
    subdir = path / behavior / config_key
    if subdir.is_dir():
        return subdir
    # Fall back to {behavior}/ if operator gave an already-narrowed dir
    subdir = path / behavior
    if subdir.is_dir():
        return subdir

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
    """Load the consolidated behavior.json for a SUP.

    PHASE emits one file per SUP at {config_dir}/behavior.json. Its sections
    map 1:1 onto BehavioralConfig fields; downstream consumers read each
    section's shape verbatim, so this loader does no translation.

    Returns an empty BehavioralConfig if the dir or file is missing — that's
    the baseline case (V0/V1 runs with no PHASE feedback).
    """
    path = config_dir / "behavior.json"
    if not path.exists():
        return BehavioralConfig()

    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[behavior] Warning: Failed to load {path}: {e}")
        return BehavioralConfig()

    timing = data.get("timing") or {}
    content = data.get("content") or {}

    # activity_pattern is carved from timing — activity_probability_per_hour
    # and the long-idle knobs logically belong to the activity config even
    # though PHASE groups them with timing in the on-disk file.
    activity: Optional[dict] = None
    if any(k in timing for k in
           ("activity_probability_per_hour", "long_idle_probability",
            "long_idle_duration_minutes")):
        activity = {
            k: timing[k] for k in
            ("activity_probability_per_hour", "long_idle_probability",
             "long_idle_duration_minutes")
            if k in timing
        }

    prompt: Optional[dict] = None
    if "prompt_content" in data:
        prompt = {"prompt_content": data["prompt_content"]}

    return BehavioralConfig(
        timing_profile=timing or None,
        variance_injection=timing.get("variance"),
        activity_pattern=activity,
        workflow_weights=content.get("workflow_weights"),
        site_config=content.get("site_categories"),
        behavior_modifiers=data.get("behavior"),
        diversity_injection=data.get("diversity"),
        prompt_augmentation=prompt,
    )


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


def build_calibrated_timing_config(timing_profile: dict):
    """Build a CalibratedTimingConfig from the behavior.json timing section.

    timing_profile is the raw `timing` dict from behavior.json:
      hourly_distribution:  [24 floats summing to 1]
      burst_percentiles:
        connections_per_burst: {"5":..,"25":..,"50":..,"75":..,"95":..,"max":..}
        idle_gap_minutes:      {"5":..,"25":..,"50":..,"75":..,"95":..}
        burst_duration_minutes:{"5":..,"25":..,"50":..,"75":..,"95":..}
    """
    from common.timing.phase_timing import CalibratedTimingConfig

    burst = timing_profile["burst_percentiles"]
    return CalibratedTimingConfig(
        dataset="feedback",
        hourly_fractions=timing_profile["hourly_distribution"],
        burst_duration=burst["burst_duration_minutes"],
        idle_gap=burst["idle_gap_minutes"],
        connections_per_burst=burst["connections_per_burst"],
    )
