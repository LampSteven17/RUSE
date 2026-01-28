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
from common.logging.llm_callbacks import create_langchain_callback
from common.logging.task_categorizer import categorize_task

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

    Performs AI-powered web browsing tasks with dynamic categorization
    based on task content (browser, video, office, etc.).
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
            category="browser"  # Default, will be updated per-task
        )
        self.model_name = get_model(model)
        self.prompts = prompts
        self.headless = headless
        self.max_steps = max_steps
        self._llm = None
        self._logger = None  # Store logger for LLM callback setup

        # Combine all task lists for variety
        self.all_tasks = DEFAULT_TASKS + RESEARCH_TASKS + BROWSING_TASKS

    def _get_llm(self, logger: Optional["AgentLogger"] = None):
        """Lazy-load the LLM with logging callbacks."""
        # If logger changed, recreate LLM with new callbacks
        if logger and logger != self._logger:
            self._llm = None
            self._logger = logger

        if self._llm is None:
            from browser_use import ChatOllama
            callbacks = None
            if self._logger:
                handler = create_langchain_callback(self._logger)
                if handler is not None:
                    callbacks = [handler]

            # Create base LLM - callbacks are added via with_config for compatibility
            # with different langchain-ollama versions
            llm = ChatOllama(model=self.model_name)

            # Attach callbacks using with_config (works across langchain versions)
            if callbacks:
                try:
                    self._llm = llm.with_config({"callbacks": callbacks})
                except Exception:
                    # Fallback: some versions don't support with_config for callbacks
                    self._llm = llm
                    if self._logger:
                        self._logger.warning("Could not attach LLM callbacks via with_config")
            else:
                self._llm = llm
        return self._llm

    def _get_browser_session(self):
        """Create browser session with container-safe configuration."""
        return BrowserSession(
            headless=self.headless,
            channel="chromium",
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-extensions',
                '--disable-gpu',
            ]
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
                llm=self._get_llm(logger),
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
            # Log task selection decision
            if logger:
                logger.decision(
                    choice="browsing_task",
                    options=self.all_tasks[:5] if len(self.all_tasks) > 5 else self.all_tasks,
                    selected=task,
                    context=f"Task from {len(self.all_tasks)} available tasks",
                    method="random"
                )

        # Dynamically categorize based on task content
        self.category = categorize_task(task, default="browser")

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
