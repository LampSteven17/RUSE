"""RAMPART audit — placeholder.

The DECOY audit (decoy/audit.py) is the only fully-implemented health
audit so far. RAMPART's audit semantics differ enough (AD health,
pyhuman scheduled-task / systemd state across Win + Linux endpoints,
DNS zone freshness, Moodle reachability) that the DECOY checks don't
transfer. This stub exists so __main__'s `audit --rampart` dispatch has
a target — the UX is "you tried to audit, here's why nothing happened."
"""

from __future__ import annotations

from pathlib import Path

from ..core import output


def run_rampart_audit(deploy_dir: Path) -> int:
    """Stub. Returns 1 with an explanatory message."""
    output.error("RAMPART audit not yet implemented")
    output.dim(
        "  TODO: AD health probe, pyhuman state on Linux + Windows endpoints,\n"
        "  DNS zone freshness, Moodle reachability, simulate-logins seed sanity."
    )
    return 1
