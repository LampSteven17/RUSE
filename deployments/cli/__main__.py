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
        epilog="""default behavior (no scope flags):
  Deploys BOTH controls AND every discovered PHASE feedback dataset for the
  chosen type. Use --controls or --feedback to narrow.

scope flags:
  --controls            deploy baseline controls only
  --feedback            deploy feedback variants (all, or --target/--source one)

RUSE-only granular flags (combine any; each implies --feedback):
  --timing --workflow --modifiers --sites --prompts
  --activity --diversity --variance
  --all-feedback        all of the above (same as --feedback)

Feedback without --target/--source = batch every discovered dataset.
Pass --target or --source to deploy a single dataset.

examples:
  ./deploy --ruse                          controls + ALL feedback datasets
  ./deploy --ruse --controls               baseline controls only
  ./deploy --ruse --feedback               ALL feedback datasets (no controls)
  ./deploy --ruse --feedback --target sum24  single dataset (no controls)
  ./deploy --ruse --controls --feedback    controls + ALL feedback (explicit)
  ./deploy --ruse --controls --target sum24  controls + single feedback
  ./deploy --ghosts                        controls + ALL GHOSTS feedback
  ./deploy --rampart --controls            RAMPART baseline only""",
    )
    p.add_argument("--ruse", action="store_true", help="Deploy RUSE SUP agents (default)")
    p.add_argument("--rampart", action="store_true", help="Deploy RAMPART enterprise network")
    p.add_argument("--ghosts", action="store_true", help="Deploy GHOSTS NPC traffic generators")
    p.add_argument("config_name", nargs="?", help="Deployment config name (default: {type}-controls)")

    # Scope flags — opt into just controls, just feedback, or (default) both.
    p.add_argument("--controls", action="store_true", help="Deploy baseline controls (no feedback)")
    p.add_argument("--feedback", action="store_true", help="Deploy PHASE feedback variants")

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

    # --- Resolve deploy type and configs_spec ---
    deploy_type = "rampart" if args.rampart else ("ghosts" if args.ghosts else "ruse")

    # Granular flags always win; bare --feedback / --all-feedback = "all".
    selected = [fname for flag, fname in _CONFIG_FLAGS.items() if getattr(args, flag, False)]
    if selected:
        configs_spec = ",".join(selected)
    elif args.feedback or args.all_configs:
        configs_spec = "all"
    else:
        configs_spec = None

    # --- Resolve intent: controls? feedback? ---
    # --target / --source imply feedback (harmless shorthand).
    explicit_feedback = bool(configs_spec) or bool(args.source) or bool(args.target)
    explicit_controls = args.controls
    single_selector = args.target or args.source

    # Default (neither flag specified): deploy BOTH controls and all feedback.
    # This matches the "full experimental run" mental model and is the new
    # behavior as of 2026-04-23. Use --controls or --feedback to narrow.
    if not explicit_feedback and not explicit_controls:
        want_controls = True
        want_feedback = True
        configs_spec = "all"
    else:
        want_controls = explicit_controls
        want_feedback = explicit_feedback
        if want_feedback and not configs_spec:
            configs_spec = "all"

    # --- Build plan: list of (label, behavior_source, configs_spec) tasks ---
    plan = _build_deploy_plan(
        deploy_type=deploy_type,
        config_name=args.config_name,
        want_controls=want_controls,
        want_feedback=want_feedback,
        configs_spec=configs_spec,
        single_selector=single_selector,
        target=args.target,
        source=args.source,
    )
    if plan is None:
        return 1
    if not plan:
        output.error("Nothing to deploy. Use --controls and/or --feedback.")
        return 1

    # --- Show plan + single confirm prompt ---
    if not _show_plan_and_confirm(plan, deploy_type):
        return 0

    # --- Execute ---
    return _execute_plan(plan, deploy_type, args.config_name)


def _build_deploy_plan(
    deploy_type: str,
    config_name: str | None,
    want_controls: bool,
    want_feedback: bool,
    configs_spec: str | None,
    single_selector: str | None,
    target: str | None,
    source: str | None,
) -> list[dict] | None:
    """Resolve controls + feedback intent into an ordered list of deploy tasks.

    Each task is a dict: {label, behavior_source (Path|None), configs_spec,
    manifest (dict|None), is_controls (bool)}.

    Returns None on hard failure (e.g. feedback requested but source can't
    be resolved). Returns empty list if nothing to do.
    """
    from .commands.feedback import (
        find_all_feedback_sources, find_feedback_by_target, load_manifest,
    )

    plan: list[dict] = []

    if want_controls:
        plan.append({
            "label": f"{deploy_type}-controls (baseline)",
            "behavior_source": None,
            "configs_spec": None,
            "manifest": None,
            "is_controls": True,
        })

    if want_feedback:
        sources: list[dict] = []
        if single_selector:
            # Resolve single selector to one source
            if source:
                from pathlib import Path as _Path
                src_path = _Path(source)
                if not src_path.is_dir():
                    output.error(f"ERROR: --source not a directory: {src_path}")
                    return None
                sources = [{"path": src_path, "dataset": src_path.name, "preset": "?"}]
            elif target:
                src_path = find_feedback_by_target(target, deploy_type=deploy_type)
                if not src_path:
                    output.error(f"ERROR: No feedback source for target '{target}' "
                                 f"under /mnt/AXES2U1/feedback/{deploy_type}-controls/")
                    return None
                sources = [{"path": src_path, "dataset": src_path.name, "preset": "?"}]
        else:
            sources = find_all_feedback_sources(deploy_type)
            if not sources:
                output.error(f"ERROR: No PHASE feedback configs found for '{deploy_type}'")
                output.info(f"  Searched: /mnt/AXES2U1/feedback/{deploy_type}-controls/")
                return None

        for src in sources:
            mf = load_manifest(src["path"])
            plan.append({
                "label": f"{deploy_type}-feedback: {src['dataset']}",
                "behavior_source": src["path"],
                "configs_spec": configs_spec,
                "manifest": mf,
                "is_controls": False,
            })

    return plan


def _show_plan_and_confirm(plan: list[dict], deploy_type: str) -> bool:
    """Render the combined plan (controls + feedback manifests) and ask y/N.

    Fails loud (returns False) if any feedback task's manifest.target
    doesn't match deploy_type. Skips the prompt when the plan is a single
    controls task — no ambiguity there.
    """
    from .commands.feedback import manifest_summary_lines, validate_manifest_target

    n = len(plan)
    output.banner(f"DEPLOY PLAN ({deploy_type}, {n} task{'s' if n != 1 else ''})")
    output.info("")

    any_mismatch = False
    for i, task in enumerate(plan, 1):
        output.info(f"  {i}. {task['label']}")
        if task["is_controls"]:
            output.info(f"      (baseline controls — no PHASE feedback)")
        else:
            mf = task["manifest"]
            err = validate_manifest_target(mf, deploy_type)
            for line in manifest_summary_lines(
                task["behavior_source"], mf, indent="      ",
            ):
                output.info(line)
            if err:
                output.error(f"      FAIL: {err}")
                any_mismatch = True
        output.info("")

    if any_mismatch:
        output.error("Aborting: one or more manifests don't match deploy type.")
        return False

    # Don't prompt for a single controls-only deploy — legacy behavior and
    # there's nothing to confirm beyond the banner.
    if n == 1 and plan[0]["is_controls"]:
        return True

    if not output.confirm(f"Deploy these {n} config(s)?"):
        output.info("Cancelled.")
        return False
    return True


def _execute_plan(
    plan: list[dict], deploy_type: str, config_name: str | None,
) -> int:
    """Run each task in the plan sequentially. Returns 0 iff all succeed."""
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
    results: list[tuple[str, int]] = []

    for i, task in enumerate(plan, 1):
        output.info("")
        output.info(f"[{i}/{len(plan)}] Deploying {task['label']}...")
        src = task["behavior_source"]
        try:
            rc = spinup(
                base_config, DEPLOY_DIR,
                str(src) if src else None,
                task["configs_spec"],
            )
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
        except Exception as e:
            output.error(f"  ERROR: {e}")
            rc = 1
        results.append((task["label"], rc))
        if rc != 0:
            output.error(f"  FAILED: {task['label']} (rc={rc})")

    if len(results) > 1:
        output.info("")
        output.banner("DEPLOY SUMMARY")
        for label, rc in results:
            status = "OK" if rc == 0 else f"FAILED (rc={rc})"
            output.info(f"  {label}: {status}")
        output.info("")

    failed = sum(1 for _, rc in results if rc != 0)
    if failed:
        output.info(f"DONE: {len(results) - failed}/{len(results)} succeeded, {failed} failed")
        return 1
    output.info(f"DONE: all {len(results)} deployment(s) launched")
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
