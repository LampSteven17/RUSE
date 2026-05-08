"""OpenStack CLI wrapper with caching."""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path


class OpenStack:
    """Wrapper around the OpenStack CLI that sources credentials from an RC file."""

    def __init__(self, rc_file: Path | None = None):
        self.rc_file = rc_file or Path.home() / "vxn3kr-bot-rc"
        self._server_cache: list[str] | None = None

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """Run an openstack CLI command with sourced credentials."""
        cmd = f"source {shlex.quote(str(self.rc_file))} && openstack {shlex.join(args)}"
        return subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True,
            text=True,
            check=check,
        )

    def server_list(self, refresh: bool = False) -> list[str]:
        """Return list of server names. Cached after first call."""
        if self._server_cache is None or refresh:
            result = self._run("server", "list", "-f", "value", "-c", "Name", check=False)
            if result.returncode == 0:
                self._server_cache = [
                    line.strip() for line in result.stdout.splitlines() if line.strip()
                ]
            else:
                self._server_cache = []
        return self._server_cache

    def has_vms_with_prefix(self, prefix: str) -> bool:
        """Check if any VMs exist with the given name prefix."""
        return any(name.startswith(prefix) for name in self.server_list())

    def count_vms_with_prefix(self, prefix: str) -> int:
        """Count VMs matching a name prefix."""
        return sum(1 for name in self.server_list() if name.startswith(prefix))

    def server_list_with_ids(self, prefix: str | None = None) -> list[dict]:
        """Return list of {id, name} dicts, optionally filtered by prefix."""
        result = self._run("server", "list", "-f", "value", "-c", "ID", "-c", "Name", check=False)
        servers = []
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) == 2:
                    sid, name = parts
                    if prefix is None or name.startswith(prefix):
                        servers.append({"id": sid, "name": name})
        return servers

    def server_delete(self, server_id: str) -> bool:
        """Delete a server by ID. Returns True on success."""
        result = self._run("server", "delete", server_id, check=False)
        return result.returncode == 0

    def server_show(self, name_or_id: str) -> dict | None:
        """Get server details as dict. Returns None if not found."""
        result = self._run("server", "show", name_or_id, "-f", "json", check=False)
        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return None
        return None

    def server_exists(self, name_or_id: str) -> bool:
        """Check if a server exists."""
        result = self._run("server", "show", name_or_id, "-f", "value", "-c", "status", check=False)
        return result.returncode == 0

    def volume_list(self, prefix: str | None = None) -> list[dict]:
        """List volumes, optionally filtered by name prefix."""
        result = self._run("volume", "list", "-f", "json", check=False)
        volumes = []
        if result.returncode == 0:
            try:
                all_vols = json.loads(result.stdout)
                for v in all_vols:
                    name = v.get("Name", "")
                    if prefix is None or name.startswith(prefix):
                        volumes.append(v)
            except json.JSONDecodeError:
                pass
        return volumes

    def volume_delete(self, volume_id: str) -> bool:
        """Delete a volume by ID."""
        result = self._run("volume", "delete", volume_id, check=False)
        return result.returncode == 0

    def find_orphaned_volumes(self, size: int = 200) -> list[dict]:
        """Find volumes with no name, given size, and 'available' status."""
        result = self._run("volume", "list", "-f", "json", check=False)
        orphans = []
        if result.returncode == 0:
            try:
                for v in json.loads(result.stdout):
                    if (
                        not v.get("Name", "").strip()
                        and v.get("Size") == size
                        and v.get("Status") == "available"
                    ):
                        orphans.append(v)
            except json.JSONDecodeError:
                pass
        return orphans

    def zone_list(self) -> list[dict]:
        """List DNS zones."""
        result = self._run("zone", "list", "-f", "json", check=False)
        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return []
        return []

    def zone_find(self, name: str) -> dict | None:
        """Find a DNS zone by name."""
        for z in self.zone_list():
            zone_name = z.get("name", "")
            # Designate zone names have trailing dot
            if zone_name in (name, f"{name}."):
                return z
        return None

    def zone_delete(self, zone_id: str) -> bool:
        """Delete a DNS zone."""
        result = self._run("zone", "delete", zone_id, check=False)
        return result.returncode == 0

    def invalidate_cache(self) -> None:
        """Force cache refresh on next server_list() call."""
        self._server_cache = None
