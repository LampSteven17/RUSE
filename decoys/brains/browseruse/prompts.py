"""
Prompt configuration for BrowserUse agents.

BrowserUse agents are LLM-driven, so their behavior is controlled through prompts.
The task prompt defines what to do, and the content prompt provides style guidelines.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class BUPrompts:
    """Prompt configuration for BrowserUse."""

    task: str
    content: Optional[str] = None

    def build_full_prompt(self) -> str:
        """Combine prompts into single instruction for the agent."""
        parts = [self.task]

        if self.content:
            parts.append(f"\n\n[Content Guidelines]\n{self.content}")

        return "".join(parts)


# Default prompts (no augmentation - baseline B series)
DEFAULT_PROMPTS = BUPrompts(
    task="Complete the browsing task.",
    content=None,
)

# PHASE-improved prompts (for B2 series with PHASE timing)
PHASE_PROMPTS = BUPrompts(
    task="Complete the browsing task naturally, as a human would.",

    content="""
When generating any text (searches, form inputs, etc.):
- Use natural, conversational language
- Vary your word choices
- Occasionally include minor typos (realistic)
- Match the context and tone of the site

When interacting with the browser:
- Pause 1-3 seconds before clicking
- Scroll gradually, not instantly
- Read content before taking action
- Occasionally explore related links
- Move through pages at a natural pace
""",
)

# MCHP-like prompts (mimics MCHP timing patterns)
MCHP_LIKE_PROMPTS = BUPrompts(
    task="Complete the browsing task with human-like timing.",

    content="""
When interacting with the browser:
- Wait 2-10 seconds between actions
- Scroll in small increments
- Pause to 'read' content before proceeding
- Complete tasks in clusters, then take longer breaks
""",
)
