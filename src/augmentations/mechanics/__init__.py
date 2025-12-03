"""
Mechanics augmentation module.

Provides behavioral prompts for BrowserUse and SmolAgents brains.
MCHP mechanics are handled natively by its Selenium/PyAutoGUI workflows.
"""
from augmentations.mechanics.prompts import (
    DEFAULT_BROWSER_MECHANICS,
    DEFAULT_RESEARCH_MECHANICS,
    NO_MECHANICS,
    PHASE_BROWSER_MECHANICS,
    PHASE_RESEARCH_MECHANICS,
    MCHP_LIKE_BROWSER_MECHANICS,
    MCHP_LIKE_RESEARCH_MECHANICS,
    TIME_OF_DAY_MECHANICS,
)

__all__ = [
    'DEFAULT_BROWSER_MECHANICS',
    'DEFAULT_RESEARCH_MECHANICS',
    'NO_MECHANICS',
    'PHASE_BROWSER_MECHANICS',
    'PHASE_RESEARCH_MECHANICS',
    'MCHP_LIKE_BROWSER_MECHANICS',
    'MCHP_LIKE_RESEARCH_MECHANICS',
    'TIME_OF_DAY_MECHANICS',
]
