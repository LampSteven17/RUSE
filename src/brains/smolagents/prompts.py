"""
Prompt configuration for SmolAgents.

SmolAgents are LLM-driven, so their behavior is controlled through prompts.
The task prompt defines what to do, and the content prompt provides style guidelines.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class SMOLPrompts:
    """Prompt configuration for SmolAgents."""

    task: str
    content: Optional[str] = None

    def build_system_prompt(self) -> Optional[str]:
        """Build system prompt from content guidelines."""
        if self.content:
            return f"[Content Guidelines]\n{self.content}"
        return None


# Default prompts (no augmentation - baseline S series)
DEFAULT_PROMPTS = SMOLPrompts(
    task="Research and answer the question.",
    content=None,
)

# PHASE-improved prompts (for S2 series with PHASE timing)
PHASE_PROMPTS = SMOLPrompts(
    task="Research and answer the question thoroughly.",

    content="""
When searching and generating responses:
- Use varied, natural search queries
- Summarize findings conversationally
- Include relevant details and context
- Cite sources when appropriate

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

    content="""
When performing research:
- Consider multiple angles before searching
- Review each result carefully before proceeding
- Take a methodical, step-by-step approach
- Pause to synthesize information before responding
""",
)
