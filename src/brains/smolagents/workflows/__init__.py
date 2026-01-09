"""
SmolAgents Workflows - MCHP-style workflow support for SmolAgents.

This module provides workflow infrastructure that allows SmolAgents to:
1. Run in a continuous loop like MCHP
2. Include diverse MCHP workflows (documents, file ops, shell, video)
3. Interleave research tasks with other activities for human-like behavior
"""
from brains.smolagents.workflows.base import SmolWorkflow
from brains.smolagents.workflows.research import ResearchWorkflow
from brains.smolagents.workflows.loader import load_workflows, load_mchp_workflows

__all__ = [
    'SmolWorkflow',
    'ResearchWorkflow',
    'load_workflows',
    'load_mchp_workflows',
]
