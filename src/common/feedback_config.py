"""
PHASE Feedback Engine configuration loader.

Loads feedback configs from a drop-in directory for hot-swappable
behavior adjustment. The PHASE Feedback Engine generates per-behavior JSON
files that adjust workflow weights, behavior modifiers, site config,
and prompt augmentation.

Directory auto-discovery (matches AgentLogger pattern):
1. RUSE_FEEDBACK_DIR env var
2. /opt/ruse/deployed_sups/<config_key>/feedback/  (production)
3. <project_root>/deployed_sups/<config_key>/feedback/     (development)

Supports two file naming conventions:
  - New (per-behavior directory): bare filenames (workflow_weights.json, etc.)
  - Legacy (flat directory): prefixed filenames (<config_key>_workflow_weights.json, etc.)

Supports two directory layouts:
  - Behavior directory: --feedback-dir points directly at M/, B.llama/, etc.
  - Experiment directory: --feedback-dir points at parent (e.g., exp3_axes-fall24/)
    and the behavior subdir is auto-resolved from config_key.
"""
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List


@dataclass
class FeedbackConfig:
    """PHASE feedback configuration for a single SUP."""
    workflow_weights: Optional[dict] = None     # {"BrowseWeb": 0.45, ...}
    behavior_modifiers: Optional[dict] = None   # {"page_dwell": {...}, ...}
    site_config: Optional[dict] = None          # {"site_categories": {...}}
    prompt_augmentation: Optional[dict] = None  # {"prompt_content": "..."}
    timing_profile: Optional[dict] = None       # calibrated timing profile

    def is_empty(self) -> bool:
        return all(v is None for v in
                   [self.workflow_weights, self.behavior_modifiers,
                    self.site_config, self.prompt_augmentation,
                    self.timing_profile])


def config_key_to_behavior_dir(config_key: str) -> str:
    """
    Map a SUP config key to its PHASE behavior directory name.

    M1-M4 → 'M', B0-B4.llama → 'B.llama', S0-S4.gemma → 'S.gemma', etc.
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
    # If the dir already has config JSONs, it's a behavior dir — use directly
    if any(path.glob("workflow_weights.json")) or any(path.glob(f"{config_key}_*.json")):
        return path

    # Check for behavior subdir
    behavior = config_key_to_behavior_dir(config_key)
    subdir = path / behavior
    if subdir.is_dir():
        return subdir

    # No subdir found — return original (loader will find nothing, which is fine)
    return path


def resolve_feedback_dir(config_key: str, override_dir: Optional[str] = None) -> Path:
    """
    Resolve the feedback directory path using auto-discovery.

    Supports pointing at either a behavior directory (has *.json files)
    or an experiment directory (has M/, B.llama/, etc. subdirs).

    Args:
        config_key: SUP config key (e.g., 'M3', 'B3.gemma')
        override_dir: Optional explicit path (from --feedback-dir CLI flag)

    Returns:
        Path to the feedback directory (created if it doesn't exist)
    """
    if override_dir:
        path = Path(override_dir).expanduser()
        resolved = _resolve_behavior_subdir(path, config_key)
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    # 1. Check env var
    env_dir = os.environ.get("RUSE_FEEDBACK_DIR")
    if env_dir:
        path = Path(env_dir).expanduser()
        resolved = _resolve_behavior_subdir(path, config_key)
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    # 2. Check deployed path
    deployed_base = Path("/opt/ruse/deployed_sups")
    if deployed_base.exists():
        path = deployed_base / config_key / "feedback"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # 3. Development fallback: relative to project root
    project_root = Path(__file__).parent.parent.parent
    path = project_root / "deployed_sups" / config_key / "feedback"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_feedback_config(feedback_dir: Path, config_key: str) -> FeedbackConfig:
    """
    Load feedback config files from the feedback directory.

    Missing files are silently skipped (fields stay None).

    Args:
        feedback_dir: Path to the feedback directory
        config_key: SUP config key for filename prefix

    Returns:
        FeedbackConfig with loaded data, or empty config if dir not found
    """
    config = FeedbackConfig()

    if not feedback_dir.exists():
        return config

    file_map = {
        "workflow_weights": f"{config_key}_workflow_weights.json",
        "behavior_modifiers": f"{config_key}_behavior_modifiers.json",
        "site_config": f"{config_key}_site_config.json",
        "prompt_augmentation": f"{config_key}_prompt_augmentation.json",
        "timing_profile": f"{config_key}_timing_profile.json",
    }

    for attr, legacy_filename in file_map.items():
        # Try bare filename first (new per-behavior directory layout)
        filepath = feedback_dir / f"{attr}.json"
        if not filepath.exists():
            # Fallback: legacy prefixed filename
            filepath = feedback_dir / legacy_filename
        if filepath.exists():
            try:
                with open(filepath, "r") as f:
                    setattr(config, attr, json.load(f))
            except (json.JSONDecodeError, OSError) as e:
                print(f"[feedback] Warning: Failed to load {filepath.name}: {e}")

    return config


def build_workflow_weights(workflows, feedback_config: FeedbackConfig) -> Optional[List[float]]:
    """
    Build a weights list parallel to workflows for random.choices().

    Args:
        workflows: List of workflow objects (must have .name attribute)
        feedback_config: FeedbackConfig with workflow_weights dict

    Returns:
        List of floats parallel to workflows, or None if no weights configured
    """
    if not feedback_config.workflow_weights:
        return None

    weights = feedback_config.workflow_weights
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

    default_weight = categories.get("default", 1.0)
    result = []

    for site in website_list:
        site_lower = site.strip().lower()
        weight = default_weight

        # Check domain_categories for explicit mapping
        for domain_substr, category in domain_map.items():
            if domain_substr.lower() in site_lower:
                weight = categories.get(category, default_weight)
                break

        result.append(float(weight))

    return result


def build_calibrated_timing_config(timing_profile: dict):
    """
    Build a CalibratedTimingConfig from a timing_profile dict.

    Mirrors the structure of load_calibration_profile() in phase_timing.py
    but accepts an already-loaded dict (from feedback JSON) instead of
    reading from a bundled profile file.

    Args:
        timing_profile: Dict with hourly_distribution, burst_characteristics keys

    Returns:
        CalibratedTimingConfig instance
    """
    from common.timing.phase_timing import CalibratedTimingConfig

    burst = timing_profile["burst_characteristics"]
    return CalibratedTimingConfig(
        dataset=timing_profile.get("dataset", "feedback"),
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

    default_weight = categories.get("default", 1.0)
    result = []

    for task in task_list:
        task_lower = task.lower()
        weight = default_weight

        # Check task_categories for keyword matching
        for keyword, category in task_categories.items():
            if keyword.lower() in task_lower:
                weight = categories.get(category, default_weight)
                break

        result.append(float(weight))

    return result
