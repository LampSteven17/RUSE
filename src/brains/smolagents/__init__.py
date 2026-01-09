"""
SmolAgents Brain - HuggingFace smolagents-based research agent.

Configurations:
- S1.llama: DEFAULT_PROMPTS + llama3.1:8b (single task mode)
- S2.gemma: DEFAULT_PROMPTS + gemma3:4b (single task mode)
- S3.deepseek: DEFAULT_PROMPTS + deepseek-r1:8b (single task mode)
- S?.model+: POST-PHASE with PHASE-improved prompts

Loop Mode (MCHP-style continuous execution):
- S1+.llama: SmolAgentLoop + llama + MCHP workflows
- S2+.gemma: SmolAgentLoop + gemma + MCHP workflows
- S3+.deepseek: SmolAgentLoop + deepseek + MCHP workflows
"""
from brains.smolagents.agent import SmolAgent, run
from brains.smolagents.prompts import SMOLPrompts, DEFAULT_PROMPTS, PHASE_PROMPTS, MCHP_LIKE_PROMPTS
from brains.smolagents.tasks import DEFAULT_TASKS, get_random_task
from brains.smolagents.loop import SmolAgentLoop

__all__ = [
    'SmolAgent',
    'SmolAgentLoop',
    'run',
    'SMOLPrompts',
    'DEFAULT_PROMPTS',
    'PHASE_PROMPTS',
    'MCHP_LIKE_PROMPTS',
    'DEFAULT_TASKS',
    'get_random_task',
]
