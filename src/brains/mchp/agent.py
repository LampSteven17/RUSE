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
import os
from importlib import import_module
from typing import Optional, TYPE_CHECKING

from common.emulation_loop import BaseEmulationLoop

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger
    from common.timing.phase_timing import CalibratedTiming

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


class MCHPAgent(BaseEmulationLoop):
    """
    MCHP (Human Emulation) Agent.

    Runs workflows in random clusters with configurable timing.

    Timing Modes:
    - No timing: Original random timing (M1 baseline)
    - calibration_profile: Calibrated timing from empirical profile (M2-M4)
    """

    def __init__(
        self,
        cluster_size: int = DEFAULT_CLUSTER_SIZE,
        task_interval: int = DEFAULT_TASK_INTERVAL,
        group_interval: int = DEFAULT_GROUP_INTERVAL,
        extra: list = None,
        logger: Optional["AgentLogger"] = None,
        calibration_profile: Optional[str] = None,
        exclude_windows_workflows: bool = False,
        seed: int = 42,
        behavior_config_dir: Optional[str] = None,
        config_key: Optional[str] = None,
    ):
        self.extra = extra or []
        self.exclude_windows_workflows = exclude_windows_workflows

        super().__init__(
            cluster_size=cluster_size,
            task_interval=task_interval,
            group_interval=group_interval,
            logger=logger,
            calibration_profile=calibration_profile,
            seed=seed,
            behavior_config_dir=behavior_config_dir,
            config_key=config_key,
        )

    # ── Brain-specific implementations ───────────────────────────────

    def _agent_type_label(self) -> str:
        return "mchp"

    def _load_workflows(self) -> list:
        """Dynamically load all workflow modules."""
        extensions = []
        workflows_dir = os.path.join(
            os.path.dirname(os.path.realpath(__file__)), 'app', 'workflows'
        )

        for root, dirs, files in os.walk(workflows_dir):
            files = [f for f in files if not f[0] == '.' and not f[0] == "_"]
            dirs[:] = [d for d in dirs if not d[0] == '.' and not d[0] == "_"]

            for file in files:
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

    def _execute_workflow(self, workflow) -> bool:
        """Execute a single MCHP workflow."""
        try:
            workflow.action(self.extra, logger=self.logger)
            if self.logger:
                self.logger.workflow_end(workflow.description, success=True)
            return True
        except Exception as e:
            if self.logger:
                self.logger.workflow_end(workflow.description, success=False, error=str(e))
            return False

    def _apply_brain_specific_config(self, fc) -> None:
        """Apply MCHP-specific behavioral config: page_dwell, nav_clicks, site_weights."""
        from common.behavioral_config import build_site_weights

        # Behavior modifiers — apply to BrowseWeb workflow
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
                    # G2: Connection reuse probability for tab management
                    conn_reuse = fc.behavior_modifiers.get("connection_reuse", {})
                    if "keep_alive_probability" in conn_reuse:
                        w.keep_alive_probability = float(conn_reuse["keep_alive_probability"])
                    else:
                        print("[WARNING] G2 keep_alive_probability DISABLED — "
                              "no behavior_modifiers.connection_reuse.keep_alive_probability, "
                              f"using default {w.keep_alive_probability}")
                    if self.logger:
                        self.logger.info("[behavior] Applied behavior_modifiers to BrowseWeb",
                                         details=fc.behavior_modifiers)
                    break

        # Site config — apply site weights to BrowseWeb workflow
        if fc.site_config:
            for w in self.workflows:
                if getattr(w, 'name', '') == 'BrowseWeb':
                    site_weights = build_site_weights(w.website_list, fc.site_config)
                    w.site_weights = site_weights
                    if site_weights and self.logger:
                        self.logger.info("[behavior] Applied site_weights to BrowseWeb")
                    break


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
