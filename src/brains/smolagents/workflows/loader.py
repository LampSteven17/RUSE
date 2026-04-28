"""
Workflow loader for SmolAgents.

Loads SmolAgents-native workflows. Always loads the three LLM-driven
content workflows (browse_web, web_search, browse_youtube). When
is_feedback=True, also loads the feedback-only workflows
(whois_lookup, download_files) which use a one-shot LLM picker for
content selection and a deterministic helper for the network call.

Feedback gating happens here, not in the workflow files themselves —
the loop's _is_feedback_deploy() determines the flag and passes it
through.
"""
from typing import List

from brains.smolagents.workflows.base import SmolWorkflow


def load_workflows(model: str = None, prompts=None,
                   is_feedback: bool = False) -> List[SmolWorkflow]:
    """
    Load SmolAgents-native workflows.

    Args:
        model: Model name for workflows
        prompts: Prompts configuration
        is_feedback: If True, also include whois_lookup + download_files
                     workflows (feedback-only)

    Returns:
        List of SmolWorkflow instances
    """
    from brains.smolagents.workflows.browse_web import load as load_browse_web
    from brains.smolagents.workflows.web_search import load as load_web_search
    from brains.smolagents.workflows.browse_youtube import load as load_browse_youtube

    workflows = [
        load_browse_web(model=model, prompts=prompts),
        load_web_search(model=model, prompts=prompts),
        load_browse_youtube(model=model, prompts=prompts),
    ]

    if is_feedback:
        from brains.smolagents.workflows.whois_lookup import load as load_whois
        from brains.smolagents.workflows.download_files import load as load_download
        workflows.extend([
            load_whois(model=model),
            load_download(model=model),
        ])

    return workflows
