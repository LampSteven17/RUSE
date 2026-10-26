"""Opt-in autonomous decisions for the canonical six workflow categories."""

from __future__ import annotations

import json
import math
import filecmp
import os
import re
import subprocess
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlsplit

from phase_workflow.registry import CANONICAL_HANDLERS, ResolvedTask
from phase_workflow.registry import WorkflowResult
from phase_workflow.workflows import (
    BOUNDED_NETWORK_COMMAND,
    SHARE_PRINCIPAL,
    SHARE_UNC,
    DailyDocumentStore,
)


_PLANNER_SYSTEM = """You choose realistic autonomous user activity on a private cyber range.
Return exactly one JSON object with wait_seconds and activity. activity is null to idle;
idle waits must be positive and activity waits nonnegative. Otherwise activity has exactly
workflow, instruction, and resource. Choose one of WebResearch, VideoViewing,
DocumentCreation, FileDownload, FileSyncUpload, NetworkShareAccess. Choose the actual
website, content, endpoint, file byte size, or SMB destination yourself; do not assume an
assigned catalog. VideoViewing uses HTTPS HLS and exactly 300 seconds. FileDownload needs
the exact positive expected_bytes. HTTPS uploads send one runtime-generated document and
HTTP success proves only request acceptance. SMB resource has kind kerberos_smb_share,
unc //host/share, and a safe relative seed path. The configured fleet share may use fleet
credentials; every other SMB destination is anonymous-only and may truthfully fail auth.
Never request, reveal, embed, or invent credentials, tokens, secrets, local host files, or
shell commands. Resource interfaces are exactly:
WebResearch {kind:direct_url,url} or {kind:search_query,query}; VideoViewing
{kind:hls_video,url,play_seconds}; DocumentCreation {kind:document,filename,title,
sections:{heading:[lines]}} or {kind:spreadsheet,filename,columns:[names],rows:[[cells]]};
FileDownload {kind:https_download,url,expected_bytes}; FileSyncUpload
{kind:https_upload,url}; NetworkShareAccess {kind:kerberos_smb_share,unc,path}.
Document filenames are basenames ending .odt or .ods. Output JSON only."""


class OllamaAutonomousPlanner:
    """Ask the already-selected comparison model for one decision."""

    def __init__(self, model: str, logger, *, requests_api=None):
        if requests_api is None:
            import requests as requests_api
        self.model = model
        self.logger = logger
        self.requests_api = requests_api
        host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        if "://" not in host:
            host = "http://" + host
        self.endpoint = host.rstrip("/") + "/api/chat"

    def __call__(self, snapshot: Mapping[str, Any]) -> str:
        from common.config.model_config import get_num_ctx, get_ollama_seed

        prompt = (
            "Current capacity and recent outcome metadata (no full history):\n"
            + json.dumps(snapshot, sort_keys=True)
            + "\nChoose when to act or idle and return one decision."
        )
        options = {"num_ctx": get_num_ctx()}
        seed = get_ollama_seed()
        if seed is not None:
            options.update(seed=seed, temperature=0.0)
        self.logger.llm_request(
            action="autonomous_decision",
            input_data={"state": snapshot},
            model=self.model,
        )
        started = time.monotonic()
        try:
            response = self.requests_api.post(
                self.endpoint,
                json={
                    "model": self.model,
                    "messages": [
                    {"role": "system", "content": _PLANNER_SYSTEM},
                    {"role": "user", "content": prompt},
                    ],
                    "format": "json",
                    "stream": False,
                    "options": options,
                },
                timeout=300,
            )
            try:
                response.raise_for_status()
                payload = response.json()
            finally:
                response.close()
            if not isinstance(payload, dict):
                raise AutonomousDecisionError("planner response payload must be an object")
            message = payload.get("message", {})
            content = message.get("content") if isinstance(message, dict) else None
            tokens = {
                "prompt_tokens": payload.get("prompt_eval_count", 0),
                "completion_tokens": payload.get("eval_count", 0),
            }
            if not isinstance(content, str):
                raise AutonomousDecisionError("planner returned no text content")
            self.logger.llm_response(
                output="autonomous planner response received",
                duration_ms=int((time.monotonic() - started) * 1000),
                model=self.model,
                tokens=tokens,
            )
            return content
        except Exception as exc:
            self.logger.llm_error(
                error=type(exc).__name__, action="autonomous_decision", fatal=True
            )
            raise


class AutonomousDecisionError(RuntimeError):
    """A planner response is not one complete safe autonomous decision."""


@dataclass(frozen=True)
class AutonomousActivity:
    workflow: str
    resource: Mapping[str, Any]
    instruction: str


@dataclass(frozen=True)
class AutonomousDecision:
    wait_seconds: float
    activity: Optional[AutonomousActivity]


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AutonomousDecisionError(f"{label} must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise AutonomousDecisionError(
            f"{label} fields must be exactly {sorted(expected)}"
        )


def _url(value: Any, *, schemes: set[str], label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AutonomousDecisionError(f"{label} must be a nonempty URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in schemes
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise AutonomousDecisionError(
            f"{label} must use {sorted(schemes)} without embedded credentials"
        )
    return value


def _filename(value: Any, suffixes: set[str]) -> str:
    if (
        not isinstance(value, str) or not value or Path(value).name != value
        or any(ord(character) < 32 or character in {'"', "'"} for character in value)
    ):
        raise AutonomousDecisionError("document filename must be one basename")
    if Path(value).suffix not in suffixes:
        raise AutonomousDecisionError(
            f"document filename must end in one of {sorted(suffixes)}"
        )
    return value


def _share_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise AutonomousDecisionError("share path must be a relative POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(character) < 32 or character in {'"', "'", ";"}
               for character in value)
    ):
        raise AutonomousDecisionError("share path is unsafe")
    return value


def _share_unc(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"//[A-Za-z0-9.-]+/[A-Za-z0-9_$.-]+", value
    ):
        raise AutonomousDecisionError("share UNC must be exactly //host/share")
    return value


def _validate_resource(workflow: str, raw: Any) -> Mapping[str, Any]:
    resource = _object(raw, "activity.resource")
    kind = resource.get("kind")
    if workflow == "WebResearch" and kind == "direct_url":
        _exact_keys(resource, {"kind", "url"}, "direct URL resource")
        _url(resource["url"], schemes={"http", "https"}, label="research URL")
    elif workflow == "WebResearch" and kind == "search_query":
        _exact_keys(resource, {"kind", "query"}, "search resource")
        if not isinstance(resource["query"], str) or not resource["query"].strip():
            raise AutonomousDecisionError("search query must be nonempty")
    elif workflow == "VideoViewing" and kind == "hls_video":
        _exact_keys(resource, {"kind", "url", "play_seconds"}, "video resource")
        _url(resource["url"], schemes={"https"}, label="video URL")
        if resource["play_seconds"] != 300:
            raise AutonomousDecisionError("video play_seconds must be 300")
    elif workflow == "DocumentCreation" and kind == "document":
        _exact_keys(
            resource, {"kind", "filename", "title", "sections"},
            "document resource",
        )
        _filename(resource["filename"], {".odt"})
        if not isinstance(resource["title"], str) or not resource["title"].strip():
            raise AutonomousDecisionError("document title must be nonempty")
        sections = resource["sections"]
        if (
            not isinstance(sections, dict)
            or not sections
            or not all(
                isinstance(title, str) and title.strip()
                and isinstance(lines, list) and lines
                and all(isinstance(line, str) for line in lines)
                for title, lines in sections.items()
            )
        ):
            raise AutonomousDecisionError("document sections are invalid")
    elif workflow == "DocumentCreation" and kind == "spreadsheet":
        _exact_keys(
            resource, {"kind", "filename", "columns", "rows"},
            "spreadsheet resource",
        )
        _filename(resource["filename"], {".ods"})
        columns, rows = resource["columns"], resource["rows"]
        if (
            not isinstance(columns, list) or not columns
            or not all(isinstance(column, str) and column for column in columns)
            or not isinstance(rows, list) or not rows
            or not all(isinstance(row, list) and len(row) == len(columns) for row in rows)
        ):
            raise AutonomousDecisionError("spreadsheet rows and columns are invalid")
    elif workflow == "FileDownload" and kind == "https_download":
        _exact_keys(
            resource, {"kind", "url", "expected_bytes"}, "download resource"
        )
        _url(resource["url"], schemes={"https"}, label="download URL")
        expected = resource["expected_bytes"]
        if isinstance(expected, bool) or not isinstance(expected, int) or expected <= 0:
            raise AutonomousDecisionError("expected_bytes must be a positive integer")
    elif workflow == "FileSyncUpload" and kind == "https_upload":
        _exact_keys(resource, {"kind", "url"}, "upload resource")
        _url(resource["url"], schemes={"https"}, label="upload URL")
    elif workflow == "NetworkShareAccess" and kind == "kerberos_smb_share":
        _exact_keys(resource, {"kind", "unc", "path"}, "share resource")
        _share_unc(resource["unc"])
        _share_path(resource["path"])
    else:
        raise AutonomousDecisionError(
            f"resource shape does not match autonomous {workflow}"
        )
    return _freeze(resource)


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def parse_autonomous_decision(raw: str) -> AutonomousDecision:
    """Parse one JSON-only planner response without repair or fallback."""
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AutonomousDecisionError("planner response must be JSON") from exc
    decision = _object(value, "planner response")
    _exact_keys(decision, {"wait_seconds", "activity"}, "planner response")
    wait = decision["wait_seconds"]
    if isinstance(wait, bool) or not isinstance(wait, (int, float)) or not math.isfinite(wait):
        raise AutonomousDecisionError("wait_seconds must be finite and numeric")
    activity = decision["activity"]
    if activity is None:
        if wait <= 0:
            raise AutonomousDecisionError("idle wait_seconds must be positive")
        return AutonomousDecision(float(wait), None)
    if wait < 0:
        raise AutonomousDecisionError("activity wait_seconds must be nonnegative")
    activity = _object(activity, "activity")
    _exact_keys(activity, {"workflow", "resource", "instruction"}, "activity")
    workflow = activity["workflow"]
    if workflow not in CANONICAL_HANDLERS:
        raise AutonomousDecisionError("activity workflow is not canonical")
    instruction = activity["instruction"]
    if not isinstance(instruction, str) or not instruction.strip():
        raise AutonomousDecisionError("activity instruction must be nonempty")
    return AutonomousDecision(
        float(wait),
        AutonomousActivity(
            workflow=workflow,
            resource=_validate_resource(workflow, activity["resource"]),
            instruction=instruction,
        ),
    )


def autonomous_task(activity: AutonomousActivity, plan, occurrence_id: str) -> ResolvedTask:
    """Bind a validated dynamic choice to installed Brain identity only."""
    if not re.fullmatch(r"autonomous-[A-Za-z0-9_-]+-[1-9][0-9]*", occurrence_id):
        raise AutonomousDecisionError("invalid autonomous occurrence identity")
    return ResolvedTask(
        workflow=activity.workflow,
        resource_profile="autonomous",
        sup_config=plan.sup_config,
        brain=plan.brain,
        brain_profile=plan.brain_profile_id,
        resource_id=occurrence_id,
        resource=activity.resource,
        instruction=activity.instruction,
        occurrence_id=occurrence_id,
    )


class AutonomousHttpsUpload:
    """POST one runtime-owned document to a model-selected HTTPS endpoint."""

    def __init__(self, documents: DailyDocumentStore, requests_api=None):
        self.documents = documents
        self.requests_api = requests_api

    def execute(self, task: ResolvedTask, workspace: Path) -> WorkflowResult:
        resource = _validate_resource("FileSyncUpload", dict(task.resource))
        local_day = Path(workspace).name
        reservation = self.documents.reserve(
            local_day, workspace, "https", task.occurrence_id
        )
        try:
            payload = reservation.path.read_bytes()
            requests_api = self.requests_api
            if requests_api is None:
                import requests as requests_api
            response = requests_api.post(
                resource["url"], data=payload, timeout=(20, 360)
            )
            try:
                response.raise_for_status()
            finally:
                close = getattr(response, "close", None)
                if close is not None:
                    close()
            self.documents.complete(reservation)
            return WorkflowResult(completed=True, artifact=str(reservation.path))
        except Exception:
            self.documents.release(reservation)
            raise


class AutonomousShareAccess:
    """Use fleet credentials only for the exact fleet UNC; otherwise anonymous."""

    def __init__(
        self,
        documents: DailyDocumentStore,
        *,
        runner=subprocess.run,
        keytab: Optional[str] = None,
        ccache: Optional[str] = None,
    ):
        self.documents = documents
        self.runner = runner
        self.keytab = keytab or os.environ.get(
            "RUSE_SHARE_KEYTAB", "/etc/ruse/ruse-share.keytab"
        )
        self.ccache = ccache or os.environ.get(
            "RUSE_SHARE_CCACHE", "/run/ruse/krb5cc_ruse_share"
        )

    def execute(self, task: ResolvedTask, workspace: Path) -> WorkflowResult:
        resource = _validate_resource("NetworkShareAccess", dict(task.resource))
        unc, seed = resource["unc"], resource["path"]
        workspace = Path(workspace)
        local_day = workspace.name
        reservation = self.documents.reserve(
            local_day, workspace, "share", task.occurrence_id
        )
        downloaded = workspace / f"{task.occurrence_id}-{PurePosixPath(seed).name}"
        roundtrip = workspace / f".{task.occurrence_id}-share-upload-roundtrip"
        remote_name = (
            f"Incoming/{task.sup_config}/{local_day}/"
            f"{task.occurrence_id}-{reservation.path.name}"
        )
        env = dict(os.environ)
        fleet = unc == SHARE_UNC
        if fleet:
            env["KRB5CCNAME"] = f"FILE:{self.ccache}"
            auth = ["--use-kerberos=required", "--no-pass"]
        else:
            for name in ("KRB5CCNAME", "PASSWD", "PASSWD_FD", "PASSWD_FILE"):
                env.pop(name, None)
            auth = ["--use-kerberos=off", "--user=%", "--no-pass"]
        upload_attempted = False
        try:
            if fleet:
                self._run([
                    *BOUNDED_NETWORK_COMMAND,
                    "kinit", "-c", self.ccache, "-kt", self.keytab,
                    SHARE_PRINCIPAL,
                ], env)
            parent = str(PurePosixPath(seed).parent)
            self._smb(unc, auth, f'ls "{parent}"', env)
            self._smb(unc, auth, f'get "{seed}" "{downloaded}"', env)
            if not downloaded.is_file() or downloaded.stat().st_size <= 0:
                raise RuntimeError("selected share seed was not downloaded")
            remote_directory = str(PurePosixPath(remote_name).parent)
            try:
                upload_attempted = True
                self._smb(
                    unc, auth,
                    f'mkdir "{remote_directory}"; '
                    f'put "{reservation.path}" "{remote_name}"',
                    env,
                )
                self._smb(unc, auth, f'get "{remote_name}" "{roundtrip}"', env)
                if not roundtrip.is_file() or not filecmp.cmp(
                    reservation.path, roundtrip, shallow=False
                ):
                    raise RuntimeError("remote share upload round-trip mismatch")
            finally:
                roundtrip.unlink(missing_ok=True)
                if upload_attempted:
                    self._smb(unc, auth, f'del "{remote_name}"', env)
            self.documents.complete(reservation)
            return WorkflowResult(completed=True, artifact=str(downloaded))
        except Exception:
            self.documents.release(reservation)
            raise

    def _smb(self, unc: str, auth: list[str], command: str, env: dict[str, str]) -> str:
        return self._run([
            *BOUNDED_NETWORK_COMMAND, "smbclient", unc, *auth, "-c", command,
        ], env)

    def _run(self, argv: list[str], env: dict[str, str]) -> str:
        completed = self.runner(
            argv, check=True, capture_output=True, text=True, env=env
        )
        return completed.stdout or ""


class AutonomousExecutor:
    """Execute overlapping model-timed choices; planning errors are fatal."""

    def __init__(
        self,
        plan,
        planner: Callable[[Mapping[str, Any]], str],
        registry,
        logger,
        *,
        stop_event: Optional[threading.Event] = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        background_policy=None,
    ):
        self.plan = plan
        self.planner = planner
        self.registry = registry
        self.logger = logger
        self.stop_event = stop_event or threading.Event()
        self.now = now
        self.background_policy = background_policy
        self._sequence = 0
        self._state_lock = threading.Lock()
        self._last_result: Optional[dict[str, Any]] = None
        self._generated_documents: set[str] = set()
        session_id = str(logger.session_id)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
            raise AutonomousDecisionError("logger session identity is unsafe")
        self._session_id = session_id

    def run_forever(self) -> None:
        running: dict[Future, dict[str, Any]] = {}
        pool = ThreadPoolExecutor(
            max_workers=self.plan.max_parallel,
            thread_name_prefix="autonomous-workflow",
        )
        planner_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="autonomous-planner")
        try:
            while not self.stop_event.is_set():
                self._update_background()
                self._reap(running)
                while len(running) >= self.plan.max_parallel:
                    wait(
                        tuple(running), timeout=self._seconds_to_next_minute(),
                        return_when=FIRST_COMPLETED,
                    )
                    self._update_background()
                    self._reap(running)
                    if self.stop_event.is_set():
                        break
                if self.stop_event.is_set():
                    break
                with self._state_lock:
                    last_result = dict(self._last_result) if self._last_result else None
                    generated_documents = sorted(self._generated_documents)
                snapshot = {
                    "current_utc": self.now().isoformat(),
                    "current_local": self.now().astimezone(self.plan.timezone).isoformat(),
                    "timezone": str(self.plan.timezone),
                    "max_parallel": self.plan.max_parallel,
                    "available_slots": self.plan.max_parallel - len(running),
                    "running": [
                        {
                            "occurrence_id": details["occurrence_id"],
                            "workflow": details["workflow"],
                        }
                        for details in running.values()
                    ],
                    "last_result": last_result,
                    "generated_documents": generated_documents,
                }
                decision = parse_autonomous_decision(
                    self._invoke_planner(planner_pool, snapshot)
                )
                self._sequence += 1
                occurrence = f"autonomous-{self._session_id}-{self._sequence}"
                chosen_at = self.now()
                details = {
                    "mode": "autonomous",
                    "occurrence_id": occurrence,
                    "chosen_at": chosen_at.isoformat(),
                    "wait_seconds": decision.wait_seconds,
                    "workflow": decision.activity.workflow if decision.activity else None,
                    "resource": _thaw(decision.activity.resource) if decision.activity else None,
                }
                self.logger.info("Autonomous decision", details)
                if self._wait(decision.wait_seconds):
                    break
                if decision.activity is None:
                    self.logger.info("Autonomous idle completed", details)
                    continue
                task = autonomous_task(decision.activity, self.plan, occurrence)
                future = pool.submit(self._execute, task, details)
                running[future] = details
        finally:
            planner_pool.shutdown(wait=True, cancel_futures=True)
            pool.shutdown(wait=True, cancel_futures=True)
            self._reap(running)

    def close(self) -> None:
        self.stop_event.set()

    def _execute(self, task: ResolvedTask, details: Mapping[str, Any]) -> None:
        started = self.now()
        try:
            result = self.registry.execute(
                task, started.astimezone(self.plan.timezone).date().isoformat()
            )
            status = "success" if result.completed else "failed"
            self.logger.info("Autonomous activity finished", {
                **details,
                "actual_start": started.isoformat(),
                "actual_end": self.now().isoformat(),
                "status": status,
                "artifact": result.artifact,
            })
            with self._state_lock:
                if task.workflow == "DocumentCreation" and result.artifact:
                    self._generated_documents.add(Path(result.artifact).name)
                self._last_result = {
                    "occurrence_id": task.occurrence_id,
                    "workflow": task.workflow,
                    "status": status,
                    "generated_document": (
                        Path(result.artifact).name if result.artifact else None
                    ),
                }
        except Exception as exc:
            self.logger.warning("Autonomous activity failed", {
                **details,
                "actual_start": started.isoformat(),
                "actual_end": self.now().isoformat(),
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
            with self._state_lock:
                self._last_result = {
                    "occurrence_id": task.occurrence_id,
                    "workflow": task.workflow,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                }

    def _wait(self, seconds: float) -> bool:
        deadline = self.now().timestamp() + seconds
        while True:
            self._update_background()
            remaining = deadline - self.now().timestamp()
            if remaining <= 0:
                return self.stop_event.is_set()
            if self.stop_event.wait(min(remaining, self._seconds_to_next_minute())):
                return True

    def _invoke_planner(self, pool: ThreadPoolExecutor, snapshot: Mapping[str, Any]) -> str:
        future = pool.submit(self.planner, snapshot)
        while not future.done():
            wait((future,), timeout=self._seconds_to_next_minute())
            self._update_background()
        return future.result()

    def _seconds_to_next_minute(self) -> float:
        now = self.now()
        return max(0.001, 60.0 - now.second - now.microsecond / 1_000_000)

    def _update_background(self) -> None:
        if self.background_policy is not None:
            self.background_policy.update(self.now())

    @staticmethod
    def _reap(running: dict[Future, dict[str, Any]]) -> None:
        for future in tuple(running):
            if future.done():
                future.result()
                del running[future]
