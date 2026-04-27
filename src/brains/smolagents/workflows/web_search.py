"""
Web search workflow for SmolAgents.

Performs explicit web searches using CodeAgent + DuckDuckGoSearchTool.
"""
import random
from typing import Optional, TYPE_CHECKING

from smolagents import CodeAgent, LiteLLMModel, DuckDuckGoSearchTool, VisitWebpageTool

from brains.smolagents.tools import DownloadFileTool, WhoisLookupTool
from brains.smolagents.workflows.base import SmolWorkflow
from common.config.model_config import get_model, get_ollama_seed, get_num_ctx
from common.logging.llm_callbacks import setup_litellm_callbacks

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger

# Track if LiteLLM callbacks have been registered (they're global)
_litellm_callbacks_registered = False


WORKFLOW_NAME = 'WebSearch'
WORKFLOW_DESCRIPTION = 'Search the web for information'

# Web search tasks - explicit search-oriented queries.
# Tagged 2026-04-27 with site categories for schema consistency with
# BROWSE_WEB_TASKS / BROWSE_YOUTUBE_TASKS. WebSearch does not consume
# site_weights (only BrowseWeb does), but tuples keep the data shape uniform.
# 50 entries.
WEB_SEARCH_TASKS = [
    # === lightweight (reference / news search) ===
    ("Find the latest cybersecurity vulnerabilities reported this month", "lightweight"),
    ("Search for recent IETF RFC publications", "lightweight"),
    ("Find recent CVE entries on the NVD database", "lightweight"),
    ("Search for current major news headlines", "lightweight"),
    ("Find the most recent Linux kernel release notes", "lightweight"),
    ("Search for recent earthquake events worldwide", "lightweight"),
    ("Find the latest weather advisories from NOAA", "lightweight"),
    ("Search for recent space mission updates from NASA", "lightweight"),
    ("Find new entries in the Wikipedia featured-articles list", "lightweight"),
    ("Search for the latest economic indicators released by the Fed", "lightweight"),
    ("Find recent FDA drug approvals", "lightweight"),
    ("Search for the latest UN Security Council meeting summaries", "lightweight"),
    ("Find current rankings of universities worldwide", "lightweight"),
    ("Search for the latest CDC public-health bulletins", "lightweight"),
    ("Find recent peer-reviewed papers on climate change", "lightweight"),
    ("Search for the most-cited papers of the past year on arxiv", "lightweight"),
    ("Find recent statistics on global internet usage", "lightweight"),
    # === medium (tutorial / Q&A / dev research) ===
    ("Compare React vs Vue vs Angular for web development", "medium"),
    ("Search for the best Python libraries for data analysis", "medium"),
    ("Find recent developments in large language models", "medium"),
    ("Search for cloud computing cost optimization strategies", "medium"),
    ("Find the top programming languages by popularity in 2024", "medium"),
    ("Search for best practices in API design and REST endpoints", "medium"),
    ("Find comparisons of different database systems for web apps", "medium"),
    ("Search for recent breakthroughs in quantum computing", "medium"),
    ("Find the latest trends in DevOps and CI/CD pipelines", "medium"),
    ("Search for machine learning model deployment best practices", "medium"),
    ("Find recent open source projects gaining traction", "medium"),
    ("Search for web application security testing methodologies", "medium"),
    ("Find comparisons of containerization tools and platforms", "medium"),
    ("Search for the latest updates in the JavaScript ecosystem", "medium"),
    ("Compare popular IDEs for Rust development", "medium"),
    ("Search for tutorials on Kubernetes operators", "medium"),
    ("Find guides on building zero-trust network architectures", "medium"),
    ("Search for performance benchmarks of NoSQL databases", "medium"),
    ("Compare authentication libraries across major web frameworks", "medium"),
    ("Search for tutorials on writing custom Linux kernel modules", "medium"),
    ("Find guides on optimizing PostgreSQL query performance", "medium"),
    # === heavy (video-research / streaming-related searches) ===
    ("Search for popular conference talk videos this year", "heavy"),
    ("Find streaming platform comparisons for indie filmmakers", "heavy"),
    ("Search for trending livestream events online", "heavy"),
    ("Find documentaries available on free streaming services", "heavy"),
    ("Search for tutorials on video editing in DaVinci Resolve", "heavy"),
    ("Find live-coding streams on programming languages", "heavy"),
    ("Search for the most-watched gaming streams of the week", "heavy"),
    ("Find recent music album releases on streaming platforms", "heavy"),
    ("Search for podcast episodes about cybersecurity", "heavy"),
    ("Find video archives of academic conferences", "heavy"),
    ("Search for free online film festival programs", "heavy"),
    ("Find the most popular Twitch categories right now", "heavy"),
    ("Search for archived NASA mission video footage", "heavy"),
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
                tools=[
                    DuckDuckGoSearchTool(),
                    VisitWebpageTool(),
                    WhoisLookupTool(),
                    DownloadFileTool(),
                ],
                model=llm,
                instructions=instructions,
                max_steps=self.max_steps,
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
            # Tasks are (query, category) tuples since 2026-04-27. WebSearch
            # does not consume site_weights — flat random over all tasks.
            task = random.choice(WEB_SEARCH_TASKS)[0]
            if logger:
                logger.decision(
                    choice="web_search_task",
                    options=[t for t, _ in WEB_SEARCH_TASKS[:5]],
                    selected=task,
                    context=f"Task from {len(WEB_SEARCH_TASKS)} available tasks",
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
            print(f"Search completed: {str(result)[:200]}...")
            # Heuristic: non-empty result = success
            success = result is not None and str(result).strip() != ""
            return result, success
        except Exception as e:
            print(f"Web search error: {e}")
            raise

    def cleanup(self):
        """Clean up agent resources."""
        self._agent = None
