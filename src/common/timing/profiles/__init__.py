"""
Bundled PHASE timing profiles for calibrated timing.

Loads timing profiles (JSON) from this directory.
These profiles contain empirical distributions from real network traffic
used to calibrate SUP agent timing behavior.
"""
import json
from pathlib import Path

PROFILE_DIR = Path(__file__).parent

VALID_DATASETS = {"summer24", "fall24", "spring25"}


def load_profile(dataset: str) -> dict:
    """Load a timing profile JSON by dataset name.

    Args:
        dataset: One of "summer24", "fall24", "spring25"

    Returns:
        Parsed JSON profile dict with keys:
        - hourly_distribution: {mean_fraction, std_fraction, median_fraction}
        - burst_characteristics: {burst_duration_minutes, idle_gap_minutes, connections_per_burst}
    """
    if dataset not in VALID_DATASETS:
        raise ValueError(f"Unknown dataset '{dataset}'. Valid: {sorted(VALID_DATASETS)}")

    path = PROFILE_DIR / f"{dataset}_profile.json"
    if not path.exists():
        raise FileNotFoundError(f"Timing profile not found: {path}")

    with open(path) as f:
        return json.load(f)
