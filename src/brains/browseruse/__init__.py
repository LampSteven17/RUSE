"""
BrowserUse Brain - AI-powered browser automation.

Configurations:
- B1.llama: DEFAULT_PROMPTS + llama3.1:8b
- B2.gemma: DEFAULT_PROMPTS + gemma3:4b
- B3.deepseek: DEFAULT_PROMPTS + deepseek-r1:8b
"""
from brains.browseruse.agent import BrowserUseAgent, run
from brains.browseruse.prompts import BUPrompts, DEFAULT_PROMPTS, PHASE_PROMPTS, MCHP_LIKE_PROMPTS
from brains.browseruse.tasks import DEFAULT_TASKS, get_random_task

__all__ = [
    'BrowserUseAgent',
    'run',
    'BUPrompts',
    'DEFAULT_PROMPTS',
    'PHASE_PROMPTS',
    'MCHP_LIKE_PROMPTS',
    'DEFAULT_TASKS',
    'get_random_task',
]
