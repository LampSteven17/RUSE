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
from datetime import datetime, timezone
from time import sleep, monotonic
from typing import Optional

from common.behavioral_config import MODE_FEEDBACK, MODE_CONTROLS


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
        self._cluster_distinct = set()
        self._cluster_remaining = 0
        # Window-mode contract state (PHASE 2026-05-08).
        # Mirrors BehavioralConfig fields so the emulation loop can gate
        # cluster execution without re-walking behavior.json each tick.
        self._mode = None  # MODE_FEEDBACK / MODE_CONTROLS — set on first reload
        self._volume_target = None  # target_conn_per_minute_during_active
        # Soft fence deadline: if set, the cluster's inner loop must not
        # spawn a new workflow once monotonic() exceeds this. Reset every
        # cluster boundary; None outside windows.
        self._cluster_deadline_ts = None

        self.workflows = []
        self._running = False

        # Defer CalibratedTiming init if behavioral configs will provide variance/activity
        # — otherwise we'd emit transient startup warnings before _reload_behavioral_config()
        # re-creates it with the proper variance_config and activity_config dicts.
        if self.calibration_profile and not self._behavior_config_dir:
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

        # load_behavioral_config raises RuntimeError if behavior.json is
        # missing — service crash-loops, audit surfaces it. No legacy
        # baseline path: every SUP must have a config.
        fc = load_behavioral_config(Path(self._behavior_config_dir), self._config_key)

        # Stash mode for the gate + cluster loop.
        self._mode = fc.mode
        self._volume_target = fc.target_conn_per_minute_during_active
        n_windows = len(fc.active_minute_windows or [])
        on_minutes = sum(e - s for s, e in (fc.active_minute_windows or []))

        # Summary log
        if self.logger:
            self.logger.info(
                f"[behavior] Config reload mode={fc.mode}",
                details={
                    "config_key": self._config_key,
                    "mode": fc.mode,
                    "n_windows": n_windows,
                    "on_minutes": on_minutes,
                    "target_conn_per_min": fc.target_conn_per_minute_during_active,
                    "hard_fence_seconds": fc.hard_fence_seconds,
                },
            )

        # Workflow weights + brain-specific config (feedback only — controls
        # has its own content schema and bypasses the workflow-weights path).
        if fc.mode == MODE_FEEDBACK:
            self._workflow_weights = build_workflow_weights(self.workflows, fc)
            if self._workflow_weights and self.logger:
                self.logger.info(
                    f"[behavior] Loaded workflow_weights for {self._config_key}",
                    details={"weights": fc.workflow_weights})
        else:
            self._workflow_weights = None
        self._apply_brain_specific_config(fc)

        # CalibratedTiming setup. Both modes carry burst_percentiles +
        # variance — controls' is hardcoded floor, feedback's is PHASE-tuned.
        # Build it for both so the gate has access to current_window/fence.
        if fc.timing_profile:
            from common.timing.phase_timing import CalibratedTiming
            old_last_activity = (self._phase_timing._last_activity_time
                                 if self._phase_timing else None)
            try:
                config = build_calibrated_timing_config(fc.timing_profile)
                self._phase_timing = CalibratedTiming(
                    config,
                    variance_config=fc.variance_injection,
                )
                self._phase_timing._last_activity_time = old_last_activity
                if self.logger:
                    self.logger.info("[behavior] Hot-swapped timing_profile",
                                     details={"dataset": config.dataset})
            except (KeyError, TypeError) as e:
                # Controls schema may not nest burst_percentiles the way
                # CalibratedTiming expects; fall back to no-calibrated-timing
                # so the gate still works (it only needs the windows).
                self._phase_timing = None
                if self.logger:
                    self.logger.info(
                        f"[behavior] timing_profile schema lean "
                        f"(mode={fc.mode}) — running gate-only",
                        details={"error": str(e)[:120]})
        elif self.calibration_profile and self._phase_timing is None:
            from common.timing.phase_timing import CalibratedTiming, load_calibration_profile
            config = load_calibration_profile(self.calibration_profile)
            self._phase_timing = CalibratedTiming(config)
            print(f"Calibrated timing ({self.calibration_profile}) - activity level: {self._phase_timing.get_activity_level()}")
        elif self._phase_timing and fc.variance_injection:
            self._phase_timing.update_variance_config(fc.variance_injection)

        # Diversity injection — feedback only (controls has no diversity block)
        if fc.diversity_injection:
            self._diversity_config = fc.diversity_injection
            bg_config = fc.diversity_injection.get("background_services", {})
            if self._background_svc is None:
                from common.background_services import BackgroundServiceGenerator
                self._background_svc = BackgroundServiceGenerator(bg_config, self.logger)
            else:
                self._background_svc.update_config(bg_config)

        # Push window contract — both modes consume it identically.
        if self._phase_timing is not None:
            self._phase_timing.update_window_contract(
                windows=fc.active_minute_windows,
                hard_fence_seconds=fc.hard_fence_seconds,
                min_window_minutes=fc.min_window_minutes,
                window_mode=fc.mode,
            )

        # Feature status report — always print so you know what's active
        rotation = (self._diversity_config or {}).get("workflow_rotation", {})
        min_distinct = rotation.get("min_distinct_per_cluster", 0)
        max_consec = rotation.get("max_consecutive_same", 0)
        has_bg_svc = self._background_svc is not None
        has_prompt_aug = bool(fc.prompt_augmentation and fc.prompt_augmentation.get("prompt_content"))

        # Ablation-gated omissions are intentional, not bugs. PHASE's
        # feedback engine runs per-feature ablation against the target
        # detection model; sections whose knobs don't move the score are
        # deliberately left out. Distinguish:
        #   [INFO]    ... ablation-gated (PHASE dropped it on purpose)
        #   [WARNING] ... DISABLED (unexpected omission, treat as bug)
        gated = fc.is_ablation_gated() if fc else False
        tag = "[INFO]" if gated else "[WARNING]"
        reason_suffix = (" (ablation-gated — no behavioral lever for this model)"
                         if gated else "")

        if min_distinct == 0:
            print(f"{tag} D2 min_distinct_per_cluster DISABLED — "
                  f"no diversity_injection.workflow_rotation.min_distinct_per_cluster"
                  f"{reason_suffix}")
        if max_consec == 0:
            print(f"{tag} D2 max_consecutive_same DISABLED — "
                  f"no diversity_injection.workflow_rotation.max_consecutive_same"
                  f"{reason_suffix}")
        if not has_bg_svc:
            print(f"{tag} D4 background services DISABLED — "
                  f"no diversity_injection.background_services"
                  f"{reason_suffix}")
        if not has_prompt_aug:
            print(f"{tag} G1 prompt_augmentation DISABLED — "
                  f"no prompt_augmentation.prompt_content"
                  f"{reason_suffix}")
        # W4: workflow_weights absent on a non-empty feedback config = partial
        # PHASE output (content.workflow_weights missing). Agent falls back to
        # uniform random — indistinguishable from baseline without this warning.
        if not fc.workflow_weights:
            print(f"{tag} W4 workflow_weights DISABLED — "
                  f"no content.workflow_weights, using uniform random selection"
                  f"{reason_suffix}")
        # W3 site_config: consumer wired 2026-04-27 (SmolAgents BrowseWebWorkflow
        # filters its task pool by category using content.site_categories
        # weights — see SmolAgentLoop._apply_brain_specific_config). Previous
        # "UNUSED" INFO line removed. BrowserUse + MCHP do not consume
        # site_config; if wired later, the [INFO] guard belongs in their
        # respective _apply_brain_specific_config paths, not here.

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
        """Select workflow with diversity-aware rotation.

        Enforces two constraints from diversity_injection.workflow_rotation:
        - max_consecutive_same: penalizes N identical picks in a row
        - min_distinct_per_cluster (D2): near cluster end, penalizes already-seen
          workflows to force diversity within the cluster
        """
        rotation = (self._diversity_config or {}).get("workflow_rotation", {})
        max_consec = rotation.get("max_consecutive_same", 99)
        min_distinct = rotation.get("min_distinct_per_cluster", 0)

        weights = list(self._workflow_weights) if self._workflow_weights else [1.0] * len(self.workflows)

        # Penalize consecutive same workflow
        if len(self._recent_workflows) >= max_consec:
            last_name = self._recent_workflows[-1]
            if all(w == last_name for w in self._recent_workflows[-max_consec:]):
                for i, w in enumerate(self.workflows):
                    if getattr(w, 'name', '') == last_name:
                        weights[i] *= 0.1

        # D2: Near cluster end, force diversity if below minimum distinct count
        if min_distinct > 0 and self._cluster_remaining > 0:
            needed = min_distinct - len(self._cluster_distinct)
            if needed > 0 and self._cluster_remaining <= needed:
                for i, w in enumerate(self.workflows):
                    if getattr(w, 'name', '') in self._cluster_distinct:
                        weights[i] *= 0.01  # strong penalty, not zero (graceful)

        workflow = random.choices(self.workflows, weights=weights, k=1)[0]
        name = getattr(workflow, 'name', '')
        self._recent_workflows.append(name)
        if len(self._recent_workflows) > 10:
            self._recent_workflows.pop(0)
        self._cluster_distinct.add(name)
        self._cluster_remaining -= 1
        return workflow

    # ── Main emulation loop ──────────────────────────────────────────

    # Cap on a single sleep-until-next-window. Shorter than the longest
    # gap so the reload tick fires (and PHASE re-rolls / hot-patches land)
    # at least every CAP_S even during long idle stretches.
    _WINDOW_GATE_SLEEP_CAP_S = 30 * 60  # 30 minutes
    # Minimum remaining-in-window required before starting a cluster.
    # Below this we sleep through the window end rather than spawn a
    # workflow that can't complete inside the active period (gemma's slow
    # path takes 60-120s; 90s is the floor that keeps us inside the
    # window with margin).
    _START_ONLY_FLOOR_S = 90
    # Cap how often the IDLE_ALL_DAY loop wakes to re-check config.
    _IDLE_ALL_DAY_TICK_S = 30 * 60

    def _window_gate_sleep_then_continue(self) -> bool:
        """Window-mode gate. Both feedback and controls modes consume the
        gate identically — the only difference is the windows themselves
        (feedback emits 5–15 narrow windows; controls emits a single
        60-min slot). Returns True if the loop should `continue` (sleep
        happened); False if execution should proceed to run a cluster.

        States:
          outside any window → sleep until next start (capped)  → True
          inside, remaining < 90s+fence → sleep through end     → True
          inside, runway OK → set cluster deadline              → False
          no _phase_timing or no windows → fall through         → False
        """
        if self._phase_timing is None or not self._phase_timing.has_windows():
            self._cluster_deadline_ts = None
            return False

        if self._phase_timing.current_window() is None:
            # Outside any window — sleep until the next one starts.
            wait = self._phase_timing.time_until_next_window_start()
            wait = min(wait, self._WINDOW_GATE_SLEEP_CAP_S)
            wait = max(wait, 1.0)
            if self.logger:
                self.logger.info(
                    f"[window] outside windows — sleeping {wait/60:.1f}min "
                    f"until next start (capped at "
                    f"{self._WINDOW_GATE_SLEEP_CAP_S//60}min)",
                    details={"wait_s": wait})
            sleep(wait)
            return True

        # Inside a window. Check remaining vs start-only floor.
        remaining = self._phase_timing.time_until_window_end() or 0.0
        hard_fence = self._phase_timing._hard_fence_seconds
        usable = max(0.0, remaining - hard_fence)
        if usable < self._START_ONLY_FLOOR_S:
            if self.logger:
                self.logger.info(
                    f"[window] only {usable:.0f}s usable in current "
                    f"window (< {self._START_ONLY_FLOOR_S}s floor) — "
                    f"sleeping through end",
                    details={"remaining_s": remaining,
                             "hard_fence_s": hard_fence})
            sleep(remaining + 1.0)
            return True

        self._cluster_deadline_ts = monotonic() + usable
        return False

    def _emulation_loop(self):
        """Main emulation loop — runs workflows in clusters."""
        while self._running:
            self._reload_behavioral_config()

            # Window-mode gate (PHASE 2026-05-08). Identical behavior for
            # both feedback and controls modes — the windows themselves
            # carry the difference. Sleep until next window if outside;
            # otherwise set a cluster deadline and fall through.
            if self._window_gate_sleep_then_continue():
                continue

            # Push window-state to D4 background services so deficit-burst
            # tops up bg-conn rate to target_conn_per_minute_during_active
            # while inside an active window. Outside a window (LEGACY /
            # BASELINE), in_window=False disables the burst — bg-svc
            # falls back to its hour-rate behavior.
            if self._background_svc is not None:
                self._background_svc.set_window_state(
                    in_window=self._cluster_deadline_ts is not None,
                    volume_target=self._volume_target,
                )

            # Log activity level
            if self._phase_timing:
                activity_level = self._phase_timing.get_activity_level()
                current_hour = datetime.now(timezone.utc).hour
                print(f"[{datetime.now().strftime('%H:%M')}] Activity level: {activity_level} (UTC hour {current_hour})")
                if self.logger:
                    self.logger.info(f"Activity level: {activity_level}", details={
                        "hour": current_hour, "level": activity_level
                    })

            cluster_size = self._get_cluster_size()

            # D2: Reset per-cluster diversity tracking
            self._cluster_distinct = set()
            self._cluster_remaining = cluster_size

            if self.logger:
                self.logger.decision(
                    choice="cluster_size",
                    selected=str(cluster_size),
                    context="Tasks to run in this cluster",
                    method="calibrated" if self._phase_timing else "random"
                )

            for _ in range(cluster_size):
                # Soft fence (option B): if the cluster's deadline has passed,
                # don't start a new workflow. Lets in-flight workflows finish
                # naturally — they'll overshoot the window by ≤max_steps × per-
                # step_delay, typically 30-60s, which is acceptable.
                if (self._cluster_deadline_ts is not None
                        and monotonic() >= self._cluster_deadline_ts):
                    if self.logger:
                        self.logger.info(
                            "[window] cluster deadline reached — "
                            "skipping remaining workflows in cluster",
                            details={"deadline_ts": self._cluster_deadline_ts})
                    break

                task_delay = self._get_task_delay()
                if self.logger:
                    self.logger.timing_delay(task_delay, reason="inter_task")
                sleep(task_delay)

                # Re-check fence after the inter-task sleep — task_delay
                # can be tens of seconds.
                if (self._cluster_deadline_ts is not None
                        and monotonic() >= self._cluster_deadline_ts):
                    if self.logger:
                        self.logger.info(
                            "[window] cluster deadline reached during "
                            "inter-task sleep — skipping remainder")
                    break

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
