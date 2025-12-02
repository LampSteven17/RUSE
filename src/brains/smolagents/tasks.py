"""
Task definitions for SmolAgents agent.

These are example tasks that can be run by the agent.
"""

# Default research tasks
DEFAULT_TASKS = [
    "What is the current weather in Paris?",
    "What are the latest developments in artificial intelligence?",
    "Explain quantum computing in simple terms",
    "What are the top programming languages in 2024?",
    "Summarize recent news about space exploration",
]

# Technical research tasks
TECHNICAL_TASKS = [
    "Explain how large language models work",
    "What are the differences between Python and JavaScript?",
    "Describe the architecture of a modern web application",
    "What are best practices for API design?",
]

# General knowledge tasks
GENERAL_TASKS = [
    "What is the history of the internet?",
    "Explain the theory of relativity",
    "What are the major causes of climate change?",
    "Describe the human immune system",
]


def get_random_task(task_list: list = None) -> str:
    """Get a random task from the specified list."""
    import random
    tasks = task_list or DEFAULT_TASKS
    return random.choice(tasks)
