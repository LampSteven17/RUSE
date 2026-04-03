"""
Base emulation loop for all RUSE brain agents.

Provides the shared cluster-based execution pattern:
  cluster → inter-task delays → workflow selection → inter-cluster delays

Subclasses implement brain-specific behavior:
  _load_workflows()              — load workflow objects
  _execute_workflow(workflow)     — run a single workflow (brain-specific API)
  _apply_brain_specific_config() — apply brain-specific behavioral config
  _agent_type_label()            — "mchp", "browseruse_loop", "smolagents_loop"
"""

import random
import signal
import sys
from abc import ABC, abstractmethod
from datetime import datetime
from time import sleep
from typing import Optional


class BaseEmulationLoop(ABC):
    """Abstract base class for RUSE brain emulation loops."""

    def __init__(
        self,
        cluster_size: int = 5,
        task_interval: int = 30,
        group_interval: int = 600,
        logger=None,
        calibration_profile: Optional[str] = None,
        seed: int = 42,
        behavior_config_dir: Optional[str] = None,
        config_key: Optional[str] = None,
    ):
        self.seed = seed
        self.cluster_size = cluster_size
        self.task_interval = task_interval
        self.group_interval = group_interval
        self.logger = logger
        self.calibration_profile = calibration_profile
        self._phase_timing = None  # CalibratedTiming instance, or None for baselines
        self._tasks_completed = 0
        self._behavior_config_dir = behavior_config_dir
        self._config_key = config_key
        self._workflow_weights = None
        self._diversity_config = None
        self._background_svc = None
        self._recent_workflows = []

        self.workflows = []
        self._running = False

        if self.calibration_profile:
            self._init_calibrated_timing()

    # ── Abstract methods (subclasses must implement) ─────────────────

    @abstractmethod
    def _load_workflows(self) -> list:
        """Load and return workflow objects for this brain."""
        ...

    @abstractmethod
    def _execute_workflow(self, workflow) -> bool:
        """Execute a single workflow. Return True on success, False on failure."""
        ...

    @abstractmethod
    def _apply_brain_specific_config(self, fc) -> None:
        """Apply brain-specific parts of behavioral config (e.g., page_dwell, task_weights)."""
        ...

    @abstractmethod
    def _agent_type_label(self) -> str:
        """Return agent type string for logging (e.g., 'mchp', 'browseruse_loop')."""
        ...

    # ── Timing initialization ────────────────────────────────────────

    def _init_calibrated_timing(self):
        """Initialize calibrated timing from an empirical profile."""
        from common.timing.phase_timing import CalibratedTiming, load_calibration_profile
        config = load_calibration_profile(self.calibration_profile)
        self._phase_timing = CalibratedTiming(config)
        print(f"Calibrated timing ({self.calibration_profile}) - activity level: {self._phase_timing.get_activity_level()}")

    # ── Timing helpers ───────────────────────────────────────────────

    def _get_cluster_size(self) -> int:
        if self._phase_timing:
            return self._phase_timing.get_cluster_size()
        return random.randint(1, self.cluster_size)

    def _get_task_delay(self) -> float:
        if self._phase_timing:
            return self._phase_timing.get_task_delay()
        return random.randrange(self.task_interval)

    def _get_cluster_delay(self) -> float:
        if self._phase_timing:
            if self._phase_timing.should_take_break(self._tasks_completed):
                self._tasks_completed = 0
                return self._phase_timing.get_break_duration()
            return self._phase_timing.get_cluster_delay()
        return random.randrange(self.group_interval)

    # ── Behavioral config reload ─────────────────────────────────────

    def _reload_behavioral_config(self):
        """Reload behavioral config from disk (hot-swap support)."""
        if not self._behavior_config_dir or not self._config_key:
            self._workflow_weights = None
            return

        from pathlib import Path
        from common.behavioral_config import (
            load_behavioral_config, build_workflow_weights,
            build_calibrated_timing_config,
        )

        fc = load_behavioral_config(Path(self._behavior_config_dir), self._config_key)

        if fc.is_empty():
            self._workflow_weights = None
            return

        # Summary log: which configs were loaded (single line for PHASE tracking)
        if self.logger:
            loaded = {k: (v is not None) for k, v in [
                ("workflow_weights", fc.workflow_weights),
                ("behavior_modifiers", fc.behavior_modifiers),
                ("site_config", fc.site_config),
                ("prompt_augmentation", fc.prompt_augmentation),
                ("timing_profile", fc.timing_profile),
                ("variance_injection", fc.variance_injection),
                ("diversity_injection", fc.diversity_injection),
                ("activity_pattern", fc.activity_pattern),
            ]}
            active = [k for k, v in loaded.items() if v]
            self.logger.info(f"[behavior] Config reload: {len(active)} configs active",
                             details={"config_key": self._config_key, "configs": loaded})

        # Workflow weights (shared across all brains)
        self._workflow_weights = build_workflow_weights(self.workflows, fc)
        if self._workflow_weights and self.logger:
            self.logger.info(f"[behavior] Loaded workflow_weights for {self._config_key}",
                             details={"weights": fc.workflow_weights})

        # Brain-specific config (page_dwell, task_weights, max_steps, etc.)
        self._apply_brain_specific_config(fc)

        # Timing profile — hot-swap calibrated timing (pass variance + activity configs)
        if fc.timing_profile:
            from common.timing.phase_timing import CalibratedTiming
            old_last_activity = (self._phase_timing._last_activity_time
                                 if self._phase_timing else None)
            config = build_calibrated_timing_config(fc.timing_profile)
            self._phase_timing = CalibratedTiming(
                config,
                variance_config=fc.variance_injection,
                activity_config=fc.activity_pattern,
            )
            self._phase_timing._last_activity_time = old_last_activity
            if self.logger:
                self.logger.info("[behavior] Hot-swapped timing_profile",
                                 details={"dataset": config.dataset})
        elif self._phase_timing:
            if fc.variance_injection:
                self._phase_timing.update_variance_config(fc.variance_injection)
                if self.logger:
                    self.logger.info("[behavior] Applied variance_injection",
                                     details=fc.variance_injection)
            if fc.activity_pattern:
                self._phase_timing.update_activity_config(fc.activity_pattern)
                if self.logger:
                    self.logger.info("[behavior] Applied activity_pattern",
                                     details={"daily_shape": bool(fc.activity_pattern.get("daily_shape")),
                                              "idle_behavior": bool(fc.activity_pattern.get("idle_behavior"))})

        # Diversity injection — workflow rotation + background services
        if fc.diversity_injection:
            self._diversity_config = fc.diversity_injection
            bg_config = fc.diversity_injection.get("background_services", {})
            if self._background_svc is None:
                from common.background_services import BackgroundServiceGenerator
                self._background_svc = BackgroundServiceGenerator(bg_config, self.logger)
            else:
                self._background_svc.update_config(bg_config)
            if self.logger:
                self.logger.info("[behavior] Applied diversity_injection",
                                 details={"rotation": fc.diversity_injection.get("workflow_rotation", {})})

    # ── Workflow selection ────────────────────────────────────────────

    def _select_workflow(self):
        """Select next workflow using diversity rotation, weights, or uniform random."""
        if self._diversity_config:
            return self._select_workflow_with_rotation()
        elif self._workflow_weights:
            return random.choices(self.workflows, weights=self._workflow_weights, k=1)[0]
        else:
            return self.workflows[random.randrange(len(self.workflows))]

    def _select_workflow_with_rotation(self):
        """Select workflow with diversity-aware rotation."""
        rotation = (self._diversity_config or {}).get("workflow_rotation", {})
        max_consec = rotation.get("max_consecutive_same", 99)

        weights = list(self._workflow_weights) if self._workflow_weights else [1.0] * len(self.workflows)

        if len(self._recent_workflows) >= max_consec:
            last_name = self._recent_workflows[-1]
            if all(w == last_name for w in self._recent_workflows[-max_consec:]):
                for i, w in enumerate(self.workflows):
                    if getattr(w, 'name', '') == last_name:
                        weights[i] *= 0.1

        workflow = random.choices(self.workflows, weights=weights, k=1)[0]
        self._recent_workflows.append(getattr(workflow, 'name', ''))
        if len(self._recent_workflows) > 10:
            self._recent_workflows.pop(0)
        return workflow

    # ── Main emulation loop ──────────────────────────────────────────

    def _emulation_loop(self):
        """Main emulation loop — runs workflows in clusters."""
        while self._running:
            self._reload_behavioral_config()

            # Activity pattern: skip low-activity hours
            if self._phase_timing and self._phase_timing.should_skip_hour():
                    now = datetime.now()
                    seconds_until_next_hour = (60 - now.minute) * 60 - now.second
                    skip_time = seconds_until_next_hour + random.uniform(0, 300)
                    if self.logger:
                        self.logger.info(f"[activity] Skipping low-activity hour {now.hour}, sleeping {skip_time/60:.0f}min")
                    sleep(skip_time)
                    continue

            # Activity pattern: long idle injection
            if self._phase_timing:
                should_idle, idle_duration = self._phase_timing.should_take_long_idle()
                if should_idle:
                    if self.logger:
                        self.logger.info(f"[activity] Long idle: {idle_duration/60:.0f}min")
                    sleep(idle_duration)

            # Log activity level
            if self._phase_timing:
                activity_level = self._phase_timing.get_activity_level()
                current_hour = datetime.now().hour
                print(f"[{datetime.now().strftime('%H:%M')}] Activity level: {activity_level}")
                if self.logger:
                    self.logger.info(f"Activity level: {activity_level}", details={
                        "hour": current_hour, "level": activity_level
                    })

            cluster_size = self._get_cluster_size()

            if self.logger:
                self.logger.decision(
                    choice="cluster_size",
                    selected=str(cluster_size),
                    context="Tasks to run in this cluster",
                    method="calibrated" if self._phase_timing else "random"
                )

            for _ in range(cluster_size):
                task_delay = self._get_task_delay()
                if self.logger:
                    self.logger.timing_delay(task_delay, reason="inter_task")
                sleep(task_delay)

                # Background service traffic
                if self._background_svc:
                    self._background_svc.maybe_generate()

                # Select workflow
                workflow = self._select_workflow()
                workflow_name = workflow.description

                if self.logger:
                    workflow_options = [w.name for w in self.workflows]
                    self.logger.decision(
                        choice="workflow_selection",
                        options=workflow_options,
                        selected=workflow.name,
                        context=workflow_name,
                        method="behavior_weighted" if self._workflow_weights else "random"
                    )

                print(workflow.display)

                if self.logger:
                    params = {
                        "agent_type": self._agent_type_label(),
                        "workflow_class": workflow.__class__.__name__,
                        "phase_timing": self._phase_timing is not None,
                    }
                    if hasattr(workflow, 'category'):
                        params["category"] = workflow.category
                    self.logger.workflow_start(workflow_name, params=params)

                success = self._execute_workflow(workflow)

                if success:
                    self._tasks_completed += 1
                    if self._phase_timing:
                        self._phase_timing.record_activity()

            # Inter-cluster delay
            group_delay = self._get_cluster_delay()
            if self.logger:
                self.logger.timing_delay(group_delay, reason="inter_cluster")
            sleep(group_delay)

    # ── Lifecycle ────────────────────────────────────────────────────

    def run(self):
        """Start the emulation loop."""
        if self.seed != 0:
            random.seed(self.seed)
        else:
            random.seed()

        self.workflows = self._load_workflows()
        self._reload_behavioral_config()

        if not self.workflows:
            print("Error: No workflows loaded!")
            if self.logger:
                self.logger.error("No workflows loaded", fatal=True)
            return

        self._running = True
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        label = self._agent_type_label()
        print(f"\nStarting {label} with {len(self.workflows)} workflows")
        print(f"PHASE timing: {self._phase_timing is not None}")
        if not self._phase_timing is not None:
            print(f"Timing: cluster_size={self.cluster_size}, task_interval={self.task_interval}, group_interval={self.group_interval}")
        print("-" * 60)

        if self.logger:
            self.logger.info(f"{label} started", details={
                "workflow_count": len(self.workflows),
                "phase_timing": self._phase_timing is not None,
            })

        try:
            self._emulation_loop()
        except KeyboardInterrupt:
            self.stop()
            sys.exit(0)

    def stop(self):
        """Stop the emulation and cleanup workflows."""
        if not self._running:
            return
        self._running = False
        label = self._agent_type_label()
        print(f"\nTerminating {label}...")
        if self.logger:
            self.logger.info(f"{label} terminating")
        for workflow in self.workflows:
            try:
                workflow.cleanup()
            except Exception:
                pass

    def _signal_handler(self, sig, frame):
        self.stop()
        sys.exit(0)
