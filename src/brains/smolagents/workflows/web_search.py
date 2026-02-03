"""
Web search workflow for SmolAgents.

Performs explicit web searches using CodeAgent + DuckDuckGoSearchTool.
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


WORKFLOW_NAME = 'WebSearch'
WORKFLOW_DESCRIPTION = 'Search the web for information'

# Web search tasks - explicit search-oriented queries
WEB_SEARCH_TASKS = [
    "Find the latest cybersecurity vulnerabilities reported this month",
    "Compare React vs Vue vs Angular for web development",
    "Search for the best Python libraries for data analysis",
    "Find recent developments in large language models",
    "Search for cloud computing cost optimization strategies",
    "Find the top programming languages by popularity in 2024",
    "Search for best practices in API design and REST endpoints",
    "Find comparisons of different database systems for web apps",
    "Search for recent breakthroughs in quantum computing",
    "Find the latest trends in DevOps and CI/CD pipelines",
    "Search for machine learning model deployment best practices",
    "Find recent open source projects gaining traction",
    "Search for web application security testing methodologies",
    "Find comparisons of containerization tools and platforms",
    "Search for the latest updates in the JavaScript ecosystem",
]


def load(model: str = None, prompts=None):
    """Load the web search workflow."""
    return WebSearchWorkflow(model=model, prompts=prompts)


class WebSearchWorkflow(SmolWorkflow):
    """
    Web search workflow using SmolAgents.

    Performs explicit web searches using CodeAgent + DuckDuckGoSearchTool.
    """

    def __init__(self, model: str = None, prompts=None):
        super().__init__(
            name=WORKFLOW_NAME,
            description=WORKFLOW_DESCRIPTION,
            category="browser"
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
        """Execute a web search task."""
        global _litellm_callbacks_registered

        if logger and not _litellm_callbacks_registered:
            setup_litellm_callbacks(logger)
            _litellm_callbacks_registered = True

        task = None
        if extra and isinstance(extra, dict):
            task = extra.get('task')
        if task is None:
            task = random.choice(WEB_SEARCH_TASKS)
            if logger:
                logger.decision(
                    choice="web_search_task",
                    options=WEB_SEARCH_TASKS[:5],
                    selected=task,
                    context=f"Task from {len(WEB_SEARCH_TASKS)} available tasks",
                    method="random"
                )

        self.category = "browser"
        self.description = task[:50] + "..." if len(task) > 50 else task
        print(self.display)

        step_name = "web_search"
        if logger:
            logger.step_start(step_name, category="browser", message=task)

        try:
            agent = self._get_agent()
            result = agent.run(task)
            if logger:
                logger.step_success(step_name)
            print(f"Search completed: {str(result)[:200]}...")
            return result
        except Exception as e:
            if logger:
                logger.step_error(step_name, message=str(e))
            print(f"Web search error: {e}")
            raise

    def cleanup(self):
        """Clean up agent resources."""
        self._agent = None
