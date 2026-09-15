"""Startup wiring for the canonical workflow runtime."""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Optional

from common.logging.agent_logger import AgentLogger
from phase_workflow.brains import build_brain
from phase_workflow.configurations import is_workflow_configuration
from phase_workflow.executor import DailyExecutor
from phase_workflow.loader import load_workflow_plan
from phase_workflow.registry import WorkflowRegistry
from phase_workflow.probes import probe_day_index, validate_probe_plans


class ProbeExecutor(DailyExecutor):
    """Select a validated daily variant before the ordinary scheduler resolves work.

    Active occurrences retain their immutable tasks, the same registry/Brain,
    worker pool, workspace date and capacity slot across midnight.
    """

    def __init__(self, plans, started_at, registry, logger, **kwargs):
        self._probe_plans = plans
        self._probe_started_at = started_at
        self._probe_logger = logger
        super().__init__(plans[0], registry, logger.workflow_plan_terminal, **kwargs)

    def _load_day(self, local_day, *, initial):
        index = probe_day_index(self._probe_started_at, local_day, len(self._probe_plans))
        self.plan = self._probe_plans[index]
        resource_id = self.plan.windows[0].sequence[0].resource_id
        self._probe_logger.info("Probe daily plan selected", {
            "local_date": local_day.isoformat(), "cycle_index": index,
            "resource_id": resource_id, "filename": f"{resource_id}.json",
        })
        super()._load_day(local_day, initial=initial)


def run_probe_runtime(config_key, workflow, behavior_config_dir, *, stop_event=None):
    """Explicit probe installation only; idle never constructs an empty plan."""
    if config_key != "scripted-cpu" or os.environ.get("RUSE_DEPLOYMENT_TYPE") != "probe":
        raise RuntimeError("probe runtime requires an explicit scripted-cpu Probe installation")
    started_at = os.environ["RUSE_PROBE_STARTED_AT"]
    source = None if workflow == "idle" else resolve_behavior_path(config_key, behavior_config_dir).parent
    plans = validate_probe_plans(source, None if workflow == "idle" else workflow)
    logger = AgentLogger(agent_type=config_key)
    session_config = {
        "deployment_type": "probe", "probe_workflow": workflow,
        "sup_config": config_key, "brain": "scripted", "hardware": "cpu",
        "timezone": "America/New_York", "max_parallel": 10,
        "started_at": started_at,
    }
    if plans:
        session_config.update(schema=plans[0].schema, resource_profile=plans[0].resource_profile)
    logger.session_start(config=session_config)
    registry = executor = None
    try:
        if not plans:
            # No Brain, scheduler, fake workflow or periodic traffic generator.
            (stop_event if stop_event is not None else threading.Event()).wait()
            return
        plan = plans[0]
        registry = WorkflowRegistry(
            plan, build_brain(plan.brain, plan.brain_profile, logger),
            source.parent / "workspace", isolate_occurrences=True,
        )
        executor = ProbeExecutor(plans, started_at, registry, logger)
        executor.run_forever(stop_event)
    except KeyboardInterrupt:
        logger.info("Probe stopped by user")
    except Exception as exc:
        logger.session_fail(message="Probe runtime failed", exception=exc)
        raise
    finally:
        try:
            if executor is not None:
                executor.close()
        finally:
            if registry is not None:
                registry.close()
            logger.session_end()


_GPU_TIER_MODELS = {
    "v100": "gemma4:26b",
    "rtx": "gemma4:e4b",
}


def _select_runtime_brain_profile(plan):
    """Bind the deployed hardware tier to the immutable runtime profile."""
    gpu_tier = os.environ.get("RUSE_WORKFLOW_GPU_TIER", "v100")
    if gpu_tier not in _GPU_TIER_MODELS:
        raise RuntimeError(f"unsupported canonical workflow GPU tier: {gpu_tier}")
    if plan.hardware != "gpu":
        return plan.brain_profile, gpu_tier, None

    ollama_model = _GPU_TIER_MODELS[gpu_tier]
    installed_model = os.environ.get("OLLAMA_MODEL", ollama_model)
    if installed_model != ollama_model:
        raise RuntimeError(
            "canonical workflow model does not match deployed GPU tier: "
            f"tier={gpu_tier}, model={installed_model}"
        )
    model = plan.brain_profile.get("model")
    if not isinstance(model, Mapping):
        raise RuntimeError("canonical GPU Brain profile is missing its model")
    selected_model = MappingProxyType({**model, "ollama": ollama_model})
    selected_profile = MappingProxyType({
        **plan.brain_profile,
        "model": selected_model,
    })
    return selected_profile, gpu_tier, ollama_model


def resolve_behavior_path(config_key: str, override_dir: Optional[str]) -> Path:
    """Resolve one startup file without creating directories or fallbacks."""
    if override_dir is not None:
        root = Path(override_dir).expanduser()
    elif os.environ.get("RUSE_BEHAVIOR_CONFIG_DIR"):
        root = Path(os.environ["RUSE_BEHAVIOR_CONFIG_DIR"]).expanduser()
    elif Path("/opt/ruse/deployed_sups").is_dir():
        root = Path("/opt/ruse/deployed_sups") / config_key / "behavioral_configurations"
    else:
        root = (
            Path(__file__).resolve().parents[2]
            / "deployed_sups"
            / config_key
            / "behavioral_configurations"
        )
    return root / "behavior.json"


def run_workflow_runtime(config_key: str, behavior_config_dir: Optional[str]) -> None:
    if not is_workflow_configuration(config_key):
        raise RuntimeError(f"unsupported workflow configuration: {config_key}")
    behavior_path = resolve_behavior_path(config_key, behavior_config_dir)
    # The only read of behavior.json. Any exception escapes and prevents startup.
    plan = load_workflow_plan(behavior_path, config_key)
    brain_profile, gpu_tier, ollama_model = _select_runtime_brain_profile(plan)
    logger = AgentLogger(agent_type=config_key)
    session_config = {
        "schema": plan.schema,
        "sup_config": plan.sup_config,
        "brain": plan.brain,
        "hardware": plan.hardware,
        "gpu_tier": gpu_tier,
        "resource_profile": plan.resource_profile,
        "timezone": str(plan.timezone),
        "max_parallel": plan.max_parallel,
    }
    if ollama_model is not None:
        session_config["ollama_model"] = ollama_model
    logger.session_start(config=session_config)
    registry = WorkflowRegistry(
        plan,
        build_brain(plan.brain, brain_profile, logger),
        behavior_path.parent / "workspace",
    )
    executor = DailyExecutor(plan, registry, logger.workflow_plan_terminal)
    try:
        executor.run_forever()
    except KeyboardInterrupt:
        logger.info("Workflow runtime stopped by user")
    except Exception as exc:
        logger.session_fail(message="Workflow runtime failed", exception=exc)
        raise
    finally:
        try:
            executor.close()
        finally:
            registry.close()
            logger.session_end()
