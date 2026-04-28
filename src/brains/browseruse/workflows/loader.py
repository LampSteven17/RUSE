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
    max_steps: int = 10,
    is_feedback: bool = False,
) -> List[BUWorkflow]:
    """
    Load BrowserUse-native workflows.

    Always loads the three Playwright-driven content workflows
    (browse_web, web_search, browse_youtube). When is_feedback=True,
    also loads whois_lookup + download_files which bypass Playwright
    entirely (LLM picker via Ollama HTTP, deterministic socket/requests
    helper for the actual flow).

    Args:
        model: Model name for workflows
        prompts: Prompts configuration
        headless: Run browser in headless mode
        max_steps: Maximum steps per task
        is_feedback: If True, also include whois_lookup + download_files

    Returns:
        List of BUWorkflow instances
    """
    from brains.browseruse.workflows.browse_web import load as load_browse_web
    from brains.browseruse.workflows.web_search import load as load_web_search
    from brains.browseruse.workflows.browse_youtube import load as load_browse_youtube

    workflows = [
        load_browse_web(model=model, prompts=prompts, headless=headless, max_steps=max_steps),
        load_web_search(model=model, prompts=prompts, headless=headless, max_steps=max_steps),
        load_browse_youtube(model=model, prompts=prompts, headless=headless, max_steps=max_steps),
    ]

    if is_feedback:
        from brains.browseruse.workflows.whois_lookup import load as load_whois
        from brains.browseruse.workflows.download_files import load as load_download
        workflows.extend([
            load_whois(model=model),
            load_download(model=model),
        ])

    return workflows
