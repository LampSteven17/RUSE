"""
Browse YouTube workflow for SmolAgents.

Researches YouTube video content using CodeAgent + DuckDuckGoSearchTool.
"""
import random
from typing import Optional, TYPE_CHECKING

from smolagents import CodeAgent, LiteLLMModel, DuckDuckGoSearchTool, VisitWebpageTool

from brains.smolagents.tools import WhoisLookupTool
from brains.smolagents.workflows.base import SmolWorkflow
from common.config.model_config import get_model, get_ollama_seed, get_num_ctx
from common.logging.llm_callbacks import setup_litellm_callbacks

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger

# Track if LiteLLM callbacks have been registered (they're global)
_litellm_callbacks_registered = False


WORKFLOW_NAME = 'BrowseYouTube'
WORKFLOW_DESCRIPTION = 'Research YouTube video content'

# YouTube research tasks - queries about video content.
# Tagged 2026-04-27 with site categories for schema consistency. All tasks
# are "heavy" by content type (video research) but the field is preserved
# so the data shape matches BROWSE_WEB_TASKS / WEB_SEARCH_TASKS. YouTube
# workflow does not consume site_weights. 50 entries.
BROWSE_YOUTUBE_TASKS = [
    ("What are the most popular tech YouTube channels right now?", "heavy"),
    ("Find trending machine learning video content on YouTube", "heavy"),
    ("What cooking tutorial channels are popular on YouTube?", "heavy"),
    ("Search for the best Python programming tutorial series on YouTube", "heavy"),
    ("What are the top science education YouTube channels?", "heavy"),
    ("Find popular travel vlog channels on YouTube", "heavy"),
    ("What gaming content is trending on YouTube this month?", "heavy"),
    ("Search for highly rated music production tutorial videos", "heavy"),
    ("What fitness and workout channels are popular on YouTube?", "heavy"),
    ("Find the best DIY and home improvement YouTube channels", "heavy"),
    ("What documentary-style YouTube channels cover history?", "heavy"),
    ("Search for popular product review channels on YouTube", "heavy"),
    ("What space and astronomy content is trending on YouTube?", "heavy"),
    ("Find educational math tutorial YouTube channels", "heavy"),
    ("What photography tutorial content is popular on YouTube?", "heavy"),
    ("Find popular woodworking channels on YouTube", "heavy"),
    ("What 3D printing channels are trending on YouTube?", "heavy"),
    ("Search for popular language learning YouTube channels", "heavy"),
    ("What are the top chess instruction YouTube channels?", "heavy"),
    ("Find video essays on classic literature on YouTube", "heavy"),
    ("What are the most popular news commentary YouTube channels?", "heavy"),
    ("Search for car review YouTube channels", "heavy"),
    ("Find popular electronics teardown YouTube channels", "heavy"),
    ("What art tutorial channels are popular on YouTube?", "heavy"),
    ("Search for nature documentary creators on YouTube", "heavy"),
    ("Find popular astrophotography YouTube channels", "heavy"),
    ("What classical music performance channels are popular on YouTube?", "heavy"),
    ("Search for jazz tutorial YouTube channels", "heavy"),
    ("Find popular guitar lesson YouTube channels", "heavy"),
    ("What stand-up comedy clip channels are popular on YouTube?", "heavy"),
    ("Search for popular short-film YouTube channels", "heavy"),
    ("Find popular animation creators on YouTube", "heavy"),
    ("What history-explainer YouTube channels are popular?", "heavy"),
    ("Search for chemistry experiment YouTube channels", "heavy"),
    ("Find popular astronomy YouTube channels for beginners", "heavy"),
    ("What philosophy lecture series are popular on YouTube?", "heavy"),
    ("Search for popular podcast clips on YouTube", "heavy"),
    ("Find popular conference recording archives on YouTube", "heavy"),
    ("What aviation and flight YouTube channels are popular?", "heavy"),
    ("Search for popular sailing-vlog YouTube channels", "heavy"),
    ("Find popular blacksmithing channels on YouTube", "heavy"),
    ("What kayaking and outdoor adventure channels are popular on YouTube?", "heavy"),
    ("Search for popular indoor gardening YouTube channels", "heavy"),
    ("Find popular speed-running channels on YouTube", "heavy"),
    ("What live-coding YouTube channels are popular?", "heavy"),
    ("Search for security research YouTube talks", "heavy"),
    ("Find popular reverse-engineering YouTube channels", "heavy"),
    ("What CTF walkthrough YouTube channels are popular?", "heavy"),
    ("Search for popular synth-music tutorial YouTube channels", "heavy"),
    ("Find popular field-recording channels on YouTube", "heavy"),
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
        # Floor bumped 6 → 10 (2026-04-27): with VisitWebpageTool added, gemma
        # has more reason to keep going past a single search. PHASE
        # behavior_modifiers.max_steps still overrides per-deploy.
        self.max_steps = 10

    def _get_agent(self):
        """Lazy-load the SmolAgents CodeAgent."""
        if self._agent is None:
            model_id = f"ollama/{self.model_name}"
            # tier-aware num_ctx — see brains/smolagents/agent.py for rationale
            llm_kwargs = {"model_id": model_id, "num_ctx": get_num_ctx()}
            ollama_seed = get_ollama_seed()
            if ollama_seed is not None:
                llm_kwargs["seed"] = ollama_seed
                llm_kwargs["temperature"] = 0.0
            llm = LiteLLMModel(**llm_kwargs)

            instructions = None
            if self.prompts is not None:
                instructions = self.prompts.build_system_prompt()

            self._agent = CodeAgent(
                tools=[DuckDuckGoSearchTool(), VisitWebpageTool(), WhoisLookupTool()],
                model=llm,
                instructions=instructions,
                max_steps=self.max_steps,
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
            # Tasks are (query, category) tuples since 2026-04-27. YouTube
            # does not consume site_weights — flat random over all tasks.
            task = random.choice(BROWSE_YOUTUBE_TASKS)[0]
            if logger:
                logger.decision(
                    choice="browse_youtube_task",
                    options=[t for t, _ in BROWSE_YOUTUBE_TASKS[:5]],
                    selected=task,
                    context=f"Task from {len(BROWSE_YOUTUBE_TASKS)} available tasks",
                    method="random"
                )

        self.category = "video"
        self.description = task[:50] + "..." if len(task) > 50 else task
        print(self.display)

        # Steps are logged at the action level by the LLM response parser
        # in LiteLLMLoggingCallback (search, navigate, type_text, etc.)
        try:
            agent = self._get_agent()
            result = agent.run(task)
            print(f"YouTube browse completed: {str(result)[:200]}...")
            # Heuristic: non-empty result = success
            success = result is not None and str(result).strip() != ""
            return result, success
        except Exception as e:
            print(f"YouTube browse error: {e}")
            raise

    def cleanup(self):
        """Clean up agent resources."""
        self._agent = None
