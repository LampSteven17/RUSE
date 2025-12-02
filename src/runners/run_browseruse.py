"""
Runner for BrowserUse brain configurations.

Configurations:
- B1.llama: BrowserUse + llama3.1:8b
- B2.gemma: BrowserUse + gemma3:4b
- B3.deepseek: BrowserUse + deepseek-r1:8b
- B?.model+: POST-PHASE with PHASE-improved prompts
"""
from runners.run_config import SUPConfig


def run_browseruse(config: SUPConfig, task: str = None):
    """
    Run BrowserUse brain with configured model and prompts.

    Args:
        config: SUP configuration
        task: Optional task to run (uses default if not provided)
    """
    from brains.browseruse import BrowserUseAgent, DEFAULT_PROMPTS, PHASE_PROMPTS
    from brains.browseruse.tasks import get_random_task

    # Select prompts based on PHASE flag
    prompts = PHASE_PROMPTS if config.phase else DEFAULT_PROMPTS

    # Use provided task or get a random one
    if task is None:
        task = get_random_task()

    print(f"Running BrowserUse agent (config: {config.config_key})")
    print(f"PHASE mode: {config.phase}")

    agent = BrowserUseAgent(
        prompts=prompts,
        model=config.model,
    )

    return agent.run(task)


if __name__ == "__main__":
    import argparse
    from runners.run_config import build_config

    parser = argparse.ArgumentParser(description="Run BrowserUse brain")
    parser.add_argument("task", nargs="?", default=None, help="Task to perform")
    parser.add_argument("--model", choices=["llama", "gemma", "deepseek"], default="llama")
    parser.add_argument("--phase", action="store_true", help="Enable PHASE-improved prompts")
    args = parser.parse_args()

    config = build_config(
        brain="browseruse",
        model=args.model,
        phase=args.phase,
    )

    run_browseruse(config, task=args.task)
