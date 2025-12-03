"""
Runners module for SUP agent configurations.

Provides entry points for all experiment configurations.
"""
from runners.run_config import (
    SUPConfig,
    CONFIGS,
    get_config,
    list_configs,
    build_config,
)
from runners.run_mchp import run_mchp
from runners.run_browseruse import run_browseruse
from runners.run_smolagents import run_smolagents

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
