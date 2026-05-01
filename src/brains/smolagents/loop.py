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
        """Load all workflows for the loop.

        whois_lookup / download_files registration is gated per-flag from
        behavior.json (behavior.enable_whois, behavior.enable_download).
        PHASE's dumb_baseline writes both as false; PHASE feedback proper
        writes true. _reload_behavioral_config will raise downstream if
        the file is missing.
        """
        from pathlib import Path
        from brains.smolagents.workflows.loader import load_workflows
        from common.behavioral_config import load_workflow_gates

        gates = (load_workflow_gates(Path(self._behavior_config_dir))
                 if self._behavior_config_dir
                 else {"enable_whois": True, "enable_download": True})
        print(f"Loading workflows (gates={gates})...")
        if self.logger:
            self.logger.info("Loading workflows", details=gates)
        workflows = load_workflows(
            model=self.model,
            prompts=self.prompts,
            enable_whois=gates["enable_whois"],
            enable_download=gates["enable_download"],
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
        """Apply SmolAgents-specific behavioral config: max_steps, prompt
        augmentation, site_config, and feedback-only pool propagation.
        """
        # PHASE per-target content pools. Propagate to the dedicated
        # whois_lookup / download_files workflows when present; workflows
        # fall back to module-level FALLBACK_* lists when None.
        for w in self.workflows:
            wname = getattr(w, "name", "")
            if wname == "WhoisLookup" and hasattr(w, "domain_pool"):
                w.domain_pool = fc.whois_domain_pool
            elif wname == "DownloadFiles" and hasattr(w, "url_pool"):
                w.url_pool = fc.download_url_pool

        # W3 site_config — propagate content.site_categories to BrowseWebWorkflow.
        # Only BrowseWebWorkflow consumes site_weights; WebSearch + YouTube
        # ignore the field by design (search-result diversity comes from the
        # search engine, not category steering; YouTube is uniformly heavy).
        if fc.site_config:
            applied = 0
            for w in self.workflows:
                if getattr(w, "name", "") == "BrowseWeb" and hasattr(w, "site_weights"):
                    w.site_weights = dict(fc.site_config)
                    applied += 1
            if self.logger:
                self.logger.info("[behavior] Applied site_config",
                                 details={"weights": fc.site_config, "workflows": applied})

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
