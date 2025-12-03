"""
Three-prompt configuration for BrowserUse agents.

Prompts are structured as:
- task: What to do (the specific browsing task)
- content: How to generate/write content (text style, language patterns)
- mechanics: How to interact with the browser (timing, scrolling, clicking)
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class BUPrompts:
    """Prompt configuration for BrowserUse."""

    task: str
    content: Optional[str] = None
    mechanics: Optional[str] = None

    def build_full_prompt(self) -> str:
        """Combine prompts into single instruction for the agent."""
        parts = [self.task]

        if self.content:
            parts.append(f"\n\n[Content Guidelines]\n{self.content}")

        if self.mechanics:
            parts.append(f"\n\n[Interaction Guidelines]\n{self.mechanics}")

        return "".join(parts)


# Default prompts (no augmentation - baseline B series)
DEFAULT_PROMPTS = BUPrompts(
    task="Complete the browsing task.",
    content=None,
    mechanics=None,
)

# PHASE-improved prompts (for POST-PHASE experiments)
PHASE_PROMPTS = BUPrompts(
    task="Complete the browsing task naturally, as a human would.",

    content="""
When generating any text (searches, form inputs, etc.):
- Use natural, conversational language
- Vary your word choices
- Occasionally include minor typos (realistic)
- Match the context and tone of the site
""",

    mechanics="""
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

    content=None,

    mechanics="""
When interacting with the browser:
- Wait 2-10 seconds between actions
- Scroll in small increments
- Pause to 'read' content before proceeding
- Complete tasks in clusters, then take longer breaks
""",
)
