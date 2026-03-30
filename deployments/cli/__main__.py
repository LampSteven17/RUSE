"""RUSE Deploy CLI — Python-based deployment orchestrator.

Entry points (via shell scripts in deployments/):
  ./deploy   → python3 -m cli deploy [--ruse|--rampart|--ghosts] [--feedback] ...
  ./teardown → python3 -m cli teardown <target> | --all
  ./list     → python3 -m cli list
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from . import output

# Resolve paths relative to the deployments/ directory
DEPLOY_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = DEPLOY_DIR / "logs"


def _deploy_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="deploy",
        description="Deploy RUSE SUP agents, RAMPART enterprise networks, or GHOSTS NPCs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""unified feedback flag:
  --feedback            deploy with PHASE behavioral feedback (all types)

RUSE-only granular flags (combine any):
  --timing              timing_profile.json
  --workflow            workflow_weights.json
  --modifiers           behavior_modifiers.json
  --sites               site_config.json
  --prompts             prompt_augmentation.json
  --activity            activity_pattern.json
  --diversity           diversity_injection.json
  --variance            variance_injection.json
  --all-feedback        all of the above (same as --feedback)

examples:
  ./deploy --ruse                          baseline controls (no feedback)
  ./deploy --ruse --feedback               all PHASE behavioral configs
  ./deploy --ruse --timing                 timing feedback only
  ./deploy --ruse --timing --workflow      timing + workflow weights
  ./deploy --ruse --all-feedback           all behavioral configs (same as --feedback)
  ./deploy --ruse --feedback --source ~/p  explicit PHASE source
  ./deploy --ghosts --feedback             GHOSTS + PHASE feedback
  ./deploy --rampart                       RAMPART enterprise network
  ./deploy --rampart --feedback            RAMPART + PHASE per-node roles""",
    )
    p.add_argument("--ruse", action="store_true", help="Deploy RUSE SUP agents (default)")
    p.add_argument("--rampart", action="store_true", help="Deploy RAMPART enterprise network")
    p.add_argument("--ghosts", action="store_true", help="Deploy GHOSTS NPC traffic generators")
    p.add_argument("config_name", nargs="?", help="Deployment config name (default: ruse-controls)")

    # Unified feedback flag — works for all deployment types
    p.add_argument("--feedback", action="store_true", help="Deploy with PHASE behavioral feedback")

    # RUSE-only granular config flags — one per config file
    p.add_argument("--timing", action="store_true", help="Include timing_profile.json (RUSE only)")
    p.add_argument("--workflow", action="store_true", help="Include workflow_weights.json (RUSE only)")
    p.add_argument("--modifiers", action="store_true", help="Include behavior_modifiers.json (RUSE only)")
    p.add_argument("--sites", action="store_true", help="Include site_config.json (RUSE only)")
    p.add_argument("--prompts", action="store_true", help="Include prompt_augmentation.json (RUSE only)")
    p.add_argument("--activity", action="store_true", help="Include activity_pattern.json (RUSE only)")
    p.add_argument("--diversity", action="store_true", help="Include diversity_injection.json (RUSE only)")
    p.add_argument("--variance", action="store_true", help="Include variance_injection.json (RUSE only)")
    p.add_argument("--all-feedback", action="store_true", dest="all_configs", help="All behavioral configs (same as --feedback)")

    p.add_argument("--source", type=str, help="Explicit PHASE feedback source directory")
    p.add_argument("--target", type=str, help="Dataset target (e.g., summer24, fall24, spring25)")
    return p


def _teardown_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="teardown", description="Teardown RUSE deployments")
    p.add_argument("target", nargs="?", help="Teardown target: name-MMDDYYHHMMSS")
    p.add_argument("--all", action="store_true", dest="teardown_all", help="Delete ALL RUSE, Enterprise, and GHOSTS VMs")
    return p


def _list_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(prog="list", description="List active RUSE deployments")


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]

    if not argv:
        print("Usage: deploy|teardown|list [options]", file=sys.stderr)
        return 1

    command = argv[0]
    rest = argv[1:]

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        if command == "deploy":
            return _cmd_deploy(rest)
        elif command == "teardown":
            return _cmd_teardown(rest)
        elif command == "list":
            return _cmd_list(rest)
        else:
            print(f"Unknown command: {command}", file=sys.stderr)
            print("Usage: deploy|teardown|list [options]", file=sys.stderr)
            return 1

    except KeyboardInterrupt:
        output.info("\nInterrupted.")
        return 130


    # Flag name → config filename
_CONFIG_FLAGS = {
    "timing":    "timing_profile.json",
    "workflow":  "workflow_weights.json",
    "modifiers": "behavior_modifiers.json",
    "sites":     "site_config.json",
    "prompts":   "prompt_augmentation.json",
    "activity":  "activity_pattern.json",
    "diversity": "diversity_injection.json",
    "variance":  "variance_injection.json",
}


def _cmd_deploy(argv: list[str]) -> int:
    parser = _deploy_parser()
    args = parser.parse_args(argv)

    # --feedback is the unified flag; --all-feedback is a RUSE synonym
    # Build configs_spec from --feedback or individual granular flags
    if args.feedback or args.all_configs:
        configs_spec = "all"
    else:
        selected = [fname for flag, fname in _CONFIG_FLAGS.items() if getattr(args, flag, False)]
        configs_spec = ",".join(selected) if selected else None

    if args.rampart:
        from .commands.rampart import run_rampart_spinup
        from .commands.feedback import resolve_feedback_args
        behavior_source, resolved_configs = resolve_feedback_args(
            configs_spec=configs_spec,
            source=args.source,
            target=args.target,
            deploy_type="rampart",
        )
        return run_rampart_spinup(args.config_name, DEPLOY_DIR, behavior_source, resolved_configs)

    if args.ghosts:
        from .commands.ghosts import run_ghosts_spinup
        from .commands.feedback import resolve_feedback_args
        behavior_source, resolved_configs = resolve_feedback_args(
            configs_spec=configs_spec,
            source=args.source,
            target=args.target,
            deploy_type="ghosts",
        )
        return run_ghosts_spinup(args.config_name, DEPLOY_DIR, behavior_source, resolved_configs)

    # Default: RUSE SUP deployment
    config_name = args.config_name or "ruse-controls"

    from .commands.spinup import run_ruse_spinup
    from .commands.feedback import resolve_feedback_args
    behavior_source, resolved_configs = resolve_feedback_args(
        configs_spec=configs_spec,
        source=args.source,
        target=args.target,
        deploy_type="ruse",  # default deploy type
    )
    return run_ruse_spinup(config_name, DEPLOY_DIR, behavior_source, resolved_configs)


def _cmd_teardown(argv: list[str]) -> int:
    parser = _teardown_parser()
    args = parser.parse_args(argv)

    if args.teardown_all:
        from .commands.teardown import run_teardown_all
        return run_teardown_all(DEPLOY_DIR)

    if not args.target:
        output.error("ERROR: specify a target (name-MMDDYYHHMMSS) or use --all")
        parser.print_help(sys.stderr)
        return 1

    from .commands.teardown import run_teardown
    return run_teardown(args.target, DEPLOY_DIR)


def _cmd_list(argv: list[str]) -> int:
    _list_parser().parse_args(argv)  # just for --help support
    from .commands.list_cmd import run_list
    return run_list(DEPLOY_DIR)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda *_: sys.exit(130))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    sys.exit(main())
