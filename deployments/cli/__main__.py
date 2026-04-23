"""RUSE Deploy CLI — Python-based deployment orchestrator.

Entry points (via shell scripts in deployments/):
  ./deploy   → python3 -m cli deploy [--ruse|--rampart|--ghosts] [--feedback] ...
  ./teardown → python3 -m cli teardown <target> | --all
  ./list     → python3 -m cli list
  ./shrink   → python3 -m cli shrink <target>
  ./audit    → python3 -m cli audit
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

Feedback without --target/--source = batch over every discovered dataset.
Pass --target or --source to deploy a single dataset.

examples:
  ./deploy --ruse                          baseline controls (no feedback)
  ./deploy --ruse --feedback               ALL PHASE feedback datasets (batch)
  ./deploy --ruse --feedback --target sum24  single dataset
  ./deploy --ruse --timing                 ALL datasets, timing-only (batch)
  ./deploy --ruse --timing --target sum24  single dataset, timing-only
  ./deploy --ruse --feedback --source ~/p  explicit PHASE source (single)
  ./deploy --ghosts --feedback             ALL GHOSTS feedback datasets (batch)
  ./deploy --rampart                       RAMPART baseline (no feedback)
  ./deploy --rampart --feedback            ALL RAMPART feedback datasets (batch)""",
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

    p.add_argument("--source", type=str, help="Explicit PHASE feedback source directory (single)")
    p.add_argument("--target", type=str, help="Dataset target, e.g. summer24, fall24, vt-50gb, cptc8 (single)")
    return p


def _teardown_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="teardown",
        description="Teardown RUSE deployments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  ./teardown ruse-controls-040226205037   teardown a specific deployment
  ./teardown --ruse --feedback            teardown all active RUSE feedback deployments
  ./teardown --rampart                    teardown all active RAMPART deployments
  ./teardown --ghosts --feedback          teardown all active GHOSTS feedback deployments
  ./teardown --all                        nuclear: delete ALL VMs""",
    )
    p.add_argument("target", nargs="?", help="Teardown target: name-MMDDYYHHMMSS")
    p.add_argument("--all", action="store_true", dest="teardown_all", help="Delete ALL RUSE, Enterprise, and GHOSTS VMs")

    # Filter flags for batch teardown
    p.add_argument("--ruse", action="store_true", help="Filter: RUSE SUP deployments")
    p.add_argument("--rampart", action="store_true", help="Filter: RAMPART enterprise deployments")
    p.add_argument("--ghosts", action="store_true", help="Filter: GHOSTS NPC deployments")
    p.add_argument("--feedback", action="store_true", help="Filter: only feedback-enabled deployments")
    return p


def _list_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(prog="list", description="List active RUSE deployments")


def _audit_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="audit",
        description="Full health audit of all active RUSE SUP deployments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""checks per VM:
  - SSH reachable
  - SUP systemd service active
  - Brain process running
  - Ollama model loaded (matches expected for behavior)
  - GPU model loaded into VRAM (V100 VMs)
  - Recent log activity (latest jsonl fresh)
  - MCHP maintenance cron entries (M VMs)

cross-deployment:
  - OpenStack vs inventory orphans/missing
  - PHASE experiments.json registration
  - duplicate run_ids

Outputs a terminal summary + markdown report at deployments/logs/audit_*.md""",
    )


def _shrink_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shrink",
        description="Shrink a running deployment in-place to match its top-level config.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""how it works:
  1. Diffs the run's config.yaml snapshot against the top-level config.yaml
  2. Deletes surplus VMs from OpenStack
  3. Cleans up inventory.ini, ssh_config_snippet.txt, ~/.ssh/config block,
     and PHASE experiments.json
  4. Updates the run snapshot to match the desired config

Surviving VMs keep running with their existing behavioral configs —
no reboot, no reinstall.

example:
  # 1. Edit deployments/ruse-controls/config.yaml to remove unwanted VMs
  # 2. Run shrink against the active run
  ./shrink ruse-controls-040226205037""",
    )
    p.add_argument("target", help="Deployment target: name-MMDDYYHHMMSS")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]

    if not argv:
        print("Usage: deploy|teardown|list|shrink|audit [options]", file=sys.stderr)
        return 1

    command = argv[0]
    rest = argv[1:]

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    session_log = output.start_session_log(LOGS_DIR, command)

    try:
        if command == "deploy":
            return _cmd_deploy(rest)
        elif command == "teardown":
            return _cmd_teardown(rest)
        elif command == "list":
            return _cmd_list(rest)
        elif command == "shrink":
            return _cmd_shrink(rest)
        elif command == "audit":
            return _cmd_audit(rest)
        else:
            print(f"Unknown command: {command}", file=sys.stderr)
            print("Usage: deploy|teardown|list|shrink|audit [options]", file=sys.stderr)
            return 1

    except KeyboardInterrupt:
        output.info("\nInterrupted.")
        return 130

    finally:
        output.close_session_log()


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

    # Build configs_spec: granular flags always win when present.
    # --feedback alone = "all", --feedback --timing = timing only,
    # --all-feedback = "all" regardless of granular flags.
    selected = [fname for flag, fname in _CONFIG_FLAGS.items() if getattr(args, flag, False)]
    if selected:
        configs_spec = ",".join(selected)
    elif args.feedback or args.all_configs:
        configs_spec = "all"
    else:
        configs_spec = None

    # Batch-by-default: if feedback is requested but no single-target selector
    # was given (--target, --source, or a positional config_name), deploy
    # every PHASE source the system discovers for this deploy_type. To hit
    # one dataset, pass --target or --source. Previously this required an
    # explicit --batch flag which was easy to forget and led to operators
    # accidentally deploying just the most-recent dir when they wanted all.
    deploy_type = "rampart" if args.rampart else ("ghosts" if args.ghosts else "ruse")
    single_selector = args.target or args.source or args.config_name
    if configs_spec and not single_selector:
        return _cmd_batch_deploy(deploy_type, configs_spec, args.config_name)

    if args.rampart:
        from .commands.rampart import run_rampart_spinup
        from .commands.feedback import resolve_feedback_args
        behavior_source, resolved_configs = resolve_feedback_args(
            configs_spec=configs_spec,
            source=args.source,
            target=args.target,
            deploy_type="rampart",
        )
        if not _confirm_single_feedback(behavior_source, "rampart"):
            return 0
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
        if not _confirm_single_feedback(behavior_source, "ghosts"):
            return 0
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
    if not _confirm_single_feedback(behavior_source, "ruse"):
        return 0
    return run_ruse_spinup(config_name, DEPLOY_DIR, behavior_source, resolved_configs)


def _confirm_single_feedback(behavior_source: str | None, deploy_type: str) -> bool:
    """Show manifest details + y/N prompt before a single feedback deploy.

    Baseline (no-feedback) deploys pass behavior_source=None and skip the
    prompt entirely — controls never needed a confirmation. Returns True
    when the deploy should proceed (either no feedback, or user said yes).
    Returns False on target mismatch (hard fail) or user decline.
    """
    if not behavior_source:
        return True  # baseline, nothing to confirm
    from pathlib import Path as _Path
    from .commands.feedback import (
        load_manifest, manifest_summary_lines, validate_manifest_target,
    )
    src_path = _Path(behavior_source)
    mf = load_manifest(src_path)
    err = validate_manifest_target(mf, deploy_type)
    output.banner("PHASE feedback manifest")
    output.info("")
    for line in manifest_summary_lines(src_path, mf):
        output.info(line)
    output.info("")
    if err:
        output.error(f"  FAIL: {err}")
        output.error("Aborting: manifest target does not match deploy type.")
        return False
    if not output.confirm("Proceed with deploy?"):
        output.info("Cancelled.")
        return False
    return True


def _cmd_batch_deploy(deploy_type: str, configs_spec: str, config_name: str | None) -> int:
    """Deploy all available PHASE feedback configs for the given type."""
    from .commands.feedback import (
        find_all_feedback_sources, load_manifest, manifest_summary_lines,
        validate_manifest_target,
    )

    sources = find_all_feedback_sources(deploy_type)
    if not sources:
        output.error(f"ERROR: No PHASE feedback configs found for '{deploy_type}'")
        output.info(f"  Searched: /mnt/AXES2U1/feedback/{deploy_type}-controls/")
        return 1

    # Show what will be deployed, with manifest details per source so the
    # operator can confirm freshness + target + preset in one prompt.
    output.banner(f"BATCH DEPLOY ({deploy_type}, {configs_spec})")
    output.info("")
    any_mismatch = False
    for i, src in enumerate(sources, 1):
        mf = load_manifest(src["path"])
        err = validate_manifest_target(mf, deploy_type)
        output.info(f"  {i}. {src['dataset']}  (preset: {src['preset']})")
        for line in manifest_summary_lines(src["path"], mf, indent="      "):
            output.info(line)
        if err:
            output.error(f"      FAIL: {err}")
            any_mismatch = True
        output.info("")

    if any_mismatch:
        output.error("Aborting batch: one or more manifests don't match deploy type.")
        return 1

    if not output.confirm(f"Deploy these {len(sources)} feedback config(s)?"):
        output.info("Cancelled.")
        return 0

    # Select the right spinup function and default config
    if deploy_type == "rampart":
        from .commands.rampart import run_rampart_spinup as spinup
        default_config = "rampart-controls"
    elif deploy_type == "ghosts":
        from .commands.ghosts import run_ghosts_spinup as spinup
        default_config = "ghosts-controls"
    else:
        from .commands.spinup import run_ruse_spinup as spinup
        default_config = "ruse-controls"

    base_config = config_name or default_config

    # Deploy each feedback source
    failed = 0
    results: list[tuple[str, int]] = []
    for i, src in enumerate(sources, 1):
        output.info("")
        output.info(f"[{i}/{len(sources)}] Deploying {src['dataset']} ({src['preset']})...")
        try:
            rc = spinup(base_config, DEPLOY_DIR, str(src["path"]), configs_spec)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
        except Exception as e:
            output.error(f"  ERROR: {e}")
            rc = 1

        results.append((src["dataset"], rc))
        if rc != 0:
            output.error(f"  FAILED: {src['dataset']} (rc={rc})")
            failed += 1

    # Final summary
    output.info("")
    output.banner("BATCH DEPLOY SUMMARY")
    for dataset, rc in results:
        status = "OK" if rc == 0 else f"FAILED (rc={rc})"
        output.info(f"  {dataset}: {status}")
    output.info("")

    if failed:
        output.info(f"DONE: {len(sources) - failed}/{len(sources)} succeeded, {failed} failed")
        return 1

    output.info(f"DONE: all {len(sources)} deployment(s) launched")
    return 0


def _cmd_teardown(argv: list[str]) -> int:
    parser = _teardown_parser()
    args = parser.parse_args(argv)

    if args.teardown_all:
        from .commands.teardown import run_teardown_all
        return run_teardown_all(DEPLOY_DIR)

    has_filter = args.ruse or args.rampart or args.ghosts or args.feedback
    if has_filter:
        from .commands.teardown import run_teardown_filtered
        return run_teardown_filtered(
            DEPLOY_DIR,
            types={"ruse": args.ruse, "rampart": args.rampart, "ghosts": args.ghosts},
            feedback_only=args.feedback,
        )

    if not args.target:
        output.error("ERROR: specify a target (name-MMDDYYHHMMSS), use filter flags, or use --all")
        parser.print_help(sys.stderr)
        return 1

    from .commands.teardown import run_teardown
    return run_teardown(args.target, DEPLOY_DIR)


def _cmd_list(argv: list[str]) -> int:
    _list_parser().parse_args(argv)  # just for --help support
    from .commands.list_cmd import run_list
    return run_list(DEPLOY_DIR)


def _cmd_shrink(argv: list[str]) -> int:
    parser = _shrink_parser()
    args = parser.parse_args(argv)
    from .commands.shrink import run_shrink
    return run_shrink(args.target, DEPLOY_DIR)


def _cmd_audit(argv: list[str]) -> int:
    _audit_parser().parse_args(argv)  # just for --help
    from .commands.audit import run_audit
    return run_audit(DEPLOY_DIR)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda *_: sys.exit(130))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    sys.exit(main())
