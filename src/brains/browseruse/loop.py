"""
BrowserUseLoop - Continuous execution for BrowserUse.

Runs BrowserUse-native workflows (browse_web, web_search, browse_youtube)
in clusters with configurable timing.
"""
import signal
import random
import sys
from datetime import datetime
from time import sleep
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger
    from common.timing.phase_timing import PhaseTiming, PhaseTimingConfig

from brains.browseruse.prompts import BUPrompts

# Default timing parameters (matching MCHP defaults)
DEFAULT_CLUSTER_SIZE = 5
DEFAULT_TASK_INTERVAL = 10
DEFAULT_GROUP_INTERVAL = 500


class BrowserUseLoop:
    """
    BrowserUse agent with continuous execution.

    Runs native BrowserUse workflows in random clusters with configurable timing.
    """

    def __init__(
        self,
        model: str = None,
        prompts: BUPrompts = None,
        headless: bool = True,
        max_steps: int = 10,
        cluster_size: int = DEFAULT_CLUSTER_SIZE,
        task_interval: int = DEFAULT_TASK_INTERVAL,
        group_interval: int = DEFAULT_GROUP_INTERVAL,
        logger: Optional["AgentLogger"] = None,
        calibration_profile: Optional[str] = None,
        use_phase_timing: bool = True,
        seed: int = 42,
        feedback_dir: Optional[str] = None,
        config_key: Optional[str] = None,
    ):
        self.seed = seed
        self.model = model
        self.prompts = prompts
        self.headless = headless
        self.max_steps = max_steps
        self.cluster_size = cluster_size
        self.task_interval = task_interval
        self.group_interval = group_interval
        self.logger = logger
        self.calibration_profile = calibration_profile
        self.use_phase_timing = use_phase_timing or (calibration_profile is not None)

        self.workflows = []
        self._running = False
        self._phase_timing = None
        self._tasks_completed = 0
        self._feedback_dir = feedback_dir
        self._config_key = config_key
        self._workflow_weights = None

        if self.calibration_profile:
            self._init_calibrated_timing()
        elif self.use_phase_timing:
            self._init_phase_timing()

    def _init_calibrated_timing(self):
        from common.timing.phase_timing import CalibratedTiming, load_calibration_profile
        config = load_calibration_profile(self.calibration_profile)
        self._phase_timing = CalibratedTiming(config)

    def _init_phase_timing(self):
        from common.timing.phase_timing import PhaseTiming, PhaseTimingConfig
        config = PhaseTimingConfig(
            min_cluster_size=3, max_cluster_size=8,
            min_task_delay=5.0, max_task_delay=30.0,
            min_cluster_delay=120.0, max_cluster_delay=600.0,
            enable_hourly_adjustment=True,
        )
        self._phase_timing = PhaseTiming(config)

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
            if self._phase_timing.should_take_break(self._tasks_completed):
                self._tasks_completed = 0
                return self._phase_timing.get_break_duration()
            return self._phase_timing.get_cluster_delay()
        return random.randrange(self.group_interval)

    def _reload_feedback(self):
        """Reload feedback config from disk (hot-swap support)."""
        if not self._feedback_dir or not self._config_key:
            self._workflow_weights = None
            return

        from pathlib import Path
        from common.feedback_config import (
            load_feedback_config, build_workflow_weights, build_task_weights,
            build_calibrated_timing_config,
        )

        fc = load_feedback_config(Path(self._feedback_dir), self._config_key)

        if fc.is_empty():
            self._workflow_weights = None
            return

        # Workflow weights
        self._workflow_weights = build_workflow_weights(self.workflows, fc)
        if self._workflow_weights and self.logger:
            self.logger.info(f"[feedback] Loaded workflow_weights for {self._config_key}",
                             details={"weights": fc.workflow_weights})

        # Site config - apply task weights to BrowseWeb workflow
        if fc.site_config:
            for w in self.workflows:
                if getattr(w, 'name', '') == 'BrowseWeb':
                    from brains.browseruse.workflows.browse_web import BROWSE_WEB_TASKS
                    task_weights = build_task_weights(BROWSE_WEB_TASKS, fc.site_config)
                    w.task_weights = task_weights
                    if task_weights and self.logger:
                        self.logger.info("[feedback] Applied task_weights to BrowseWeb")
                    break

        # Behavior modifiers — max_steps per workflow
        if fc.behavior_modifiers:
            max_steps_global = fc.behavior_modifiers.get("max_steps")
            per_workflow = fc.behavior_modifiers.get("per_workflow", {})
            for w in self.workflows:
                wname = getattr(w, 'name', '') or w.__class__.__name__
                new_max = per_workflow.get(wname, max_steps_global)
                if new_max is not None and hasattr(w, 'max_steps'):
                    w.max_steps = int(new_max)
            if self.logger:
                self.logger.info("[feedback] Applied behavior_modifiers",
                                 details=fc.behavior_modifiers)

        # Timing profile — hot-swap calibrated timing
        if fc.timing_profile:
            from common.timing.phase_timing import CalibratedTiming
            old_last_activity = (self._phase_timing._last_activity_time
                                 if self._phase_timing else None)
            config = build_calibrated_timing_config(fc.timing_profile)
            self._phase_timing = CalibratedTiming(config)
            self._phase_timing._last_activity_time = old_last_activity
            self.use_phase_timing = True
            if self.logger:
                self.logger.info("[feedback] Hot-swapped timing_profile",
                                 details={"dataset": config.dataset})

    def _load_workflows(self):
        """Load all workflows for the loop."""
        from brains.browseruse.workflows.loader import load_workflows

        print("Loading workflows...")
        if self.logger:
            self.logger.info("Loading workflows")
        workflows = load_workflows(
            model=self.model,
            prompts=self.prompts,
            headless=self.headless,
            max_steps=self.max_steps,
        )
        print(f"Loaded {len(workflows)} workflows")

        # Log workflow distribution
        categories = {}
        for w in workflows:
            cat = getattr(w, 'category', 'Unknown')
            categories[cat] = categories.get(cat, 0) + 1
        print(f"Workflow distribution: {categories}")

        if self.logger:
            self.logger.info("Workflows loaded", details={
                "count": len(workflows),
                "distribution": categories
            })

        return workflows

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
                        "agent_type": "browseruse_loop",
                        "workflow_class": workflow.__class__.__name__,
                        "category": getattr(workflow, 'category', 'Unknown'),
                        "phase_timing": self.use_phase_timing
                    })

                try:
                    action_result = workflow.action(logger=self.logger)
                    if isinstance(action_result, tuple):
                        result, success = action_result
                    else:
                        result, success = action_result, True
                    self._tasks_completed += 1
                    if self._phase_timing:
                        self._phase_timing.record_activity()
                    if self.logger:
                        self.logger.workflow_end(workflow_name, success=success)
                except Exception as e:
                    print(f"Workflow error: {e}")
                    if self.logger:
                        self.logger.workflow_end(workflow_name, success=False, error=str(e))
                        self.logger.error(f"Workflow '{workflow_name}' failed", exception=e)
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
        """Start the BrowserUseLoop emulation."""
        if self.seed != 0:
            random.seed(self.seed)
        else:
            random.seed()
        self.workflows = self._load_workflows()
        self._reload_feedback()

        if not self.workflows:
            print("Error: No workflows loaded!")
            if self.logger:
                self.logger.error("No workflows loaded", fatal=True)
            return

        self._running = True

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        print(f"\nStarting BrowserUseLoop with {len(self.workflows)} workflows")
        print(f"PHASE timing: {self.use_phase_timing}")
        if not self.use_phase_timing:
            print(f"Timing: cluster_size={self.cluster_size}, task_interval={self.task_interval}, group_interval={self.group_interval}")
        print("-" * 60)

        if self.logger:
            self.logger.info("BrowserUseLoop started", details={
                "workflow_count": len(self.workflows),
                "phase_timing": self.use_phase_timing
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
        print("\nTerminating BrowserUseLoop...")
        if self.logger:
            self.logger.info("BrowserUseLoop terminating")
        for workflow in self.workflows:
            try:
                workflow.cleanup()
            except Exception:
                pass


def run(
    model: str = None,
    prompts: BUPrompts = None,
    headless: bool = True,
    max_steps: int = 10,
    cluster_size: int = DEFAULT_CLUSTER_SIZE,
    task_interval: int = DEFAULT_TASK_INTERVAL,
    group_interval: int = DEFAULT_GROUP_INTERVAL,
):
    """Convenience function to run BrowserUseLoop."""
    agent = BrowserUseLoop(
        model=model,
        prompts=prompts,
        headless=headless,
        max_steps=max_steps,
        cluster_size=cluster_size,
        task_interval=task_interval,
        group_interval=group_interval,
    )
    agent.run()


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='BrowserUse Loop Agent')
    parser.add_argument('--model', choices=['llama', 'gemma', 'deepseek'], default='llama')
    parser.add_argument('--no-headless', action='store_true', help='Run browser with GUI')
    parser.add_argument('--max-steps', type=int, default=10)
    parser.add_argument('--clustersize', type=int, default=DEFAULT_CLUSTER_SIZE)
    parser.add_argument('--taskinterval', type=int, default=DEFAULT_TASK_INTERVAL)
    parser.add_argument('--taskgroupinterval', type=int, default=DEFAULT_GROUP_INTERVAL)
    args = parser.parse_args()

    run(
        model=args.model,
        headless=not args.no_headless,
        max_steps=args.max_steps,
        cluster_size=args.clustersize,
        task_interval=args.taskinterval,
        group_interval=args.taskgroupinterval,
    )
