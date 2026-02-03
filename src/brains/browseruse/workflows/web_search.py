"""
Web search workflow for BrowserUse.

Performs Google searches using BrowserUse's Playwright-based agent.
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


WORKFLOW_NAME = 'WebSearch'
WORKFLOW_DESCRIPTION = 'Search the web using Google'

# Web search tasks - Google searches on various topics
WEB_SEARCH_TASKS = [
    "Search Google for 'OpenAI news'",
    "Search Google for 'best Python tutorials 2024'",
    "Search for recent developments in large language models",
    "Search for 'machine learning best practices'",
    "Search Google for 'latest cybersecurity news'",
    "Search for 'React vs Vue comparison 2024'",
    "Search Google for 'cloud computing trends'",
    "Search for 'how to optimize database queries'",
    "Search Google for 'open source projects trending'",
    "Search for 'artificial intelligence applications healthcare'",
    "Search Google for 'remote work productivity tips'",
    "Search for 'web development frameworks comparison'",
    "Search Google for 'data science career guide'",
    "Search for 'containerization Docker Kubernetes tutorial'",
    "Search Google for 'programming language benchmarks 2024'",
]


def load(model: str = None, prompts: BUPrompts = None, headless: bool = True, max_steps: int = 10):
    """Load the web search workflow."""
    return WebSearchWorkflow(model=model, prompts=prompts, headless=headless, max_steps=max_steps)


class WebSearchWorkflow(BUWorkflow):
    """
    Web search workflow using BrowserUse.

    Performs Google searches using Playwright-based browser automation.
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
        """Run a search task asynchronously."""
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
            print(f"Web search error: {e}")
            raise

    def action(self, extra=None, logger: Optional["AgentLogger"] = None):
        """Execute a web search task."""
        task = None
        if extra and isinstance(extra, dict):
            task = extra.get('task')
        if task is None:
            task = random.choice(WEB_SEARCH_TASKS)
            if logger:
                logger.decision(
                    choice="web_search_task",
                    options=WEB_SEARCH_TASKS[:5],
                    selected=task,
                    context=f"Task from {len(WEB_SEARCH_TASKS)} available tasks",
                    method="random"
                )

        self.category = "browser"
        self.description = task[:50] + "..." if len(task) > 50 else task
        print(self.display)

        step_name = "web_search"
        if logger:
            logger.step_start(step_name, category="browser", message=task)

        try:
            result = asyncio.run(self._run_task_async(task, logger))
            if logger:
                logger.step_success(step_name)
            if result:
                print(f"Search completed: {str(result)[:200]}...")
            return result
        except Exception as e:
            if logger:
                logger.step_error(step_name, message=str(e))
            raise

    def cleanup(self):
        """Clean up agent resources."""
        self._llm = None
