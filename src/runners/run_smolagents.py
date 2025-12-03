"""
Runner for SmolAgents brain configurations.

Configurations:
- S1.llama: SmolAgents + llama3.1:8b
- S2.gemma: SmolAgents + gemma3:4b
- S3.deepseek: SmolAgents + deepseek-r1:8b
- S?.model+: POST-PHASE with PHASE-improved prompts
"""
from runners.run_config import SUPConfig


def run_smolagents(config: SUPConfig, task: str = None):
    """
    Run SmolAgents brain with configured model and prompts.

    Args:
        config: SUP configuration
        task: Optional task to run (uses default if not provided)
    """
    from brains.smolagents import SmolAgent, DEFAULT_PROMPTS, PHASE_PROMPTS
    from brains.smolagents.tasks import get_random_task

    # Select prompts based on PHASE flag
    prompts = PHASE_PROMPTS if config.phase else DEFAULT_PROMPTS

    # Use provided task or get a random one
    if task is None:
        task = get_random_task()

    print(f"Running SmolAgents agent (config: {config.config_key})")
    print(f"PHASE mode: {config.phase}")

    agent = SmolAgent(
        prompts=prompts,
        model=config.model,
    )

    return agent.run(task)


if __name__ == "__main__":
    import argparse
    from runners.run_config import build_config

    parser = argparse.ArgumentParser(description="Run SmolAgents brain")
    parser.add_argument("task", nargs="?", default=None, help="Task to perform")
    parser.add_argument("--model", choices=["llama", "gemma", "deepseek"], default="llama")
    parser.add_argument("--phase", action="store_true", help="Enable PHASE-improved prompts")
    args = parser.parse_args()

    config = build_config(
        brain="smolagents",
        model=args.model,
        phase=args.phase,
    )

    run_smolagents(config, task=args.task)
