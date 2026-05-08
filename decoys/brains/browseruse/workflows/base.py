"""
Base workflow class for BrowserUse workflows.

Provides a consistent interface matching MCHP's BaseWorkflow.
"""
from abc import abstractmethod
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger


class BUWorkflow:
    """
    Base class for BrowserUse workflows.

    Mirrors the MCHP BaseWorkflow interface for consistency.
    """

    __slots__ = ['name', 'description', 'category']

    # Valid categories matching StepCategory enum
    VALID_CATEGORIES = ["browser", "video", "office", "shell", "programming", "email", "authentication", "other"]

    def __init__(self, name: str, description: str, category: str = "browser"):
        """
        Initialize workflow.

        Args:
            name: Short workflow identifier
            description: Human-readable description
            category: Workflow category (browser, video, office, shell, programming, email, authentication, other)
        """
        self.name = name
        self.description = description
        self.category = category if category in self.VALID_CATEGORIES else "browser"

    @property
    def display(self) -> str:
        """Format workflow info for display."""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return f'[{timestamp}] Running Task: {self.description}'

    @abstractmethod
    def action(self, extra=None, logger: Optional["AgentLogger"] = None):
        """
        Execute the workflow action.

        Args:
            extra: Extra parameters passed from the agent
            logger: Optional AgentLogger for structured logging
        """
        pass

    def cleanup(self):
        """Clean up any resources. Override in subclasses if needed."""
        pass
