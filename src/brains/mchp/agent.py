"""
MCHP Brain - Human behavior emulation agent.

This is a thin wrapper around the original human.py logic,
providing a class-based interface for the unified SUP runner.

Configurations (exp-3):
- M0: Upstream MITRE pyhuman (control - DO NOT MODIFY)
- M1: RUSE MCHP baseline (no timing)
- M2: MCHP + summer24 calibrated timing
- M3: MCHP + fall24 calibrated timing
- M4: MCHP + spring25 calibrated timing
"""
import signal
import os
import random
import sys
from datetime import datetime
from importlib import import_module
from time import sleep
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger
    from common.timing.phase_timing import PhaseTiming, PhaseTimingConfig

# Default timing parameters (original MCHP)
DEFAULT_CLUSTER_SIZE = 5
DEFAULT_TASK_INTERVAL = 10
DEFAULT_GROUP_INTERVAL = 500

# Windows-only workflows (use os.startfile or other Windows-specific APIs)
# These are excluded for M2+ configs which run on Linux with LLM augmentation
# Note: open_office_calc.py and open_office_writer.py now support LibreOffice on Linux
WINDOWS_ONLY_WORKFLOWS = {
    'ms_paint.py',
}


class MCHPAgent:
    """
    MCHP (Human Emulation) Agent.

    Runs workflows in random clusters with configurable timing.

    Timing Modes:
    - No timing: Original random timing (M1 baseline)
    - calibration_profile: Calibrated timing from empirical profile (M2-M4)
    - use_phase_timing: Legacy PHASE timing (exp-2 compat)
    """

    def __init__(
        self,
        cluster_size: int = DEFAULT_CLUSTER_SIZE,
        task_interval: int = DEFAULT_TASK_INTERVAL,
        group_interval: int = DEFAULT_GROUP_INTERVAL,
        extra: list = None,
        logger: Optional["AgentLogger"] = None,
        calibration_profile: Optional[str] = None,
        use_phase_timing: bool = False,
        phase_config: Optional["PhaseTimingConfig"] = None,
        exclude_windows_workflows: bool = False,
        seed: int = 42,
        feedback_dir: Optional[str] = None,
        config_key: Optional[str] = None,
    ):
        self.seed = seed
        self.cluster_size = cluster_size
        self.task_interval = task_interval
        self.group_interval = group_interval
        self.extra = extra or []
        self.workflows = []
        self._running = False
        self.logger = logger
        self.calibration_profile = calibration_profile
        self.use_phase_timing = use_phase_timing or (calibration_profile is not None)
        self._phase_timing = None
        self._phase_config = phase_config
        self._tasks_completed = 0
        self.exclude_windows_workflows = exclude_windows_workflows
        self._feedback_dir = feedback_dir
        self._config_key = config_key
        self._workflow_weights = None

        if self.calibration_profile:
            self._init_calibrated_timing()
        elif self.use_phase_timing:
            self._init_phase_timing()

    def _init_calibrated_timing(self):
        """Initialize calibrated timing from an empirical profile."""
        from common.timing.phase_timing import CalibratedTiming, load_calibration_profile
        config = load_calibration_profile(self.calibration_profile)
        self._phase_timing = CalibratedTiming(config)
        print(f"Calibrated timing ({self.calibration_profile}) - activity level: {self._phase_timing.get_activity_level()}")

    def _init_phase_timing(self):
        """Initialize legacy PHASE timing module."""
        from common.timing.phase_timing import PhaseTiming, PhaseTimingConfig

        if self._phase_config is None:
            self._phase_config = PhaseTimingConfig(
                min_cluster_size=3,
                max_cluster_size=8,
                min_task_delay=5.0,
                max_task_delay=30.0,
                min_cluster_delay=120.0,
                max_cluster_delay=600.0,
                enable_hourly_adjustment=True,
            )

        self._phase_timing = PhaseTiming(self._phase_config)
        print(f"PHASE timing enabled - current activity level: {self._phase_timing.get_activity_level()}")

    def _reload_feedback(self):
        """Reload feedback config from disk (hot-swap support)."""
        if not self._feedback_dir or not self._config_key:
            self._workflow_weights = None
            return

        from pathlib import Path
        from common.feedback_config import load_feedback_config, build_workflow_weights, build_site_weights

        fc = load_feedback_config(Path(self._feedback_dir), self._config_key)

        if fc.is_empty():
            self._workflow_weights = None
            return

        # Workflow weights
        self._workflow_weights = build_workflow_weights(self.workflows, fc)
        if self._workflow_weights and self.logger:
            self.logger.info(f"[feedback] Loaded workflow_weights for {self._config_key}",
                             details={"weights": fc.workflow_weights})

        # Behavior modifiers - apply to BrowseWeb workflow
        if fc.behavior_modifiers:
            for w in self.workflows:
                if getattr(w, 'name', '') == 'BrowseWeb':
                    page_dwell = fc.behavior_modifiers.get("page_dwell", {})
                    if "max_seconds" in page_dwell:
                        w.max_sleep_time = int(page_dwell["max_seconds"])
                    if "min_seconds" in page_dwell:
                        w.min_sleep_time = int(page_dwell["min_seconds"])
                    nav_clicks = fc.behavior_modifiers.get("navigation_clicks", {})
                    if "max" in nav_clicks:
                        w.max_navigation_clicks = int(nav_clicks["max"])
                    if self.logger:
                        self.logger.info(f"[feedback] Applied behavior_modifiers to BrowseWeb",
                                         details=fc.behavior_modifiers)
                    break

        # Site config - apply site weights to BrowseWeb workflow
        if fc.site_config:
            for w in self.workflows:
                if getattr(w, 'name', '') == 'BrowseWeb':
                    site_weights = build_site_weights(w.website_list, fc.site_config)
                    w.site_weights = site_weights
                    if site_weights and self.logger:
                        self.logger.info(f"[feedback] Applied site_weights to BrowseWeb")
                    break

    def _get_cluster_size(self) -> int:
        """Get cluster size based on timing mode."""
        if self.use_phase_timing and self._phase_timing:
            return self._phase_timing.get_cluster_size()
        return random.randint(1, self.cluster_size)

    def _get_task_delay(self) -> float:
        """Get inter-task delay based on timing mode."""
        if self.use_phase_timing and self._phase_timing:
            return self._phase_timing.get_task_delay()
        return random.randrange(self.task_interval)

    def _get_cluster_delay(self) -> float:
        """Get inter-cluster delay based on timing mode."""
        if self.use_phase_timing and self._phase_timing:
            # Check if we should take a longer break
            if self._phase_timing.should_take_break(self._tasks_completed):
                self._tasks_completed = 0  # Reset counter
                delay = self._phase_timing.get_break_duration()
                if self.logger:
                    self.logger.timing_delay(delay, reason="extended_break")
                return delay
            return self._phase_timing.get_cluster_delay()
        return random.randrange(self.group_interval)

    def _import_workflows(self):
        """Dynamically load all workflow modules."""
        extensions = []
        workflows_dir = os.path.join(
            os.path.dirname(os.path.realpath(__file__)), 'app', 'workflows'
        )

        for root, dirs, files in os.walk(workflows_dir):
            files = [f for f in files if not f[0] == '.' and not f[0] == "_"]
            dirs[:] = [d for d in dirs if not d[0] == '.' and not d[0] == "_"]

            for file in files:
                # Skip Windows-only workflows for M2+ configs (LLM-augmented, run on Linux)
                if self.exclude_windows_workflows and file in WINDOWS_ONLY_WORKFLOWS:
                    print(f"Skipping Windows-only workflow: {file}")
                    continue

                try:
                    extensions.append(self._load_module('app.workflows', file))
                except Exception as e:
                    print(f'Error could not load workflow: {e}')

        return extensions

    def _load_module(self, root: str, file: str):
        """Load a single workflow module."""
        module_name = file.split('.')[0]
        full_module = f"brains.mchp.{root}.{module_name}"
        workflow_module = import_module(full_module)
        return getattr(workflow_module, 'load')()

    def _emulation_loop(self):
        """Main emulation loop - runs workflows in clusters."""
        while self._running:
            # Hot-reload feedback config at each cluster boundary
            self._reload_feedback()

            # Log activity level if using PHASE timing
            if self.use_phase_timing and self._phase_timing:
                activity_level = self._phase_timing.get_activity_level()
                current_hour = datetime.now().hour
                print(f"[{datetime.now().strftime('%H:%M')}] Activity level: {activity_level}")
                if self.logger:
                    self.logger.info(f"Activity level: {activity_level}", details={
                        "hour": current_hour,
                        "level": activity_level
                    })

            cluster_size = self._get_cluster_size()

            # Log cluster size decision
            if self.logger:
                self.logger.decision(
                    choice="cluster_size",
                    selected=str(cluster_size),
                    context=f"Tasks to run in this cluster",
                    method="phase" if self.use_phase_timing else "random"
                )

            for _ in range(cluster_size):
                # Inter-task delay
                task_delay = self._get_task_delay()
                if self.logger:
                    self.logger.timing_delay(task_delay, reason="inter_task")
                sleep(task_delay)

                # Select and run workflow
                if self._workflow_weights:
                    workflow = random.choices(self.workflows, weights=self._workflow_weights, k=1)[0]
                else:
                    index = random.randrange(len(self.workflows))
                    workflow = self.workflows[index]
                # Use description for consistency with S/B agents (they use task as workflow name)
                workflow_name = workflow.description

                # Log workflow selection decision
                if self.logger:
                    workflow_options = [w.name for w in self.workflows]
                    self.logger.decision(
                        choice="workflow_selection",
                        options=workflow_options,
                        selected=workflow.name,
                        context=workflow_name,
                        method="feedback_weighted" if self._workflow_weights else "random"
                    )

                print(workflow.display)

                if self.logger:
                    self.logger.workflow_start(workflow_name, params={
                        "agent_type": "mchp",
                        "workflow_class": workflow.__class__.__name__,
                        "phase_timing": self.use_phase_timing
                    })

                try:
                    workflow.action(self.extra, logger=self.logger)
                    self._tasks_completed += 1
                    if self._phase_timing:
                        self._phase_timing.record_activity()
                    if self.logger:
                        self.logger.workflow_end(workflow_name, success=True)
                except Exception as e:
                    if self.logger:
                        self.logger.workflow_end(workflow_name, success=False, error=str(e))
                    # Don't re-raise - continue with next workflow

            # Inter-cluster delay
            group_delay = self._get_cluster_delay()
            if self.logger:
                self.logger.timing_delay(group_delay, reason="inter_cluster")
            sleep(group_delay)

    def _signal_handler(self, sig, frame):
        """Handle shutdown signals gracefully."""
        self.stop()
        sys.exit(0)

    def run(self):
        """Start the MCHP emulation loop."""
        if self.seed != 0:
            random.seed(self.seed)
        else:
            random.seed()
        self.workflows = self._import_workflows()
        self._reload_feedback()
        self._running = True

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        print(f"\nStarting MCHP agent with {len(self.workflows)} workflows")
        print(f"PHASE timing: {self.use_phase_timing}")
        if not self.use_phase_timing:
            print(f"Timing: cluster_size={self.cluster_size}, task_interval={self.task_interval}, group_interval={self.group_interval}")
        print("-" * 60)

        try:
            self._emulation_loop()
        except KeyboardInterrupt:
            self.stop()
            sys.exit(0)

    def stop(self):
        """Stop the emulation and cleanup workflows."""
        if not self._running:
            return  # Already stopped
        self._running = False
        print("\nTerminating MCHP agent...")
        for workflow in self.workflows:
            try:
                workflow.cleanup()
            except Exception:
                pass  # Ignore cleanup errors on shutdown


def run(cluster_size=DEFAULT_CLUSTER_SIZE, task_interval=DEFAULT_TASK_INTERVAL,
        group_interval=DEFAULT_GROUP_INTERVAL, extra=None, use_phase_timing=False):
    """Convenience function to run MCHP agent."""
    agent = MCHPAgent(
        cluster_size=cluster_size,
        task_interval=task_interval,
        group_interval=group_interval,
        extra=extra,
        use_phase_timing=use_phase_timing,
    )
    agent.run()


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='MCHP Human Emulation Agent')
    parser.add_argument('--clustersize', type=int, default=DEFAULT_CLUSTER_SIZE)
    parser.add_argument('--taskinterval', type=int, default=DEFAULT_TASK_INTERVAL)
    parser.add_argument('--taskgroupinterval', type=int, default=DEFAULT_GROUP_INTERVAL)
    parser.add_argument('--extra', nargs='*', default=[])
    parser.add_argument('--phase-timing', action='store_true',
                        help='Enable PHASE timing with time-of-day awareness')
    args = parser.parse_args()

    run(
        cluster_size=args.clustersize,
        task_interval=args.taskinterval,
        group_interval=args.taskgroupinterval,
        extra=args.extra,
        use_phase_timing=args.phase_timing,
    )
