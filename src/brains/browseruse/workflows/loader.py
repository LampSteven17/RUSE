"""
Workflow loader for BrowserUse.

Loads BrowserUse-native workflows and optionally imports MCHP workflows
to provide activity diversity (Documents, File Ops, Shell, Video).
"""
import os
from importlib import import_module
from typing import List, Optional

from brains.browseruse.workflows.base import BUWorkflow
from brains.browseruse.prompts import BUPrompts


# MCHP workflow categories for diversity tracking
MCHP_WORKFLOW_CATEGORIES = {
    'browse_web': 'Web Browsing',
    'browse_youtube': 'Video',
    'google_search': 'Web Browsing',
    'download_files': 'File Ops',
    'execute_command': 'Shell',
    'spawn_shell': 'Shell',
    'ms_paint': 'Documents',
    'open_office_calc': 'Documents',
    'open_office_writer': 'Documents',
}


def load_workflows(
    model: str = None,
    prompts: BUPrompts = None,
    headless: bool = True,
    max_steps: int = 10
) -> List[BUWorkflow]:
    """
    Load BrowserUse-native workflows.

    Args:
        model: Model name for browsing workflows
        prompts: Prompts configuration for browsing
        headless: Run browser in headless mode
        max_steps: Maximum steps per browsing task

    Returns:
        List of BUWorkflow instances
    """
    workflows = []

    # Load the browsing workflow
    from brains.browseruse.workflows.browsing import load as load_browsing
    workflows.append(load_browsing(model=model, prompts=prompts, headless=headless, max_steps=max_steps))

    return workflows


def load_mchp_workflows(
    exclude: Optional[List[str]] = None,
    include_categories: Optional[List[str]] = None
) -> list:
    """
    Import MCHP workflows for activity diversity.

    This allows BrowserUse to perform document editing, file operations,
    shell commands, and video watching - matching MCHP's activity mix.

    Args:
        exclude: List of workflow names to exclude (e.g., ['browse_web'] to avoid
                 duplicate web browsing since BrowserUse already handles that)
        include_categories: Only include workflows from these categories.
                           Options: 'Web Browsing', 'Documents', 'File Ops', 'Shell', 'Video'

    Returns:
        List of MCHP workflow instances
    """
    exclude = exclude or []
    workflows = []

    # Path to MCHP workflows
    mchp_workflows_dir = os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        '..', '..', 'mchp', 'app', 'workflows'
    )

    if not os.path.exists(mchp_workflows_dir):
        print(f"Warning: MCHP workflows directory not found: {mchp_workflows_dir}")
        return workflows

    for root, dirs, files in os.walk(mchp_workflows_dir):
        # Skip hidden/private files
        files = [f for f in files if not f.startswith('.') and not f.startswith('_')]
        dirs[:] = [d for d in dirs if not d.startswith('.') and not d.startswith('_')]

        for file in files:
            if not file.endswith('.py'):
                continue

            module_name = file.split('.')[0]

            # Check exclusions
            if module_name in exclude:
                continue

            # Check category filter
            if include_categories:
                category = MCHP_WORKFLOW_CATEGORIES.get(module_name, 'Unknown')
                if category not in include_categories:
                    continue

            try:
                full_module = f"brains.mchp.app.workflows.{module_name}"
                workflow_module = import_module(full_module)
                workflow = getattr(workflow_module, 'load')()
                workflows.append(workflow)
                print(f"  Loaded MCHP workflow: {module_name}")
            except Exception as e:
                print(f"  Warning: Could not load MCHP workflow {module_name}: {e}")

    return workflows


def load_diverse_workflows(
    model: str = None,
    prompts: BUPrompts = None,
    headless: bool = True,
    max_steps: int = 10,
    include_mchp: bool = True,
    mchp_categories: Optional[List[str]] = None
) -> list:
    """
    Load a diverse mix of workflows for human-like activity patterns.

    This is the recommended loader for BrowserUse configurations that
    need to appear more human-like by having diverse activities.

    Args:
        model: Model name for BrowserUse browsing
        prompts: Prompts configuration for browsing
        headless: Run browser in headless mode
        max_steps: Maximum steps per browsing task
        include_mchp: Whether to include MCHP workflows
        mchp_categories: Which MCHP categories to include. Default includes all
                        non-web categories to avoid duplicate web activity.

    Returns:
        List of workflow instances (mixed BrowserUse + MCHP)
    """
    workflows = []

    # Load BrowserUse browsing workflow
    workflows.extend(load_workflows(
        model=model,
        prompts=prompts,
        headless=headless,
        max_steps=max_steps
    ))

    # Load MCHP workflows for diversity
    if include_mchp:
        # Default: include non-web MCHP workflows since BrowserUse handles browsing
        if mchp_categories is None:
            mchp_categories = ['Documents', 'File Ops', 'Shell', 'Video']

        mchp_workflows = load_mchp_workflows(
            exclude=['browse_web', 'google_search'],  # BrowserUse handles web
            include_categories=mchp_categories
        )
        workflows.extend(mchp_workflows)

    return workflows
