"""RUSE Deploy CLI — Python-based deployment orchestrator.

Entry points (via shell scripts at RUSE/ root):
  ./deploy   → python3 -m deployment_engine deploy [--decoy|--rampart|--ghosts] [--feedback] ...
  ./teardown → python3 -m deployment_engine teardown <target> | --all
  ./list     → python3 -m deployment_engine list
  ./shrink   → python3 -m deployment_engine shrink <target>
  ./audit    → python3 -m deployment_engine audit [--decoy|--rampart|--ghosts]

Layout (post 2026-05-08 restructure):
  deployment_engine/        ← code (this package)
    core/                   ← shared utilities (output, config, openstack,
                              ansible_runner, ssh_config, vm_naming,
                              feedback, teardown_steps, deploy_steps,
                              register_experiment, enterprise_ssh_config)
    decoy/  rampart/  ghosts/   ← per-type spinup, teardown, audit
    teardown.py / list.py / shrink.py    ← thin top-level routers
    playbooks/              ← Ansible YAMLs

  deployments/              ← state only (no code)
    {type}-controls/        ← per-deploy config + runs/
    {type}-feedback-*/
    hosts.ini, ansible.cfg, catalog.yaml, logs/
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from .core import output

# ENGINE_DIR = where this code + playbooks live (RUSE/deployment_engine).
# DEPLOY_DIR = where per-deploy state lives (RUSE/deployments/), set
# below from ENGINE_DIR.parent. Two distinct roots so code and state are
# decoupled.
ENGINE_DIR = Path(__file__).resolve().parent
DEPLOY_DIR = ENGINE_DIR.parent / "deployments"
LOGS_DIR = DEPLOY_DIR / "logs"


def _deploy_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="deploy",
        description="Deploy DECOY SUP agents, RAMPART enterprise networks, or GHOSTS NPCs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""default behavior (no scope flags):
  Deploys BOTH controls AND every discovered PHASE feedback dataset for the
  chosen type. Use --controls or --feedback to narrow.

scope flags:
  --controls            deploy baseline controls only
  --feedback            deploy feedback variants (all, or --target/--source one)

Feedback without --target/--source = batch every discovered dataset.
Pass --target or --source to deploy a single dataset.

examples:
  ./deploy --decoy                          controls + ALL feedback datasets
  ./deploy --decoy --controls               baseline controls only
  ./deploy --decoy --feedback               ALL feedback datasets (no controls)
  ./deploy --decoy --feedback --target sum24  single dataset (no controls)
  ./deploy --decoy --controls --feedback    controls + ALL feedback (explicit)
  ./deploy --decoy --controls --target sum24  controls + single feedback
  ./deploy --ghosts                         controls + ALL GHOSTS feedback
  ./deploy --rampart --controls             RAMPART baseline only""",
    )
    p.add_argument("--decoy", action="store_true", help="Deploy DECOY SUP agents (default)")
    p.add_argument("--rampart", action="store_true", help="Deploy RAMPART enterprise network")
    p.add_argument("--ghosts", action="store_true", help="Deploy GHOSTS NPC traffic generators")
    p.add_argument("config_name", nargs="?", help="Deployment config name (default: {type}-controls)")

    # Scope flags — opt into just controls, just feedback, or (default) both.
    p.add_argument("--controls", action="store_true", help="Deploy baseline controls (no feedback)")
    p.add_argument("--feedback", action="store_true", help="Deploy PHASE feedback variants")

    p.add_argument("--source", type=str, help="Explicit PHASE feedback source directory (single)")
    p.add_argument("--target", type=str, help="Dataset target, e.g. summer24, fall24, vt-50gb, cptc8 (single)")
    return p


def _teardown_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="teardown",
        description="Teardown deployments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  ./teardown decoy-controls-040226205037   teardown a specific deployment
  ./teardown --decoy --feedback            teardown all active DECOY feedback deployments
  ./teardown --rampart                     teardown all active RAMPART deployments
  ./teardown --ghosts --feedback           teardown all active GHOSTS feedback deployments
  ./teardown --all                         nuclear: delete ALL VMs""",
    )
    p.add_argument("target", nargs="?", help="Teardown target: name-MMDDYYHHMMSS")
    p.add_argument("--all", action="store_true", dest="teardown_all", help="Delete ALL DECOY, Enterprise, and GHOSTS VMs")

    # Filter flags for batch teardown
    p.add_argument("--decoy", action="store_true", help="Filter: DECOY SUP deployments")
    p.add_argument("--rampart", action="store_true", help="Filter: RAMPART enterprise deployments")
    p.add_argument("--ghosts", action="store_true", help="Filter: GHOSTS NPC deployments")
    p.add_argument("--feedback", action="store_true", help="Filter: only feedback-enabled deployments")
    return p


def _list_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(prog="list", description="List active deployments")


def _audit_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="audit",
        description="Health audit of active deployments (DECOY by default)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""subsystem flags (mutually exclusive; default --decoy):
  --decoy     audit DECOY SUP deployments (default)
  --rampart   audit RAMPART enterprise deployments (not yet implemented)
  --ghosts    audit GHOSTS NPC deployments (not yet implemented)

DECOY checks per VM:
  - SSH reachable
  - SUP systemd service active + NRestarts probe
  - Brain process running
  - Ollama model loaded (matches expected for behavior)
  - GPU model loaded into VRAM (V100 VMs)
  - Recent log activity (latest jsonl fresh)
  - MCHP maintenance cron entries (M VMs)
  - behavior.json present + window-mode contract (FEEDBACK / CONTROLS / FATAL)
  - Volume — median bg-conn/min during ON-windows vs target

cross-deployment:
  - OpenStack vs inventory orphans/missing
  - PHASE experiments.json registration
  - duplicate run_ids
  - orphan boot volumes

Outputs a terminal summary + markdown report at deployments/logs/audit_*.md""",
    )
    p.add_argument("--decoy", action="store_true", help="Audit DECOY deployments (default)")
    p.add_argument("--rampart", action="store_true", help="Audit RAMPART deployments (not yet implemented)")
    p.add_argument("--ghosts", action="store_true", help="Audit GHOSTS deployments (not yet implemented)")
    return p


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
  # 1. Edit deployments/decoy-controls/config.yaml to remove unwanted VMs
  # 2. Run shrink against the active run
  ./shrink decoy-controls-040226205037""",
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


def _cmd_deploy(argv: list[str]) -> int:
    parser = _deploy_parser()
    args = parser.parse_args(argv)

    # --- Resolve deploy type ---
    deploy_type = "rampart" if args.rampart else ("ghosts" if args.ghosts else "decoy")

    # PHASE consolidated to a single behavior.json per SUP — there are no
    # per-config-file knobs to filter on the deploy side anymore. configs_spec
    # is plumbed through to the distribute playbook for legacy reasons; "all"
    # means "copy *.json" (i.e. behavior.json), None means controls-only path.
    configs_spec = "all" if args.feedback else None

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
    from .core.feedback import (
        find_all_feedback_sources, find_feedback_by_target, load_manifest,
    )
    from .core.config import DeploymentConfig
    from pathlib import Path as _Path

    plan: list[dict] = []

    if want_controls:
        # Pull controls' PHASE source from the deployment's config.yaml so
        # the plan render can show date + location (same shape as feedback).
        # Post 2026-05-08 the controls/ slot is PHASE-emitted with its own
        # manifest.json + per-SUP behavior.json (mode=controls).
        controls_cfg_path = (DEPLOY_DIR / f"{deploy_type}-controls"
                             / "config.yaml")
        controls_source: _Path | None = None
        controls_manifest = None
        if controls_cfg_path.exists():
            try:
                cfg = DeploymentConfig.load(controls_cfg_path)
                if cfg.behavior_source:
                    controls_source = _Path(cfg.behavior_source)
                    controls_manifest = load_manifest(controls_source)
            except Exception:
                # Don't block the plan on a malformed config — render the
                # legacy "baseline controls — no PHASE feedback" line below.
                pass
        plan.append({
            "label": f"{deploy_type}-controls (baseline)",
            "behavior_source": controls_source,
            "configs_spec": None,
            "manifest": controls_manifest,
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
    from .core.feedback import manifest_summary_lines, validate_manifest_target

    n = len(plan)
    output.banner(f"DEPLOY PLAN ({deploy_type}, {n} task{'s' if n != 1 else ''})")
    output.info("")

    any_mismatch = False
    for i, task in enumerate(plan, 1):
        output.info(f"  {i}. {task['label']}")
        if task["is_controls"]:
            # Controls now ship a PHASE-emitted manifest (post 2026-05-08).
            # Render the same source/date/sup_runs summary as feedback when
            # available; fall back to the legacy line only if the controls
            # slot hasn't been generated yet.
            src = task.get("behavior_source")
            mf = task.get("manifest")
            if src is None:
                output.info(f"      (baseline controls — no PHASE feedback)")
            else:
                for line in manifest_summary_lines(src, mf, indent="      "):
                    output.info(line)
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
        from .rampart.spinup import run_rampart_spinup as spinup
        default_config = "rampart-controls"
    elif deploy_type == "ghosts":
        from .ghosts.spinup import run_ghosts_spinup as spinup
        default_config = "ghosts-controls"
    else:
        from .decoy.spinup import run_decoy_spinup as spinup
        default_config = "decoy-controls"

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
        from .teardown import run_teardown_all
        return run_teardown_all(DEPLOY_DIR)

    has_filter = args.decoy or args.rampart or args.ghosts or args.feedback
    if has_filter:
        from .teardown import run_teardown_filtered
        return run_teardown_filtered(
            DEPLOY_DIR,
            types={"decoy": args.decoy, "rampart": args.rampart, "ghosts": args.ghosts},
            feedback_only=args.feedback,
        )

    if not args.target:
        output.error("ERROR: specify a target (name-MMDDYYHHMMSS), use filter flags, or use --all")
        parser.print_help(sys.stderr)
        return 1

    from .teardown import run_teardown
    return run_teardown(args.target, DEPLOY_DIR)


def _cmd_list(argv: list[str]) -> int:
    _list_parser().parse_args(argv)  # just for --help support
    from .list import run_list
    return run_list(DEPLOY_DIR)


def _cmd_shrink(argv: list[str]) -> int:
    parser = _shrink_parser()
    args = parser.parse_args(argv)
    from .shrink import run_shrink
    return run_shrink(args.target, DEPLOY_DIR)


def _cmd_audit(argv: list[str]) -> int:
    args = _audit_parser().parse_args(argv)

    # Mutual exclusion. Default decoy when none specified.
    flags = sum(1 for f in (args.decoy, args.rampart, args.ghosts) if f)
    if flags > 1:
        output.error("Pass at most one of --decoy / --rampart / --ghosts")
        return 1

    if args.rampart:
        from .rampart.audit import run_rampart_audit
        return run_rampart_audit(DEPLOY_DIR)
    if args.ghosts:
        from .ghosts.audit import run_ghosts_audit
        return run_ghosts_audit(DEPLOY_DIR)

    # Default: --decoy
    from .decoy.audit import run_audit
    return run_audit(DEPLOY_DIR)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda *_: sys.exit(130))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    sys.exit(main())
