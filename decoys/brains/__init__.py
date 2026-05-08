"""
Brains module for SUP agents.

Available brains:
- mchp: Human behavior emulation (Selenium/PyAutoGUI)
- browseruse: AI-powered browser automation (Playwright)
- smolagents: HuggingFace research agent

Import specific brains directly to avoid loading unused dependencies:
    from brains.mchp import MCHPAgent
    from brains.browseruse import BrowserUseAgent
    from brains.smolagents import SmolAgent
"""

__all__ = ['mchp', 'browseruse', 'smolagents']
