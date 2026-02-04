"""
Browse web workflow for SmolAgents.

Researches general web content using CodeAgent + DuckDuckGoSearchTool.
"""
import random
from typing import Optional, TYPE_CHECKING

from smolagents import CodeAgent, LiteLLMModel, DuckDuckGoSearchTool

from brains.smolagents.workflows.base import SmolWorkflow
from common.config.model_config import get_model, get_ollama_seed
from common.logging.llm_callbacks import setup_litellm_callbacks

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger

# Track if LiteLLM callbacks have been registered (they're global)
_litellm_callbacks_registered = False


WORKFLOW_NAME = 'BrowseWeb'
WORKFLOW_DESCRIPTION = 'Browse the web and read content'

# Web browsing tasks - general knowledge queries that simulate reading websites
BROWSE_WEB_TASKS = [
    "What is the history of the internet?",
    "Describe the human immune system",
    "What are the major features of Wikipedia?",
    "Summarize how Reddit works as a social platform",
    "What is Hacker News and what kind of stories appear there?",
    "Describe the BBC's news coverage areas",
    "What topics does TechCrunch cover?",
    "Summarize what ESPN covers in sports news",
    "What kind of reporting does Reuters focus on?",
    "Describe the main sections of a typical news website",
    "What are the most popular content categories on Medium?",
    "Describe how Stack Overflow helps programmers",
    "What types of articles does Ars Technica publish?",
    "Summarize what The Verge covers in technology",
    "What are the main features of Wired magazine's website?",
]


def load(model: str = None, prompts=None):
    """Load the browse web workflow."""
    return BrowseWebWorkflow(model=model, prompts=prompts)


class BrowseWebWorkflow(SmolWorkflow):
    """
    Web browsing workflow using SmolAgents.

    Researches general web topics using CodeAgent + DuckDuckGoSearchTool.
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
            llm_kwargs = {"model_id": model_id}
            ollama_seed = get_ollama_seed()
            if ollama_seed is not None:
                llm_kwargs["seed"] = ollama_seed
                llm_kwargs["temperature"] = 0.0
            llm = LiteLLMModel(**llm_kwargs)

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
        """Execute a web browsing task."""
        global _litellm_callbacks_registered

        if logger and not _litellm_callbacks_registered:
            setup_litellm_callbacks(logger)
            _litellm_callbacks_registered = True

        task = None
        if extra and isinstance(extra, dict):
            task = extra.get('task')
        if task is None:
            task = random.choice(BROWSE_WEB_TASKS)
            if logger:
                logger.decision(
                    choice="browse_web_task",
                    options=BROWSE_WEB_TASKS[:5],
                    selected=task,
                    context=f"Task from {len(BROWSE_WEB_TASKS)} available tasks",
                    method="random"
                )

        self.category = "browser"
        self.description = task[:50] + "..." if len(task) > 50 else task
        print(self.display)

        # Steps are logged at the action level by the LLM response parser
        # in LiteLLMLoggingCallback (search, navigate, type_text, etc.)
        try:
            agent = self._get_agent()
            result = agent.run(task)
            print(f"Browse completed: {str(result)[:200]}...")
            return result
        except Exception as e:
            print(f"Browse web error: {e}")
            raise

    def cleanup(self):
        """Clean up agent resources."""
        self._agent = None
