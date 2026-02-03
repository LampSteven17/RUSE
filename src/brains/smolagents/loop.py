"""
SmolAgentLoop - Continuous execution for SmolAgents.

Runs SmolAgents-native workflows (browse_web, web_search, browse_youtube)
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

# Default timing parameters (matching MCHP defaults)
DEFAULT_CLUSTER_SIZE = 5
DEFAULT_TASK_INTERVAL = 10
DEFAULT_GROUP_INTERVAL = 500


class SmolAgentLoop:
    """
    SmolAgents agent with continuous execution.

    Runs native SmolAgents workflows in random clusters with configurable timing.
    """

    def __init__(
        self,
        model: str = None,
        prompts=None,
        cluster_size: int = DEFAULT_CLUSTER_SIZE,
        task_interval: int = DEFAULT_TASK_INTERVAL,
        group_interval: int = DEFAULT_GROUP_INTERVAL,
        logger: Optional["AgentLogger"] = None,
        use_phase_timing: bool = True,
    ):
        """
        Initialize the SmolAgentLoop.

        Args:
            model: Model name for SmolAgents (llama, gemma, deepseek)
            prompts: Prompts configuration for research
            cluster_size: Max workflows per cluster (used if phase_timing disabled)
            task_interval: Max seconds between tasks (used if phase_timing disabled)
            group_interval: Max seconds between clusters (used if phase_timing disabled)
            logger: AgentLogger for structured logging
            use_phase_timing: Enable PHASE timing with time-of-day awareness
        """
        self.model = model
        self.prompts = prompts
        self.cluster_size = cluster_size
        self.task_interval = task_interval
        self.group_interval = group_interval
        self.logger = logger
        self.use_phase_timing = use_phase_timing

        self.workflows = []
        self._running = False
        self._phase_timing = None
        self._tasks_completed = 0

        # Initialize PHASE timing if enabled
        if self.use_phase_timing:
            self._init_phase_timing()

    def _init_phase_timing(self):
        """Initialize PHASE timing module."""
        from common.timing.phase_timing import PhaseTiming, PhaseTimingConfig

        config = PhaseTimingConfig(
            min_cluster_size=3,
            max_cluster_size=8,
            min_task_delay=5.0,
            max_task_delay=30.0,
            min_cluster_delay=120.0,
            max_cluster_delay=600.0,
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

    def _load_workflows(self):
        """Load all workflows for the loop."""
        from brains.smolagents.workflows.loader import load_workflows

        print("Loading workflows...")
        if self.logger:
            self.logger.info("Loading workflows")
        workflows = load_workflows(
            model=self.model,
            prompts=self.prompts,
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
                        method="random"
                    )

                print(workflow.display)

                if self.logger:
                    self.logger.workflow_start(workflow_name, params={
                        "agent_type": "smolagents_loop",
                        "workflow_class": workflow.__class__.__name__,
                        "category": getattr(workflow, 'category', 'Unknown'),
                        "phase_timing": self.use_phase_timing
                    })

                try:
                    workflow.action(logger=self.logger)
                    self._tasks_completed += 1
                    if self._phase_timing:
                        self._phase_timing.record_activity()
                    if self.logger:
                        self.logger.workflow_end(workflow_name, success=True)
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
        """Start the SmolAgentLoop emulation."""
        random.seed()
        self.workflows = self._load_workflows()

        if not self.workflows:
            print("Error: No workflows loaded!")
            if self.logger:
                self.logger.error("No workflows loaded", fatal=True)
            return

        self._running = True

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        print(f"\nStarting SmolAgentLoop with {len(self.workflows)} workflows")
        print(f"PHASE timing: {self.use_phase_timing}")
        if not self.use_phase_timing:
            print(f"Timing: cluster_size={self.cluster_size}, task_interval={self.task_interval}, group_interval={self.group_interval}")
        print("-" * 60)

        if self.logger:
            self.logger.info("SmolAgentLoop started", details={
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
        print("\nTerminating SmolAgentLoop...")
        if self.logger:
            self.logger.info("SmolAgentLoop terminating")
        for workflow in self.workflows:
            try:
                workflow.cleanup()
            except Exception:
                pass


def run(
    model: str = None,
    prompts=None,
    cluster_size: int = DEFAULT_CLUSTER_SIZE,
    task_interval: int = DEFAULT_TASK_INTERVAL,
    group_interval: int = DEFAULT_GROUP_INTERVAL,
):
    """Convenience function to run SmolAgentLoop."""
    agent = SmolAgentLoop(
        model=model,
        prompts=prompts,
        cluster_size=cluster_size,
        task_interval=task_interval,
        group_interval=group_interval,
    )
    agent.run()


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='SmolAgents Loop Agent')
    parser.add_argument('--model', choices=['llama', 'gemma', 'deepseek'], default='llama')
    parser.add_argument('--clustersize', type=int, default=DEFAULT_CLUSTER_SIZE)
    parser.add_argument('--taskinterval', type=int, default=DEFAULT_TASK_INTERVAL)
    parser.add_argument('--taskgroupinterval', type=int, default=DEFAULT_GROUP_INTERVAL)
    args = parser.parse_args()

    run(
        model=args.model,
        cluster_size=args.clustersize,
        task_interval=args.taskinterval,
        group_interval=args.taskgroupinterval,
    )
