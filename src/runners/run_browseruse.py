"""
Runner for BrowserUse brain configurations.

Single-Task Configurations:
- B1.llama: BrowserUse + llama3.1:8b
- B2.gemma: BrowserUse + gemma3:4b
- B3.deepseek: BrowserUse + deepseek-r1:8b

Loop Mode Configurations (continuous execution with native workflows):
- B4.llama: BrowserUseLoop + llama + PHASE timing
- B5.gemma: BrowserUseLoop + gemma + PHASE timing
- B6.deepseek: BrowserUseLoop + deepseek + PHASE timing
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
        logger.session_success(message="BrowserUse agent completed successfully",
                               details={"result": str(result)[:500] if result else None})
        return result
    except KeyboardInterrupt:
        logger.info("Agent stopped by user (KeyboardInterrupt)")
        # No session_fail - interruption is not failure
    except Exception as e:
        logger.session_fail(message="BrowserUse agent failed", exception=e)
        raise
    finally:
        logger.session_end()


def run_browseruse_loop(config: SUPConfig, use_phase_timing: bool = True):
    """
    Run BrowserUse in loop mode (continuous execution with native workflows).

    Args:
        config: SUP configuration
        use_phase_timing: Enable PHASE timing with time-of-day awareness
    """
    # Initialize structured logger
    logger = AgentLogger(agent_type=config.config_key)
    logger.session_start(config={
        "brain": config.brain,
        "model": config.model,
        "phase": config.phase,
        "loop_mode": True,
        "phase_timing": use_phase_timing,
        "config_key": config.config_key
    })

    from brains.browseruse import BrowserUseLoop, DEFAULT_PROMPTS, PHASE_PROMPTS

    # Select prompts based on PHASE flag
    prompts = PHASE_PROMPTS if config.phase else DEFAULT_PROMPTS

    log(f"Running BrowserUse loop (config: {config.config_key})")
    log(f"PHASE mode: {config.phase}")
    log(f"PHASE timing: {use_phase_timing}")
    logger.info("Starting BrowserUse loop", details={
        "config_key": config.config_key,
        "phase": config.phase,
        "phase_timing": use_phase_timing
    })

    try:
        agent = BrowserUseLoop(
            model=config.model,
            prompts=prompts,
            logger=logger,
            use_phase_timing=use_phase_timing,
        )
        agent.run()
        logger.session_success(message="BrowserUse loop completed successfully")
    except KeyboardInterrupt:
        logger.info("Agent stopped by user (KeyboardInterrupt)")
        # No session_fail - interruption is not failure
    except Exception as e:
        logger.session_fail(message="BrowserUse loop failed", exception=e)
        raise
    finally:
        logger.session_end()


if __name__ == "__main__":
    import argparse
    from runners.run_config import build_config

    parser = argparse.ArgumentParser(description="Run BrowserUse brain")
    parser.add_argument("task", nargs="?", default=None, help="Task to perform (single-task mode)")
    parser.add_argument("--model", choices=["llama", "gemma", "deepseek", "lfm", "ministral", "qwen"], default="llama")
    parser.add_argument("--phase", action="store_true", help="Enable PHASE-improved prompts")
    parser.add_argument("--loop", action="store_true", help="Run in loop mode")
    parser.add_argument("--cpu", action="store_true", help="CPU-only deployment (BC series)")
    parser.add_argument("--no-phase-timing", action="store_true",
                        help="Disable PHASE timing (use random timing instead)")
    args = parser.parse_args()

    config = build_config(
        brain="browseruse",
        model=args.model,
        phase=args.phase,
        cpu_only=args.cpu,
    )

    if args.loop:
        run_browseruse_loop(
            config,
            use_phase_timing=not args.no_phase_timing
        )
    else:
        run_browseruse(config, task=args.task)
