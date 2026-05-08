"""GHOSTS audit — placeholder.

DECOY audit checks (Ollama / GPU / brain process) don't apply to GHOSTS
NPCs. GHOSTS-specific concerns: API VM Docker stack health, NPC
ghosts-client systemd state, NRestarts as memcap-OOM signal, .NET RSS
trend, registered-machine count via /api/machines.
"""

from __future__ import annotations

from pathlib import Path

from ... import output


def run_ghosts_audit(deploy_dir: Path) -> int:
    """Stub. Returns 1 with an explanatory message."""
    output.error("GHOSTS audit not yet implemented")
    output.dim(
        "  TODO: API /api/machines healthcheck, NPC systemd state +\n"
        "  NRestarts (memcap signal), .NET RSS trend, container restart count."
    )
    return 1
