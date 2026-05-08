"""
SmolAgents Workflows - native workflow support for SmolAgents.

Provides three self-contained workflows implemented through SmolAgents'
CodeAgent + DuckDuckGoSearchTool:
1. BrowseWeb - Research general web content
2. WebSearch - Perform explicit web searches
3. BrowseYouTube - Research YouTube video content
"""
from brains.smolagents.workflows.base import SmolWorkflow
from brains.smolagents.workflows.browse_web import BrowseWebWorkflow
from brains.smolagents.workflows.web_search import WebSearchWorkflow
from brains.smolagents.workflows.browse_youtube import BrowseYouTubeWorkflow
from brains.smolagents.workflows.loader import load_workflows

__all__ = [
    'SmolWorkflow',
    'BrowseWebWorkflow',
    'WebSearchWorkflow',
    'BrowseYouTubeWorkflow',
    'load_workflows',
]
