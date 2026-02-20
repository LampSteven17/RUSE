"""
Default behavioral configurations for SUP agents.

Provides factory-default configs that are copied to deployed_sups/<key>/behavioral_configurations/
during install. The PHASE feedback engine's output overwrites specific files when deployed.

Usage:
    from common.behavioral_configurations import get_defaults_dir

    defaults_dir = get_defaults_dir()
    # Copy all *.json from defaults_dir to the target behavioral_configurations/ directory
"""
from pathlib import Path


def get_defaults_dir() -> Path:
    """Return the path to the bundled default behavioral configuration files."""
    return Path(__file__).parent / "defaults"
