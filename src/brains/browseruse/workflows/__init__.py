"""
BrowserUse Workflows - MCHP-style workflow support for BrowserUse.

This module provides workflow infrastructure that allows BrowserUse to:
1. Run in a continuous loop like MCHP
2. Include diverse MCHP workflows (documents, file ops, shell, video)
3. Interleave browsing tasks with other activities for human-like behavior
"""
from brains.browseruse.workflows.base import BUWorkflow
from brains.browseruse.workflows.browsing import BrowsingWorkflow
from brains.browseruse.workflows.loader import load_workflows, load_mchp_workflows

__all__ = [
    'BUWorkflow',
    'BrowsingWorkflow',
    'load_workflows',
    'load_mchp_workflows',
]
