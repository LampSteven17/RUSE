"""Terminal output helpers for RUSE deploy CLI.

Style: monochrome. No colors. Plain text with ASCII banners.
All output is also teed to a session log file when start_session_log() is called.
"""

import sys
import time
from pathlib import Path

_BANNER_WIDTH = 64
_session_log = None  # file handle, opened by start_session_log()
_session_log_path = None  # Path, set by start_session_log()


def start_session_log(logs_dir: Path, command: str) -> Path:
    """Open a session log file. All subsequent _write() calls are teed to it."""
    global _session_log, _session_log_path
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    log_path = logs_dir / f"session-{command}-{ts}.log"
    _session_log = open(log_path, "w")
    _session_log_path = log_path
    _session_log.write(f"# RUSE CLI session: {command} at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    _session_log.flush()
    return log_path


def close_session_log() -> None:
    """Close the session log file and print its path so operator can find it."""
    global _session_log, _session_log_path
    if _session_log:
        path = getattr(_session_log, "name", None) or _session_log_path
        _session_log.close()
        _session_log = None
        if path:
            # Print directly to stderr — the log file is now closed so we can't tee
            print("", file=sys.stderr)
            print(f"Session log: {path}", file=sys.stderr)


def _write(text: str) -> None:
    print(text, file=sys.stderr)
    if _session_log:
        _session_log.write(text + "\n")
        _session_log.flush()


# ── Output functions ─────────────────────────────────────────────────

def banner(text: str) -> None:
    """ASCII banner header for major sections.

    ################################################################
      DEPLOY
    ################################################################
    """
    rule = "#" * _BANNER_WIDTH
    _write("")
    _write(rule)
    _write(f"  {text}")
    _write(rule)
    _write("")


def header(text: str) -> None:
    """Section header with dashes."""
    _write(f"--- {text} ---")


def section(text: str) -> None:
    """Step/phase label."""
    _write(text)


def success(text: str) -> None:
    _write(text)


def error(text: str) -> None:
    _write(text)


def warn(text: str) -> None:
    _write(text)


def dim(text: str) -> None:
    _write(text)


def info(text: str) -> None:
    _write(text)


# ── Convenience aliases ──────────────────────────────────────────────

def bold(text: str, **_kw) -> None:
    header(text)


def green(text: str) -> None:
    success(text)


def red(text: str) -> None:
    error(text)


def yellow(text: str) -> None:
    warn(text)


# ── Formatting helpers ───────────────────────────────────────────────

def format_duration(seconds: float) -> str:
    secs = int(seconds)
    if secs >= 3600:
        return f"{secs // 3600}h{(secs % 3600) // 60:02d}m{secs % 60:02d}s"
    elif secs >= 60:
        return f"{secs // 60}m{secs % 60:02d}s"
    else:
        return f"{secs}s"


def timestamp() -> str:
    """Current wall-clock time as HH:MM:SS."""
    return time.strftime("%H:%M:%S")


def table(
    headers: list[str],
    rows: list[list[str]],
    indent: int = 2,
    col_widths: list[int] | None = None,
) -> None:
    """Print a formatted table with auto-width or pre-computed column widths."""
    if not rows:
        return

    if col_widths is None:
        all_rows = [headers] + rows
        col_widths = []
        for col_idx in range(len(headers)):
            max_width = 0
            for row in all_rows:
                if col_idx < len(row):
                    max_width = max(max_width, len(row[col_idx]))
            col_widths.append(max_width)

    prefix = " " * indent

    header_line = "  ".join(h.ljust(w) for h, w in zip(headers, col_widths))
    _write(f"{prefix}{header_line}")
    _write(f"{prefix}{'-' * len(header_line)}")

    for row in rows:
        cells = []
        for cell, width in zip(row, col_widths):
            cells.append(cell.ljust(width))
        _write(f"{prefix}{'  '.join(cells)}")


def confirm(prompt: str) -> bool:
    """Ask yes/no confirmation."""
    try:
        answer = input(f"  {prompt} [y/N] ")
        return answer.strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def confirm_destructive(prompt: str, confirmation_text: str = "DELETE ALL") -> bool:
    """Two-step confirmation for destructive operations."""
    if not confirm(prompt):
        return False
    try:
        answer = input(f"  Type {confirmation_text} to confirm: ")
        return answer.strip() == confirmation_text
    except (EOFError, KeyboardInterrupt):
        print()
        return False
