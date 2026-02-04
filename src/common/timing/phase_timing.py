"""
DOLOS-DEPLOY PHASE Timing Module

Provides realistic timing patterns for agent behavior that mimic human activity.
Includes time-of-day awareness, activity clustering, and configurable profiles.

Usage:
    from common.timing.phase_timing import PhaseTiming, PhaseTimingConfig

    timing = PhaseTiming()  # Use default config
    # Or with custom config
    config = PhaseTimingConfig(min_cluster_size=3, max_cluster_size=10)
    timing = PhaseTiming(config)

    cluster_size = timing.get_cluster_size()
    task_delay = timing.get_task_delay()
    cluster_delay = timing.get_cluster_delay()
"""

import random
import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class PhaseTimingConfig:
    """Configuration for PHASE timing behavior."""

    # Cluster configuration
    min_cluster_size: int = 3
    max_cluster_size: int = 8

    # Inter-task delays (seconds)
    min_task_delay: float = 5.0
    max_task_delay: float = 30.0

    # Inter-cluster delays (seconds)
    min_cluster_delay: float = 120.0  # 2 minutes
    max_cluster_delay: float = 600.0  # 10 minutes

    # Typing simulation (characters per second)
    min_typing_speed: float = 2.0
    max_typing_speed: float = 8.0

    # Reading simulation (seconds per 100 words)
    min_reading_time: float = 10.0
    max_reading_time: float = 30.0

    # Enable time-of-day activity adjustment
    enable_hourly_adjustment: bool = True

    # Activity multipliers by hour (0-23)
    # Values < 1 reduce activity, values > 1 increase activity
    hourly_activity: Dict[int, float] = field(default_factory=lambda: {
        0: 0.1,    # 12 AM - very low
        1: 0.05,   # 1 AM - minimal
        2: 0.02,   # 2 AM - minimal
        3: 0.02,   # 3 AM - minimal
        4: 0.05,   # 4 AM - minimal
        5: 0.2,    # 5 AM - waking up
        6: 0.4,    # 6 AM - early morning
        7: 0.6,    # 7 AM - morning routine
        8: 0.8,    # 8 AM - starting work
        9: 1.0,    # 9 AM - peak morning
        10: 1.2,   # 10 AM - peak
        11: 1.1,   # 11 AM - high
        12: 0.7,   # 12 PM - lunch break
        13: 0.8,   # 1 PM - post-lunch
        14: 1.0,   # 2 PM - afternoon
        15: 1.1,   # 3 PM - afternoon peak
        16: 1.0,   # 4 PM - late afternoon
        17: 0.8,   # 5 PM - winding down
        18: 0.6,   # 6 PM - evening
        19: 0.5,   # 7 PM - evening
        20: 0.4,   # 8 PM - late evening
        21: 0.3,   # 9 PM - night
        22: 0.2,   # 10 PM - late night
        23: 0.15,  # 11 PM - late night
    })

    # Variance in delays (0.0 = exact, 1.0 = highly variable)
    delay_variance: float = 0.3


class PhaseTiming:
    """
    Realistic timing controller for agent behavior.

    Provides human-like timing patterns including:
    - Variable cluster sizes
    - Time-of-day activity adjustment
    - Realistic inter-task and inter-cluster delays
    - Typing and reading time simulation
    """

    def __init__(self, config: Optional[PhaseTimingConfig] = None):
        """
        Initialize timing controller.

        Args:
            config: Optional configuration. Uses defaults if not provided.
        """
        self.config = config or PhaseTimingConfig()
        self._last_activity_time: Optional[float] = None

    def _get_hourly_multiplier(self) -> float:
        """Get activity multiplier for current hour."""
        if not self.config.enable_hourly_adjustment:
            return 1.0

        current_hour = datetime.now().hour
        return self.config.hourly_activity.get(current_hour, 1.0)

    def _add_variance(self, base_value: float) -> float:
        """Add random variance to a value."""
        variance = self.config.delay_variance
        multiplier = random.uniform(1 - variance, 1 + variance)
        return base_value * multiplier

    def get_cluster_size(self) -> int:
        """
        Get the number of tasks for the current cluster.

        Adjusts based on time of day - more tasks during peak hours.
        """
        hourly_mult = self._get_hourly_multiplier()

        # Base range
        min_size = self.config.min_cluster_size
        max_size = self.config.max_cluster_size

        # Adjust range based on activity level
        adjusted_min = max(1, int(min_size * hourly_mult))
        adjusted_max = max(adjusted_min + 1, int(max_size * hourly_mult))

        return random.randint(adjusted_min, adjusted_max)

    def get_task_delay(self) -> float:
        """
        Get delay before the next task within a cluster.

        Returns delay in seconds.
        """
        hourly_mult = self._get_hourly_multiplier()

        base_delay = random.uniform(
            self.config.min_task_delay,
            self.config.max_task_delay
        )

        # Lower activity = longer delays
        adjusted_delay = base_delay / hourly_mult if hourly_mult > 0 else base_delay * 2

        return self._add_variance(adjusted_delay)

    def get_cluster_delay(self) -> float:
        """
        Get delay before the next cluster of tasks.

        Returns delay in seconds.
        """
        hourly_mult = self._get_hourly_multiplier()

        base_delay = random.uniform(
            self.config.min_cluster_delay,
            self.config.max_cluster_delay
        )

        # Lower activity = longer breaks between clusters
        adjusted_delay = base_delay / hourly_mult if hourly_mult > 0 else base_delay * 3

        return self._add_variance(adjusted_delay)

    def get_typing_delay(self, text_length: int) -> float:
        """
        Get realistic typing time for a given text length.

        Args:
            text_length: Number of characters to "type"

        Returns:
            Time in seconds to type the text
        """
        chars_per_second = random.uniform(
            self.config.min_typing_speed,
            self.config.max_typing_speed
        )
        return text_length / chars_per_second

    def get_reading_delay(self, word_count: int) -> float:
        """
        Get realistic reading time for content.

        Args:
            word_count: Number of words to "read"

        Returns:
            Time in seconds to read the content
        """
        time_per_100_words = random.uniform(
            self.config.min_reading_time,
            self.config.max_reading_time
        )
        return (word_count / 100) * time_per_100_words

    def get_think_delay(self) -> float:
        """
        Get a short "thinking" delay for decision making.

        Returns delay in seconds (typically 1-5 seconds).
        """
        return random.uniform(1.0, 5.0)

    def get_page_load_delay(self) -> float:
        """
        Get realistic page load wait time.

        Returns delay in seconds (typically 2-10 seconds).
        """
        return random.uniform(2.0, 10.0)

    def should_take_break(self, tasks_completed: int) -> bool:
        """
        Determine if agent should take a longer break.

        Args:
            tasks_completed: Number of tasks completed in current session

        Returns:
            True if a longer break is recommended
        """
        hourly_mult = self._get_hourly_multiplier()

        # Lower activity hours = more likely to take breaks
        break_threshold = 5 * hourly_mult if hourly_mult > 0 else 2

        return tasks_completed >= break_threshold and random.random() > 0.5

    def get_break_duration(self) -> float:
        """
        Get duration for a longer break.

        Returns duration in seconds (5-30 minutes).
        """
        base_duration = random.uniform(300, 1800)  # 5-30 minutes
        return self._add_variance(base_duration)

    def is_active_hour(self) -> bool:
        """
        Check if current hour is an active period.

        Returns True if hourly multiplier >= 0.5
        """
        return self._get_hourly_multiplier() >= 0.5

    def get_activity_level(self) -> str:
        """
        Get human-readable activity level for current hour.

        Returns: "minimal", "low", "moderate", "high", or "peak"
        """
        mult = self._get_hourly_multiplier()

        if mult < 0.1:
            return "minimal"
        elif mult < 0.4:
            return "low"
        elif mult < 0.8:
            return "moderate"
        elif mult < 1.1:
            return "high"
        else:
            return "peak"

    def record_activity(self) -> None:
        """Record that activity occurred (for tracking)."""
        self._last_activity_time = time.time()

    def time_since_last_activity(self) -> Optional[float]:
        """Get seconds since last recorded activity."""
        if self._last_activity_time is None:
            return None
        return time.time() - self._last_activity_time


# Preset configurations for different agent profiles
PRESET_CONFIGS = {
    "default": PhaseTimingConfig(),

    "aggressive": PhaseTimingConfig(
        min_cluster_size=5,
        max_cluster_size=12,
        min_task_delay=2.0,
        max_task_delay=15.0,
        min_cluster_delay=60.0,
        max_cluster_delay=300.0,
    ),

    "conservative": PhaseTimingConfig(
        min_cluster_size=2,
        max_cluster_size=5,
        min_task_delay=15.0,
        max_task_delay=60.0,
        min_cluster_delay=300.0,
        max_cluster_delay=900.0,
    ),

    "mchp_compatible": PhaseTimingConfig(
        # Match original MCHP defaults
        min_cluster_size=5,
        max_cluster_size=5,
        min_task_delay=0.0,
        max_task_delay=10.0,
        min_cluster_delay=0.0,
        max_cluster_delay=500.0,
        enable_hourly_adjustment=False,  # MCHP doesn't have this
    ),
}


def get_preset_config(name: str) -> PhaseTimingConfig:
    """
    Get a preset timing configuration.

    Args:
        name: Preset name ("default", "aggressive", "conservative", "mchp_compatible")

    Returns:
        PhaseTimingConfig instance

    Raises:
        ValueError if preset name is unknown
    """
    if name not in PRESET_CONFIGS:
        available = ", ".join(PRESET_CONFIGS.keys())
        raise ValueError(f"Unknown preset '{name}'. Available: {available}")
    return PRESET_CONFIGS[name]


# ============================================================================
# Calibrated Timing (exp-3+)
# ============================================================================

@dataclass
class CalibratedTimingConfig:
    """Configuration loaded from an empirical timing profile."""
    dataset: str                        # "summer24", "fall24", "spring25"
    hourly_fractions: list              # 24-element array (mean_fraction per hour)
    burst_duration: dict                # percentile data {5, 25, 50, 75, 95}
    idle_gap: dict                      # percentile data {5, 25, 50, 75, 95}
    connections_per_burst: dict         # percentile data {5, 25, 50, 75, 95}


class CalibratedTiming:
    """
    Timing controller that samples from empirical distributions.

    Uses percentile-based interpolation from real network traffic profiles
    instead of hardcoded timing parameters. Same public interface as PhaseTiming.
    """

    _PERCENTILE_POINTS = [0.05, 0.25, 0.50, 0.75, 0.95]
    _PERCENTILE_KEYS = ["5", "25", "50", "75", "95"]

    def __init__(self, config: CalibratedTimingConfig):
        self.config = config
        self._last_activity_time: Optional[float] = None

        # Normalize hourly fractions so peak hour = 1.0
        max_fraction = max(config.hourly_fractions)
        if max_fraction > 0:
            self._hourly_scale = [f / max_fraction for f in config.hourly_fractions]
        else:
            self._hourly_scale = [1.0] * 24

    def _sample_percentile(self, percentiles: dict) -> float:
        """Sample by interpolating between p5/p25/p50/p75/p95 breakpoints."""
        u = random.random()
        points = self._PERCENTILE_POINTS
        values = [float(percentiles[k]) for k in self._PERCENTILE_KEYS]

        if u <= points[0]:
            return values[0]
        if u >= points[-1]:
            overshoot = (u - points[-1]) / (1.0 - points[-1])
            return values[-1] * (1.0 + 0.5 * overshoot)

        for i in range(len(points) - 1):
            if points[i] <= u <= points[i + 1]:
                t = (u - points[i]) / (points[i + 1] - points[i])
                return values[i] + t * (values[i + 1] - values[i])

        return values[2]  # fallback: median

    def _get_hourly_scale(self) -> float:
        """Get current hour's activity scale factor (0..1, peak=1.0)."""
        return self._hourly_scale[datetime.now().hour]

    def get_cluster_size(self) -> int:
        """Sample connections_per_burst from the profile."""
        raw = self._sample_percentile(self.config.connections_per_burst)
        scaled = raw * self._get_hourly_scale()
        return max(1, min(int(scaled), 15))

    def get_task_delay(self) -> float:
        """Intra-burst pacing derived from burst_duration / connections_per_burst."""
        burst_min = self._sample_percentile(self.config.burst_duration)
        conns = max(1, self._sample_percentile(self.config.connections_per_burst))
        per_task_seconds = (burst_min * 60.0) / conns
        return max(2.0, min(per_task_seconds, 60.0))

    def get_cluster_delay(self) -> float:
        """Sample idle_gap, scaled inversely by hourly activity."""
        gap_minutes = self._sample_percentile(self.config.idle_gap)
        hourly_scale = self._get_hourly_scale()
        scale_factor = 1.0 / hourly_scale if hourly_scale > 0.05 else 20.0
        gap_seconds = gap_minutes * 60.0 * scale_factor
        return max(30.0, min(gap_seconds, 3600.0))

    def should_take_break(self, tasks_completed: int) -> bool:
        """Decide whether to take an extended break based on hourly fraction."""
        hourly_scale = self._get_hourly_scale()
        break_threshold = max(2, int(8 * hourly_scale))
        return tasks_completed >= break_threshold and random.random() > 0.4

    def get_break_duration(self) -> float:
        """Extended idle gap for breaks (5-30 minutes)."""
        gap_minutes = self._sample_percentile(self.config.idle_gap)
        break_minutes = gap_minutes * random.uniform(2.0, 5.0)
        return max(300.0, min(break_minutes * 60.0, 1800.0))

    def is_active_hour(self) -> bool:
        return self._get_hourly_scale() >= 0.5

    def get_activity_level(self) -> str:
        scale = self._get_hourly_scale()
        if scale < 0.3:
            return "minimal"
        elif scale < 0.5:
            return "low"
        elif scale < 0.7:
            return "moderate"
        elif scale < 0.9:
            return "high"
        else:
            return "peak"

    def record_activity(self) -> None:
        self._last_activity_time = time.time()

    def time_since_last_activity(self) -> Optional[float]:
        if self._last_activity_time is None:
            return None
        return time.time() - self._last_activity_time


def load_calibration_profile(dataset: str) -> CalibratedTimingConfig:
    """Load a CalibratedTimingConfig from a bundled profile JSON.

    Args:
        dataset: One of "summer24", "fall24", "spring25"
    """
    from common.timing.profiles import load_profile

    profile = load_profile(dataset)
    burst = profile["burst_characteristics"]

    return CalibratedTimingConfig(
        dataset=dataset,
        hourly_fractions=profile["hourly_distribution"]["mean_fraction"],
        burst_duration=burst["burst_duration_minutes"]["percentiles"],
        idle_gap=burst["idle_gap_minutes"]["percentiles"],
        connections_per_burst=burst["connections_per_burst"]["percentiles"],
    )
