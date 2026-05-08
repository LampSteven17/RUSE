"""
Task definitions for SmolAgents agent.

Aggregates tasks from all native workflows for use in single-task mode.
"""

# Default research tasks (subset for single-task mode)
DEFAULT_TASKS = [
    "What is the current weather in Paris?",
    "What are the latest developments in artificial intelligence?",
    "Explain quantum computing in simple terms",
    "What are the top programming languages in 2024?",
    "Summarize recent news about space exploration",
]

# Technical research tasks
TECHNICAL_TASKS = [
    "Find the latest cybersecurity vulnerabilities reported this month",
    "Compare React vs Vue vs Angular for web development",
    "Search for the best Python libraries for data analysis",
    "Find recent developments in large language models",
    "Search for cloud computing cost optimization strategies",
    "Find the top programming languages by popularity in 2024",
    "Search for best practices in API design and REST endpoints",
    "Find comparisons of different database systems for web apps",
    "Search for recent breakthroughs in quantum computing",
    "Find the latest trends in DevOps and CI/CD pipelines",
    "Search for machine learning model deployment best practices",
    "Find recent open source projects gaining traction",
    "Search for web application security testing methodologies",
    "Find comparisons of containerization tools and platforms",
    "Search for the latest updates in the JavaScript ecosystem",
]

# General knowledge tasks
GENERAL_TASKS = [
    "What is the history of the internet?",
    "Describe the human immune system",
    "What are the major features of Wikipedia?",
    "Summarize how Reddit works as a social platform",
    "What is Hacker News and what kind of stories appear there?",
    "Describe the BBC's news coverage areas",
    "What topics does TechCrunch cover?",
    "Summarize what ESPN covers in sports news",
    "What kind of reporting does Reuters focus on?",
    "Describe the main sections of a typical news website",
    "What are the most popular content categories on Medium?",
    "Describe how Stack Overflow helps programmers",
    "What types of articles does Ars Technica publish?",
    "Summarize what The Verge covers in technology",
    "What are the main features of Wired magazine's website?",
]

# All tasks from all workflows
ALL_TASKS = DEFAULT_TASKS + GENERAL_TASKS + TECHNICAL_TASKS


def get_random_task(task_list: list = None) -> str:
    """Get a random task from the specified list (defaults to ALL_TASKS)."""
    import random
    tasks = task_list or ALL_TASKS
    return random.choice(tasks)
