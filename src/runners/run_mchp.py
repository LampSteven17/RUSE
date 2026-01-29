"""
Runner for MCHP brain configurations.

Baseline Configurations:
- M0: Upstream MITRE pyhuman (control - DO NOT MODIFY)
- M1: Pure MCHP (no augmentation, original timing)
- M2.llama: MCHP + SmolAgents content/mechanics
- M2a.llama: MCHP + SmolAgents content only
- M2b.llama: MCHP + SmolAgents mechanics only
- M3.llama: MCHP + BrowserUse content/mechanics
- M3a.llama: MCHP + BrowserUse content only
- M3b.llama: MCHP + BrowserUse mechanics only

PHASE Timing Configurations (M1-M3 with time-of-day awareness):
- Use --phase-timing flag to enable circadian rhythm patterns
- Reduces activity 2AM-6AM, peaks 10AM-5PM
- NOT for M0 (control must remain unchanged)
"""
import os
from datetime import datetime
from runners.run_config import SUPConfig
from common.logging.agent_logger import AgentLogger


def log(msg: str):
    """Print with timestamp."""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] {msg}")


def run_mchp(config: SUPConfig, use_phase_timing: bool = False):
    """
    Run MCHP brain with optional augmentations.

    For M1 (pure MCHP), runs the original MCHP agent unchanged.
    For M2/M3 configurations, sets up LLM backend for content generation.

    Args:
        config: SUP configuration
        use_phase_timing: Enable PHASE timing with time-of-day awareness
    """
    # Initialize structured logger
    logger = AgentLogger(agent_type=config.config_key)
    logger.session_start(config={
        "brain": config.brain,
        "content": config.content,
        "mechanics": config.mechanics,
        "model": config.model,
        "phase": config.phase,
        "phase_timing": use_phase_timing,
        "config_key": config.config_key
    })

    # Set up environment for augmented configurations
    if config.content != "none" or config.mechanics != "none":
        # Determine which backend to use
        if config.content == "smolagents" or config.mechanics == "smolagents":
            os.environ["HYBRID_LLM_BACKEND"] = "smol"
        elif config.content == "browseruse" or config.mechanics == "browseruse":
            os.environ["HYBRID_LLM_BACKEND"] = "bu"

        # Set model
        from common.config.model_config import get_model
        model_name = get_model(config.model)
        os.environ["OLLAMA_MODEL"] = model_name
        os.environ["LITELLM_MODEL"] = f"ollama/{model_name}"

        # Set logger for augmentations (LLM content generation)
        from augmentations.content import set_logger
        set_logger(logger)

    # Import and run the MCHP agent
    from brains.mchp import MCHPAgent

    # Exclude Windows-only workflows for augmented configs (M2+)
    # These use os.startfile() which only works on Windows
    is_augmented = config.content != "none" or config.mechanics != "none"

    log(f"Running MCHP agent (config: {config.config_key})")
    log(f"PHASE timing: {use_phase_timing}")
    if is_augmented:
        log("Excluding Windows-only workflows (OpenOffice, MSPaint)")
    logger.info(f"Starting MCHP agent", details={
        "config_key": config.config_key,
        "phase_timing": use_phase_timing
    })

    try:
        agent = MCHPAgent(
            logger=logger,
            use_phase_timing=use_phase_timing,
            exclude_windows_workflows=is_augmented
        )
        agent.run()
        logger.session_success(message="MCHP agent completed successfully")
    except KeyboardInterrupt:
        logger.info("Agent stopped by user (KeyboardInterrupt)")
        # No session_fail - interruption is not failure
    except Exception as e:
        logger.session_fail(message="MCHP agent failed", exception=e)
        raise
    finally:
        logger.session_end()


if __name__ == "__main__":
    import argparse
    from runners.run_config import build_config

    parser = argparse.ArgumentParser(description="Run MCHP brain")
    parser.add_argument("--content", choices=["none", "smolagents", "browseruse"], default="none")
    parser.add_argument("--mechanics", choices=["none", "smolagents", "browseruse"], default="none")
    parser.add_argument("--model", choices=["llama", "gemma", "deepseek"], default="llama")
    parser.add_argument("--phase-timing", action="store_true",
                        help="Enable PHASE timing with time-of-day awareness (for M1-M3)")
    args = parser.parse_args()

    config = build_config(
        brain="mchp",
        content=args.content,
        mechanics=args.mechanics,
        model=args.model,
        phase=args.phase_timing,  # Pass phase_timing to get correct config key (M2 vs M4, M3 vs M5)
    )

    run_mchp(config, use_phase_timing=args.phase_timing)
