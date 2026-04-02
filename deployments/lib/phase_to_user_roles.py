#!/usr/bin/env python3
"""Translate PHASE per-node behavioral configs into a user-roles.json.

Reads per-node feedback directories from PHASE output and produces a modified
user-roles.json where each endpoint node gets its own role with PHASE-derived
timing overrides. Also generates a matching enterprise config with updated
role references.

Usage:
    python3 phase_to_user_roles.py <feedback_dir> <baseline_roles> <enterprise_config> \
        [--output-dir dir]

Missing configs are handled gracefully — each mapping falls back to baseline values.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from pathlib import Path


# ── PHASE → pyhuman workflow name mapping ────────────────────────────────

PHASE_TO_PYHUMAN = {
    "BrowseWeb": "browse_web",
    "GoogleSearch": "google_search",
    "WebSearch": "google_search",       # pyhuman has no separate web search
    "BrowseYoutube": "browse_youtube",
    "DownloadFiles": "download_files",
    "SpawnShell": "spawn_shell",
    "ExecuteCommand": "spawn_shell",
}

# Enterprise-only pyhuman workflows with no PHASE equivalent.
# Always retained regardless of PHASE weights.
ENTERPRISE_ONLY_WORKFLOWS = {"browse_iis", "browse_shibboleth", "moodle", "build_software"}

# Default pyhuman timing parameters (matching current hardcoded values)
DEFAULT_CLUSTERSIZE = "5"
DEFAULT_TASKINTERVAL = "10"
DEFAULT_TASKGROUPINTERVAL = "500"


# ── Config loading ──────────────────────────────────────────────────────

def _load(feedback_dir: Path, name: str) -> dict | None:
    path = feedback_dir / name
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _find_node_feedback_dir(feedback_base: Path, node_name: str) -> Path | None:
    """Find PHASE feedback directory for a node.

    PHASE uses double-nested structure: {node}/{node}/{configs}.
    Falls back to single-nested: {node}/{configs}.
    """
    # Double-nested (standard PHASE output)
    candidate = feedback_base / node_name / node_name
    if candidate.is_dir() and any(candidate.glob("*.json")):
        return candidate

    # Single-nested fallback
    candidate = feedback_base / node_name
    if candidate.is_dir() and any(candidate.glob("*.json")):
        return candidate

    return None


# ── Per-field translators ───────────────────────────────────────────────

def _extract_day_start(activity: dict | None, baseline_role: dict) -> tuple[str, str]:
    """Extract day_start_hour_min/max from activity_pattern."""
    if not activity:
        return baseline_role["day_start_hour_min"], baseline_role["day_start_hour_max"]

    daily = activity.get("daily_shape", {})
    rng = daily.get("active_hour_range", None)
    if not rng or len(rng) != 2:
        return baseline_role["day_start_hour_min"], baseline_role["day_start_hour_max"]

    start = int(rng[0])
    # Give a 4-hour window for start variation, capped at active range end
    end = min(start + 4, int(rng[1]))
    return str(start), str(end)


def _extract_daily_hours(
    activity: dict | None, baseline_role: dict,
) -> tuple[list[str], list[str]]:
    """Extract activity_daily_min/max_hours from activity_pattern.

    Distributes target_active_hours across a 7-day week with weekend reduction.
    Index 0 = Monday, 6 = Sunday (matching user-roles.json convention).
    """
    if not activity:
        return baseline_role["activity_daily_min_hours"], baseline_role["activity_daily_max_hours"]

    daily = activity.get("daily_shape", {})
    target = daily.get("target_active_hours", None)
    if target is None:
        return baseline_role["activity_daily_min_hours"], baseline_role["activity_daily_max_hours"]

    target = float(target)

    # Weekday: target * 0.8 min, target * 1.0 max
    # Weekend: target * 0.2 min, target * 0.5 max
    # Capped at 22 hours
    wd_min = str(max(0, int(target * 0.8)))
    wd_max = str(min(22, int(target * 1.0)))
    we_min = str(max(0, int(target * 0.2)))
    we_max = str(min(22, int(target * 0.5)))

    #                    Mon     Tue     Wed     Thu     Fri     Sat     Sun
    min_hours = [we_min, wd_min, wd_min, wd_min, wd_min, wd_min, we_min]
    max_hours = [we_max, wd_max, wd_max, wd_max, wd_max, wd_max, we_max]
    return min_hours, max_hours


def _extract_logins_per_hour(
    activity: dict | None, baseline_role: dict,
) -> tuple[str, str]:
    """Extract activity_min/max_logins_per_hour from per-hour probabilities."""
    if not activity:
        return baseline_role["activity_min_logins_per_hour"], baseline_role["activity_max_logins_per_hour"]

    daily = activity.get("daily_shape", {})
    probs = daily.get("per_hour_activity_probability", [])
    if not probs:
        return baseline_role["activity_min_logins_per_hour"], baseline_role["activity_max_logins_per_hour"]

    # Scale probabilities to login counts (probability * scale_factor)
    # A probability of 0.0417 (1/24, uniform) ≈ baseline login rate
    scale = 12  # maps 0.0417 → ~0.5, 0.1 → ~1.2
    active_probs = [p for p in probs if p > 0.001]
    if not active_probs:
        return baseline_role["activity_min_logins_per_hour"], baseline_role["activity_max_logins_per_hour"]

    min_rate = max(0, int(min(active_probs) * scale))
    max_rate = max(1, int(max(active_probs) * scale) + 1)
    return str(min_rate), str(max_rate)


def _extract_login_length(
    modifiers: dict | None, baseline_role: dict,
) -> tuple[str, str]:
    """Extract min/max_login_length from behavior_modifiers page_dwell.

    Page dwell is per-page; a login session contains many page views.
    Scale up to session-level durations.
    """
    if not modifiers:
        return baseline_role["min_login_length"], baseline_role["max_login_length"]

    dwell = modifiers.get("page_dwell", {})
    min_s = dwell.get("min_seconds", None)
    max_s = dwell.get("max_seconds", None)
    if min_s is None or max_s is None:
        return baseline_role["min_login_length"], baseline_role["max_login_length"]

    # A session is many page views: scale dwell by ~100x for session length
    # min_login_length: at least 1 second
    # max_login_length: capped at 14400 (4 hours)
    session_min = str(max(1, int(min_s)))
    session_max = str(min(14400, int(max_s * 100)))
    return session_min, session_max


def _extract_workflows(
    workflow_weights: dict | None, baseline_role: dict,
) -> list[str]:
    """Extract filtered workflow list from workflow_weights.json.

    Maps PHASE CamelCase names to pyhuman snake_case. Drops workflows
    with weight == 0. Enterprise-only workflows (no PHASE equivalent)
    are always retained.
    """
    baseline_workflows = list(baseline_role.get("workflows", []))
    if not workflow_weights:
        return baseline_workflows

    raw = workflow_weights.get("workflow_weights", {})
    if not raw or not isinstance(raw, dict):
        return baseline_workflows

    # Determine which pyhuman workflows PHASE wants to keep vs drop
    phase_keep: set[str] = set()
    phase_drop: set[str] = set()
    for phase_name, weight in raw.items():
        if not isinstance(weight, (int, float)):
            continue
        pyhuman_name = PHASE_TO_PYHUMAN.get(phase_name)
        if not pyhuman_name:
            continue  # Unknown PHASE workflow (e.g. OpenOfficeWriter)
        if weight > 0:
            phase_keep.add(pyhuman_name)
        else:
            phase_drop.add(pyhuman_name)

    # Build result: start from baseline, drop zero-weight PHASE-mapped workflows
    result = []
    for wf in baseline_workflows:
        if wf in ENTERPRISE_ONLY_WORKFLOWS:
            result.append(wf)  # Always keep enterprise-only
        elif wf in phase_drop and wf not in phase_keep:
            continue  # PHASE says weight=0 for this one
        else:
            result.append(wf)

    return result if result else baseline_workflows


def _extract_timing_params(
    timing: dict | None, variance: dict | None, baseline_role: dict,
) -> tuple[str, str, str]:
    """Extract clustersize/taskinterval/taskgroupinterval from timing_profile.json.

    Maps:
      connections_per_burst p50 → clustersize
      idle_gap_minutes p50 → taskgroupinterval (minutes → seconds)
    taskinterval is scaled proportionally from clustersize.

    If variance_injection.json provides cluster_size_cv, applies deterministic
    per-node jitter to clustersize using a hash of the role name.

    Returns (clustersize, taskinterval, taskgroupinterval) as strings.
    """
    fallback = (
        baseline_role.get("clustersize", DEFAULT_CLUSTERSIZE),
        baseline_role.get("taskinterval", DEFAULT_TASKINTERVAL),
        baseline_role.get("taskgroupinterval", DEFAULT_TASKGROUPINTERVAL),
    )
    if not timing:
        return fallback

    burst = timing.get("burst_characteristics", {})
    cpb = burst.get("connections_per_burst", {}).get("percentiles", {})
    idle = burst.get("idle_gap_minutes", {}).get("percentiles", {})

    cpb_p50 = cpb.get("50")
    idle_p50 = idle.get("50")

    if cpb_p50 is None and idle_p50 is None:
        return fallback

    # clustersize: connections_per_burst p50, clamped [1, 50]
    if cpb_p50 is not None:
        cs = max(1, min(50, int(round(float(cpb_p50)))))
    else:
        cs = int(fallback[0])

    # taskgroupinterval: idle_gap_minutes p50 → seconds, clamped [10, 3600]
    if idle_p50 is not None:
        tgi = max(10, min(3600, int(round(float(idle_p50) * 60))))
    else:
        tgi = int(fallback[2])

    # Apply variance jitter to clustersize if available
    if variance:
        cv = variance.get("volume_variance", {}).get("cluster_size_cv")
        if cv is not None and float(cv) > 0:
            # Deterministic per-node jitter using hash of role name
            node_hash = int(hashlib.md5(
                baseline_role.get("name", "").encode()
            ).hexdigest()[:8], 16)
            direction = (node_hash % 200 - 100) / 100.0  # [-1.0, 1.0)
            cs = max(1, min(50, int(round(cs * (1 + float(cv) * 0.5 * direction)))))

    # taskinterval: scale relative to clustersize (2s per task), clamped [1, 120]
    ti = max(1, min(120, cs * 2))

    return str(cs), str(ti), str(tgi)


# ── Main generator ──────────────────────────────────────────────────────

def generate_user_roles(
    feedback_dir: Path,
    baseline_user_roles_path: Path,
    enterprise_config_path: Path,
    output_dir: Path,
) -> dict:
    """Read PHASE per-node configs and produce user-roles + enterprise config.

    Returns dict with:
        user_roles_path: Path to generated user-roles-feedback.json
        enterprise_config_path: Path to generated enterprise-config-feedback.json
        role_count: number of per-node roles created
        nodes_processed: list of node names processed
    """
    # Load baseline roles
    baseline = json.loads(baseline_user_roles_path.read_text())
    roles_by_name = {r["name"]: r for r in baseline["roles"]}

    # Load enterprise config
    enterprise = json.loads(enterprise_config_path.read_text())
    nodes = enterprise.get("nodes", [])

    # Track which baseline roles are still needed (for nodes without feedback)
    needed_baseline_roles = set()
    per_node_roles = []
    nodes_processed = []
    modified_enterprise = copy.deepcopy(enterprise)

    for i, node in enumerate(nodes):
        node_name = node["name"]
        user_role_name = node.get("user")

        # Skip nodes without a user (dc1-3, linep1, etc.)
        if not user_role_name:
            continue

        # Find baseline role for this node
        baseline_role = roles_by_name.get(user_role_name)
        if not baseline_role:
            needed_baseline_roles.add(user_role_name)
            continue

        # Find PHASE feedback for this node
        node_feedback_dir = _find_node_feedback_dir(feedback_dir, node_name)
        if not node_feedback_dir:
            # No PHASE feedback — keep baseline role
            needed_baseline_roles.add(user_role_name)
            continue

        # Load PHASE configs
        activity = _load(node_feedback_dir, "activity_pattern.json")
        modifiers = _load(node_feedback_dir, "behavior_modifiers.json")
        workflow_weights = _load(node_feedback_dir, "workflow_weights.json")
        timing = _load(node_feedback_dir, "timing_profile.json")
        variance = _load(node_feedback_dir, "variance_injection.json")

        # Clone baseline and override with PHASE values
        role = copy.deepcopy(baseline_role)
        role["name"] = f"{node_name}_user"

        role["day_start_hour_min"], role["day_start_hour_max"] = \
            _extract_day_start(activity, baseline_role)

        role["activity_daily_min_hours"], role["activity_daily_max_hours"] = \
            _extract_daily_hours(activity, baseline_role)

        role["activity_min_logins_per_hour"], role["activity_max_logins_per_hour"] = \
            _extract_logins_per_hour(activity, baseline_role)

        role["min_login_length"], role["max_login_length"] = \
            _extract_login_length(modifiers, baseline_role)

        role["workflows"] = _extract_workflows(workflow_weights, baseline_role)

        role["clustersize"], role["taskinterval"], role["taskgroupinterval"] = \
            _extract_timing_params(timing, variance, baseline_role)

        per_node_roles.append(role)
        nodes_processed.append(node_name)

        # Update enterprise config to reference the per-node role
        modified_enterprise["nodes"][i]["user"] = role["name"]

    # Build final roles list: per-node roles + any still-needed baseline roles
    all_roles = list(per_node_roles)
    for role_name in sorted(needed_baseline_roles):
        if role_name in roles_by_name:
            all_roles.append(roles_by_name[role_name])

    output_roles = {"roles": all_roles}

    # Write outputs
    roles_path = output_dir / "user-roles-feedback.json"
    enterprise_path = output_dir / "enterprise-config-feedback.json"

    roles_path.write_text(json.dumps(output_roles, indent=2) + "\n")
    enterprise_path.write_text(json.dumps(modified_enterprise, indent=2) + "\n")

    return {
        "user_roles_path": roles_path,
        "enterprise_config_path": enterprise_path,
        "role_count": len(per_node_roles),
        "nodes_processed": nodes_processed,
    }


# ── CLI entry point ─────────────────────────────────────────────────────

def main() -> int:
    if len(sys.argv) < 4:
        print(
            "Usage: phase_to_user_roles.py <feedback_dir> <baseline_roles> "
            "<enterprise_config> [--output-dir dir]",
            file=sys.stderr,
        )
        return 1

    feedback_dir = Path(sys.argv[1])
    baseline_path = Path(sys.argv[2])
    enterprise_path = Path(sys.argv[3])

    output_dir = Path(".")
    if len(sys.argv) > 5 and sys.argv[4] == "--output-dir":
        output_dir = Path(sys.argv[5])
    output_dir.mkdir(parents=True, exist_ok=True)

    for p, label in [(feedback_dir, "feedback dir"), (baseline_path, "baseline roles"),
                     (enterprise_path, "enterprise config")]:
        if not p.exists():
            print(f"Not found ({label}): {p}", file=sys.stderr)
            return 1

    result = generate_user_roles(feedback_dir, baseline_path, enterprise_path, output_dir)
    print(f"Generated {result['role_count']} per-node roles for: {', '.join(result['nodes_processed'])}")
    print(f"  User roles: {result['user_roles_path']}")
    print(f"  Enterprise: {result['enterprise_config_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
