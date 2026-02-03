"""
Browse YouTube workflow for SmolAgents.

Researches YouTube video content using CodeAgent + DuckDuckGoSearchTool.
"""
import random
from typing import Optional, TYPE_CHECKING

from smolagents import CodeAgent, LiteLLMModel, DuckDuckGoSearchTool

from brains.smolagents.workflows.base import SmolWorkflow
from common.config.model_config import get_model
from common.logging.llm_callbacks import setup_litellm_callbacks

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger

# Track if LiteLLM callbacks have been registered (they're global)
_litellm_callbacks_registered = False


WORKFLOW_NAME = 'BrowseYouTube'
WORKFLOW_DESCRIPTION = 'Research YouTube video content'

# YouTube research tasks - queries about video content
BROWSE_YOUTUBE_TASKS = [
    "What are the most popular tech YouTube channels right now?",
    "Find trending machine learning video content on YouTube",
    "What cooking tutorial channels are popular on YouTube?",
    "Search for the best Python programming tutorial series on YouTube",
    "What are the top science education YouTube channels?",
    "Find popular travel vlog channels on YouTube",
    "What gaming content is trending on YouTube this month?",
    "Search for highly rated music production tutorial videos",
    "What fitness and workout channels are popular on YouTube?",
    "Find the best DIY and home improvement YouTube channels",
    "What documentary-style YouTube channels cover history?",
    "Search for popular product review channels on YouTube",
    "What space and astronomy content is trending on YouTube?",
    "Find educational math tutorial YouTube channels",
    "What photography tutorial content is popular on YouTube?",
]


def load(model: str = None, prompts=None):
    """Load the browse YouTube workflow."""
    return BrowseYouTubeWorkflow(model=model, prompts=prompts)


class BrowseYouTubeWorkflow(SmolWorkflow):
    """
    YouTube browsing workflow using SmolAgents.

    Researches YouTube video content using CodeAgent + DuckDuckGoSearchTool.
    """

    def __init__(self, model: str = None, prompts=None):
        super().__init__(
            name=WORKFLOW_NAME,
            description=WORKFLOW_DESCRIPTION,
            category="video"
        )
        self.model_name = get_model(model)
        self.prompts = prompts
        self._agent = None

    def _get_agent(self):
        """Lazy-load the SmolAgents CodeAgent."""
        if self._agent is None:
            model_id = f"ollama/{self.model_name}"
            llm = LiteLLMModel(model_id=model_id)

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
        """Execute a YouTube browsing task."""
        global _litellm_callbacks_registered

        if logger and not _litellm_callbacks_registered:
            setup_litellm_callbacks(logger)
            _litellm_callbacks_registered = True

        task = None
        if extra and isinstance(extra, dict):
            task = extra.get('task')
        if task is None:
            task = random.choice(BROWSE_YOUTUBE_TASKS)
            if logger:
                logger.decision(
                    choice="browse_youtube_task",
                    options=BROWSE_YOUTUBE_TASKS[:5],
                    selected=task,
                    context=f"Task from {len(BROWSE_YOUTUBE_TASKS)} available tasks",
                    method="random"
                )

        self.category = "video"
        self.description = task[:50] + "..." if len(task) > 50 else task
        print(self.display)

        step_name = "watch_video"
        if logger:
            logger.step_start(step_name, category="video", message=task)

        try:
            agent = self._get_agent()
            result = agent.run(task)
            if logger:
                logger.step_success(step_name)
            print(f"YouTube browse completed: {str(result)[:200]}...")
            return result
        except Exception as e:
            if logger:
                logger.step_error(step_name, message=str(e))
            print(f"YouTube browse error: {e}")
            raise

    def cleanup(self):
        """Clean up agent resources."""
        self._agent = None
