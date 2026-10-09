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
from phase_workflow.probes import PROBE_SUPS, probe_day_index, validate_probe_plans
from phase_workflow.ntp_policy import NtpPolicy, ntp_lifecycle


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
    if config_key not in PROBE_SUPS or os.environ.get("RUSE_DEPLOYMENT_TYPE") != "probe":
        raise RuntimeError("probe runtime requires an explicit Scripted or MCHP CPU Probe installation")
    started_at = os.environ["RUSE_PROBE_STARTED_AT"]
    source = None if workflow == "idle" else resolve_behavior_path(config_key, behavior_config_dir).parent
    plans = validate_probe_plans(source, None if workflow == "idle" else workflow, config_key)
    logger = AgentLogger(agent_type=config_key)
    session_config = {
        "deployment_type": "probe", "probe_workflow": workflow,
        "sup_config": config_key, "brain": "mchp" if config_key == "mchp-cpu" else "scripted", "hardware": "cpu",
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

_TEXT_ONLY_COMPARISON_MODELS = frozenset({
    "deepseek-r1:8b-0528-qwen3-q4_K_M",
    "gpt-oss:20b",
    "granite4.2:8b-q4_K_M",
})

_COMPARISON_MODELS = frozenset({
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
})

_CPU_COMPARISON_MODELS = frozenset({
    "qwen3.8:27b-q4_K_M",
    "qwen3.5:9b-q4_K_M",
    "gemma4:e4b-it-qat",
    "ministral-3:3b-instruct-2512-q4_K_M",
    "deepseek-r1:8b-0528-qwen3-q4_K_M",
    "gpt-oss:20b",
    "granite4.2:8b-q4_K_M",
    "llama3.2-vision:11b-instruct-q4_K_M",
})


def _select_runtime_brain_profile(plan):
    """Bind the deployed hardware tier to the immutable runtime profile."""
    gpu_tier = os.environ.get("RUSE_WORKFLOW_GPU_TIER", "v100")
    comparison = os.environ.get("RUSE_LLM_COMPARISON") == "1"
    comparison_hardware = os.environ.get("RUSE_COMPARISON_HARDWARE", "gpu")
    if gpu_tier not in _GPU_TIER_MODELS and not (
        comparison and comparison_hardware == "cpu" and gpu_tier == "cpu"
    ):
        raise RuntimeError(f"unsupported canonical workflow GPU tier: {gpu_tier}")
    requested_model = os.environ.get("RUSE_WORKFLOW_MODEL")
    if bool(requested_model) != comparison:
        raise RuntimeError("explicit workflow model and comparison marker must be provided together")
    if comparison and plan.hardware != "gpu":
        raise RuntimeError("LLM comparison requires a canonical GPU Brain")
    if plan.hardware != "gpu":
        return plan.brain_profile, gpu_tier, None

    approved_models = (
        _COMPARISON_MODELS if comparison_hardware == "gpu" else _CPU_COMPARISON_MODELS
    )
    if comparison and (gpu_tier != ("v100" if comparison_hardware == "gpu" else "cpu")
                       or requested_model not in approved_models
                       or comparison_hardware not in {"gpu", "cpu"}):
        raise RuntimeError("LLM comparison model and hardware selection are invalid")
    ollama_model = requested_model or _GPU_TIER_MODELS[gpu_tier]
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
    profile_values = {
        **plan.brain_profile,
        "model": selected_model,
    }
    if (comparison
            and plan.brain == "browseruse"
            and ollama_model in _TEXT_ONLY_COMPARISON_MODELS):
        profile_values["use_vision"] = False
    selected_profile = MappingProxyType(profile_values)
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
        "hardware": (
            os.environ.get("RUSE_COMPARISON_HARDWARE", plan.hardware)
            if os.environ.get("RUSE_LLM_COMPARISON") == "1" else plan.hardware
        ),
        "gpu_tier": gpu_tier,
        "resource_profile": plan.resource_profile,
        "timezone": str(plan.timezone),
        "max_parallel": plan.max_parallel,
    }
    if ollama_model is not None:
        session_config["ollama_model"] = ollama_model
    if os.environ.get("RUSE_LLM_COMPARISON") == "1":
        session_config["requested_model"] = os.environ["RUSE_WORKFLOW_MODEL"]
        session_config["comparison_hardware"] = os.environ.get("RUSE_COMPARISON_HARDWARE", "gpu")
        if plan.brain == "browseruse":
            session_config["use_vision"] = brain_profile.get("use_vision", True)
    logger.session_start(config=session_config)
    policy = (NtpPolicy(plan.ntp_off_intervals, plan.timezone, logger)
              if plan.ntp_off_intervals is not None else None)
    try:
        with ntp_lifecycle(policy):
            registry = WorkflowRegistry(
                plan,
                build_brain(plan.brain, brain_profile, logger),
                behavior_path.parent / "workspace",
            )
            executor = None
            try:
                executor = DailyExecutor(plan, registry, logger.workflow_plan_terminal,
                                         background_policy=policy)
                executor.run_forever()
            except KeyboardInterrupt:
                logger.info("Workflow runtime stopped by user")
            finally:
                try:
                    if executor is not None:
                        executor.close()
                finally:
                    registry.close()
    except Exception as exc:
        logger.session_fail(message="Workflow runtime failed", exception=exc)
        raise
    finally:
        logger.session_end()
