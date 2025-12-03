"""
Brains module for SUP agents.

Available brains:
- mchp: Human behavior emulation (Selenium/PyAutoGUI)
- browseruse: AI-powered browser automation (Playwright)
- smolagents: HuggingFace research agent
"""
from brains import mchp
from brains import browseruse
from brains import smolagents

__all__ = ['mchp', 'browseruse', 'smolagents']
