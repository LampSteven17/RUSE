"""
Browse web workflow for SmolAgents.

Researches general web content using CodeAgent + DuckDuckGoSearchTool.
"""
import random
from typing import Optional, TYPE_CHECKING

from smolagents import CodeAgent, LiteLLMModel, DuckDuckGoSearchTool, VisitWebpageTool

from brains.smolagents.workflows.base import SmolWorkflow
from common.config.model_config import get_model, get_ollama_seed, get_num_ctx
from common.logging.llm_callbacks import setup_litellm_callbacks

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger

# Track if LiteLLM callbacks have been registered (they're global)
_litellm_callbacks_registered = False


WORKFLOW_NAME = 'BrowseWeb'
WORKFLOW_DESCRIPTION = 'Browse the web and read content'

# Web browsing tasks - general knowledge queries that simulate reading websites.
# Tagged 2026-04-27 with site categories for content.site_categories steering:
#   lightweight = Wikipedia, BBC News, blog, mostly-text reference
#   medium      = forum/Q&A, tutorial, magazine-format
#   heavy       = video, streaming, image-heavy
# Format: (query_string, category). 50 entries.
BROWSE_WEB_TASKS = [
    # === lightweight (reference / news / blog text) ===
    ("What is the history of the internet?", "lightweight"),
    ("Describe the human immune system", "lightweight"),
    ("What are the major features of Wikipedia?", "lightweight"),
    ("Describe the BBC's news coverage areas", "lightweight"),
    ("What kind of reporting does Reuters focus on?", "lightweight"),
    ("Describe the main sections of a typical news website", "lightweight"),
    ("Summarize what AP News covers", "lightweight"),
    ("What is The Guardian's editorial focus?", "lightweight"),
    ("Describe NPR's content categories", "lightweight"),
    ("What sections appear on Britannica.com?", "lightweight"),
    ("Summarize the structure of arxiv.org", "lightweight"),
    ("What is the layout of a typical encyclopedia entry?", "lightweight"),
    ("Describe what Project Gutenberg offers", "lightweight"),
    ("What is the purpose of the Internet Archive?", "lightweight"),
    ("Summarize how the Library of Congress site is organized", "lightweight"),
    ("Describe how WHO publishes health guidance online", "lightweight"),
    ("What does the CDC website cover?", "lightweight"),
    # === medium (tutorials / Q&A / magazine) ===
    ("What is Hacker News and what kind of stories appear there?", "medium"),
    ("Summarize how Reddit works as a social platform", "medium"),
    ("What topics does TechCrunch cover?", "medium"),
    ("What are the most popular content categories on Medium?", "medium"),
    ("Describe how Stack Overflow helps programmers", "medium"),
    ("What types of articles does Ars Technica publish?", "medium"),
    ("Summarize what The Verge covers in technology", "medium"),
    ("What are the main features of Wired magazine's website?", "medium"),
    ("Describe how Quora structures its question pages", "medium"),
    ("What does Server Fault host?", "medium"),
    ("Summarize how GitHub Discussions works", "medium"),
    ("What is freeCodeCamp's curriculum structure?", "medium"),
    ("Describe Real Python's tutorial format", "medium"),
    ("What does MDN Web Docs provide for developers?", "medium"),
    ("Summarize how Dev.to differs from Medium", "medium"),
    ("What kind of content appears on Lobste.rs?", "medium"),
    ("Describe Hashnode's blogging platform", "medium"),
    # === heavy (video / streaming / image-heavy) ===
    ("What kind of content is popular on Twitch?", "heavy"),
    ("Describe what Vimeo offers compared to YouTube", "heavy"),
    ("Summarize how Pinterest organizes image boards", "heavy"),
    ("What is Imgur primarily used for?", "heavy"),
    ("Describe Flickr's photo-sharing model", "heavy"),
    ("What kind of content appears on TikTok's discover page?", "heavy"),
    ("Summarize how Instagram Reels are recommended", "heavy"),
    ("Describe DailyMotion's main video categories", "heavy"),
    ("What does the Met Museum's online collection look like?", "heavy"),
    ("Summarize the layout of a Spotify artist page", "heavy"),
    ("Describe how SoundCloud presents tracks", "heavy"),
    ("What types of media appear on Bandcamp?", "heavy"),
    ("Describe how a typical streaming platform displays its catalog", "heavy"),
    ("What is the layout of a typical video gaming review site?", "heavy"),
    ("Summarize how IGN organizes game coverage", "heavy"),
    ("What does Giphy host?", "heavy"),
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
        # Floor bumped 6 → 10 (2026-04-27): with VisitWebpageTool added, gemma
        # has more reason to keep going past a single search. PHASE
        # behavior_modifiers.max_steps still overrides per-deploy.
        self.max_steps = 10
        self.task_weights = None
        # Set by SmolAgentLoop._apply_brain_specific_config from
        # content.site_categories (e.g. {"lightweight": 0.55, "medium": 0.3,
        # "heavy": 0.15}). When None, fall back to flat random over all tasks.
        self.site_weights = None

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
                tools=[DuckDuckGoSearchTool(), VisitWebpageTool()],
                model=llm,
                instructions=instructions,
                max_steps=self.max_steps,
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
            # Tasks are (query, category) tuples since 2026-04-27. Three
            # selection modes, in priority order:
            #   1. site_weights (PHASE content.site_categories) — pick a
            #      category, then random within that category's pool.
            #   2. task_weights (legacy per-task weighting) — random.choices
            #      over flat list.
            #   3. fallback — flat random over all tasks.
            if self.site_weights:
                cats = list(self.site_weights.keys())
                weights = list(self.site_weights.values())
                chosen_cat = random.choices(cats, weights=weights, k=1)[0]
                pool = [t for t, c in BROWSE_WEB_TASKS if c == chosen_cat]
                if not pool:
                    pool = [t for t, _ in BROWSE_WEB_TASKS]
                task = random.choice(pool)
                selection_method = f"site_weighted:{chosen_cat}"
            elif self.task_weights:
                task = random.choices(BROWSE_WEB_TASKS, weights=self.task_weights, k=1)[0][0]
                selection_method = "behavior_weighted"
            else:
                task = random.choice(BROWSE_WEB_TASKS)[0]
                selection_method = "random"
            if logger:
                logger.decision(
                    choice="browse_web_task",
                    options=[t for t, _ in BROWSE_WEB_TASKS[:5]],
                    selected=task,
                    context=f"Task from {len(BROWSE_WEB_TASKS)} available tasks",
                    method=selection_method
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
            # Heuristic: non-empty result = success
            success = result is not None and str(result).strip() != ""
            return result, success
        except Exception as e:
            print(f"Browse web error: {e}")
            raise

    def cleanup(self):
        """Clean up agent resources."""
        self._agent = None
