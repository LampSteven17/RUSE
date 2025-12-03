"""
SUP - Synthetic User Persona

Unified agent framework for human behavior simulation experiments.

Usage:
    python -m sup --brain <BRAIN> [options]
    python -m sup <CONFIG_KEY>

See `python -m sup --help` for full usage.
"""
from runners import (
    SUPConfig,
    CONFIGS,
    get_config,
    list_configs,
    build_config,
    run_mchp,
    run_browseruse,
    run_smolagents,
)

__version__ = "0.1.0"

__all__ = [
    'SUPConfig',
    'CONFIGS',
    'get_config',
    'list_configs',
    'build_config',
    'run_mchp',
    'run_browseruse',
    'run_smolagents',
]
