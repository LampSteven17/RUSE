"""Feedback source auto-detection and config generation."""

from __future__ import annotations

import json
from pathlib import Path

from .. import output


FEEDBACK_BASE = Path("/mnt/AXES2U1/feedback")

# Post 2026-04-23 layout:
#   /mnt/AXES2U1/feedback/
#     ├── ruse-controls/{dataset}/...
#     ├── rampart-controls/{dataset}/...
#     └── ghosts-controls/{dataset}/...
# Each dataset dir contains a manifest.json + per-SUP/NPC/node configs.
# The type subtree is selected via _type_root(deploy_type).


# ── Manifest helpers (post 2026-04-23 /mnt/AXES2U1/feedback layout) ─────
#
# PHASE now writes a manifest.json alongside each generated feedback source.
# It's a provenance index — deploy_key, training_dataset, version_preset,
# model_name, generated_at_utc, active_features_union, per-SUP ok/skipped
# status. Not a config itself (nested per-SUP files still carry the knobs),
# but indispensable for operator confirmation: "am I about to deploy the
# right feedback, freshly generated, for the right target?"

def load_manifest(source: Path) -> dict | None:
    """Load manifest.json from a PHASE source dir. Returns None if missing/malformed.

    Missing manifest is not a failure — older sources (and hand-built dev
    dirs) won't have one. Caller decides how strict to be.
    """
    mf = source / "manifest.json"
    if not mf.exists():
        return None
    try:
        return json.loads(mf.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _format_age(iso_ts: str) -> str:
    """Format how long ago an ISO-8601 UTC timestamp was, as '(12m ago)' etc."""
    import datetime
    try:
        gen_dt = datetime.datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return ""
    if gen_dt.tzinfo is None:
        gen_dt = gen_dt.replace(tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    secs = int((now - gen_dt).total_seconds())
    if secs < 60:
        return f"({secs}s ago)"
    if secs < 3600:
        return f"({secs // 60}m ago)"
    if secs < 86400:
        return f"({secs // 3600}h ago)"
    return f"({secs // 86400}d ago)"


def manifest_summary_lines(
    source: Path, manifest: dict | None, indent: str = "    ",
) -> list[str]:
    """Return lines summarizing a manifest for user display.

    Shape (indent applied to each line):
      source:     /mnt/AXES2U1/feedback/ruse-controls/axes-summer24
      dataset:    axes-summer24       preset: std-ctrls
      model:      v7.1.2_double_bilstm_min_axes-summer24-ctrl
      generated:  2026-04-23T16:15:19Z  (12m ago)
      active:     ['service']          (other features ablation-gated)
      sup_runs:   5 ok, 2 skipped
    """
    lines = [f"{indent}source:     {source}"]
    if not manifest:
        lines.append(f"{indent}(no manifest.json — legacy / dev source)")
        return lines

    dataset = manifest.get("training_dataset", "?")
    preset = manifest.get("version_preset", "?")
    model = manifest.get("model_name", "?")
    generated = manifest.get("generated_at_utc", "")
    active = manifest.get("active_features_union", [])
    sup_runs = manifest.get("sup_runs", [])
    ok = sum(1 for r in sup_runs if r.get("status") == "ok")
    skipped = sum(1 for r in sup_runs if r.get("status") == "skipped")

    age = _format_age(generated) if generated else ""
    age_suffix = f"  {age}" if age else ""
    active_suffix = "  (other features ablation-gated)" if not active else ""

    lines.append(f"{indent}dataset:    {dataset}       preset: {preset}")
    lines.append(f"{indent}model:      {model}")
    lines.append(f"{indent}generated:  {generated}{age_suffix}")
    lines.append(f"{indent}active:     {active}{active_suffix}")
    lines.append(f"{indent}sup_runs:   {ok} ok, {skipped} skipped")
    return lines


def validate_manifest_target(
    manifest: dict | None, deploy_type: str,
) -> str | None:
    """Assert manifest.target matches the deploy type. Returns error msg or None.

    Catches the class of bugs where an operator points a ruse deploy at a
    rampart source (the file-layout globs would still match because each
    type validates its own nested shape, but the manifest is authoritative).
    """
    if not manifest:
        return None  # no manifest = can't check, defer to layout glob
    target = manifest.get("target")
    if not target:
        return None
    expected = "ruse" if deploy_type in ("ruse", "sup", None) else deploy_type
    if target != expected:
        return (
            f"manifest.target={target!r} does not match deploy type {expected!r} — "
            f"source at {manifest.get('deploy_key', '?')} was generated for a "
            f"different target"
        )
    return None

# GPU-conserving 5-VM feedback template (gemma4 cutover 2026-04-08):
# gemma only, V100 only (no RTX) — V100 uses gemma4:26b, CPU uses gemma4:e2b.
FEEDBACK_TEMPLATE = [
    {"behavior": "M2",        "flavor": "v1.14vcpu.28g",        "count": 1},
    {"behavior": "B2.gemma",  "flavor": "v100-1gpu.14vcpu.28g", "count": 1},
    {"behavior": "S2.gemma",  "flavor": "v100-1gpu.14vcpu.28g", "count": 1},
    {"behavior": "B2C.gemma", "flavor": "v1.14vcpu.28g",        "count": 1},
    {"behavior": "S2C.gemma", "flavor": "v1.14vcpu.28g",        "count": 1},
]

# Maps PHASE dataset names → short abbreviations for deployment directory names.
# Used by generate_feedback_config() to abbreviate "axes-summer24" → "sum24" etc.
# Lookup: try exact match on full dataset name first, then substring (longest key first).
# Dataset names are extracted from the PHASE source directory name via
# _parse_source_name() (middle component of {experiment}_{dataset}_{preset}).
DATASET_ABBREVIATIONS = {
    # PHASE canonical names (exact match from the source dir's dataset component)
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


def _type_root(deploy_type: str | None) -> Path | None:
    """Return the per-type subtree under FEEDBACK_BASE, e.g. ruse-controls.

    Returns None if FEEDBACK_BASE doesn't exist or the subtree is missing.
    deploy_type=None / "ruse" / "sup" all resolve to ruse-controls.
    """
    if not FEEDBACK_BASE.is_dir():
        return None
    kind = _deploy_type_prefix(deploy_type)  # "ruse" | "rampart" | "ghosts"
    root = FEEDBACK_BASE / f"{kind}-controls"
    return root if root.is_dir() else None


def auto_detect_feedback_source(deploy_type: str | None = None) -> Path | None:
    """Find the most recent dataset subdir under {type}-controls/.

    New layout (/mnt/AXES2U1/feedback/{type}-controls/{dataset}/) is scoped
    per-type, so there's no cross-type fallback like the old flat layout.
    """
    root = _type_root(deploy_type)
    if root is None:
        return None

    best = None
    best_mtime = 0.0
    for d in root.iterdir():
        if not d.is_dir():
            continue
        if not _is_valid_feedback_source(d, deploy_type):
            continue
        mtime = d.stat().st_mtime
        if mtime > best_mtime:
            best_mtime = mtime
            best = d

    return best


# ── PHASE source validation & metadata extraction (post Stage 2) ───────────
#
# Stage 2 dropped manifest.json. Feedback source dirs are now identified by
# their generated file layout (type-specific globs), and metadata is parsed
# from the directory name itself: {experiment}_{dataset}_{preset}.


def _parse_source_name(source_dir: Path) -> tuple[str, str, str]:
    """Extract (experiment, dataset, preset) from a feedback source dir.

    Post 2026-04-23 layout: source_dir.name IS the dataset (e.g.
    "axes-summer24"), and preset + deploy_key live in manifest.json. When
    available, the manifest is authoritative — dataset/preset come from it
    and experiment is synthesized as "axes-{deploy_key}".

    Falls back to directory-name-only parsing when manifest is missing,
    using `std-ctrls` as the default preset (matches the only preset PHASE
    currently emits). This keeps hand-built dev sources working without a
    manifest.
    """
    manifest = load_manifest(source_dir)
    if manifest:
        deploy_key = manifest.get("deploy_key", source_dir.parent.name)
        dataset = manifest.get("training_dataset", source_dir.name)
        preset = manifest.get("version_preset", "std-ctrls")
        return (f"axes-{deploy_key}", dataset, preset)

    # Fallback: dir name is the dataset, parent dir name is the deploy_key.
    return (f"axes-{source_dir.parent.name}", source_dir.name, "std-ctrls")


def _is_valid_feedback_source(source_dir: Path, deploy_type: str | None) -> bool:
    """Check whether source_dir contains the expected file layout.

    Validity is inferred from the presence of the generator's output files:
      RUSE    — {behavior}/{sup}/behavior.json        (consolidated per-SUP file)
                {behavior}/{sup}/timing_profile.json  (legacy 8-file fallback)
      RAMPART — {bare_node}/user-roles.json           (per-node pyhuman configs)
      GHOSTS  — npc-*/timeline.json                   (per-NPC timelines)

    RUSE consolidated its 8 per-SUP JSONs into a single behavior.json as of
    2026-04-16. The legacy timing_profile.json glob is kept so feedback
    sources generated before the cutover still validate and deploy.

    If deploy_type is None/unknown, accept any of the recognised patterns.
    """
    if not source_dir.is_dir():
        return False

    if deploy_type == "rampart":
        return any(source_dir.glob("*/user-roles.json"))
    if deploy_type == "ghosts":
        return any(source_dir.glob("npc-*/timeline.json"))
    if deploy_type in ("ruse", "sup", None):
        return (
            any(source_dir.glob("*/*/behavior.json"))
            or any(source_dir.glob("*/*/timing_profile.json"))
        )

    # Unknown type — accept any marker
    return (
        any(source_dir.glob("*/user-roles.json"))
        or any(source_dir.glob("npc-*/timeline.json"))
        or any(source_dir.glob("*/*/behavior.json"))
        or any(source_dir.glob("*/*/timing_profile.json"))
    )


def find_all_feedback_sources(deploy_type: str | None = None) -> list[dict]:
    """Find all PHASE feedback dataset dirs under {type}-controls/.

    Returns list of dicts sorted by dataset name:
        [{"path": Path, "name": str, "preset": str, "dataset": str}, ...]

    A directory is included if its file layout matches the generator
    output for that type. Metadata (preset, dataset) comes from
    manifest.json when present; otherwise directory name is treated as
    the dataset with preset defaulting to "std-ctrls".
    """
    root = _type_root(deploy_type)
    if root is None:
        return []

    results = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        if not _is_valid_feedback_source(d, deploy_type):
            continue

        _experiment, dataset, preset = _parse_source_name(d)
        results.append({
            "path": d,
            "name": d.name,
            "preset": preset,
            "dataset": dataset,
        })

    return results


# Known dataset targets — maps short/friendly names to search strings
# used against feedback dataset directory names under
# /mnt/AXES2U1/feedback/{type}-controls/.
# The search string must appear somewhere in the directory name.
DATASET_TARGETS = {
    # AXES seasonal
    "summer24": "summer24",
    "sum24": "summer24",
    "axes-summer24": "axes-summer24",
    "fall24": "fall24",
    "axes-fall24": "axes-fall24",
    "spring25": "spring25",
    "spr25": "spring25",
    "axes-spring25": "axes-spring25",
    "summer25": "summer25",
    "sum25": "summer25",
    "axes-summer25": "axes-summer25",
    "fall25": "fall25",
    "axes-fall25": "axes-fall25",
    # AXES aggregate
    "2025": "axes-2025",
    "axes-2025": "axes-2025",
    "all": "axes-all",
    "axall": "axes-all",
    "axes-all": "axes-all",
    "year": "axes-year",
    "axyear": "axes-year",
    "axes-year": "axes-year",
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
    """Find a feedback dataset dir matching the given target name.

    e.g. target="summer24" or "sum24" resolves to dir "axes-summer24"
    under {FEEDBACK_BASE}/{type}-controls/. DATASET_TARGETS normalizes
    short aliases. Most-recent mtime wins if multiple dirs match.
    """
    root = _type_root(deploy_type)
    if root is None:
        return None

    # Normalize target name to its canonical dataset substring
    search = DATASET_TARGETS.get(target, target)

    best = None
    best_mtime = 0.0
    for d in root.iterdir():
        if not d.is_dir() or search not in d.name:
            continue
        if not _is_valid_feedback_source(d, deploy_type):
            continue
        mtime = d.stat().st_mtime
        if mtime > best_mtime:
            best_mtime = mtime
            best = d

    return best


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
    """Generate a RUSE feedback deployment config.yaml. Returns deployment name."""
    import yaml

    if not _is_valid_feedback_source(source_dir, "ruse"):
        output.error(
            f"ERROR: {source_dir} is not a valid RUSE feedback source "
            f"(no {{behavior}}/{{sup}}/behavior.json or legacy "
            f"timing_profile.json files found)"
        )
        raise SystemExit(1)

    _experiment, dataset, preset_name = _parse_source_name(source_dir)

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

    if not _is_valid_feedback_source(source_dir, "rampart"):
        output.error(
            f"ERROR: {source_dir} is not a valid RAMPART feedback source "
            f"(no {{bare_node}}/user-roles.json files found)"
        )
        raise SystemExit(1)

    _experiment, dataset, preset_name = _parse_source_name(source_dir)

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

    if not _is_valid_feedback_source(source_dir, "ghosts"):
        output.error(
            f"ERROR: {source_dir} is not a valid GHOSTS feedback source "
            f"(no npc-*/timeline.json files found)"
        )
        raise SystemExit(1)

    _experiment, dataset, preset_name = _parse_source_name(source_dir)

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
