"""Terminal output helpers for RUSE deploy CLI.

Style: monochrome. No colors. Plain text with ASCII banners.
"""

import sys
import time

_BANNER_WIDTH = 64


def _write(text: str) -> None:
    print(text, file=sys.stderr)


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
