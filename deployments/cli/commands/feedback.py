"""Feedback source auto-detection and config generation."""

from __future__ import annotations

import json
from pathlib import Path

from .. import output


FEEDBACK_BASE = Path.home() / "PHASE" / "feedback_engine" / "configs"

# Standard 11-VM feedback template
FEEDBACK_TEMPLATE = [
    {"behavior": "M2", "flavor": "v1.14vcpu.28g", "count": 1},
    {"behavior": "B2.llama", "flavor": "v100-1gpu.14vcpu.28g", "count": 1},
    {"behavior": "B2.gemma", "flavor": "v100-1gpu.14vcpu.28g", "count": 1},
    {"behavior": "S2.llama", "flavor": "v100-1gpu.14vcpu.28g", "count": 1},
    {"behavior": "S2.gemma", "flavor": "v100-1gpu.14vcpu.28g", "count": 1},
    {"behavior": "B2C.llama", "flavor": "v1.14vcpu.28g", "count": 1},
    {"behavior": "B2C.gemma", "flavor": "v1.14vcpu.28g", "count": 1},
    {"behavior": "S2C.llama", "flavor": "v1.14vcpu.28g", "count": 1},
    {"behavior": "S2C.gemma", "flavor": "v1.14vcpu.28g", "count": 1},
    {"behavior": "B2R.llama", "flavor": "rtx2080ti-1gpu.14vcpu.28g", "count": 1},
    {"behavior": "B2R.gemma", "flavor": "rtx2080ti-1gpu.14vcpu.28g", "count": 1},
]

DATASET_ABBREVIATIONS = {
    "summer24": "sum24",
    "sum24": "sum24",
    "fall24": "fall24",
    "spring25": "spr25",
    "spr25": "spr25",
}


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


# Known dataset targets — maps short names to search strings
DATASET_TARGETS = {
    "summer24": "summer24",
    "sum24": "summer24",
    "fall24": "fall24",
    "spring25": "spring25",
    "spr25": "spring25",
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

    # Abbreviate dataset
    dataset_abbrev = dataset
    for key, abbrev in DATASET_ABBREVIATIONS.items():
        if key in dataset:
            dataset_abbrev = abbrev
            break
    if dataset_abbrev == dataset:
        dataset_abbrev = dataset[:6]

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
            "v1.14vcpu.28g": 5,
            "v100-1gpu.14vcpu.28g": 4,
            "rtx2080ti-1gpu.14vcpu.28g": 2,
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

    # Abbreviate dataset
    dataset_abbrev = dataset
    for key, abbrev in DATASET_ABBREVIATIONS.items():
        if key in dataset:
            dataset_abbrev = abbrev
            break
    if dataset_abbrev == dataset:
        dataset_abbrev = dataset[:6]

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

    # Abbreviate dataset
    dataset_abbrev = dataset
    for key, abbrev in DATASET_ABBREVIATIONS.items():
        if key in dataset:
            dataset_abbrev = abbrev
            break
    if dataset_abbrev == dataset:
        dataset_abbrev = dataset[:6]

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

    config = {
        "deployment_name": dep_name,
        "type": "ghosts",
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
