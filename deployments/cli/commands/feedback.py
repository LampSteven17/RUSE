"""Feedback source auto-detection and config generation."""

from __future__ import annotations

import json
from pathlib import Path

from .. import output


FEEDBACK_BASE = Path.home() / "PHASE" / "feedback_engine" / "configs"

# GPU-conserving 5-VM feedback template (gemma4 cutover 2026-04-08):
# gemma only, V100 only (no RTX) — V100 uses gemma4:26b, CPU uses gemma4:e2b.
FEEDBACK_TEMPLATE = [
    {"behavior": "M2",        "flavor": "v1.14vcpu.28g",        "count": 1},
    {"behavior": "B2.gemma",  "flavor": "v100-1gpu.14vcpu.28g", "count": 1},
    {"behavior": "S2.gemma",  "flavor": "v100-1gpu.14vcpu.28g", "count": 1},
    {"behavior": "B2C.gemma", "flavor": "v1.14vcpu.28g",        "count": 1},
    {"behavior": "S2C.gemma", "flavor": "v1.14vcpu.28g",        "count": 1},
]

# Maps PHASE manifest dataset names → short abbreviations for deployment directory names.
# Used by generate_feedback_config() to abbreviate "axes-summer24" → "sum24" etc.
# Lookup: try exact match on full dataset name first, then substring (longest key first).
DATASET_ABBREVIATIONS = {
    # PHASE canonical names (exact match from manifest "dataset" field)
    "axes-summer24": "sum24",
    "axes-fall24": "fall24",
    "axes-spring25": "spr25",
    "axes-summer25": "sum25",
    "axes-fall25": "fall25",
    "axes-2025": "2025",
    "axes-all": "axall",
    "axes-year": "axyear",
    "cptc8-23": "cptc8",
    "cptc9-24": "cptc9",
    "vt-fall22-1gb": "vt1g",
    "vt-fall22-10gb": "vt10g",
    "vt-fall22-50gb": "vt50g",
    # Short aliases (for CLI --target convenience and substring fallback)
    "summer24": "sum24",
    "sum24": "sum24",
    "fall24": "fall24",
    "spring25": "spr25",
    "spr25": "spr25",
    "summer25": "sum25",
    "sum25": "sum25",
    "fall25": "fall25",
    "cptc8": "cptc8",
    "cptc9": "cptc9",
    "vt-1gb": "vt1g",
    "vt1g": "vt1g",
    "vt-10gb": "vt10g",
    "vt10g": "vt10g",
    "vt-50gb": "vt50g",
    "vt50g": "vt50g",
}


def _abbreviate_dataset(dataset: str) -> str:
    """Abbreviate a PHASE dataset name for use in deployment directory names.

    Tries exact match first, then longest substring match to avoid
    false positives (e.g., "all" matching "vt-fall22-1gb").
    """
    # Exact match
    if dataset in DATASET_ABBREVIATIONS:
        return DATASET_ABBREVIATIONS[dataset]

    # Longest substring match (avoids "all" matching "fall22")
    best_key = ""
    best_abbrev = ""
    for key, abbrev in DATASET_ABBREVIATIONS.items():
        if key in dataset and len(key) > len(best_key):
            best_key = key
            best_abbrev = abbrev

    return best_abbrev if best_abbrev else dataset[:6]


def resolve_feedback_args(
    configs_spec: str | None = None,
    source: str | None = None,
    target: str | None = None,
    deploy_type: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolve feedback CLI args into (behavior_source, configs_spec).

    configs_spec: "all", comma-separated filenames, or None (baseline).
    source: explicit PHASE directory path, or None (auto-detect).
    target: dataset target name (e.g., "summer24") to match against feedback dirs.
    deploy_type: "ghosts" or "ruse" — prefers matching feedback source.

    Returns (None, None) if no feedback configs were requested.
    """
    if not configs_spec:
        return None, None

    # Determine source
    behavior_source = source
    if not behavior_source:
        if target:
            detected = find_feedback_by_target(target, deploy_type=deploy_type)
        else:
            detected = auto_detect_feedback_source(deploy_type=deploy_type)
        if not detected:
            msg = f"No PHASE feedback configs found for target '{target}'" if target else "No PHASE feedback configs found"
            output.info(f"ERROR: {msg}. Use --source <path>")
            raise SystemExit(1)
        behavior_source = str(detected)

    source_path = Path(behavior_source)
    if not source_path.is_dir():
        output.info(f"ERROR: Feedback source not found: {behavior_source}")
        raise SystemExit(1)

    output.info(f"  Feedback source: {behavior_source}")
    output.info(f"  Configs: {configs_spec}")
    return behavior_source, configs_spec


def auto_detect_feedback_source(deploy_type: str | None = None) -> Path | None:
    """Find the most recent PHASE feedback config directory.

    If deploy_type is set, prefers directories matching that type
    (e.g., deploy_type="ghosts" prefers dirs containing "ghosts").
    Falls back to any directory if no type-specific match exists.
    """
    if not FEEDBACK_BASE.is_dir():
        return None

    type_prefix = _deploy_type_prefix(deploy_type)

    best_typed = None
    best_typed_mtime = 0.0
    best_any = None
    best_any_mtime = 0.0

    for d in FEEDBACK_BASE.iterdir():
        if not d.is_dir():
            continue
        mtime = d.stat().st_mtime
        if mtime > best_any_mtime:
            best_any_mtime = mtime
            best_any = d
        if type_prefix and type_prefix in d.name and mtime > best_typed_mtime:
            best_typed_mtime = mtime
            best_typed = d

    return best_typed or best_any


def find_all_feedback_sources(deploy_type: str | None = None) -> list[dict]:
    """Find all PHASE feedback config directories matching deploy type.

    Returns list of dicts sorted by dataset name:
        [{"path": Path, "name": str, "preset": str, "dataset": str}, ...]

    Only includes directories with a valid manifest.json.
    """
    if not FEEDBACK_BASE.is_dir():
        return []

    type_prefix = _deploy_type_prefix(deploy_type)
    results = []

    for d in sorted(FEEDBACK_BASE.iterdir()):
        if not d.is_dir():
            continue
        manifest_path = d / "manifest.json"
        if not manifest_path.exists():
            continue
        if type_prefix and type_prefix not in d.name:
            continue

        try:
            manifest = json.loads(manifest_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        results.append({
            "path": d,
            "name": d.name,
            "preset": manifest.get("preset_name", "?"),
            "dataset": manifest.get("dataset", "?"),
        })

    return results


# Known dataset targets — maps short/friendly names to search strings
# used against PHASE feedback directory names in ~/PHASE/feedback_engine/configs/.
# The search string must appear somewhere in the directory name.
DATASET_TARGETS = {
    # AXES seasonal
    "summer24": "summer24",
    "sum24": "summer24",
    "fall24": "fall24",
    "spring25": "spring25",
    "spr25": "spring25",
    "summer25": "summer25",
    "sum25": "summer25",
    "fall25": "fall25",
    # AXES aggregate
    "2025": "axes-2025",
    "all": "axes-all",
    "year": "axes-year",
    # CPTC
    "cptc8-23": "cptc8-23",
    "cptc8": "cptc8-23",
    "cptc9-24": "cptc9-24",
    "cptc9": "cptc9-24",
    # VirusTotal
    "vt-fall22-1gb": "vt-fall22-1gb",
    "vt-1gb": "vt-fall22-1gb",
    "vt1g": "vt-fall22-1gb",
    "vt-fall22-10gb": "vt-fall22-10gb",
    "vt-10gb": "vt-fall22-10gb",
    "vt10g": "vt-fall22-10gb",
    "vt-fall22-50gb": "vt-fall22-50gb",
    "vt-50gb": "vt-fall22-50gb",
    "vt50g": "vt-fall22-50gb",
}


def find_feedback_by_target(target: str, deploy_type: str | None = None) -> Path | None:
    """Find a PHASE feedback directory matching the given dataset target.

    Matches against directory names in ~/PHASE/feedback_engine/configs/.
    e.g., target="summer24" matches "axes-ruse-controls_axes-summer24_std-ctrls".

    If deploy_type is set, prefers directories matching that type
    (e.g., deploy_type="ghosts" prefers "axes-ghosts-*" over "axes-ruse-*").
    Falls back to any match if no type-specific match exists.
    """
    if not FEEDBACK_BASE.is_dir():
        return None

    # Normalize target name
    search = DATASET_TARGETS.get(target, target)
    type_prefix = _deploy_type_prefix(deploy_type)

    best_typed = None
    best_typed_mtime = 0.0
    best_any = None
    best_any_mtime = 0.0

    for d in FEEDBACK_BASE.iterdir():
        if not d.is_dir() or search not in d.name:
            continue
        mtime = d.stat().st_mtime
        if mtime > best_any_mtime:
            best_any_mtime = mtime
            best_any = d
        if type_prefix and type_prefix in d.name and mtime > best_typed_mtime:
            best_typed_mtime = mtime
            best_typed = d

    return best_typed or best_any


def _deploy_type_prefix(deploy_type: str | None) -> str | None:
    """Map deploy_type to the prefix used in PHASE feedback directory names.

    Defaults to "ruse" when no type is specified.
    """
    if deploy_type == "ghosts":
        return "ghosts"
    if deploy_type == "rampart":
        return "rampart"
    # Default: ruse (covers "ruse", "sup", and None)
    return "ruse"


def generate_feedback_config(
    source_dir: Path,
    configs_spec: str,
    deploy_dir: Path,
) -> str:
    """Generate a feedback deployment config.yaml. Returns deployment name."""
    import yaml

    manifest_path = source_dir / "manifest.json"
    if not manifest_path.exists():
        output.error(f"ERROR: No manifest.json in {source_dir}")
        raise SystemExit(1)

    manifest = json.loads(manifest_path.read_text())
    preset_name = manifest.get("preset_name", "unknown")
    dataset = manifest.get("dataset", "unknown")

    # Abbreviate dataset (exact match first, then longest substring match)
    dataset_abbrev = _abbreviate_dataset(dataset)

    # Scope label
    if configs_spec == "all":
        scope_label = "all"
    else:
        first = configs_spec.split(",")[0]
        scope_label = first.replace(".json", "").split("_")[0]

    preset_clean = preset_name.replace("-", "")
    dep_name = f"ruse-feedback-{preset_clean}-{dataset_abbrev}-{scope_label}"
    dep_dir = deploy_dir / dep_name
    dep_dir.mkdir(parents=True, exist_ok=True)

    # Build behavior_configs field
    if configs_spec == "all":
        behavior_configs = "all"
    else:
        behavior_configs = [f.strip() for f in configs_spec.split(",")]

    config = {
        "deployment_name": dep_name,
        "behavior_source": str(source_dir),
        "behavior_configs": behavior_configs,
        "flavor_capacity": {
            "v1.14vcpu.28g": 3,
            "v100-1gpu.14vcpu.28g": 2,
        },
        "deployments": FEEDBACK_TEMPLATE,
    }

    config_path = dep_dir / "config.yaml"
    with open(config_path, "w") as f:
        f.write(f"---\n")
        f.write(f"# Auto-generated feedback deployment: {dep_name}\n")
        f.write(f"# Feedback source: {source_dir}\n")
        f.write(f"# Preset: {preset_name} | Dataset: {dataset} | Scope: {scope_label}\n\n")
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    return dep_name


def generate_rampart_feedback_config(
    source_dir: Path,
    configs_spec: str,
    deploy_dir: Path,
    base_config_name: str = "rampart-controls",
) -> str:
    """Generate a RAMPART feedback deployment config.yaml. Returns deployment name."""
    import yaml

    manifest_path = source_dir / "manifest.json"
    if not manifest_path.exists():
        output.error(f"ERROR: No manifest.json in {source_dir}")
        raise SystemExit(1)

    manifest = json.loads(manifest_path.read_text())
    preset_name = manifest.get("preset_name", "unknown")
    dataset = manifest.get("dataset", "unknown")

    # Abbreviate dataset (exact match first, then longest substring match)
    dataset_abbrev = _abbreviate_dataset(dataset)

    # Scope label
    if configs_spec == "all":
        scope_label = "all"
    else:
        first = configs_spec.split(",")[0]
        scope_label = first.replace(".json", "").split("_")[0]

    preset_clean = preset_name.replace("-", "")
    dep_name = f"rampart-feedback-{preset_clean}-{dataset_abbrev}-{scope_label}"
    dep_dir = deploy_dir / dep_name
    dep_dir.mkdir(parents=True, exist_ok=True)

    # Copy base config and update deployment_name
    base_config_path = deploy_dir / base_config_name / "config.yaml"
    if base_config_path.exists():
        base = yaml.safe_load(base_config_path.read_text())
    else:
        base = {"type": "rampart"}

    base["deployment_name"] = dep_name
    base["behavior_source"] = str(source_dir)
    if configs_spec == "all":
        base["behavior_configs"] = "all"
    else:
        base["behavior_configs"] = [f.strip() for f in configs_spec.split(",")]

    config_path = dep_dir / "config.yaml"
    with open(config_path, "w") as f:
        f.write(f"---\n")
        f.write(f"# Auto-generated RAMPART feedback deployment: {dep_name}\n")
        f.write(f"# Feedback source: {source_dir}\n")
        f.write(f"# Preset: {preset_name} | Dataset: {dataset} | Scope: {scope_label}\n\n")
        yaml.dump(base, f, default_flow_style=False, sort_keys=False)

    return dep_name


def generate_ghosts_feedback_config(
    source_dir: Path,
    configs_spec: str,
    deploy_dir: Path,
) -> str:
    """Generate a GHOSTS feedback deployment config.yaml. Returns deployment name."""
    import yaml

    manifest_path = source_dir / "manifest.json"
    if not manifest_path.exists():
        output.error(f"ERROR: No manifest.json in {source_dir}")
        raise SystemExit(1)

    manifest = json.loads(manifest_path.read_text())
    preset_name = manifest.get("preset_name", "unknown")
    dataset = manifest.get("dataset", "unknown")

    # Abbreviate dataset (exact match first, then longest substring match)
    dataset_abbrev = _abbreviate_dataset(dataset)

    # Scope label
    if configs_spec == "all":
        scope_label = "all"
    else:
        first = configs_spec.split(",")[0]
        scope_label = first.replace(".json", "").split("_")[0]

    preset_clean = preset_name.replace("-", "")
    dep_name = f"ghosts-feedback-{preset_clean}-{dataset_abbrev}-{scope_label}"
    dep_dir = deploy_dir / dep_name
    dep_dir.mkdir(parents=True, exist_ok=True)

    # Build behavior_configs field
    if configs_spec == "all":
        behavior_configs = "all"
    else:
        behavior_configs = [f.strip() for f in configs_spec.split(",")]

    config = {
        "deployment_name": dep_name,
        "type": "ghosts",
        "behavior_source": str(source_dir),
        "behavior_configs": behavior_configs,
        "ghosts": {
            "api_flavor": "v1.14vcpu.28g",
            "client_flavor": "v1.14vcpu.28g",
            "client_count": 5,
            "ghosts_repo": "https://github.com/cmu-sei/GHOSTS.git",
            "ghosts_branch": "master",
        },
    }

    config_path = dep_dir / "config.yaml"
    with open(config_path, "w") as f:
        f.write(f"---\n")
        f.write(f"# Auto-generated GHOSTS feedback deployment: {dep_name}\n")
        f.write(f"# Feedback source: {source_dir}\n")
        f.write(f"# Preset: {preset_name} | Dataset: {dataset} | Scope: {scope_label}\n\n")
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    return dep_name
