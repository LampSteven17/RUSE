"""
PHASE-improved content prompts for LLM augmentation.

These prompts incorporate PHASE insights for more natural content generation.
"""

# PHASE-improved content generation prompts
PHASE_PARAGRAPH_PROMPT = (
    "Write a short, natural paragraph (3-4 sentences) about a topic relevant to "
    "someone working in an office during {time_of_day}. Consider what topics are "
    "commonly discussed or thought about during this time. "
    "Write naturally, as if you were a person taking notes or drafting a document. "
    "Write ONLY the paragraph, no introduction or explanation."
)

PHASE_SENTENCE_PROMPT = (
    "Write a single, natural sentence that someone might type while working. "
    "It should sound like real human writing, not overly formal or robotic. "
    "Occasionally include a minor typo or casual phrasing. "
    "Write ONLY the sentence, nothing else."
)

PHASE_SEARCH_PROMPT = (
    "Generate a realistic Google search query that someone might type during {time_of_day}. "
    "Context: {context}. "
    "Make it natural - real searches often have typos, incomplete phrases, or casual language. "
    "Reply with ONLY the search query, nothing else."
)

PHASE_FILENAME_PROMPT = (
    "Generate a realistic filename for a document being created on {date}. "
    "Use patterns like project names, dates, or descriptive terms. "
    "Examples: q4-report, meeting-notes-dec, project-alpha-draft. "
    "Reply with ONLY the filename (no extension)."
)

PHASE_COMMENT_PROMPT = (
    "Write a quick review comment that someone might leave on a {context}. "
    "Be natural and brief - real comments are often short and informal. "
    "Keep it under 80 characters. "
    "Write ONLY the comment."
)

# Time-of-day aware content themes
TIME_OF_DAY_THEMES = {
    "morning": [
        "planning the day",
        "reviewing overnight emails",
        "morning meetings",
        "coffee and tasks",
        "project kickoffs",
    ],
    "afternoon": [
        "ongoing work",
        "collaboration",
        "project updates",
        "problem solving",
        "documentation",
    ],
    "evening": [
        "wrapping up tasks",
        "preparing for tomorrow",
        "end-of-day reports",
        "final reviews",
        "cleanup and organization",
    ],
}
