"""
SmolAgents Brain - HuggingFace smolagents-based research agent.

Configurations:
- S1.llama: DEFAULT_PROMPTS + llama3.1:8b
- S2.gemma: DEFAULT_PROMPTS + gemma3:4b
- S3.deepseek: DEFAULT_PROMPTS + deepseek-r1:8b
"""
from brains.smolagents.agent import SmolAgent, run
from brains.smolagents.prompts import SMOLPrompts, DEFAULT_PROMPTS, PHASE_PROMPTS, MCHP_LIKE_PROMPTS
from brains.smolagents.tasks import DEFAULT_TASKS, get_random_task

__all__ = [
    'SmolAgent',
    'run',
    'SMOLPrompts',
    'DEFAULT_PROMPTS',
    'PHASE_PROMPTS',
    'MCHP_LIKE_PROMPTS',
    'DEFAULT_TASKS',
    'get_random_task',
]
