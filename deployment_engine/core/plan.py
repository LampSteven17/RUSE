"""Deploy plan: build, render, execute.

A "plan" is a list of deploy tasks. For a typical `./deploy --decoy` it's
[controls task, feedback task per discovered dataset]. The plan is built
from CLI flags + filesystem discovery, rendered to the user with a
manifest summary per task, confirmed, then executed sequentially.

Per-type spinup functions are imported lazily inside execute_plan so this
module doesn't pull in all three subsystems just to build a plan.

Each task is a dict:
    {
        "label":           human-readable string,
        "behavior_source": Path | None,
        "configs_spec":    str | None,         # legacy ansible filter
        "manifest":        dict | None,
        "is_controls":     bool,
    }
"""

from __future__ import annotations

import re
import json
from pathlib import Path

from . import output
from .config import DeploymentConfig
from .feedback import (
    FeedbackSourceError,
    canonical_decoy_feedback_deployments,
    config_vm_table_lines,
    decoy_feedback_source_identity,
    find_decoy_control_generation,
    find_all_decoy_feedback_sources,
    find_decoy_feedback_by_target,
    find_all_feedback_sources,
    find_feedback_by_target,
    load_manifest,
    manifest_summary_lines,
    validate_decoy_canary_generation,
    validate_manifest_target,
)


DECOY_CANARY_CONFIG = "decoy-runtime-canary"


def build_saved_llm_comparison_plan(
    deploy_dir: Path, config_name: str,
) -> list[dict]:
    """Build one task from an existing explicit LLM-comparison config."""
    config = DeploymentConfig.load(deploy_dir / config_name / "config.yaml")
    if config.deployment_type != "decoy" or not config.llm_comparison:
        raise ValueError(
            "saved comparison deployment requires a Decoy config with "
            "llm_comparison: true"
        )

    from ..decoy.spinup import _validate_behavior_source
    errors = _validate_behavior_source(config.behavior_source, config)
    if errors:
        raise ValueError("; ".join(errors))

    from .feedback import decoy_generation_uses_network_share
    behavior = config.deployments[0]["behavior"]
    source = Path(config.behavior_source)
    return [{
        "label": config.deployment_name,
        "config_name": config_name,
        "is_baseline": True,
        "is_llm_comparison": True,
        "is_saved_config": True,
        "no_schedule": config.no_schedule,
        "is_controls": True,
        "purpose": config.purpose,
        "target": config.target,
        "model_preset": config.model_preset,
        "baseline_direction": config.baseline_direction,
        "behavior_source": source,
        "configs_spec": None,
        "manifest": None,
        "gpu_tier": config.gpu_tier,
        "requested_model": config.requested_model,
        "comparison_hardware": config.comparison_hardware,
        "share_required": config.no_schedule or decoy_generation_uses_network_share(
            source, purpose="control", expected_configs={behavior}
        ),
        "deployments": config.deployments,
    }]


def build_probe_plan(deploy_dir: Path, config_name: str | None = None,
                     *, sup_config: str = "scripted-cpu", background_services: bool = False) -> list[dict]:
    """Resolve the explicit Probe category in full before any display or execution."""
    from decoys.phase_workflow.probes import PROBE_RESOURCES, PROBE_SUPS, validate_probe_plans
    if sup_config not in PROBE_SUPS:
        raise ValueError("probe SUP must be scripted-cpu or mchp-cpu")
    if background_services and sup_config != "scripted-cpu":
        raise ValueError("background probes use the Scripted CPU idle environment")
    tasks = []
    paths = ([deploy_dir / config_name / "config.yaml"] if config_name
             else sorted(deploy_dir.glob("*/config.yaml")))
    for path in paths:
        # Avoid interpreting unrelated configuration types during probe discovery.
        import yaml
        raw = yaml.safe_load(path.read_text())
        if not isinstance(raw, dict) or raw.get("type") != "probe":
            if config_name:
                raise ValueError("--probes requires an explicit type: probe configuration")
            continue
        if bool(raw.get("probe_service")) != background_services:
            if config_name:
                raise ValueError("background probe configurations require --background-services")
            continue
        config = DeploymentConfig.load(path)
        if config.deployments[0]["behavior"] != sup_config:
            if config_name:
                raise ValueError("probe config does not match the selected --probe-sup")
            continue
        validate_probe_plans(config.behavior_source, config.probe_workflow, sup_config)
        tasks.append({
            "label": config.deployment_name, "config_name": path.parent.name,
            "behavior_source": config.behavior_source, "configs_spec": None,
            "manifest": None, "is_controls": False, "is_probe": True,
            "probe_workflow": config.probe_workflow, "purpose": "other", "target": None,
            "probe_service": config.probe_service,
            "deployments": config.deployments,
            "share_required": config.probe_workflow == "NetworkShareAccess",
        })
    if background_services and not config_name:
        if len(tasks) != 3 or {t["probe_service"] for t in tasks} != {"ntp", "firmware", "motd"}:
            raise ValueError("background selection requires exactly NTP, firmware and MOTD probes")
    elif not config_name and (len(tasks) != 7 or
            {t["probe_workflow"] for t in tasks} != {*PROBE_RESOURCES, None}):
        raise ValueError("--probes requires six distinct workflow probes and one idle reference")
    return tasks


def build_deploy_plan(
    deploy_type: str,
    *,
    want_controls: bool,
    want_feedback: bool,
    configs_spec: str | None,
    single_selector: str | list[str] | None,
    target: str | list[str] | None,
    source: str | None,
    deploy_dir: Path,
    preset: str | None = None,
    tier_plan: list[tuple[str, list[str]]] | None = None,
    gpu_tier: str = "v100",
) -> list[dict] | None:
    """Resolve controls + feedback intent into an ordered list of deploy tasks.

    `preset` scopes PHASE discovery. Decoy controls default to colfix_v12.5.0
    when it is omitted.

    `tier_plan` is a static (gpu_tier, [targets]) assignment (e.g.
    feedback.TIER_PLANS["exp1"]) — each resulting feedback task carries its
    own "gpu_tier" key, overriding the invocation-wide --gpu value.

    Returns None on hard failure. Empty list = nothing to do.
    """
    plan: list[dict] = []

    if want_controls:
        try:
            plan.append(_build_controls_task(deploy_type, deploy_dir, preset=preset))
        except FeedbackSourceError as exc:
            output.error(f"ERROR: {exc}")
            return None

    if want_feedback:
        feedback_tasks = _build_feedback_tasks(
            deploy_type, configs_spec, single_selector, target, source,
            preset=preset, tier_plan=tier_plan, gpu_tier=gpu_tier,
        )
        if feedback_tasks is None:
            return None
        plan.extend(feedback_tasks)

    return plan


LLM_COMPARISON_MODELS = (
    "gemma4:26b",
    "gemma4:e4b-it-qat",
    "qwen3.8:27b-q4_K_M",
    "qwen3.5:4b-q4_K_M",
    "ministral-3:14b-instruct-2512-q4_K_M",
    "ministral-3:3b-instruct-2512-q4_K_M",
    "deepseek-r1:8b-0528-qwen3-q4_K_M",
    "gpt-oss:20b",
    "granite4.2:8b-q4_K_M",
    "llama3.2-vision:11b-instruct-q4_K_M",
)
LLM_COMPARISON_CPU_MODELS = (
    "qwen3.8:27b-q4_K_M",
    "qwen3.5:9b-q4_K_M",
    "gemma4:e4b-it-qat",
    "ministral-3:3b-instruct-2512-q4_K_M",
    "deepseek-r1:8b-0528-qwen3-q4_K_M",
    "gpt-oss:20b",
    "granite4.2:8b-q4_K_M",
    "llama3.2-vision:11b-instruct-q4_K_M",
)


def _comparison_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def build_baseline_plan(
    preset: str,
    direction: str,
    gpu_tier: str = "v100",
    *,
    llm_comparison: bool = False,
    no_schedule: bool = False,
) -> list[dict]:
    from .feedback import (find_decoy_baseline_generation, _decoy_preset_slug,
                           canonical_decoy_feedback_deployments)

    if no_schedule and not llm_comparison:
        raise FeedbackSourceError("No-schedule mode requires the human Baseline LLM comparison")
    if llm_comparison and (direction != "human" or gpu_tier != "v100"):
        raise FeedbackSourceError("LLM comparison requires the human Baseline on V100")
    source = find_decoy_baseline_generation(preset, direction)
    name = f"decoy-baseline-{_decoy_preset_slug(preset)}-{direction}"
    if llm_comparison:
        tasks = []
        share_configs = set()
        for behavior, filename in (
            ("browseruse-gpu", "browseruse-v1.json"),
            ("smolagents-gpu", "smolagents-v1.json"),
        ):
            document = json.loads((source / filename).read_text(encoding="utf-8"))
            if any(
                entry.get("workflow") == "NetworkShareAccess"
                for window in document.get("schedule", [])
                for entry in window.get("sequence", [])
            ):
                share_configs.add(behavior)
        for model in LLM_COMPARISON_MODELS:
            for behavior in ("browseruse-gpu", "smolagents-gpu"):
                brain = behavior.removesuffix("-gpu")
                identity = f"{name}-{brain}-{_comparison_slug(model)}"
                if no_schedule:
                    identity += "-autonomous"
                tasks.append({
                    "label": identity, "config_name": identity,
                    "is_baseline": True, "is_llm_comparison": True,
                    "no_schedule": no_schedule,
                    "is_controls": True, "purpose": "control", "target": None,
                    "model_preset": preset, "baseline_direction": direction,
                    "behavior_source": source, "configs_spec": None, "manifest": None,
                    "gpu_tier": gpu_tier, "requested_model": model,
                    "comparison_hardware": "gpu",
                    "share_required": no_schedule or behavior in share_configs,
                    "deployments": [{
                        "behavior": behavior,
                        "flavor": "v100-1gpu.14vcpu.28g",
                        "count": 1,
                    }],
                })
        for model in LLM_COMPARISON_CPU_MODELS:
            for behavior in ("browseruse-gpu", "smolagents-gpu"):
                brain = behavior.removesuffix("-gpu")
                identity = f"{name}-{brain}-{_comparison_slug(model)}-cpu"
                if no_schedule:
                    identity += "-autonomous"
                tasks.append({
                    "label": identity, "config_name": identity,
                    "is_baseline": True, "is_llm_comparison": True,
                    "no_schedule": no_schedule,
                    "is_controls": True, "purpose": "control", "target": None,
                    "model_preset": preset, "baseline_direction": direction,
                    "behavior_source": source, "configs_spec": None, "manifest": None,
                    "gpu_tier": "cpu", "requested_model": model,
                    "comparison_hardware": "cpu",
                    "share_required": no_schedule or behavior in share_configs,
                    "deployments": [{
                        "behavior": behavior, "flavor": "v1.14vcpu.28g", "count": 1,
                    }],
                })
        return tasks
    return [{
        "label": name, "config_name": name, "is_baseline": True,
        "is_controls": True, "purpose": "control", "target": None,
        "model_preset": preset, "baseline_direction": direction,
        "behavior_source": source, "configs_spec": None, "manifest": None,
        "gpu_tier": gpu_tier,
        "deployments": list(canonical_decoy_feedback_deployments(gpu_tier)),
    }]


def _build_controls_task(deploy_type: str, deploy_dir: Path, *, preset: str | None = None) -> dict:
    """Build one controls task, resolving Decoy plans directly from PHASE."""
    controls_cfg_path = deploy_dir / f"{deploy_type}-controls" / "config.yaml"
    if not controls_cfg_path.exists():
        if deploy_type == "decoy":
            raise FeedbackSourceError(
                f"control deployment config not found: {controls_cfg_path}"
            )
        # No config means controls aren't set up for this type — render
        # the legacy line and let the spinup decide what to do. This is
        # the only "soft" case: a fresh repo without a controls config
        # shouldn't crash the planner.
        return {
            "label": f"{deploy_type}-controls (control)",
            "behavior_source": None,
            "configs_spec": None,
            "manifest": None,
            "is_controls": True,
            "deployments": [],
        }

    cfg = DeploymentConfig.load(controls_cfg_path)  # raises on parse errors — fail loud
    if deploy_type == "decoy":
        src = find_decoy_control_generation(preset)
        manifest = None
    else:
        src = Path(cfg.behavior_source) if cfg.behavior_source else None
        manifest = load_manifest(src) if src else None
    return {
        "label": f"{deploy_type}-controls (control)",
        "behavior_source": src,
        "configs_spec": None,
        "manifest": manifest,
        "is_controls": True,
        "deployments": cfg.deployments,
    }


def build_decoy_canary_plan(deploy_dir: Path, *, sup_config=None) -> list[dict]:
    """Build and validate the one explicit RUSE-owned runtime canary."""
    if sup_config not in {None, "mchp-cpu", "browseruse-gpu"}:
        raise FeedbackSourceError("canary SUP selection must be mchp-cpu or browseruse-gpu")
    config_name = ({"mchp-cpu": "decoy-mchp-canary", "browseruse-gpu": "decoy-gpu-canary"}
                   .get(sup_config, DECOY_CANARY_CONFIG))
    config_dir = Path(deploy_dir) / config_name
    config = DeploymentConfig.load(config_dir / "config.yaml")
    source = config_dir / "plans"
    plans = validate_decoy_canary_generation(source)
    if sup_config:
        flavor = "v1.14vcpu.28g" if sup_config == "mchp-cpu" else "v100-1gpu.14vcpu.28g"
        if config.deployments != [{"behavior": sup_config, "flavor": flavor, "count": 1}]:
            raise FeedbackSourceError("single-SUP observation canary requires exactly the selected VM")
        if any(entry.workflow != "DocumentCreation" for plan in plans.values()
               for window in plan.windows for entry in window.sequence):
            raise FeedbackSourceError("MCHP observation plans must contain only DocumentCreation")
    if config.purpose != "other" or config.target is not None:
        raise FeedbackSourceError(
            "Decoy runtime canary must use purpose=other and target=null"
        )
    return [{
        "label": f"{config_name} (RUSE-only canary)",
        "behavior_source": source,
        "configs_spec": None,
        "manifest": None,
        "is_controls": False,
        "is_canary": True,
        "config_name": config_name,
        "deployments": config.deployments,
        "gpu_tier": "v100",
    }]


def _build_feedback_tasks(
    deploy_type: str,
    configs_spec: str | None,
    single_selector: str | list[str] | None,
    target: str | list[str] | None,
    source: str | None,
    preset: str | None = None,
    tier_plan: list[tuple[str, list[str]]] | None = None,
    gpu_tier: str = "v100",
) -> list[dict] | None:
    """Resolve feedback sources into tasks. Returns None on resolution failure.

    For canonical Decoy feedback, `target` may be an ordered list of exact
    target names. Legacy Rampart/GHOSTS callers continue to pass one string.

    Legacy `target` may be a single dataset name OR a comma-separated list
    (e.g. "axes-summer24,axes-year,vt-fall22-50gb") — each is resolved
    independently and added to the plan. Useful for batching a subset
    of feedback datasets onto a specific GPU tier in one invocation.

    `preset` is the {preset}_v{version} namespace dir scoping discovery under
    {type}-controls/. A Decoy explicit source must match any selected preset;
    legacy sources continue to encode their own namespace. Required-ness is
    enforced upstream in the CLI.
    """
    if deploy_type == "decoy":
        return _build_decoy_feedback_tasks(
            configs_spec,
            target=target,
            source=source,
            preset=preset,
            gpu_tier=gpu_tier,
        )

    sources: list[dict] = []
    if tier_plan:
        # Static tier plan: resolve every (tier, targets) pair up front,
        # fail loud if any dataset is missing from the namespace. Each
        # source carries its tier so the task gets a per-task gpu_tier.
        for tier, targets in tier_plan:
            for tgt in targets:
                src_path = find_feedback_by_target(tgt, deploy_type=deploy_type,
                                                   preset=preset)
                if not src_path:
                    ns = f"{preset}/" if preset else ""
                    output.error(
                        f"ERROR: tier plan target '{tgt}' (tier {tier}) not found "
                        f"under /mnt/AXES2U1/feedback/{deploy_type}-controls/{ns}"
                    )
                    return None
                sources.append({"path": src_path, "dataset": src_path.name,
                                "preset": preset or "?", "gpu_tier": tier})
    elif single_selector:
        if source:
            src_path = Path(source)
            if not src_path.is_dir():
                output.error(f"ERROR: --source not a directory: {src_path}")
                return None
            sources = [{"path": src_path, "dataset": src_path.name, "preset": "?"}]
        elif target:
            # Comma-separated list → multi-target batch. Resolve each
            # independently; fail loud if any one is unknown.
            targets = [t.strip() for t in target.split(",") if t.strip()]
            for tgt in targets:
                src_path = find_feedback_by_target(tgt, deploy_type=deploy_type,
                                                   preset=preset)
                if not src_path:
                    ns = f"{preset}/" if preset else ""
                    output.error(
                        f"ERROR: No feedback source for target '{tgt}' "
                        f"under /mnt/AXES2U1/feedback/{deploy_type}-controls/{ns}"
                    )
                    return None
                sources.append({"path": src_path, "dataset": src_path.name, "preset": preset or "?"})
    else:
        sources = find_all_feedback_sources(deploy_type, preset=preset)
        if not sources:
            ns = f"{preset}/" if preset else ""
            output.error(f"ERROR: No PHASE feedback configs found for '{deploy_type}'")
            output.info(f"  Searched: /mnt/AXES2U1/feedback/{deploy_type}-controls/{ns}")
            return None

    return [
        {
            "label": (f"{deploy_type}-feedback: {src['dataset']}"
                      + (f" [{src['gpu_tier']}]" if src.get("gpu_tier") else "")),
            "behavior_source": src["path"],
            "configs_spec": configs_spec,
            "manifest": load_manifest(src["path"]),
            "is_controls": False,
            # Per-task tier from a static tier plan; None = use the
            # invocation-wide --gpu value.
            "gpu_tier": src.get("gpu_tier"),
        }
        for src in sources
    ]


def _build_decoy_feedback_tasks(
    configs_spec: str | None,
    *,
    target: str | list[str] | None,
    source: str | None,
    preset: str | None,
    gpu_tier: str,
) -> list[dict] | None:
    """Resolve canonical flat PHASE generations for Decoy only."""
    try:
        if source:
            source_path = Path(source)
            source_preset, source_target = decoy_feedback_source_identity(source_path)
            if preset is not None and source_preset != preset:
                raise FeedbackSourceError(
                    f"--source preset {source_preset!r} conflicts with selected preset {preset!r}"
                )
            sources = [{
                "path": source_path,
                "preset": source_preset,
                "target": source_target,
            }]
        elif target:
            if not preset:
                raise FeedbackSourceError(
                    "--preset is required for Decoy feedback discovery"
                )
            targets = [target] if isinstance(target, str) else target
            sources = []
            for selected_target in targets:
                if "," in selected_target:
                    raise FeedbackSourceError(
                        "canonical Decoy --target values must be "
                        "space-separated"
                    )
                sources.append({
                    "path": find_decoy_feedback_by_target(
                        selected_target, preset
                    ),
                    "preset": preset,
                    "target": selected_target,
                })
        else:
            if not preset:
                raise FeedbackSourceError(
                    "--preset is required for Decoy feedback discovery"
                )
            sources = find_all_decoy_feedback_sources(preset)
    except FeedbackSourceError as exc:
        output.error(f"ERROR: {exc}")
        return None

    try:
        deployments = canonical_decoy_feedback_deployments(gpu_tier)
    except FeedbackSourceError as exc:
        output.error(f"ERROR: {exc}")
        return None

    return [
        {
            "label": f"decoy-feedback: {item['target']}",
            "behavior_source": item["path"],
            "configs_spec": configs_spec,
            "manifest": None,
            "is_controls": False,
            "purpose": "feedback",
            "target": item["target"],
            "preset": item["preset"],
            "deployments": [dict(dep) for dep in deployments],
            "gpu_tier": gpu_tier,
        }
        for item in sources
    ]


def show_plan_and_confirm(
    plan: list[dict], deploy_type: str, gpu_tier: str = "v100",
) -> bool:
    """Render the combined plan + ask y/N. Returns True iff the user confirms.

    Fails loud (returns False) if a legacy Rampart/GHOSTS manifest target does
    not match deploy_type. Skips the prompt for one controls-only task.

    gpu_tier only applies to DECOY feedback tasks and is rendered as a
    `tier:` line so the operator can confirm flavor + LLM model before
    burning quota.
    """
    n = len(plan)
    output.banner(f"DEPLOY PLAN ({deploy_type}, {n} task{'s' if n != 1 else ''})")
    output.info("")
    if plan and all(task.get("is_llm_comparison") for task in plan):
        gpu_count = sum(task["comparison_hardware"] == "gpu" for task in plan)
        cpu_count = sum(task["comparison_hardware"] == "cpu" for task in plan)
        sidecars = sum(bool(task.get("share_required")) for task in plan)
        output.info(
            f"  Comparison total: {n} SUPs ({gpu_count} GPU, {cpu_count} CPU) "
            f"+ {sidecars} selected-plan share sidecars = {n + sidecars} VMs"
        )
        if all(task.get("no_schedule") for task in plan):
            output.info("  Mode: autonomous; no PHASE timing; all six activities available")
        output.info("")

    any_mismatch = False
    for i, task in enumerate(plan, 1):
        output.info(f"  {i}. {task['label']}")
        if task.get("is_probe"):
            output.info("      category: probe; purpose: other; target: null")
            if task.get("probe_service"):
                output.info(f"      background service: {task['probe_service']} (hourly observations; no workflows)")
            else:
                output.info(f"      workflow: {task['probe_workflow'] or 'idle (no scheduled work)'}")
            output.info(f"      source: {task['behavior_source'] or 'none'}")
            for line in config_vm_table_lines(task["deployments"]):
                output.info(f"      {line}")
            if task["share_required"]:
                output.info("      + one fleet-local v1.small share sidecar (not a measured SUP)")
            continue
        if task.get("is_canary"):
            source = task["behavior_source"]
            output.info("      purpose:     canary (RUSE-only)")
            output.info("      target:      —")
            output.info(f"      source:      {source}")
            output.info("")
            count = sum(item.get("count", 1) for item in task["deployments"])
            tier = "cpu-only" if task["config_name"] == "decoy-mchp-canary" else "v100"
            output.info(f"      VMs to provision ({count}), tier={tier}:")
            for line in config_vm_table_lines(
                task["deployments"], indent="        ", gpu_tier="v100"
            ):
                output.info(line)
        elif task["is_controls"]:
            src = task.get("behavior_source")
            mf = task.get("manifest")
            if deploy_type == "decoy":
                output.info("      purpose:     control")
                if task.get("is_baseline"):
                    output.info(f"      baseline:    {task['baseline_direction']}")
                    output.info(f"      preset:      {task['model_preset']}")
                    if task.get("no_schedule"):
                        output.info("      mode:        autonomous (no PHASE timing; all six activities)")
                    if task.get("requested_model"):
                        output.info(f"      model:       {task['requested_model']}")
                        output.info(f"      hardware:    {task['comparison_hardware']}")
                        if (task["deployments"][0]["behavior"] == "browseruse-gpu"
                                and task["requested_model"] in {
                                    "deepseek-r1:8b-0528-qwen3-q4_K_M",
                                    "gpt-oss:20b",
                                    "granite4.2:8b-q4_K_M",
                                }):
                            output.info("      BrowserUse:  use_vision=False (comparison profile)")
                        if task.get("share_required"):
                            output.info("      sidecar:     one fleet-local v1.small share host")
                    output.info("      target:      null (global)")
                output.info(f"      generation:  {src.name}")
                output.info(f"      source:      {src}")
            elif src is None:
                output.info("      (control — no PHASE feedback)")
            else:
                for line in manifest_summary_lines(src, mf, indent="      "):
                    output.info(line)
            # Controls VMs come from config.yaml's deployments, not a tier
            # template — render them so the operator sees the full baseline
            # topology (C0/M0/M1 + V100/RTX/CPU SUPs), same as feedback tasks.
            deps = task.get("deployments")
            if deps:
                output.info("")
                output.info(f"      VMs to provision ({sum(d.get('count', 1) for d in deps)}):")
                for line in config_vm_table_lines(deps, indent="        ",
                                                 gpu_tier=task.get("gpu_tier", "v100"),
                                                 requested_model=task.get("requested_model")):
                    output.info(line)
        else:
            if deploy_type == "decoy":
                source = task["behavior_source"]
                output.info("      purpose:     feedback")
                output.info(f"      target:      {task['target']}")
                output.info(f"      generation:  {source.name}")
                output.info(f"      source:      {source}")
                output.info("")
                task_gpu_tier = task.get("gpu_tier") or gpu_tier
                output.info(
                    f"      VMs to provision (4), tier={task_gpu_tier}:"
                )
                for line in config_vm_table_lines(
                    task["deployments"],
                    indent="        ",
                    gpu_tier=task_gpu_tier,
                ):
                    output.info(line)
            else:
                mf = task["manifest"]
                err = validate_manifest_target(mf, deploy_type)
                for line in manifest_summary_lines(
                    task["behavior_source"], mf, indent="      ",
                ):
                    output.info(line)
                if err:
                    output.error(f"      FAIL: {err}")
                    any_mismatch = True
        output.info("")

    if any_mismatch:
        output.error("Aborting: one or more manifests don't match deploy type.")
        return False

    if n == 1 and plan[0]["is_controls"] and not plan[0].get("is_baseline"):
        return True

    if not output.confirm(f"Deploy these {n} config(s)?"):
        output.info("Cancelled.")
        return False
    return True


def execute_plan(
    plan: list[dict], deploy_type: str, config_name: str | None,
    deploy_dir: Path, gpu_tier: str = "v100",
) -> int:
    """Run each task in the plan sequentially. Returns 0 iff all succeed.

    Lazy-imports the per-type spinup so this module doesn't pull in all
    three subsystems unconditionally.
    """
    if deploy_type == "rampart":
        from ..rampart.spinup import run_rampart_spinup as spinup
        default_config = "rampart-controls"
    elif deploy_type == "ghosts":
        from ..ghosts.spinup import run_ghosts_spinup as spinup
        default_config = "ghosts-controls"
    else:
        from ..decoy.spinup import run_decoy_spinup as spinup
        default_config = "decoy-controls"

    base_config = config_name or default_config
    results: list[tuple[str, int]] = []

    for i, task in enumerate(plan, 1):
        output.info("")
        output.info(f"[{i}/{len(plan)}] Deploying {task['label']}...")
        # Decoy controls pass their selected PHASE generation to the fixed
        # decoy-controls deployment. Other systems retain their established
        # config.yaml-owned controls source behavior.
        if task["is_controls"] and deploy_type != "decoy":
            spinup_source: str | None = None
        else:
            src = task["behavior_source"]
            spinup_source = str(src) if src else None
        try:
            if task.get("is_baseline") and not task.get("is_saved_config"):
                from .feedback import generate_baseline_config
                generate_baseline_config(task, deploy_dir)
            # gpu_tier only applies to decoy deploys + feedback tasks. Controls
            # tasks use their own config.yaml-declared flavors and ignore the kwarg;
            # rampart/ghosts spinups don't accept it. Pass conditionally.
            spinup_kwargs = {}
            if deploy_type == "decoy" and not task["is_controls"]:
                spinup_kwargs["gpu_tier"] = task.get("gpu_tier") or gpu_tier
            rc = spinup(
                task.get("config_name", base_config), deploy_dir,
                spinup_source,
                task["configs_spec"],
                **spinup_kwargs,
            )
        except SystemExit as e:
            # Per-type spinups call sys.exit() on validation failure. Convert
            # to rc so the batch-summary still renders. Other deploys in the
            # plan continue.
            rc = e.code if isinstance(e.code, int) else 1
            # 130 = SIGINT via __main__.py's signal handler. Don't chug to the
            # next task — propagate so the user's Ctrl+C aborts the batch.
            if rc == 130:
                raise
        results.append((task["label"], rc))
        if rc != 0:
            output.error(f"  FAILED: {task['label']} (rc={rc})")

    if len(results) > 1:
        output.info("")
        output.banner("DEPLOY SUMMARY")
        for label, rc in results:
            status = "OK" if rc == 0 else f"FAILED (rc={rc})"
            output.info(f"  {label}: {status}")
        output.info("")

    failed = sum(1 for _, rc in results if rc != 0)
    if failed:
        output.info(f"DONE: {len(results) - failed}/{len(results)} succeeded, {failed} failed")
        return 1
    output.info(f"DONE: all {len(results)} deployment(s) launched")
    return 0
