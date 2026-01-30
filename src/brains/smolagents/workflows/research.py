"""
Research workflow for SmolAgents.

Wraps SmolAgents research tasks as a workflow that can be run
in the MCHP-style loop alongside other workflows.
"""
import random
from typing import Optional, TYPE_CHECKING

from smolagents import CodeAgent, LiteLLMModel, DuckDuckGoSearchTool

from brains.smolagents.workflows.base import SmolWorkflow
from brains.smolagents.tasks import DEFAULT_TASKS, TECHNICAL_TASKS, GENERAL_TASKS
from common.config.model_config import get_model
from common.logging.llm_callbacks import setup_litellm_callbacks
from common.logging.task_categorizer import categorize_task

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger

# Track if LiteLLM callbacks have been registered (they're global)
_litellm_callbacks_registered = False


WORKFLOW_NAME = 'SmolResearch'
WORKFLOW_DESCRIPTION = 'Research a topic using web search'


def load(model: str = None, prompts=None):
    """Load the research workflow."""
    return ResearchWorkflow(model=model, prompts=prompts)


class ResearchWorkflow(SmolWorkflow):
    """
    Research workflow using SmolAgents.

    Performs web searches and research tasks with dynamic categorization
    based on task content (browser, video, office, etc.).
    """

    def __init__(self, model: str = None, prompts=None):
        super().__init__(
            name=WORKFLOW_NAME,
            description=WORKFLOW_DESCRIPTION,
            category="browser"  # Default, will be updated per-task
        )
        self.model_name = get_model(model)
        self.prompts = prompts
        self._agent = None

        # Combine all task lists for variety
        self.all_tasks = DEFAULT_TASKS + TECHNICAL_TASKS + GENERAL_TASKS

    def _get_agent(self):
        """Lazy-load the SmolAgents CodeAgent."""
        if self._agent is None:
            model_id = f"ollama/{self.model_name}"
            llm = LiteLLMModel(model_id=model_id)

            # Build instructions from prompts if provided
            instructions = None
            if self.prompts is not None:
                instructions = self.prompts.build_system_prompt()

            self._agent = CodeAgent(
                tools=[DuckDuckGoSearchTool()],
                model=llm,
                instructions=instructions,
            )
        return self._agent

    def action(self, extra=None, logger: Optional["AgentLogger"] = None):
        """
        Execute a research task.

        Args:
            extra: Extra parameters (can include 'task' to override random selection)
            logger: Optional AgentLogger for structured logging
        """
        global _litellm_callbacks_registered

        # Set up LiteLLM callbacks if logger provided and not already registered
        if logger and not _litellm_callbacks_registered:
            setup_litellm_callbacks(logger)
            _litellm_callbacks_registered = True

        # Get task from extra or pick random
        task = None
        if extra and isinstance(extra, dict):
            task = extra.get('task')
        if task is None:
            task = random.choice(self.all_tasks)
            # Log task selection decision
            if logger:
                logger.decision(
                    choice="research_task",
                    options=self.all_tasks[:5] if len(self.all_tasks) > 5 else self.all_tasks,
                    selected=task,
                    context=f"Task from {len(self.all_tasks)} available tasks",
                    method="random"
                )

        # Dynamically categorize based on task content
        self.category = categorize_task(task, default="browser")

        # Update description for logging
        self.description = task[:50] + "..." if len(task) > 50 else task

        print(self.display)

        try:
            agent = self._get_agent()
            result = agent.run(task)
            print(f"Research completed: {str(result)[:200]}...")
            return result
        except Exception as e:
            print(f"Research error: {e}")
            raise

    def cleanup(self):
        """Clean up agent resources."""
        self._agent = None
