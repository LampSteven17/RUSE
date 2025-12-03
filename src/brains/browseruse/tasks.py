"""
Task definitions for BrowserUse agent.

These are example tasks that can be run by the agent.
"""

# Default browsing tasks
DEFAULT_TASKS = [
    "Visit google.com and search for 'OpenAI news'",
    "Go to wikipedia.org and read about artificial intelligence",
    "Visit reddit.com and browse the front page",
    "Search for 'best Python tutorials 2024' on Google",
    "Go to news.ycombinator.com and read the top stories",
]

# Research-oriented tasks
RESEARCH_TASKS = [
    "Search for recent developments in large language models",
    "Find and summarize the top 3 articles about machine learning",
    "Look up the current weather and news for New York City",
    "Research the history of the internet and summarize key events",
]

# Shopping/browsing tasks
BROWSING_TASKS = [
    "Browse Amazon and look at trending products",
    "Visit a news website and summarize the headlines",
    "Go to YouTube and find popular tech videos",
]


def get_random_task(task_list: list = None) -> str:
    """Get a random task from the specified list."""
    import random
    tasks = task_list or DEFAULT_TASKS
    return random.choice(tasks)
