"""
Default mechanics prompts for agent behavior.

These prompts guide how agents interact with browsers and systems.
Used by BrowserUse and SmolAgents brains.
"""

# Default browser interaction prompt
DEFAULT_BROWSER_MECHANICS = """
When interacting with the browser:
- Click on elements directly
- Type text at a normal pace
- Navigate pages efficiently
- Complete tasks in a straightforward manner
"""

# Default research mechanics prompt
DEFAULT_RESEARCH_MECHANICS = """
When performing research:
- Search for information directly
- Review results and select relevant sources
- Extract key information efficiently
- Synthesize findings into a response
"""

# No mechanics (for baseline configurations)
NO_MECHANICS = None
