"""
BrowserUse Brain - AI-powered browser automation agent.

Supports three-prompt configuration for content and mechanics control.
"""
import os
import asyncio
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger

from common.logging.llm_callbacks import create_langchain_callback


def log(msg: str):
    """Print with timestamp."""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] {msg}")

from browser_use import Agent
from browser_use.browser.session import BrowserSession

from common.config.model_config import get_model
from brains.browseruse.prompts import BUPrompts, DEFAULT_PROMPTS


class LLMWrapper:
    """
    Wrapper for LangChain ChatOllama that allows arbitrary attribute assignment.

    browser_use's token_cost_service tries to monkey-patch 'ainvoke' and other
    attributes on the LLM. Pydantic models block this with extra='ignore'.
    This wrapper allows those dynamic attributes while delegating all other
    operations to the wrapped LLM instance.
    """

    def __init__(self, llm):
        # Use object.__setattr__ to avoid triggering our own __setattr__
        object.__setattr__(self, '_llm', llm)
        object.__setattr__(self, '_extra_attrs', {})
        # Set provider that browser_use expects
        self._extra_attrs['provider'] = 'ollama'
        # browser_use accesses model_name but ChatOllama uses 'model'
        self._extra_attrs['model_name'] = llm.model

    def __getattr__(self, name):
        # First check our extra attributes
        extra = object.__getattribute__(self, '_extra_attrs')
        if name in extra:
            return extra[name]
        # Then delegate to the wrapped LLM
        llm = object.__getattribute__(self, '_llm')
        return getattr(llm, name)

    def __setattr__(self, name, value):
        # Store in extra_attrs to allow browser_use's monkey-patching
        if name in ('_llm', '_extra_attrs'):
            object.__setattr__(self, name, value)
        else:
            self._extra_attrs[name] = value

    def __call__(self, *args, **kwargs):
        return self._llm(*args, **kwargs)


class BrowserUseAgent:
    """
    BrowserUse agent with three-prompt support.

    Configurations:
    - B1.llama: DEFAULT_PROMPTS + llama3.1:8b
    - B2.gemma: DEFAULT_PROMPTS + gemma3:4b
    - B3.deepseek: DEFAULT_PROMPTS + deepseek-r1:8b
    - B?.model+: PHASE_PROMPTS + any model (POST-PHASE)
    """

    def __init__(
        self,
        prompts: BUPrompts = DEFAULT_PROMPTS,
        model: str = None,
        headless: bool = True,
        max_steps: int = 10,
        logger: Optional["AgentLogger"] = None,
    ):
        self.prompts = prompts
        self.model_name = get_model(model)
        self.headless = headless
        self.max_steps = max_steps
        self.logger = logger
        self._llm = None
        self._browser_session = None

    def _get_llm(self):
        """Lazy-load the LLM with logging callbacks."""
        if self._llm is None:
            # Import from langchain_ollama directly (NOT browser_use) to support callbacks
            from langchain_ollama import ChatOllama
            # Set up LangChain logging callbacks if logger is provided
            callbacks = None
            if self.logger:
                handler = create_langchain_callback(self.logger)
                if handler is not None:
                    callbacks = [handler]

            # Create LLM with callbacks in constructor (langchain_ollama supports this)
            llm = ChatOllama(model=self.model_name, callbacks=callbacks)

            # Wrap in LLMWrapper to allow browser_use's monkey-patching
            # browser_use tries to setattr 'ainvoke' and 'provider' on the LLM,
            # which Pydantic blocks. LLMWrapper allows these dynamic attributes.
            self._llm = LLMWrapper(llm)
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

    async def run_async(self, task: str) -> Optional[str]:
        """
        Run a task asynchronously with configured prompts.

        Args:
            task: The specific task to perform (e.g., "Search for Python tutorials")

        Returns:
            Result from the agent, or None on error
        """
        # Build full prompt from task + content + mechanics guidelines
        full_prompt = BUPrompts(
            task=task,
            content=self.prompts.content,
            mechanics=self.prompts.mechanics,
        ).build_full_prompt()

        log(f"Starting BrowserUse agent with model: {self.model_name}")
        log(f"Task: {task}")

        # Use the task itself as workflow name for better queryability
        workflow_name = task[:100] if len(task) > 100 else task

        if self.logger:
            self.logger.workflow_start(workflow_name, params={
                "agent_type": "browseruse",
                "model": self.model_name
            })

        try:
            browser_session = self._get_browser_session()
            agent = Agent(
                task=full_prompt,
                llm=self._get_llm(),
                browser_session=browser_session,
            )
            result = await agent.run(max_steps=self.max_steps)
            log("Task completed successfully!")

            if self.logger:
                self.logger.workflow_end(workflow_name, success=True,
                                        result=str(result)[:500] if result else None)
            return result
        except Exception as e:
            log(f"Error running agent: {e}")
            import traceback
            traceback.print_exc()

            if self.logger:
                self.logger.workflow_end(workflow_name, success=False, error=str(e))
            return None

    def run(self, task: str) -> Optional[str]:
        """
        Run a task synchronously.

        Args:
            task: The specific task to perform

        Returns:
            Result from the agent, or None on error
        """
        return asyncio.run(self.run_async(task))


def run(task: str, model: str = None, prompts: BUPrompts = DEFAULT_PROMPTS,
        headless: bool = True, max_steps: int = 10) -> Optional[str]:
    """Convenience function to run BrowserUse agent."""
    agent = BrowserUseAgent(
        prompts=prompts,
        model=model,
        headless=headless,
        max_steps=max_steps,
    )
    return agent.run(task)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='BrowserUse Agent')
    parser.add_argument('task', nargs='?', default="Visit google.com and search for 'OpenAI news'")
    parser.add_argument('--model', type=str, default=None, help='Model key: llama, gemma, deepseek')
    parser.add_argument('--phase', action='store_true', help='Use PHASE-improved prompts')
    parser.add_argument('--max-steps', type=int, default=10)
    parser.add_argument('--headless', action='store_true', default=True)
    args = parser.parse_args()

    from brains.browseruse.prompts import PHASE_PROMPTS

    prompts = PHASE_PROMPTS if args.phase else DEFAULT_PROMPTS

    run(
        task=args.task,
        model=args.model,
        prompts=prompts,
        headless=args.headless,
        max_steps=args.max_steps,
    )
