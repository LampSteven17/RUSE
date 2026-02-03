"""
Workflow loader for BrowserUse.

Loads BrowserUse-native workflows (browse_web, web_search, browse_youtube).
All workflows use BrowserUse's Playwright-based agent - no cross-brain imports.
"""
from typing import List

from brains.browseruse.workflows.base import BUWorkflow
from brains.browseruse.prompts import BUPrompts


def load_workflows(
    model: str = None,
    prompts: BUPrompts = None,
    headless: bool = True,
    max_steps: int = 10
) -> List[BUWorkflow]:
    """
    Load all BrowserUse-native workflows.

    Args:
        model: Model name for workflows
        prompts: Prompts configuration
        headless: Run browser in headless mode
        max_steps: Maximum steps per task

    Returns:
        List of BUWorkflow instances (browse_web, web_search, browse_youtube)
    """
    from brains.browseruse.workflows.browse_web import load as load_browse_web
    from brains.browseruse.workflows.web_search import load as load_web_search
    from brains.browseruse.workflows.browse_youtube import load as load_browse_youtube

    return [
        load_browse_web(model=model, prompts=prompts, headless=headless, max_steps=max_steps),
        load_web_search(model=model, prompts=prompts, headless=headless, max_steps=max_steps),
        load_browse_youtube(model=model, prompts=prompts, headless=headless, max_steps=max_steps),
    ]
