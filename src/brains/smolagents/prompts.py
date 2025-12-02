"""
Three-prompt configuration for SmolAgents.

Prompts are structured as:
- task: What to do (the specific research/code task)
- content: How to generate/present content (writing style, formatting)
- mechanics: How to perform research (search strategies, source selection)
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class SMOLPrompts:
    """Prompt configuration for SmolAgents."""

    task: str
    content: Optional[str] = None
    mechanics: Optional[str] = None

    def build_system_prompt(self) -> Optional[str]:
        """Build system prompt from content + mechanics guidelines."""
        parts = []

        if self.content:
            parts.append(f"[Content Guidelines]\n{self.content}")

        if self.mechanics:
            parts.append(f"\n[Behavior Guidelines]\n{self.mechanics}")

        return "\n".join(parts) if parts else None


# Default prompts (no augmentation - baseline S series)
DEFAULT_PROMPTS = SMOLPrompts(
    task="Research and answer the question.",
    content=None,
    mechanics=None,
)

# PHASE-improved prompts (for POST-PHASE experiments)
PHASE_PROMPTS = SMOLPrompts(
    task="Research and answer the question thoroughly.",

    content="""
When searching and generating responses:
- Use varied, natural search queries
- Summarize findings conversationally
- Include relevant details and context
- Cite sources when appropriate
""",

    mechanics="""
When performing research:
- Try multiple search queries if needed
- Take time to review and compare results
- Prefer authoritative and recent sources
- Verify information across multiple sources when possible
""",
)

# MCHP-like prompts (mimics MCHP timing patterns conceptually)
MCHP_LIKE_PROMPTS = SMOLPrompts(
    task="Research and answer the question with careful consideration.",

    content=None,

    mechanics="""
When performing research:
- Consider multiple angles before searching
- Review each result carefully before proceeding
- Take a methodical, step-by-step approach
- Pause to synthesize information before responding
""",
)
