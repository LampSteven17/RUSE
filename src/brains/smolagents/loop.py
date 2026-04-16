"""
SmolAgentLoop - Continuous execution for SmolAgents.

Runs SmolAgents-native workflows (browse_web, web_search, browse_youtube)
in clusters with configurable timing.
"""
from typing import Optional, TYPE_CHECKING

from common.emulation_loop import BaseEmulationLoop

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger

# Default timing parameters (matching MCHP defaults)
DEFAULT_CLUSTER_SIZE = 5
DEFAULT_TASK_INTERVAL = 10
DEFAULT_GROUP_INTERVAL = 500


class SmolAgentLoop(BaseEmulationLoop):
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
        calibration_profile: Optional[str] = None,
        seed: int = 42,
        behavior_config_dir: Optional[str] = None,
        config_key: Optional[str] = None,
    ):
        self.model = model
        self.prompts = prompts

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
        return "smolagents_loop"

    def _load_workflows(self) -> list:
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

    def _execute_workflow(self, workflow) -> bool:
        """Execute a single SmolAgents workflow."""
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
        """Apply SmolAgents-specific behavioral config: max_steps, prompt augmentation."""
        # Behavior modifiers — max_steps per workflow (force agent re-creation on change)
        if fc.behavior_modifiers:
            max_steps_global = fc.behavior_modifiers.get("max_steps")
            per_workflow = fc.behavior_modifiers.get("per_workflow", {})
            for w in self.workflows:
                wname = getattr(w, 'name', '') or w.__class__.__name__
                new_max = per_workflow.get(wname, max_steps_global)
                if new_max is not None and hasattr(w, 'max_steps'):
                    old_max = w.max_steps
                    w.max_steps = int(new_max)
                    if old_max != w.max_steps:
                        w._agent = None  # Force re-creation with new max_steps
            if self.logger:
                self.logger.info("[behavior] Applied behavior_modifiers",
                                 details=fc.behavior_modifiers)

        # G1: Inject PHASE behavioral guidance into SmolAgents prompts
        augmentation = (fc.prompt_augmentation or {}).get("prompt_content", "")
        if augmentation:
            from brains.smolagents.prompts import SMOLPrompts
            applied = 0
            for w in self.workflows:
                if hasattr(w, 'prompts') and w.prompts:
                    existing = w.prompts.content or ""
                    w.prompts = SMOLPrompts(
                        task=w.prompts.task,
                        content=f"{existing}\n\n[PHASE Behavioral Guidance]\n{augmentation}" if existing else augmentation,
                    )
                    w._agent = None  # Force re-creation with new prompts
                    applied += 1
                elif hasattr(w, 'prompts'):
                    w.prompts = SMOLPrompts(task="Research and answer the question.", content=augmentation)
                    w._agent = None
                    applied += 1
            if self.logger:
                self.logger.info("[behavior] Applied prompt_augmentation",
                                 details={"length": len(augmentation), "workflows": applied})
