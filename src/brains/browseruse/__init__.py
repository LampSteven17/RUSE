"""
BrowserUse Brain - AI-powered browser automation.

Single-Task Configurations:
- B1.llama: DEFAULT_PROMPTS + llama3.1:8b
- B2.gemma: DEFAULT_PROMPTS + gemma3:4b
- B3.deepseek: DEFAULT_PROMPTS + deepseek-r1:8b

Loop Mode Configurations (continuous execution with native workflows):
- B4.llama: BrowserUseLoop + llama + PHASE timing
- B5.gemma: BrowserUseLoop + gemma + PHASE timing
- B6.deepseek: BrowserUseLoop + deepseek + PHASE timing

Native workflows: BrowseWeb, WebSearch, BrowseYouTube (all Playwright-based).
"""
from brains.browseruse.agent import BrowserUseAgent, run
from brains.browseruse.prompts import BUPrompts, DEFAULT_PROMPTS, PHASE_PROMPTS, MCHP_LIKE_PROMPTS
from brains.browseruse.tasks import DEFAULT_TASKS, get_random_task
from brains.browseruse.loop import BrowserUseLoop

__all__ = [
    'BrowserUseAgent',
    'BrowserUseLoop',
    'run',
    'BUPrompts',
    'DEFAULT_PROMPTS',
    'PHASE_PROMPTS',
    'MCHP_LIKE_PROMPTS',
    'DEFAULT_TASKS',
    'get_random_task',
]
