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
    diversity_injection: Optional[dict] = None   # service entropy + workflow rotation + topology mimicry
    activity_pattern: Optional[dict] = None      # daily activity shape
    # Per-target content pools for feedback-only workflows. PHASE writes
    # these into content.{download_url_pool, whois_domain_pool}; the
    # workflows fall back to module-level lists if missing/empty.
    # download_size_pref is intentionally NOT consumed — schema marks
    # it as informational only.
    download_url_pool: Optional[List[str]] = None
    whois_domain_pool: Optional[List[str]] = None
    # PHASE ablation gate metadata — explains why sections may be intentionally
    # absent. When present, missing sections are not bugs but deliberate
    # omissions by PHASE's feedback engine (ablation showed that section's
    # knobs don't move the score on this dataset's target model). Runtime
    # code should emit [INFO] instead of [WARNING] in that case.
    ablation_gate: Optional[dict] = None
    # Generation mode marker from PHASE's _metadata.mode. "dumb_baseline" =
    # PHASE's degenerate single-hour-active baseline (used for ruse-controls).
    # Schema deviates from the calibrated PHASE feedback shape — flat
    # burst_percentiles, integer page_dwell, etc. Runtime detects this and
    # skips empirical timing/variance/activity setup that would KeyError
    # against the dumb schema.
    mode: Optional[str] = None

    def is_empty(self) -> bool:
        return all(v is None for v in
                   [self.workflow_weights, self.behavior_modifiers,
                    self.site_config, self.prompt_augmentation,
                    self.timing_profile,
                    self.variance_injection, self.diversity_injection,
                    self.activity_pattern])

    def topology_mimicry(self) -> Optional[dict]:
        """Return PHASE's topology_mimicry rates for this SUP, or None.

        Populated by PHASE as diversity.topology_mimicry. Consumed off-host
        by the per-deploy neighborhood sidecar VM — the SUP itself does not
        act on these values. Sidecar aggregates each SUP's rates into a
        sups.json master config and synthesizes inbound TCP/UDP probes
        targeting each SUP IP.

        See docs/topology-mimicry.md for the full rate schema and rationale.
        """
        if not self.diversity_injection:
            return None
        return self.diversity_injection.get("topology_mimicry")

    def is_ablation_gated(self) -> bool:
        """True if PHASE's ablation engine gated any sections off.

        Used by warning emitters to distinguish intentional omissions
        from unexpected ones.
        """
        if not self.ablation_gate:
            return False
        # ablation is active if any feature is in inactive/flat_zero OR
        # gating_features is set (the presence of gate metadata at all
        # means PHASE consciously decided what to include).
        return bool(
            self.ablation_gate.get("inactive")
            or self.ablation_gate.get("flat_zero")
            or self.ablation_gate.get("gating_features")
        )


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


def load_workflow_gates(config_dir: Path) -> dict:
    """Read just the {enable_whois, enable_download} flags from behavior.json.

    Used by brain workflow loaders to decide whether to register the
    feedback-content workflows (whois_lookup, download_files) BEFORE the
    full BehavioralConfig load. PHASE's dumb_baseline emits both as false
    to signal a single-workflow degenerate mode; PHASE feedback proper
    emits true (or omits, which we default to true).

    Missing file or malformed JSON returns defaults (both True) and lets
    the subsequent load_behavioral_config() raise the loud fail message.
    No partial validation here — this is just a peek at two booleans.
    """
    path = config_dir / "behavior.json"
    if not path.exists():
        return {"enable_whois": True, "enable_download": True}
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"enable_whois": True, "enable_download": True}
    beh = data.get("behavior") or {}
    return {
        "enable_whois":    bool(beh.get("enable_whois", True)),
        "enable_download": bool(beh.get("enable_download", True)),
    }


def load_behavioral_config(config_dir: Path, config_key: str) -> BehavioralConfig:
    """Load the consolidated behavior.json for a SUP.

    Every RUSE SUP (except C0/M0 controls, which have no service that reads
    behavior.json) MUST have a behavior.json. The deploy system writes either
    the controls defaults or PHASE-tuned feedback into
    /opt/ruse/deployed_sups/<key>/behavioral_configurations/behavior.json
    before starting the service.

    Missing file → loud RuntimeError. The brain service crash-loops; audit
    surfaces the failure on the next sweep. Previously this returned an
    empty BehavioralConfig and agents fell through to hardcoded defaults,
    which silently masked broken distribution and left no signal that
    feedback never landed.

    Malformed JSON also raises (json.JSONDecodeError) — same fail-loud
    semantics, same diagnostic path.
    """
    path = config_dir / "behavior.json"
    if not path.exists():
        msg = (
            f"\n\n"
            f"==============================================================\n"
            f"  RUSE BEHAVIORAL CONFIG MISSING — REFUSING TO START\n"
            f"==============================================================\n"
            f"  config_key:        {config_key}\n"
            f"  expected location: {path}\n"
            f"\n"
            f"  Every RUSE SUP must have a behavior.json before service\n"
            f"  start. The deploy pipeline either failed to distribute it,\n"
            f"  or someone removed it post-deploy.\n"
            f"\n"
            f"  Fix:\n"
            f"    1. Re-run distribute-behavior-configs.yaml against this VM, OR\n"
            f"    2. Re-deploy the SUP from scratch.\n"
            f"\n"
            f"  Do NOT fall back to legacy / hardcoded defaults — feedback\n"
            f"  must be verified end-to-end on every run.\n"
            f"==============================================================\n"
        )
        # Print to stderr so it lands in systemd_error.log even if logger
        # isn't configured yet, then raise to crash the service.
        import sys
        print(msg, file=sys.stderr, flush=True)
        raise RuntimeError(
            f"behavior.json missing for {config_key} at {path}"
        )

    # File present. A malformed JSON here means the source emitted a broken
    # file or the copy corrupted it — fail loud, don't silently degrade.
    with open(path, "r") as f:
        data = json.load(f)

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

    # Pull ablation gate metadata if PHASE emitted it. Presence signals
    # that missing sections are deliberate omissions, not generator bugs.
    metadata = data.get("_metadata") or {}
    ablation_gate = metadata.get("ablation_gate")
    mode = metadata.get("mode")

    # Feedback-only content pools (per-target). Empty list or missing key
    # → None → workflows fall back to their module-level FALLBACK_* lists.
    download_url_pool = content.get("download_url_pool")
    if not download_url_pool:
        download_url_pool = None
    whois_domain_pool = content.get("whois_domain_pool")
    if not whois_domain_pool:
        whois_domain_pool = None

    return BehavioralConfig(
        timing_profile=timing or None,
        variance_injection=timing.get("variance"),
        activity_pattern=activity,
        workflow_weights=content.get("workflow_weights"),
        site_config=content.get("site_categories"),
        behavior_modifiers=data.get("behavior"),
        diversity_injection=data.get("diversity"),
        prompt_augmentation=prompt,
        ablation_gate=ablation_gate,
        download_url_pool=download_url_pool,
        whois_domain_pool=whois_domain_pool,
        mode=mode,
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
