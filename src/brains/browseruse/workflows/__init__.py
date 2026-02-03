"""
BrowserUse Workflows - native workflow support for BrowserUse.

Provides three self-contained workflows implemented through BrowserUse's
Playwright-based agent:
1. BrowseWeb - Visit websites and read content
2. WebSearch - Perform Google searches
3. BrowseYouTube - Browse YouTube and watch videos
"""
from brains.browseruse.workflows.base import BUWorkflow
from brains.browseruse.workflows.browse_web import BrowseWebWorkflow
from brains.browseruse.workflows.web_search import WebSearchWorkflow
from brains.browseruse.workflows.browse_youtube import BrowseYouTubeWorkflow
from brains.browseruse.workflows.loader import load_workflows

__all__ = [
    'BUWorkflow',
    'BrowseWebWorkflow',
    'WebSearchWorkflow',
    'BrowseYouTubeWorkflow',
    'load_workflows',
]
