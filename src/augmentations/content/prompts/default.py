"""
Default content prompts for LLM augmentation.

These prompts guide LLM content generation for basic text creation.
"""

# Default content generation prompt template
DEFAULT_PARAGRAPH_PROMPT = (
    "Write a short, coherent paragraph (3-4 sentences) about a random professional topic. "
    "It could be about business, technology, science, or everyday office work. "
    "Write ONLY the paragraph, no introduction or explanation."
)

DEFAULT_SENTENCE_PROMPT = (
    "Write a single professional sentence suitable for a business document or email. "
    "Write ONLY the sentence, nothing else."
)

DEFAULT_WORD_PROMPT = "Generate a single common English word. Reply with ONLY the word."

DEFAULT_FILENAME_PROMPT = (
    "Generate a realistic filename for a business document (no extension). "
    "Use lowercase letters and dashes. Examples: project-report, meeting-notes, budget-2024. "
    "Reply with ONLY the filename."
)

DEFAULT_SEARCH_PROMPT = (
    "Generate a single realistic Google search query that someone might type. "
    "Context: {context}. "
    "Reply with ONLY the search query, nothing else."
)

DEFAULT_COMMENT_PROMPT = (
    "Write a brief review comment for a {context}. "
    "Keep it under 100 characters. Examples: 'Needs revision', 'Good work!', 'Please clarify this section'. "
    "Write ONLY the comment."
)

DEFAULT_HEADERS_PROMPT = (
    "Generate exactly {count} column headers for a business spreadsheet. "
    "Examples: Name, Date, Amount, Status, Category, Notes. "
    "Reply with ONLY the headers separated by commas."
)
