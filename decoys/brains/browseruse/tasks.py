"""
Task definitions for BrowserUse agent.

Aggregates tasks from all native workflows for use in single-task mode.
"""

# Default browsing tasks (subset for single-task mode)
DEFAULT_TASKS = [
    "Visit google.com and search for 'OpenAI news'",
    "Go to wikipedia.org and read about artificial intelligence",
    "Visit reddit.com and browse the front page",
    "Search for 'best Python tutorials 2024' on Google",
    "Go to news.ycombinator.com and read the top stories",
]

# Research-oriented tasks
RESEARCH_TASKS = [
    "Search Google for 'OpenAI news'",
    "Search Google for 'best Python tutorials 2024'",
    "Search for recent developments in large language models",
    "Search for 'machine learning best practices'",
    "Search Google for 'latest cybersecurity news'",
    "Search for 'React vs Vue comparison 2024'",
    "Search Google for 'cloud computing trends'",
    "Search for 'how to optimize database queries'",
    "Search Google for 'open source projects trending'",
    "Search for 'artificial intelligence applications healthcare'",
    "Search Google for 'remote work productivity tips'",
    "Search for 'web development frameworks comparison'",
    "Search Google for 'data science career guide'",
    "Search for 'containerization Docker Kubernetes tutorial'",
    "Search Google for 'programming language benchmarks 2024'",
]

# Browsing tasks
BROWSING_TASKS = [
    "Go to wikipedia.org and read about artificial intelligence",
    "Visit reddit.com and browse the front page",
    "Go to news.ycombinator.com and read the top stories",
    "Visit bbc.com and read the latest news headlines",
    "Go to cnn.com and browse the technology section",
    "Visit medium.com and browse popular articles",
    "Go to techcrunch.com and read the latest tech news",
    "Visit espn.com and check the latest sports scores",
    "Go to reuters.com and read the top world news",
    "Visit arstechnica.com and browse recent articles",
    "Go to theverge.com and read technology coverage",
    "Visit slashdot.org and browse the latest stories",
    "Go to wired.com and read about emerging technology",
    "Visit nature.com and browse recent science articles",
    "Go to stackoverflow.com and browse popular questions",
]

# All tasks from all workflows
ALL_TASKS = DEFAULT_TASKS + BROWSING_TASKS + RESEARCH_TASKS


def get_random_task(task_list: list = None) -> str:
    """Get a random task from the specified list (defaults to ALL_TASKS)."""
    import random
    tasks = task_list or ALL_TASKS
    return random.choice(tasks)
