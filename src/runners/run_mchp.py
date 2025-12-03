"""
Runner for MCHP brain configurations.

Configurations:
- M1: Pure MCHP (no augmentation)
- M2.llama: MCHP + SmolAgents content/mechanics
- M2a.llama: MCHP + SmolAgents content only
- M2b.llama: MCHP + SmolAgents mechanics only
- M3.llama: MCHP + BrowserUse content/mechanics
- M3a.llama: MCHP + BrowserUse content only
- M3b.llama: MCHP + BrowserUse mechanics only
"""
import os
from runners.run_config import SUPConfig


def run_mchp(config: SUPConfig):
    """
    Run MCHP brain with optional augmentations.

    For M1 (pure MCHP), runs the original MCHP agent unchanged.
    For M2/M3 configurations, sets up LLM backend for content generation.
    """
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

    # Import and run the MCHP agent
    from brains.mchp import MCHPAgent

    print(f"Running MCHP agent (config: {config.config_key})")

    agent = MCHPAgent()
    agent.run()


if __name__ == "__main__":
    import argparse
    from runners.run_config import build_config

    parser = argparse.ArgumentParser(description="Run MCHP brain")
    parser.add_argument("--content", choices=["none", "smolagents", "browseruse"], default="none")
    parser.add_argument("--mechanics", choices=["none", "smolagents", "browseruse"], default="none")
    parser.add_argument("--model", choices=["llama", "gemma", "deepseek"], default="llama")
    args = parser.parse_args()

    config = build_config(
        brain="mchp",
        content=args.content,
        mechanics=args.mechanics,
        model=args.model,
    )

    run_mchp(config)
