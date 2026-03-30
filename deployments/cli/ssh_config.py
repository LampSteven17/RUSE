"""SSH config block management for RUSE deployments."""

from __future__ import annotations

import re
from pathlib import Path

from . import output


DEFAULT_SSH_CONFIG = Path.home() / ".ssh" / "config"
MARKER_PREFIX = "# BEGIN RUSE: "
MARKER_SUFFIX = "# END RUSE: "


def install_ssh_config(
    snippet_path: Path,
    deploy_name: str,
    ssh_config: Path | None = None,
) -> int:
    """Install an SSH config snippet with RUSE markers. Returns host count."""
    ssh_config = ssh_config or DEFAULT_SSH_CONFIG
    if not snippet_path.exists():
        return 0

    # Read and clean snippet (strip comment headers)
    raw = snippet_path.read_text()
    lines = []
    for line in raw.splitlines():
        if line.startswith("#####") or line.startswith("# Add this to your"):
            continue
        lines.append(line)
    snippet = "\n".join(lines).strip()
    if not snippet:
        return 0

    marker_begin = f"{MARKER_PREFIX}{deploy_name}"
    marker_end = f"{MARKER_SUFFIX}{deploy_name}"
    block = f"{marker_begin}\n{snippet}\n{marker_end}"

    ssh_config.parent.mkdir(parents=True, exist_ok=True)

    if ssh_config.exists():
        # Remove existing block for this deployment, then append
        existing = ssh_config.read_text()
        cleaned = _remove_block(existing, marker_begin, marker_end)
        cleaned = cleaned.rstrip("\n")
        new_content = f"{cleaned}\n\n{block}\n" if cleaned else f"{block}\n"
        ssh_config.write_text(new_content)
    else:
        ssh_config.write_text(f"{block}\n")

    ssh_config.chmod(0o600)

    host_count = sum(1 for line in snippet.splitlines() if line.strip().startswith("Host "))
    output.success(f"  SSH config installed ({host_count} entries)")
    return host_count


def remove_ssh_config(deploy_name: str, ssh_config: Path | None = None) -> None:
    """Remove the SSH config block for a deployment."""
    ssh_config = ssh_config or DEFAULT_SSH_CONFIG
    if not ssh_config.exists():
        return

    marker_begin = f"{MARKER_PREFIX}{deploy_name}"
    marker_end = f"{MARKER_SUFFIX}{deploy_name}"

    content = ssh_config.read_text()
    if marker_begin not in content:
        return

    cleaned = _remove_block(content, marker_begin, marker_end).rstrip("\n") + "\n"
    ssh_config.write_text(cleaned)
    ssh_config.chmod(0o600)
    output.dim(f"  Removed SSH config for {deploy_name}")


def remove_all_ruse_blocks(ssh_config: Path | None = None) -> list[str]:
    """Remove all RUSE SSH config blocks. Returns list of removed deployment names."""
    ssh_config = ssh_config or DEFAULT_SSH_CONFIG
    if not ssh_config.exists():
        return []

    content = ssh_config.read_text()
    removed = []

    for match in re.finditer(rf"^{re.escape(MARKER_PREFIX)}(.+)$", content, re.MULTILINE):
        name = match.group(1)
        removed.append(name)

    for name in removed:
        marker_begin = f"{MARKER_PREFIX}{name}"
        marker_end = f"{MARKER_SUFFIX}{name}"
        content = _remove_block(content, marker_begin, marker_end)

    ssh_config.write_text(content.rstrip("\n") + "\n")
    ssh_config.chmod(0o600)
    return removed


def _remove_block(content: str, marker_begin: str, marker_end: str) -> str:
    """Remove a marked block from content."""
    lines = content.splitlines()
    result = []
    skip = False
    for line in lines:
        if line.strip() == marker_begin:
            skip = True
            continue
        if line.strip() == marker_end:
            skip = False
            continue
        if not skip:
            result.append(line)
    return "\n".join(result)
