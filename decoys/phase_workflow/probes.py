"""Probe-only daily selection. Plans and execution still use the canonical loader."""

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .loader import WorkflowPlanError, load_workflow_plan


# Explicit catalog order approved in PHASE plans/probes/README.md.
PROBE_RESOURCES = {
    "WebResearch": ("wikipedia_compiler", "wikipedia_geometry", "wikipedia_deep_learning",
                    "wikipedia_solar_power", "wikipedia_python"),
    "VideoViewing": ("video_hls_mux_bbb", "video_hls_apple_bipbop", "video_hls_unified_tos"),
    "FileDownload": ("download_ovh_1m", "download_ovh_10m", "download_ovh_100m",
                     "download_sasag_1m", "download_sasag_10m", "download_sasag_100m"),
    "FileSyncUpload": ("cloudflare_upload",),
    "DocumentCreation": ("document_team_meeting_notes", "spreadsheet_expense_tracker",
                         "document_project_status", "spreadsheet_inventory_tracker",
                         "document_training_outline", "spreadsheet_work_schedule",
                         "document_incident_summary", "spreadsheet_task_tracker",
                         "document_weekly_planning"),
    "NetworkShareAccess": ("share_team_notes", "share_inventory", "share_project_status"),
}
PROBE_TIMEZONE = ZoneInfo("America/New_York")


def validate_probe_plans(source, workflow):
    """Validate every daily variant before display/provisioning or runtime startup."""
    if workflow is None:
        if source is not None:
            raise WorkflowPlanError("idle probe must not have plans")
        return ()
    if workflow not in PROBE_RESOURCES or not source:
        raise WorkflowPlanError("active probe requires its workflow and plan directory")
    root = Path(source)
    resources = PROBE_RESOURCES[workflow]
    if not root.is_dir() or {p.name for p in root.iterdir()} != {f"{r}.json" for r in resources}:
        raise WorkflowPlanError(f"probe directory must contain exactly its approved daily variants: {root}")
    plans = []
    for resource in resources:
        plan = load_workflow_plan(root / f"{resource}.json", "scripted-cpu")
        if (plan.resource_profile != "feedback-v2" or str(plan.timezone) != str(PROBE_TIMEZONE)
                or plan.max_parallel != 10 or len(plan.windows) != 24):
            raise WorkflowPlanError("probe requires feedback-v2, America/New_York, max_parallel=10 and 24 windows")
        for hour, window in enumerate(plan.windows):
            if (window.start_minute != hour * 60 or window.end_minute != hour * 60 + 1
                    or len(window.sequence) != (1, 2, 4, 8, 10)[hour % 5]
                    or any(e.offset_minutes != 0 or e.workflow != workflow or e.resource_id != resource
                           for e in window.sequence)):
                raise WorkflowPlanError(f"invalid fixed-resource hourly probe burst: {resource}, hour={hour}")
        plans.append(plan)
    return tuple(plans)


def probe_day_index(started_at: str, local_day: date, count: int) -> int:
    started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    if started.tzinfo is None:
        raise ValueError("probe started_at must include its UTC offset")
    elapsed = (local_day - started.astimezone(PROBE_TIMEZONE).date()).days
    if elapsed < 0:
        raise ValueError("probe local day precedes the recorded run start")
    return elapsed % count
