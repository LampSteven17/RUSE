"""
MCHP Brain - Human behavior emulation agent.

This is a thin wrapper around the original human.py logic,
providing a class-based interface for the unified SUP runner.
"""
import signal
import os
import random
import sys
from importlib import import_module
from time import sleep

# Default timing parameters
DEFAULT_CLUSTER_SIZE = 5
DEFAULT_TASK_INTERVAL = 10
DEFAULT_GROUP_INTERVAL = 500


class MCHPAgent:
    """
    MCHP (Human Emulation) Agent.

    Runs workflows in random clusters with configurable timing.
    This is the M1 baseline configuration - no LLM augmentation.
    """

    def __init__(
        self,
        cluster_size: int = DEFAULT_CLUSTER_SIZE,
        task_interval: int = DEFAULT_TASK_INTERVAL,
        group_interval: int = DEFAULT_GROUP_INTERVAL,
        extra: list = None,
    ):
        self.cluster_size = cluster_size
        self.task_interval = task_interval
        self.group_interval = group_interval
        self.extra = extra or []
        self.workflows = []
        self._running = False

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
            for _ in range(self.cluster_size):
                sleep(random.randrange(self.task_interval))
                index = random.randrange(len(self.workflows))
                print(self.workflows[index].display)
                self.workflows[index].action(self.extra)
            sleep(random.randrange(self.group_interval))

    def _signal_handler(self, sig, frame):
        """Handle shutdown signals gracefully."""
        self.stop()
        sys.exit(0)

    def run(self):
        """Start the MCHP emulation loop."""
        random.seed()
        self.workflows = self._import_workflows()
        self._running = True

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

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
        group_interval=DEFAULT_GROUP_INTERVAL, extra=None):
    """Convenience function to run MCHP agent."""
    agent = MCHPAgent(
        cluster_size=cluster_size,
        task_interval=task_interval,
        group_interval=group_interval,
        extra=extra,
    )
    agent.run()


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='MCHP Human Emulation Agent')
    parser.add_argument('--clustersize', type=int, default=DEFAULT_CLUSTER_SIZE)
    parser.add_argument('--taskinterval', type=int, default=DEFAULT_TASK_INTERVAL)
    parser.add_argument('--taskgroupinterval', type=int, default=DEFAULT_GROUP_INTERVAL)
    parser.add_argument('--extra', nargs='*', default=[])
    args = parser.parse_args()

    run(
        cluster_size=args.clustersize,
        task_interval=args.taskinterval,
        group_interval=args.taskgroupinterval,
        extra=args.extra,
    )
