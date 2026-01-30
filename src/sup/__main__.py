"""
SUP (Synthetic User Persona) - Unified CLI

Usage:
    python -m sup --brain <BRAIN> [--content <TYPE>] [--model <MODEL>] [--phase]
    python -m sup <CONFIG_KEY>
    python -m sup --list

Examples:
    python -m sup --brain mchp                          # M1: Pure MCHP
    python -m sup --brain mchp --content llm --model llama  # M1a.llama
    python -m sup --brain browseruse --model gemma      # B1b.gemma
    python -m sup --brain smolagents --model llama --phase  # S2a.llama
    python -m sup M1                                    # Shorthand: Pure MCHP
    python -m sup B1b.gemma                             # Shorthand: BrowserUse + gemma
    python -m sup --list                                # List all configs
"""
import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description="SUP - Synthetic User Persona Agent Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Configuration Keys:
  M0          Upstream MITRE pyhuman (control - DO NOT MODIFY)
  M1          Pure MCHP (no LLM augmentation)
  M1a.llama   MCHP + llama3.1:8b content
  M1b.gemma   MCHP + gemma3:4b content
  M1c.deepseek MCHP + deepseek-r1:8b content
  M2a.llama   MCHP + llama + PHASE timing
  B1a.llama   BrowserUse + llama3.1:8b
  B1b.gemma   BrowserUse + gemma3:4b
  B1c.deepseek BrowserUse + deepseek-r1:8b
  B2a.llama   BrowserUse + llama + PHASE timing
  S1a.llama   SmolAgents + llama3.1:8b
  S1b.gemma   SmolAgents + gemma3:4b
  S1c.deepseek SmolAgents + deepseek-r1:8b
  S2a.llama   SmolAgents + llama + PHASE timing
        """
    )

    # Positional argument for config key shorthand
    parser.add_argument(
        "config_key",
        nargs="?",
        default=None,
        help="Configuration key (e.g., M1, M1a.llama, B1b.gemma)",
    )

    # Brain selection
    parser.add_argument(
        "--brain",
        choices=["mchp", "browseruse", "smolagents"],
        default=None,
        help="Brain type to use",
    )

    # Content augmentation (only for MCHP)
    parser.add_argument(
        "--content",
        choices=["none", "llm"],
        default="none",
        help="Content augmentation for MCHP (none=TextLorem, llm=LLM-generated)",
    )

    # Model selection
    parser.add_argument(
        "--model",
        choices=["llama", "gemma", "deepseek", "lfm", "ministral", "qwen"],
        default="llama",
        help="LLM model to use (default: llama)",
    )

    # PHASE flag
    parser.add_argument(
        "--phase",
        action="store_true",
        help="Enable PHASE timing (time-of-day aware activity patterns)",
    )

    # Task (for BrowserUse/SmolAgents)
    parser.add_argument(
        "--task",
        type=str,
        default=None,
        help="Task to perform (for browseruse/smolagents brains)",
    )

    # Utility flags
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available configuration keys",
    )

    args = parser.parse_args()

    # Handle --list
    if args.list:
        from runners import list_configs
        print("Available configuration keys:")
        print("-" * 40)
        for key in sorted(list_configs()):
            print(f"  {key}")
        return

    # Import config helpers
    from runners import get_config, build_config

    # Determine configuration
    config = None

    if args.config_key:
        # Use shorthand config key
        config = get_config(args.config_key)
        if config is None:
            print(f"Error: Unknown configuration key '{args.config_key}'")
            print("Use --list to see available configurations")
            sys.exit(1)
    elif args.brain:
        # Build config from arguments
        config = build_config(
            brain=args.brain,
            content=args.content,
            model=args.model,
            phase=args.phase,
        )
    else:
        # Default to M1
        print("No configuration specified, defaulting to M1 (pure MCHP)")
        config = get_config("M1")

    print(f"Configuration: {config.config_key}")
    print(f"  Brain: {config.brain}")
    print(f"  Content: {config.content}")
    print(f"  Model: {config.model}")
    print(f"  PHASE: {config.phase}")
    print("-" * 40)

    # Run the appropriate brain (import only what's needed)
    if config.brain == "mchp":
        from runners.run_mchp import run_mchp
        run_mchp(config)
    elif config.brain == "browseruse":
        from runners.run_browseruse import run_browseruse
        run_browseruse(config, task=args.task)
    elif config.brain == "smolagents":
        from runners.run_smolagents import run_smolagents
        run_smolagents(config, task=args.task)
    else:
        print(f"Error: Unknown brain type '{config.brain}'")
        sys.exit(1)


if __name__ == "__main__":
    main()
