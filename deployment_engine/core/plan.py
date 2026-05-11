"""Deploy plan: build, render, execute.

A "plan" is a list of deploy tasks. For a typical `./deploy --decoy` it's
[controls task, feedback task per discovered dataset]. The plan is built
from CLI flags + filesystem discovery, rendered to the user with a
manifest summary per task, confirmed, then executed sequentially.

Per-type spinup functions are imported lazily inside execute_plan so this
module doesn't pull in all three subsystems just to build a plan.

Each task is a dict:
    {
        "label":           human-readable string,
        "behavior_source": Path | None,
        "configs_spec":    str | None,         # legacy ansible filter
        "manifest":        dict | None,
        "is_controls":     bool,
    }
"""

from __future__ import annotations

from pathlib import Path

from . import output
from .config import DeploymentConfig
from .feedback import (
    find_all_feedback_sources,
    find_feedback_by_target,
    load_manifest,
    manifest_summary_lines,
    validate_manifest_target,
)


def build_deploy_plan(
    deploy_type: str,
    *,
    want_controls: bool,
    want_feedback: bool,
    configs_spec: str | None,
    single_selector: str | None,
    target: str | None,
    source: str | None,
    deploy_dir: Path,
) -> list[dict] | None:
    """Resolve controls + feedback intent into an ordered list of deploy tasks.

    Returns None on hard failure. Empty list = nothing to do.
    """
    plan: list[dict] = []

    if want_controls:
        plan.append(_build_controls_task(deploy_type, deploy_dir))

    if want_feedback:
        feedback_tasks = _build_feedback_tasks(
            deploy_type, configs_spec, single_selector, target, source,
        )
        if feedback_tasks is None:
            return None
        plan.extend(feedback_tasks)

    return plan


def _build_controls_task(deploy_type: str, deploy_dir: Path) -> dict:
    """Build the controls task. Reads behavior_source from the deployment's
    config.yaml. Fails loud — no silent try/except fallback.

    Post 2026-05-08 the controls/ slot is PHASE-emitted with its own
    manifest.json + per-SUP behavior.json. If the config or manifest is
    missing, that's a setup bug and we surface it; we don't paper over it.
    """
    controls_cfg_path = deploy_dir / f"{deploy_type}-controls" / "config.yaml"
    if not controls_cfg_path.exists():
        # No config means controls aren't set up for this type — render
        # the legacy line and let the spinup decide what to do. This is
        # the only "soft" case: a fresh repo without a controls config
        # shouldn't crash the planner.
        return {
            "label": f"{deploy_type}-controls (baseline)",
            "behavior_source": None,
            "configs_spec": None,
            "manifest": None,
            "is_controls": True,
        }

    cfg = DeploymentConfig.load(controls_cfg_path)  # raises on parse errors — fail loud
    src = Path(cfg.behavior_source) if cfg.behavior_source else None
    return {
        "label": f"{deploy_type}-controls (baseline)",
        "behavior_source": src,
        "configs_spec": None,
        "manifest": load_manifest(src) if src else None,
        "is_controls": True,
    }


def _build_feedback_tasks(
    deploy_type: str,
    configs_spec: str | None,
    single_selector: str | None,
    target: str | None,
    source: str | None,
) -> list[dict] | None:
    """Resolve feedback sources into tasks. Returns None on resolution failure."""
    sources: list[dict] = []
    if single_selector:
        if source:
            src_path = Path(source)
            if not src_path.is_dir():
                output.error(f"ERROR: --source not a directory: {src_path}")
                return None
            sources = [{"path": src_path, "dataset": src_path.name, "preset": "?"}]
        elif target:
            src_path = find_feedback_by_target(target, deploy_type=deploy_type)
            if not src_path:
                output.error(
                    f"ERROR: No feedback source for target '{target}' "
                    f"under /mnt/AXES2U1/feedback/{deploy_type}-controls/"
                )
                return None
            sources = [{"path": src_path, "dataset": src_path.name, "preset": "?"}]
    else:
        sources = find_all_feedback_sources(deploy_type)
        if not sources:
            output.error(f"ERROR: No PHASE feedback configs found for '{deploy_type}'")
            output.info(f"  Searched: /mnt/AXES2U1/feedback/{deploy_type}-controls/")
            return None

    return [
        {
            "label": f"{deploy_type}-feedback: {src['dataset']}",
            "behavior_source": src["path"],
            "configs_spec": configs_spec,
            "manifest": load_manifest(src["path"]),
            "is_controls": False,
        }
        for src in sources
    ]


def show_plan_and_confirm(plan: list[dict], deploy_type: str) -> bool:
    """Render the combined plan + ask y/N. Returns True iff the user confirms.

    Fails loud (returns False) if any feedback task's manifest.target doesn't
    match deploy_type. Skips the prompt when the plan is a single controls
    task — there's nothing to confirm.
    """
    n = len(plan)
    output.banner(f"DEPLOY PLAN ({deploy_type}, {n} task{'s' if n != 1 else ''})")
    output.info("")

    any_mismatch = False
    for i, task in enumerate(plan, 1):
        output.info(f"  {i}. {task['label']}")
        if task["is_controls"]:
            src = task.get("behavior_source")
            mf = task.get("manifest")
            if src is None:
                output.info("      (baseline controls — no PHASE feedback)")
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

    if n == 1 and plan[0]["is_controls"]:
        return True

    # Children spawned by parallel fan-out skip the interactive confirm —
    # the parent already collected y/N for the whole batch. Without this
    # gate, every child would hang waiting for stdin that doesn't exist.
    import os as _os
    if _os.environ.get("RUSE_BATCH_CHILD") == "1":
        return True

    if not output.confirm(f"Deploy these {n} config(s)?"):
        output.info("Cancelled.")
        return False
    return True


def execute_plan(
    plan: list[dict], deploy_type: str, config_name: str | None,
    deploy_dir: Path, parallel: int = 1,
) -> int:
    """Run each task in the plan. Returns 0 iff all succeed.

    parallel: 1 = serial (legacy); >1 = subprocess fan-out with that many
    concurrent workers. Each child invokes ./deploy as its own process with
    RUSE_BATCH_CHILD=1 so it skips the confirm prompt and the batch summary.
    Concurrency safety is shared with the teardown fan-out path:
      - ~/.ssh/config and experiments.json are fcntl-locked
      - session log + ansible log paths get pid suffixes
      - each task deploys into a disjoint config_dir/runs/{rid}/ tree

    Lazy-imports the per-type spinup so this module doesn't pull in all
    three subsystems unconditionally.
    """
    if deploy_type == "rampart":
        from ..rampart.spinup import run_rampart_spinup as spinup
        default_config = "rampart-controls"
    elif deploy_type == "ghosts":
        from ..ghosts.spinup import run_ghosts_spinup as spinup
        default_config = "ghosts-controls"
    else:
        from ..decoy.spinup import run_decoy_spinup as spinup
        default_config = "decoy-controls"

    base_config = config_name or default_config
    results: list[tuple[str, int]] = []

    if parallel > 1 and len(plan) > 1:
        return _execute_plan_parallel(
            plan, deploy_type, deploy_dir, max_workers=parallel,
        )

    for i, task in enumerate(plan, 1):
        output.info("")
        output.info(f"[{i}/{len(plan)}] Deploying {task['label']}...")
        # Controls runs deploy under their own config name ({type}-controls),
        # not a derived feedback-style dir. The controls config.yaml already
        # declares behavior_source, so spinup picks it up at load time —
        # passing it again here would route through generate_feedback_config
        # and create a parallel `decoy-feedback-stdctrls-contro-all/` dir
        # with the verbose name + duplicated state. is_controls=True signals
        # spinup to skip that branch.
        if task["is_controls"]:
            spinup_source: str | None = None
        else:
            src = task["behavior_source"]
            spinup_source = str(src) if src else None
        try:
            rc = spinup(
                base_config, deploy_dir,
                spinup_source,
                task["configs_spec"],
            )
        except SystemExit as e:
            # Per-type spinups call sys.exit() on validation failure. Convert
            # to rc so the batch-summary still renders. Other deploys in the
            # plan continue.
            rc = e.code if isinstance(e.code, int) else 1
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


def _execute_plan_parallel(
    plan: list[dict], deploy_type: str, deploy_dir: Path,
    *, max_workers: int,
) -> int:
    """Subprocess fan-out across plan tasks. Bounded by max_workers.

    Same approach as filtered teardown: each plan task becomes its own
    ./deploy CLI invocation with RUSE_BATCH_CHILD=1 to skip the confirm
    prompt. Per-child stdout/stderr captured to a log file; parent prints
    one-line status as each completes.

    Why subprocess rather than threads: the output module is process-global
    state (session log handle, stderr writes). Threading would interleave
    all 8 deploys' output into one stream. Subprocess gives each child its
    own session log path (pid-suffixed) and a clean per-child log file.

    Risk: max_workers=3 default keeps OpenStack provisioning + apt mirror
    + ollama model pull concurrency reasonable. Going wider (e.g. workers=8
    for a full batch) is allowed but operators have hit OpenStack 503s on
    8 simultaneous provision calls historically.
    """
    import os
    import subprocess
    import time as _time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    repo_root = Path(__file__).resolve().parent.parent.parent
    deploy_script = repo_root / "deploy"
    logs_dir = deploy_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    batch_ts = _time.strftime("%Y%m%d-%H%M%S")

    def _cmd_for_task(task: dict) -> list[str]:
        """Reduce a plan task to the CLI flags that produce exactly that task.

        Controls → `./deploy --{type} --controls`.
        Feedback → `./deploy --{type} --feedback --source <abs path>`
        (--source bypasses dataset-alias resolution so we don't depend
        on aliases agreeing across two code paths).
        """
        cmd = [str(deploy_script), f"--{deploy_type}"]
        if task["is_controls"]:
            cmd.append("--controls")
        else:
            cmd += ["--feedback", "--source", str(task["behavior_source"])]
        return cmd

    def _slug(label: str) -> str:
        # `decoy-feedback: axes-summer24` → `decoy-feedback-axes-summer24`
        return label.replace(":", "").replace(" ", "-").replace("(", "").replace(")", "")

    def _one(idx: int, task: dict) -> tuple[int, str, int, Path, float]:
        label = task["label"]
        log_path = logs_dir / f"deploy-parallel-{batch_ts}-{_slug(label)}.log"
        t0 = _time.monotonic()
        env = {**os.environ, "RUSE_BATCH_CHILD": "1"}
        with open(log_path, "w") as log_f:
            log_f.write(f"# Parallel deploy child for {label}\n# Started {_time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            log_f.flush()
            proc = subprocess.run(
                _cmd_for_task(task),
                cwd=str(repo_root),
                stdout=log_f, stderr=subprocess.STDOUT,
                env=env,
            )
        return idx, label, proc.returncode, log_path, _time.monotonic() - t0

    output.info("")
    output.info(
        f"Running {len(plan)} deploys in parallel (max {max_workers} concurrent)..."
    )
    output.info("(per-deployment output captured to logs/deploy-parallel-*.log)")
    output.info("")

    results: list[tuple[str, int]] = []
    failures = 0
    completed = 0
    workers = min(max_workers, len(plan))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [
            ex.submit(_one, i, task)
            for i, task in enumerate(plan, 1)
        ]
        for fut in as_completed(futures):
            idx, label, rc, log_path, elapsed = fut.result()
            completed += 1
            status = "OK  " if rc == 0 else "FAIL"
            output.info(
                f"  [{completed}/{len(plan)}] {status}  {label}  "
                f"({int(elapsed//60)}m{int(elapsed%60):02d}s)  →  {log_path.name}"
            )
            results.append((label, rc))
            if rc != 0:
                failures += 1

    output.info("")
    output.banner("DEPLOY SUMMARY")
    for label, rc in sorted(results, key=lambda x: x[0]):
        status = "OK" if rc == 0 else f"FAILED (rc={rc})"
        output.info(f"  {label}: {status}")
    output.info("")

    if failures:
        output.error(f"DONE: {len(results) - failures}/{len(results)} succeeded, {failures} failed")
        return 1
    output.info(f"DONE: all {len(results)} deployment(s) launched")
    return 0
