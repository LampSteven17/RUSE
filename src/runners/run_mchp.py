"""
Runner for MCHP brain configurations.

Configurations (exp-3):
- M0: Upstream MITRE pyhuman (control - DO NOT MODIFY)
- M1: Pure MCHP (no LLM, no timing)
- M2: MCHP + summer24 calibrated timing
- M3: MCHP + fall24 calibrated timing
- M4: MCHP + spring25 calibrated timing
"""
import os
from datetime import datetime
from runners.run_config import SUPConfig
from common.logging.agent_logger import AgentLogger


def log(msg: str):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] {msg}")


def run_mchp(config: SUPConfig, use_phase_timing: bool = False):
    """
    Run MCHP brain with optional calibrated timing.

    Args:
        config: SUP configuration
        use_phase_timing: Legacy flag, ignored if config.calibration is set.
    """
    # Determine calibration profile (config.calibration takes precedence)
    calibration_profile = config.calibration

    logger = AgentLogger(agent_type=config.config_key)
    logger.session_start(config={
        "brain": config.brain,
        "content": config.content,
        "model": config.model,
        "calibration": calibration_profile,
        "config_key": config.config_key,
        "seed": config.seed
    })

    # Set up environment for LLM-augmented configurations
    if config.content == "llm":
        from common.config.model_config import get_model
        model_name = get_model(config.model)
        os.environ["OLLAMA_MODEL"] = model_name
        os.environ["LITELLM_MODEL"] = f"ollama/{model_name}"
        os.environ["HYBRID_LLM_BACKEND"] = "litellm"
        from augmentations.content import set_logger
        set_logger(logger)

    from brains.mchp import MCHPAgent

    is_augmented = config.content == "llm"

    log(f"Running MCHP agent (config: {config.config_key})")
    log(f"Calibration: {calibration_profile or 'none'}")
    if is_augmented:
        log("Excluding Windows-only workflows (OpenOffice, MSPaint)")
    logger.info(f"Starting MCHP agent", details={
        "config_key": config.config_key,
        "calibration": calibration_profile,
    })

    try:
        agent = MCHPAgent(
            logger=logger,
            calibration_profile=calibration_profile,
            # Legacy compat: fall back to use_phase_timing if no calibration
            use_phase_timing=use_phase_timing if not calibration_profile else False,
            exclude_windows_workflows=is_augmented,
            seed=config.seed,
        )
        agent.run()
        logger.session_success(message="MCHP agent completed successfully")
    except KeyboardInterrupt:
        logger.info("Agent stopped by user (KeyboardInterrupt)")
    except Exception as e:
        logger.session_fail(message="MCHP agent failed", exception=e)
        raise
    finally:
        logger.session_end()


if __name__ == "__main__":
    import argparse
    from runners.run_config import build_config

    parser = argparse.ArgumentParser(description="Run MCHP brain")
    parser.add_argument("--content", choices=["none", "llm"], default="none")
    parser.add_argument("--model", choices=["llama", "gemma", "deepseek", "lfm", "ministral", "qwen"], default="llama")
    parser.add_argument("--calibration", choices=["summer24", "fall24", "spring25"], default=None)
    parser.add_argument("--phase-timing", action="store_true", help="Legacy: use summer24 calibration")
    args = parser.parse_args()

    calibration = args.calibration
    if args.phase_timing and not calibration:
        calibration = "summer24"

    config = build_config(brain="mchp", content=args.content, model=args.model,
                          calibration=calibration)
    run_mchp(config)
