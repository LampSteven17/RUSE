"""
BrowserUseLoop - Continuous execution for BrowserUse.

Runs BrowserUse-native workflows (browse_web, web_search, browse_youtube)
in clusters with configurable timing.
"""
from typing import Optional, TYPE_CHECKING

from common.emulation_loop import BaseEmulationLoop

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger

from brains.browseruse.prompts import BUPrompts

# Default timing parameters (matching MCHP defaults)
DEFAULT_CLUSTER_SIZE = 5
DEFAULT_TASK_INTERVAL = 10
DEFAULT_GROUP_INTERVAL = 500


class BrowserUseLoop(BaseEmulationLoop):
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
        seed: int = 42,
        behavior_config_dir: Optional[str] = None,
        config_key: Optional[str] = None,
    ):
        self.model = model
        self.prompts = prompts
        self.headless = headless
        self.max_steps = max_steps

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
        return "browseruse_loop"

    def _load_workflows(self) -> list:
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

    def _execute_workflow(self, workflow) -> bool:
        """Execute a single BrowserUse workflow."""
        try:
            action_result = workflow.action(logger=self.logger)
            if isinstance(action_result, tuple):
                result, success = action_result
            else:
                result, success = action_result, True
            if self.logger:
                self.logger.workflow_end(workflow.description, success=success)
            return success
        except Exception as e:
            print(f"Workflow error: {e}")
            if self.logger:
                self.logger.workflow_end(workflow.description, success=False, error=str(e))
                self.logger.error(f"Workflow '{workflow.description}' failed", exception=e)
            return False

    def _apply_brain_specific_config(self, fc) -> None:
        """Apply BrowserUse-specific behavioral config: task_weights, max_steps."""
        from common.behavioral_config import build_task_weights

        # Site config — apply task weights to BrowseWeb workflow
        if fc.site_config:
            for w in self.workflows:
                if getattr(w, 'name', '') == 'BrowseWeb':
                    from brains.browseruse.workflows.browse_web import BROWSE_WEB_TASKS
                    task_weights = build_task_weights(BROWSE_WEB_TASKS, fc.site_config)
                    w.task_weights = task_weights
                    if task_weights and self.logger:
                        self.logger.info("[behavior] Applied task_weights to BrowseWeb")
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
                self.logger.info("[behavior] Applied behavior_modifiers",
                                 details=fc.behavior_modifiers)

        # G1: Inject PHASE behavioral guidance into BrowserUse prompts
        augmentation = (fc.prompt_augmentation or {}).get("prompt_content", "")
        if augmentation:
            from brains.browseruse.prompts import BUPrompts
            applied = 0
            for w in self.workflows:
                if hasattr(w, 'prompts') and w.prompts:
                    existing = w.prompts.content or ""
                    w.prompts = BUPrompts(
                        task=w.prompts.task,
                        content=f"{existing}\n\n[PHASE Behavioral Guidance]\n{augmentation}" if existing else augmentation,
                    )
                    applied += 1
                elif hasattr(w, 'prompts'):
                    w.prompts = BUPrompts(task="Complete the browsing task.", content=augmentation)
                    applied += 1
            if self.logger:
                self.logger.info("[behavior] Applied prompt_augmentation",
                                 details={"length": len(augmentation), "workflows": applied})
