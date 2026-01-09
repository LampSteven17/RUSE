"""
Browsing workflow for BrowserUse.

Wraps BrowserUse browsing tasks as a workflow that can be run
in the MCHP-style loop alongside other workflows.
"""
import asyncio
import random
from typing import Optional, TYPE_CHECKING

from browser_use import Agent
from browser_use.browser.session import BrowserSession

from brains.browseruse.workflows.base import BUWorkflow
from brains.browseruse.tasks import DEFAULT_TASKS, RESEARCH_TASKS, BROWSING_TASKS
from brains.browseruse.prompts import BUPrompts
from common.config.model_config import get_model

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger


WORKFLOW_NAME = 'BrowserUseBrowsing'
WORKFLOW_DESCRIPTION = 'Browse the web using AI-powered automation'


def load(model: str = None, prompts: BUPrompts = None, headless: bool = True, max_steps: int = 10):
    """Load the browsing workflow."""
    return BrowsingWorkflow(model=model, prompts=prompts, headless=headless, max_steps=max_steps)


class BrowsingWorkflow(BUWorkflow):
    """
    Browsing workflow using BrowserUse.

    Performs AI-powered web browsing tasks, categorized as
    "Web Browsing" to match MCHP's workflow categories.
    """

    def __init__(
        self,
        model: str = None,
        prompts: BUPrompts = None,
        headless: bool = True,
        max_steps: int = 10
    ):
        super().__init__(
            name=WORKFLOW_NAME,
            description=WORKFLOW_DESCRIPTION,
            category="Web Browsing"
        )
        self.model_name = get_model(model)
        self.prompts = prompts
        self.headless = headless
        self.max_steps = max_steps
        self._llm = None

        # Combine all task lists for variety
        self.all_tasks = DEFAULT_TASKS + RESEARCH_TASKS + BROWSING_TASKS

    def _get_llm(self):
        """Lazy-load the LLM."""
        if self._llm is None:
            from browser_use import ChatOllama
            self._llm = ChatOllama(model=self.model_name)
        return self._llm

    def _get_browser_session(self):
        """Create browser session with Firefox for consistency with MCHP."""
        return BrowserSession(
            headless=self.headless,
            browser_type="firefox",
        )

    async def _run_task_async(self, task: str, logger: Optional["AgentLogger"] = None) -> Optional[str]:
        """Run a browsing task asynchronously."""
        # Build full prompt if prompts are configured
        if self.prompts:
            full_prompt = BUPrompts(
                task=task,
                content=self.prompts.content,
                mechanics=self.prompts.mechanics,
            ).build_full_prompt()
        else:
            full_prompt = task

        try:
            browser_session = self._get_browser_session()
            agent = Agent(
                task=full_prompt,
                llm=self._get_llm(),
                browser_session=browser_session,
            )
            result = await agent.run(max_steps=self.max_steps)
            return result
        except Exception as e:
            print(f"Browsing error: {e}")
            raise

    def action(self, extra=None, logger: Optional["AgentLogger"] = None):
        """
        Execute a browsing task.

        Args:
            extra: Extra parameters (can include 'task' to override random selection)
            logger: Optional AgentLogger for structured logging
        """
        # Get task from extra or pick random
        task = None
        if extra and isinstance(extra, dict):
            task = extra.get('task')
        if task is None:
            task = random.choice(self.all_tasks)

        # Update description for logging
        self.description = task[:50] + "..." if len(task) > 50 else task

        print(self.display)

        # Run the async task
        result = asyncio.run(self._run_task_async(task, logger))
        if result:
            print(f"Browsing completed: {str(result)[:200]}...")
        return result

    def cleanup(self):
        """Clean up agent resources."""
        self._llm = None
