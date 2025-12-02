"""
DOLOS-DEPLOY Agent Logger

Unified logging framework for all agent types with timestamped event tracking
for experiment analysis. Outputs JSON-Lines format for easy parsing.

Usage:
    from common.logging.agent_logger import AgentLogger

    logger = AgentLogger(agent_type="MCHP-SMOL", log_dir="/path/to/logs")
    logger.session_start()
    logger.workflow_start("google_search")
    logger.llm_request(action="generate_query", input_data={"context": "..."})
    logger.llm_response(output="search query", duration_ms=1200)
    logger.workflow_end("google_search", success=True, duration_ms=45000)
    logger.session_end()
"""

import json
import os
import uuid
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, asdict
from enum import Enum


class EventType(str, Enum):
    """Types of events that can be logged."""
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    WORKFLOW_START = "workflow_start"
    WORKFLOW_END = "workflow_end"
    LLM_REQUEST = "llm_request"
    LLM_RESPONSE = "llm_response"
    LLM_ERROR = "llm_error"
    DECISION = "decision"
    BROWSER_ACTION = "browser_action"
    GUI_ACTION = "gui_action"
    TIMING_DELAY = "timing_delay"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class LogEvent:
    """Structured log event."""
    timestamp: str
    session_id: str
    agent_type: str
    event_type: str
    workflow: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

    def to_json(self) -> str:
        """Convert event to JSON string."""
        data = asdict(self)
        # Remove None values for cleaner output
        data = {k: v for k, v in data.items() if v is not None}
        return json.dumps(data)

    @classmethod
    def from_json(cls, json_str: str) -> 'LogEvent':
        """Create event from JSON string."""
        data = json.loads(json_str)
        return cls(**data)


class AgentLogger:
    """
    Unified logging framework for DOLOS-DEPLOY agents.

    Outputs JSON-Lines format with one event per line for easy parsing
    and post-hoc analysis of agent behavior.
    """

    def __init__(
        self,
        agent_type: str,
        log_dir: Optional[str] = None,
        session_id: Optional[str] = None
    ):
        """
        Initialize the logger.

        Args:
            agent_type: Type of agent (e.g., "MCHP-SMOL", "BU-PHASE")
            log_dir: Directory for log files. Defaults to deployed_sups/{agent_type}/logs/
            session_id: Optional session ID. Auto-generated if not provided.
        """
        self.agent_type = agent_type
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.current_workflow: Optional[str] = None
        self._workflow_start_time: Optional[float] = None
        self._session_start_time: Optional[float] = None

        # Set up log directory
        if log_dir:
            self.log_dir = Path(log_dir)
        else:
            # Default to deployed_sups/{agent_type}/logs/
            base_dir = Path(__file__).parent.parent.parent.parent / "deployed_sups" / agent_type / "logs"
            self.log_dir = base_dir

        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Create log file with timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.log_file = self.log_dir / f"session_{timestamp}_{self.session_id}.jsonl"

        # Create/update symlink to latest log
        latest_link = self.log_dir / "latest.jsonl"
        if latest_link.exists() or latest_link.is_symlink():
            latest_link.unlink()
        latest_link.symlink_to(self.log_file.name)

        self._file_handle = open(self.log_file, 'a')

    def _get_timestamp(self) -> str:
        """Get current ISO timestamp with microseconds."""
        return datetime.now().isoformat()

    def _write_event(self, event: LogEvent) -> None:
        """Write event to log file."""
        self._file_handle.write(event.to_json() + "\n")
        self._file_handle.flush()

    def _log(
        self,
        event_type: EventType,
        workflow: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> LogEvent:
        """Create and write a log event."""
        event = LogEvent(
            timestamp=self._get_timestamp(),
            session_id=self.session_id,
            agent_type=self.agent_type,
            event_type=event_type.value,
            workflow=workflow or self.current_workflow,
            details=details
        )
        self._write_event(event)
        return event

    # =========================================================================
    # Session Events
    # =========================================================================

    def session_start(self, config: Optional[Dict[str, Any]] = None) -> LogEvent:
        """Log session start event."""
        self._session_start_time = time.time()
        details = {"config": config} if config else {}
        details["log_file"] = str(self.log_file)
        return self._log(EventType.SESSION_START, details=details)

    def session_end(self, summary: Optional[Dict[str, Any]] = None) -> LogEvent:
        """Log session end event."""
        details = summary or {}
        if self._session_start_time:
            details["total_duration_ms"] = int((time.time() - self._session_start_time) * 1000)
        return self._log(EventType.SESSION_END, details=details)

    # =========================================================================
    # Workflow Events
    # =========================================================================

    def workflow_start(self, workflow_name: str, params: Optional[Dict[str, Any]] = None) -> LogEvent:
        """Log workflow start event."""
        self.current_workflow = workflow_name
        self._workflow_start_time = time.time()
        details = {"params": params} if params else {}
        return self._log(EventType.WORKFLOW_START, workflow=workflow_name, details=details)

    def workflow_end(
        self,
        workflow_name: str,
        success: bool,
        duration_ms: Optional[int] = None,
        result: Optional[Any] = None,
        error: Optional[str] = None
    ) -> LogEvent:
        """Log workflow end event."""
        details = {"success": success}

        if duration_ms is not None:
            details["duration_ms"] = duration_ms
        elif self._workflow_start_time:
            details["duration_ms"] = int((time.time() - self._workflow_start_time) * 1000)

        if result is not None:
            # Truncate result if too long
            result_str = str(result)
            details["result"] = result_str[:500] if len(result_str) > 500 else result_str

        if error:
            details["error"] = error

        event = self._log(EventType.WORKFLOW_END, workflow=workflow_name, details=details)
        self.current_workflow = None
        self._workflow_start_time = None
        return event

    # =========================================================================
    # LLM Events
    # =========================================================================

    def llm_request(
        self,
        action: str,
        input_data: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None
    ) -> LogEvent:
        """Log LLM request event."""
        details = {"action": action}
        if input_data:
            # Truncate long inputs
            for key, value in input_data.items():
                if isinstance(value, str) and len(value) > 500:
                    input_data[key] = value[:500] + "..."
            details["input"] = input_data
        if model:
            details["model"] = model
        return self._log(EventType.LLM_REQUEST, details=details)

    def llm_response(
        self,
        output: str,
        duration_ms: int,
        model: Optional[str] = None,
        tokens: Optional[Dict[str, int]] = None
    ) -> LogEvent:
        """Log LLM response event."""
        details = {
            "output": output[:500] if len(output) > 500 else output,
            "duration_ms": duration_ms
        }
        if model:
            details["model"] = model
        if tokens:
            details["tokens"] = tokens
        return self._log(EventType.LLM_RESPONSE, details=details)

    def llm_error(self, error: str, action: str, fatal: bool = True) -> LogEvent:
        """
        Log LLM error event.

        Args:
            error: Error message
            action: The action that was being attempted
            fatal: If True, indicates experiment is invalid
        """
        details = {
            "error": error,
            "action": action,
            "fatal": fatal,
            "experiment_valid": not fatal
        }
        return self._log(EventType.LLM_ERROR, details=details)

    # =========================================================================
    # Decision Events
    # =========================================================================

    def decision(
        self,
        choice: str,
        options: Optional[List[str]] = None,
        selected: Optional[str] = None,
        context: Optional[str] = None,
        method: str = "llm"  # "llm" or "random"
    ) -> LogEvent:
        """Log a decision event."""
        details = {
            "choice_type": choice,
            "method": method
        }
        if options:
            # Limit options list for logging
            details["options_count"] = len(options)
            details["options_sample"] = options[:5] if len(options) > 5 else options
        if selected:
            details["selected"] = selected
        if context:
            details["context"] = context[:200] if len(context) > 200 else context
        return self._log(EventType.DECISION, details=details)

    # =========================================================================
    # Action Events
    # =========================================================================

    def browser_action(
        self,
        action: str,
        target: Optional[str] = None,
        success: bool = True,
        duration_ms: Optional[int] = None
    ) -> LogEvent:
        """Log browser action (Selenium/Playwright)."""
        details = {
            "action": action,
            "success": success
        }
        if target:
            details["target"] = target[:200] if len(target) > 200 else target
        if duration_ms:
            details["duration_ms"] = duration_ms
        return self._log(EventType.BROWSER_ACTION, details=details)

    def gui_action(
        self,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        success: bool = True
    ) -> LogEvent:
        """Log GUI action (pyautogui)."""
        details = {
            "action": action,
            "success": success
        }
        if params:
            details["params"] = params
        return self._log(EventType.GUI_ACTION, details=details)

    def timing_delay(
        self,
        seconds: float,
        reason: str = "scheduled"
    ) -> LogEvent:
        """Log timing delay event."""
        details = {
            "delay_seconds": round(seconds, 2),
            "delay_ms": int(seconds * 1000),
            "reason": reason
        }
        return self._log(EventType.TIMING_DELAY, details=details)

    # =========================================================================
    # Error/Warning Events
    # =========================================================================

    def error(
        self,
        message: str,
        fatal: bool = False,
        exception: Optional[Exception] = None
    ) -> LogEvent:
        """
        Log error event.

        Args:
            message: Error message
            fatal: If True, raises exception after logging
            exception: Original exception if available
        """
        details = {
            "message": message,
            "fatal": fatal
        }
        if exception:
            details["exception_type"] = type(exception).__name__
            details["exception_str"] = str(exception)

        event = self._log(EventType.ERROR, details=details)

        if fatal:
            raise RuntimeError(f"FATAL ERROR [{self.agent_type}]: {message}")

        return event

    def warning(self, message: str, details: Optional[Dict[str, Any]] = None) -> LogEvent:
        """Log warning event."""
        warn_details = {"message": message}
        if details:
            warn_details.update(details)
        return self._log(EventType.WARNING, details=warn_details)

    def info(self, message: str, details: Optional[Dict[str, Any]] = None) -> LogEvent:
        """Log info event."""
        info_details = {"message": message}
        if details:
            info_details.update(details)
        return self._log(EventType.INFO, details=info_details)

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def close(self) -> None:
        """Close the log file handle."""
        if self._file_handle and not self._file_handle.closed:
            self._file_handle.close()

    def __enter__(self) -> 'AgentLogger':
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        if exc_type is not None:
            self.error(
                f"Session ended with exception: {exc_val}",
                fatal=False,
                exception=exc_val
            )
        self.close()

    def get_log_path(self) -> Path:
        """Get the path to the current log file."""
        return self.log_file


def read_log_file(log_path: str) -> List[LogEvent]:
    """
    Read and parse a JSON-Lines log file.

    Args:
        log_path: Path to the .jsonl log file

    Returns:
        List of LogEvent objects
    """
    events = []
    with open(log_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(LogEvent.from_json(line))
    return events


def summarize_session(log_path: str) -> Dict[str, Any]:
    """
    Generate summary statistics for a session log.

    Args:
        log_path: Path to the .jsonl log file

    Returns:
        Dictionary with session statistics
    """
    events = read_log_file(log_path)

    summary = {
        "total_events": len(events),
        "workflows_executed": 0,
        "workflows_successful": 0,
        "workflows_failed": 0,
        "llm_requests": 0,
        "llm_errors": 0,
        "total_llm_duration_ms": 0,
        "errors": 0,
        "warnings": 0
    }

    for event in events:
        if event.event_type == EventType.WORKFLOW_END.value:
            summary["workflows_executed"] += 1
            if event.details and event.details.get("success"):
                summary["workflows_successful"] += 1
            else:
                summary["workflows_failed"] += 1
        elif event.event_type == EventType.LLM_REQUEST.value:
            summary["llm_requests"] += 1
        elif event.event_type == EventType.LLM_RESPONSE.value:
            if event.details and "duration_ms" in event.details:
                summary["total_llm_duration_ms"] += event.details["duration_ms"]
        elif event.event_type == EventType.LLM_ERROR.value:
            summary["llm_errors"] += 1
        elif event.event_type == EventType.ERROR.value:
            summary["errors"] += 1
        elif event.event_type == EventType.WARNING.value:
            summary["warnings"] += 1

    return summary
