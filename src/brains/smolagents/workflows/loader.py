"""
Workflow loader for SmolAgents.

Loads SmolAgents-native workflows (browse_web, web_search, browse_youtube).
All workflows use SmolAgents' CodeAgent + DuckDuckGoSearchTool - no cross-brain imports.
"""
from typing import List

from brains.smolagents.workflows.base import SmolWorkflow


def load_workflows(model: str = None, prompts=None) -> List[SmolWorkflow]:
    """
    Load all SmolAgents-native workflows.

    Args:
        model: Model name for workflows
        prompts: Prompts configuration

    Returns:
        List of SmolWorkflow instances (browse_web, web_search, browse_youtube)
    """
    from brains.smolagents.workflows.browse_web import load as load_browse_web
    from brains.smolagents.workflows.web_search import load as load_web_search
    from brains.smolagents.workflows.browse_youtube import load as load_browse_youtube

    return [
        load_browse_web(model=model, prompts=prompts),
        load_web_search(model=model, prompts=prompts),
        load_browse_youtube(model=model, prompts=prompts),
    ]
