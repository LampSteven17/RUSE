"""Deployment configuration loading and validation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class DeploymentConfig:
    """Parsed deployment configuration from config.yaml."""

    deployment_name: str
    deployment_type: str = "decoy"  # "decoy", "rampart", or "ghosts"
    flavor_capacity: dict[str, int] = field(default_factory=dict)
    deployments: list[dict] = field(default_factory=list)
    behavior_source: str | None = None
    behavior_configs: list[str] | str | None = None  # "all", list of filenames, or None
    enterprise: dict | None = None
    emulate: dict | None = None
    ghosts: dict | None = None

    @classmethod
    def load(cls, config_path: Path) -> DeploymentConfig:
        """Load and parse a config.yaml file."""
        with open(config_path) as f:
            raw = yaml.safe_load(f)

        return cls(
            deployment_name=raw.get("deployment_name", config_path.parent.name),
            deployment_type=raw.get("type", "decoy"),
            flavor_capacity=raw.get("flavor_capacity", {}),
            deployments=raw.get("deployments", []),
            behavior_source=raw.get("behavior_source"),
            behavior_configs=raw.get("behavior_configs"),
            enterprise=raw.get("enterprise"),
            emulate=raw.get("emulate"),
            ghosts=raw.get("ghosts"),
        )

    def vm_count(self) -> int:
        return sum(d.get("count", 1) for d in self.deployments)

    def has_behavior_configs(self) -> bool:
        return self.behavior_source is not None

    def is_rampart(self) -> bool:
        return self.deployment_type == "rampart"

    def is_ghosts(self) -> bool:
        return self.deployment_type == "ghosts"


    def count_brains(self) -> dict[str, int]:
        """Count VMs by brain category. Returns {C, M, B, S, total}."""
        counts = {"C": 0, "M": 0, "B": 0, "S": 0, "total": 0}
        for d in self.deployments:
            b = d["behavior"]
            count = d.get("count", 1)
            counts["total"] += count
            if b.startswith("C"):
                counts["C"] += count
            elif b.startswith("M"):
                counts["M"] += count
            elif b.startswith("B"):
                counts["B"] += count
            elif b.startswith("S"):
                counts["S"] += count
        return counts

    def brain_summary(self) -> str:
        """Human-readable brain count string like '3c 1m 4b 4s'."""
        c = self.count_brains()
        parts = []
        if c["C"]:
            parts.append(f"{c['C']}c")
        if c["M"]:
            parts.append(f"{c['M']}m")
        if c["B"]:
            parts.append(f"{c['B']}b")
        if c["S"]:
            parts.append(f"{c['S']}s")
        return f"{c['total']} ({' '.join(parts)})" if parts else str(c["total"])

    # --- Enterprise helpers ---

    def enterprise_workflow_dir(self) -> Path:
        if not self.enterprise:
            raise ValueError("Not an enterprise config")
        return Path(os.path.expanduser(self.enterprise.get("workflow_dir", "~/uva-cs-workflow")))

    def enterprise_cloud_config(self) -> str:
        return (self.enterprise or {}).get("cloud_config", "")

    def enterprise_config_file(self) -> str:
        return (self.enterprise or {}).get("enterprise_config", "")

    def enterprise_user_roles(self) -> str:
        return (self.enterprise or {}).get("user_roles", "")

    def emulate_seed(self) -> int:
        return (self.emulate or {}).get("seed", 42)

    def emulate_duration_days(self) -> int:
        return (self.emulate or {}).get("duration_days", 7)

    # --- GHOSTS helpers ---

    def ghosts_api_flavor(self) -> str:
        return (self.ghosts or {}).get("api_flavor", "v1.14vcpu.28g")

    def ghosts_client_flavor(self) -> str:
        return (self.ghosts or {}).get("client_flavor", "v1.14vcpu.28g")

    def ghosts_client_count(self) -> int:
        return (self.ghosts or {}).get("client_count", 5)

    def ghosts_repo(self) -> str:
        return (self.ghosts or {}).get("ghosts_repo", "https://github.com/cmu-sei/GHOSTS.git")

    def ghosts_branch(self) -> str:
        return (self.ghosts or {}).get("ghosts_branch", "master")
