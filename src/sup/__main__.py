"""
SUP (Synthetic User Persona) - Unified CLI

Usage:
    python -m sup --brain <BRAIN> [--content <CONTROLLER>] [--mechanics <CONTROLLER>] [--model <MODEL>] [--phase]
    python -m sup <CONFIG_KEY>
    python -m sup --list

Examples:
    python -m sup --brain mchp                                    # M1: Pure MCHP
    python -m sup --brain browseruse --model llama                # B1.llama
    python -m sup --brain smolagents --model gemma --phase        # S2.gemma+
    python -m sup M1                                              # Shorthand: Pure MCHP
    python -m sup B2.gemma                                        # Shorthand: BrowserUse + gemma
    python -m sup --list                                          # List all configs
"""
import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description="SUP - Synthetic User Persona Agent Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Configuration Keys:
  M1          Pure MCHP (no augmentation)
  M2.llama    MCHP + SmolAgents content/mechanics
  M3.llama    MCHP + BrowserUse content/mechanics
  B1.llama    BrowserUse + llama3.1:8b
  B2.gemma    BrowserUse + gemma3:4b
  B3.deepseek BrowserUse + deepseek-r1:8b
  S1.llama    SmolAgents + llama3.1:8b
  S2.gemma    SmolAgents + gemma3:4b
  S3.deepseek SmolAgents + deepseek-r1:8b
  *+          POST-PHASE with improved prompts (e.g., B1.llama+)
        """
    )

    # Positional argument for config key shorthand
    parser.add_argument(
        "config_key",
        nargs="?",
        default=None,
        help="Configuration key (e.g., M1, B2.gemma, S1.llama+)",
    )

    # Brain selection
    parser.add_argument(
        "--brain",
        choices=["mchp", "browseruse", "smolagents"],
        default=None,
        help="Brain type to use",
    )

    # Augmentation controllers
    parser.add_argument(
        "--content",
        choices=["none", "smolagents", "browseruse"],
        default="none",
        help="Content controller/augmentation (default: none)",
    )
    parser.add_argument(
        "--mechanics",
        choices=["none", "smolagents", "browseruse"],
        default="none",
        help="Mechanics controller/augmentation (default: none)",
    )

    # Model selection
    parser.add_argument(
        "--model",
        choices=["llama", "gemma", "deepseek"],
        default="llama",
        help="LLM model to use (default: llama)",
    )

    # PHASE flag
    parser.add_argument(
        "--phase",
        action="store_true",
        help="Enable PHASE-improved prompts",
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

    # Import runners
    from runners import get_config, build_config, run_mchp, run_browseruse, run_smolagents

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
            mechanics=args.mechanics,
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
    print(f"  Mechanics: {config.mechanics}")
    print(f"  Model: {config.model}")
    print(f"  PHASE: {config.phase}")
    print("-" * 40)

    # Run the appropriate brain
    if config.brain == "mchp":
        run_mchp(config)
    elif config.brain == "browseruse":
        run_browseruse(config, task=args.task)
    elif config.brain == "smolagents":
        run_smolagents(config, task=args.task)
    else:
        print(f"Error: Unknown brain type '{config.brain}'")
        sys.exit(1)


if __name__ == "__main__":
    main()
