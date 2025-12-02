"""
PHASE-improved mechanics prompts for agent behavior.

These prompts incorporate PHASE insights for more human-like interactions.
"""

# PHASE browser mechanics - human-like timing and behavior
PHASE_BROWSER_MECHANICS = """
When interacting with the browser, behave like a human:
- Pause 1-3 seconds before clicking (as if reading/deciding)
- Scroll gradually through content, not instantly
- Take time to "read" content before taking action
- Occasionally explore related links or content
- Move through pages at a natural, unhurried pace
- Sometimes pause longer (5-10 seconds) as if thinking
"""

# PHASE research mechanics - thorough and deliberate
PHASE_RESEARCH_MECHANICS = """
When performing research, be thorough and deliberate:
- Try multiple search queries if initial results are unclear
- Take time to review and compare multiple sources
- Prefer authoritative and recent sources
- Verify important information across sources when possible
- Consider multiple angles before concluding
- Pause between searches to synthesize information
"""

# MCHP-like mechanics - mimic MCHP timing patterns
MCHP_LIKE_BROWSER_MECHANICS = """
When interacting with the browser, use MCHP-like timing:
- Wait 2-10 seconds between actions (varied randomly)
- Scroll in small increments (partial viewport)
- Pause to 'read' content for 3-8 seconds before proceeding
- Complete 3-5 actions in a cluster, then take a longer break (30-60 seconds)
- Occasionally re-read or scroll back to previous content
"""

MCHP_LIKE_RESEARCH_MECHANICS = """
When performing research, use deliberate pacing:
- Wait 2-5 seconds between search queries
- Review each result carefully before selecting
- Take a methodical, step-by-step approach
- Pause 5-15 seconds to 'read' each source
- Work in focused bursts with breaks between topics
"""

# Time-of-day aware mechanics adjustments
TIME_OF_DAY_MECHANICS = {
    "morning": {
        "pace": "moderate",
        "breaks": "short",
        "focus": "high",
        "description": "Alert and focused, working at a steady pace with short breaks",
    },
    "afternoon": {
        "pace": "varied",
        "breaks": "moderate",
        "focus": "moderate",
        "description": "Mixed pace with some longer pauses, occasional distractions",
    },
    "evening": {
        "pace": "slow",
        "breaks": "longer",
        "focus": "declining",
        "description": "Slower pace with longer pauses, less focused, wrapping up",
    },
}
