"""
Runner for BrowserUse brain configurations.

Single-Task Configurations:
- B1.llama: BrowserUse + llama3.1:8b
- B2.gemma: BrowserUse + gemma3:4b
- B3.deepseek: BrowserUse + deepseek-r1:8b

Loop Mode Configurations (MCHP-style continuous execution):
- B4.llama: BrowserUseLoop + llama + MCHP workflows + PHASE timing
- B5.gemma: BrowserUseLoop + gemma + MCHP workflows + PHASE timing
- B6.deepseek: BrowserUseLoop + deepseek + MCHP workflows + PHASE timing
"""
from datetime import datetime
from runners.run_config import SUPConfig
from common.logging.agent_logger import AgentLogger


def log(msg: str):
    """Print with timestamp."""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] {msg}")


def run_browseruse(config: SUPConfig, task: str = None):
    """
    Run BrowserUse brain with configured model and prompts (single-task mode).

    Args:
        config: SUP configuration
        task: Optional task to run (uses default if not provided)
    """
    # Initialize structured logger
    logger = AgentLogger(agent_type=config.config_key)
    logger.session_start(config={
        "brain": config.brain,
        "model": config.model,
        "phase": config.phase,
        "config_key": config.config_key
    })

    from brains.browseruse import BrowserUseAgent, DEFAULT_PROMPTS, PHASE_PROMPTS
    from brains.browseruse.tasks import get_random_task

    # Select prompts based on PHASE flag
    prompts = PHASE_PROMPTS if config.phase else DEFAULT_PROMPTS

    # Use provided task or get a random one
    if task is None:
        task = get_random_task()

    log(f"Running BrowserUse agent (config: {config.config_key})")
    log(f"PHASE mode: {config.phase}")
    logger.info("Starting BrowserUse agent", details={
        "config_key": config.config_key,
        "phase": config.phase,
        "task": task
    })

    try:
        agent = BrowserUseAgent(
            prompts=prompts,
            model=config.model,
            logger=logger,
        )
        result = agent.run(task)
        logger.session_end(summary={"result": str(result)[:500] if result else None})
        return result
    except KeyboardInterrupt:
        logger.info("Agent stopped by user (KeyboardInterrupt)")
        logger.session_end()
    except Exception as e:
        logger.error(str(e), fatal=True, exception=e)
        logger.session_end()
        raise


def run_browseruse_loop(config: SUPConfig, include_mchp: bool = True, use_phase_timing: bool = True):
    """
    Run BrowserUse in loop mode (MCHP-style continuous execution).

    This mode runs BrowserUse browsing tasks interleaved with MCHP
    workflows for diverse, human-like activity patterns.

    Args:
        config: SUP configuration
        include_mchp: Include MCHP workflows for activity diversity
        use_phase_timing: Enable PHASE timing with time-of-day awareness
    """
    # Initialize structured logger
    logger = AgentLogger(agent_type=config.config_key)
    logger.session_start(config={
        "brain": config.brain,
        "model": config.model,
        "phase": config.phase,
        "loop_mode": True,
        "include_mchp": include_mchp,
        "phase_timing": use_phase_timing,
        "config_key": config.config_key
    })

    from brains.browseruse import BrowserUseLoop, DEFAULT_PROMPTS, PHASE_PROMPTS

    # Select prompts based on PHASE flag
    prompts = PHASE_PROMPTS if config.phase else DEFAULT_PROMPTS

    log(f"Running BrowserUse loop (config: {config.config_key})")
    log(f"PHASE mode: {config.phase}")
    log(f"PHASE timing: {use_phase_timing}")
    log(f"MCHP workflows: {include_mchp}")
    logger.info("Starting BrowserUse loop", details={
        "config_key": config.config_key,
        "phase": config.phase,
        "phase_timing": use_phase_timing,
        "include_mchp": include_mchp
    })

    try:
        agent = BrowserUseLoop(
            model=config.model,
            prompts=prompts,
            include_mchp=include_mchp,
            logger=logger,
            use_phase_timing=use_phase_timing,
        )
        agent.run()
    except KeyboardInterrupt:
        logger.info("Agent stopped by user (KeyboardInterrupt)")
    except Exception as e:
        logger.error(str(e), fatal=True, exception=e)
        raise
    finally:
        logger.session_end()


if __name__ == "__main__":
    import argparse
    from runners.run_config import build_config

    parser = argparse.ArgumentParser(description="Run BrowserUse brain")
    parser.add_argument("task", nargs="?", default=None, help="Task to perform (single-task mode)")
    parser.add_argument("--model", choices=["llama", "gemma", "deepseek"], default="llama")
    parser.add_argument("--phase", action="store_true", help="Enable PHASE-improved prompts")
    parser.add_argument("--loop", action="store_true", help="Run in loop mode (MCHP-style)")
    parser.add_argument("--no-mchp", action="store_true", help="Disable MCHP workflows in loop mode")
    parser.add_argument("--no-phase-timing", action="store_true",
                        help="Disable PHASE timing (use random timing instead)")
    args = parser.parse_args()

    config = build_config(
        brain="browseruse",
        model=args.model,
        phase=args.phase,
    )

    if args.loop:
        run_browseruse_loop(
            config,
            include_mchp=not args.no_mchp,
            use_phase_timing=not args.no_phase_timing
        )
    else:
        run_browseruse(config, task=args.task)
