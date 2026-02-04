"""
Browse web workflow for BrowserUse.

Visits websites and reads content using BrowserUse's Playwright-based agent.
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


WORKFLOW_NAME = 'BrowseWeb'
WORKFLOW_DESCRIPTION = 'Browse websites and read content'

# Web browsing tasks - visit and read various sites
BROWSE_WEB_TASKS = [
    "Go to wikipedia.org and read about artificial intelligence",
    "Visit reddit.com and browse the front page",
    "Go to news.ycombinator.com and read the top stories",
    "Visit bbc.com and read the latest news headlines",
    "Go to cnn.com and browse the technology section",
    "Visit medium.com and browse popular articles",
    "Go to techcrunch.com and read the latest tech news",
    "Visit espn.com and check the latest sports scores",
    "Go to reuters.com and read the top world news",
    "Visit arstechnica.com and browse recent articles",
    "Go to theverge.com and read technology coverage",
    "Visit slashdot.org and browse the latest stories",
    "Go to wired.com and read about emerging technology",
    "Visit nature.com and browse recent science articles",
    "Go to stackoverflow.com and browse popular questions",
]


def load(model: str = None, prompts: BUPrompts = None, headless: bool = True, max_steps: int = 10):
    """Load the browse web workflow."""
    return BrowseWebWorkflow(model=model, prompts=prompts, headless=headless, max_steps=max_steps)


class BrowseWebWorkflow(BUWorkflow):
    """
    Web browsing workflow using BrowserUse.

    Visits various websites and reads content using Playwright-based automation.
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
            category="browser"
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
        """Run a browsing task asynchronously."""
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
            print(f"Browse web error: {e}")
            raise

    def action(self, extra=None, logger: Optional["AgentLogger"] = None):
        """Execute a web browsing task."""
        task = None
        if extra and isinstance(extra, dict):
            task = extra.get('task')
        if task is None:
            task = random.choice(BROWSE_WEB_TASKS)
            if logger:
                logger.decision(
                    choice="browse_web_task",
                    options=BROWSE_WEB_TASKS[:5],
                    selected=task,
                    context=f"Task from {len(BROWSE_WEB_TASKS)} available tasks",
                    method="random"
                )

        self.category = "browser"
        self.description = task[:50] + "..." if len(task) > 50 else task
        print(self.display)

        # Steps are logged at the action level by the LLM response parser
        # in create_logged_chat_ollama (navigate, click, type_text, scroll, etc.)
        try:
            result = asyncio.run(self._run_task_async(task, logger))
            if result:
                print(f"Browse completed: {str(result)[:200]}...")
            return result
        except Exception as e:
            raise

    def cleanup(self):
        """Clean up agent resources."""
        self._llm = None
