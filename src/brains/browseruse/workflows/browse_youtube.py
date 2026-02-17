"""
Browse YouTube workflow for BrowserUse.

Browses YouTube and watches videos using BrowserUse's Playwright-based agent.
"""
import asyncio
import random
from typing import Optional, TYPE_CHECKING

from browser_use import Agent
from browser_use.browser.session import BrowserSession

from brains.browseruse.workflows.base import BUWorkflow
from brains.browseruse.prompts import BUPrompts
from brains.browseruse.agent import create_logged_chat_ollama
from common.config.model_config import get_model

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger


WORKFLOW_NAME = 'BrowseYouTube'
WORKFLOW_DESCRIPTION = 'Browse YouTube and watch videos'

# YouTube browsing tasks
BROWSE_YOUTUBE_TASKS = [
    "Go to YouTube and find popular tech review videos",
    "Search YouTube for cooking tutorial videos",
    "Go to YouTube and browse trending videos",
    "Search YouTube for 'machine learning explained' and watch a video",
    "Go to YouTube and find music videos",
    "Search YouTube for 'Python programming tutorial'",
    "Go to YouTube and browse the science category",
    "Search YouTube for 'travel vlog' and watch a recent video",
    "Go to YouTube and find DIY project tutorials",
    "Search YouTube for 'history documentary' and browse results",
    "Go to YouTube and check the gaming category",
    "Search YouTube for 'fitness workout routine'",
    "Go to YouTube and find educational content about space",
    "Search YouTube for 'product review 2024'",
    "Go to YouTube and browse recommended videos",
]


def load(model: str = None, prompts: BUPrompts = None, headless: bool = True, max_steps: int = 10):
    """Load the browse YouTube workflow."""
    return BrowseYouTubeWorkflow(model=model, prompts=prompts, headless=headless, max_steps=max_steps)


class BrowseYouTubeWorkflow(BUWorkflow):
    """
    YouTube browsing workflow using BrowserUse.

    Browses YouTube and watches videos using Playwright-based automation.
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
            category="video"
        )
        self.model_name = get_model(model)
        self.prompts = prompts
        self.headless = headless
        self.max_steps = max_steps
        self._llm = None
        self._logger = None

    def _get_llm(self, logger: Optional["AgentLogger"] = None):
        """Lazy-load the LLM with logging callbacks."""
        if logger and logger != self._logger:
            self._llm = None
            self._logger = logger
        if self._llm is None:
            self._llm = create_logged_chat_ollama(self.model_name, self._logger)
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
        """Run a YouTube browsing task asynchronously."""
        if self.prompts:
            full_prompt = BUPrompts(
                task=task,
                content=self.prompts.content,
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
            print(f"YouTube browse error: {e}")
            raise

    def action(self, extra=None, logger: Optional["AgentLogger"] = None):
        """Execute a YouTube browsing task."""
        task = None
        if extra and isinstance(extra, dict):
            task = extra.get('task')
        if task is None:
            task = random.choice(BROWSE_YOUTUBE_TASKS)
            if logger:
                logger.decision(
                    choice="browse_youtube_task",
                    options=BROWSE_YOUTUBE_TASKS[:5],
                    selected=task,
                    context=f"Task from {len(BROWSE_YOUTUBE_TASKS)} available tasks",
                    method="random"
                )

        self.category = "video"
        self.description = task[:50] + "..." if len(task) > 50 else task
        print(self.display)

        # Steps are logged at the action level by the LLM response parser
        # in create_logged_chat_ollama (navigate, click, type_text, scroll, etc.)
        try:
            result = asyncio.run(self._run_task_async(task, logger))
            if result:
                print(f"YouTube browse completed: {str(result)[:200]}...")
            # Extract success from BrowserUse AgentHistoryList judge verdict
            success = bool(result and result.is_done())
            return result, success
        except Exception as e:
            raise

    def cleanup(self):
        """Clean up agent resources."""
        self._llm = None
