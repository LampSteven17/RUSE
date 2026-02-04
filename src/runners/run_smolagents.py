"""
Runner for SmolAgents brain configurations.

Configurations (exp-3):
- S1.{llama,gemma}: SmolAgents baseline (no timing)
- S2.{llama,gemma}: SmolAgents + summer24 calibrated timing
- S3.{llama,gemma}: SmolAgents + fall24 calibrated timing
- S4.{llama,gemma}: SmolAgents + spring25 calibrated timing
"""
from datetime import datetime
from runners.run_config import SUPConfig
from common.logging.agent_logger import AgentLogger


def log(msg: str):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] {msg}")


def run_smolagents(config: SUPConfig, task: str = None):
    """Run SmolAgents brain in single-task mode."""
    logger = AgentLogger(agent_type=config.config_key)
    logger.session_start(config={
        "brain": config.brain,
        "model": config.model,
        "calibration": config.calibration,
        "config_key": config.config_key
    })

    from brains.smolagents import SmolAgent, DEFAULT_PROMPTS, PHASE_PROMPTS
    from brains.smolagents.tasks import get_random_task

    prompts = PHASE_PROMPTS if config.phase else DEFAULT_PROMPTS
    if task is None:
        task = get_random_task()

    log(f"Running SmolAgents agent (config: {config.config_key})")
    log(f"Calibration: {config.calibration or 'none'}")
    logger.info("Starting SmolAgents agent", details={
        "config_key": config.config_key,
        "calibration": config.calibration,
        "task": task
    })

    try:
        agent = SmolAgent(prompts=prompts, model=config.model, logger=logger)
        result = agent.run(task)
        logger.session_success(message="SmolAgents agent completed successfully",
                               details={"result": str(result)[:500] if result else None})
        return result
    except KeyboardInterrupt:
        logger.info("Agent stopped by user (KeyboardInterrupt)")
    except Exception as e:
        logger.session_fail(message="SmolAgents agent failed", exception=e)
        raise
    finally:
        logger.session_end()


def run_smolagents_loop(config: SUPConfig, use_phase_timing: bool = True):
    """Run SmolAgents in loop mode (continuous execution)."""
    calibration_profile = config.calibration

    logger = AgentLogger(agent_type=config.config_key)
    logger.session_start(config={
        "brain": config.brain,
        "model": config.model,
        "calibration": calibration_profile,
        "loop_mode": True,
        "config_key": config.config_key
    })

    from brains.smolagents import SmolAgentLoop, DEFAULT_PROMPTS, PHASE_PROMPTS

    prompts = PHASE_PROMPTS if config.phase else DEFAULT_PROMPTS

    log(f"Running SmolAgents loop (config: {config.config_key})")
    log(f"Calibration: {calibration_profile or 'none'}")
    logger.info("Starting SmolAgents loop", details={
        "config_key": config.config_key,
        "calibration": calibration_profile,
    })

    try:
        agent = SmolAgentLoop(
            model=config.model,
            prompts=prompts,
            logger=logger,
            calibration_profile=calibration_profile,
            # Legacy compat: fall back to use_phase_timing if no calibration
            use_phase_timing=use_phase_timing if not calibration_profile else False,
        )
        agent.run()
        logger.session_success(message="SmolAgents loop completed successfully")
    except KeyboardInterrupt:
        logger.info("Agent stopped by user (KeyboardInterrupt)")
    except Exception as e:
        logger.session_fail(message="SmolAgents loop failed", exception=e)
        raise
    finally:
        logger.session_end()


if __name__ == "__main__":
    import argparse
    from runners.run_config import build_config

    parser = argparse.ArgumentParser(description="Run SmolAgents brain")
    parser.add_argument("task", nargs="?", default=None)
    parser.add_argument("--model", choices=["llama", "gemma", "deepseek", "lfm", "ministral", "qwen"], default="llama")
    parser.add_argument("--calibration", choices=["summer24", "fall24", "spring25"], default=None)
    parser.add_argument("--phase", action="store_true", help="Legacy: use summer24 calibration")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--no-phase-timing", action="store_true")
    args = parser.parse_args()

    calibration = args.calibration
    if args.phase and not calibration:
        calibration = "summer24"

    config = build_config(brain="smolagents", model=args.model,
                          calibration=calibration, cpu_only=args.cpu)

    if args.loop:
        run_smolagents_loop(config, use_phase_timing=not args.no_phase_timing)
    else:
        run_smolagents(config, task=args.task)
