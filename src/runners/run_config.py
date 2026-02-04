"""
Configuration loader for SUP runners.

Maps configuration keys to brain/content/model combinations.

ARCHITECTURE: Brain + Content (MCHP only) + Model + Calibration

NAMING SCHEME (exp-3):
[Brain][Version].[Model]

Brain:    M = MCHP, B = BrowserUse, S = SmolAgents
Version:  1 = baseline (no timing)
          2 = calibrated to summer24
          3 = calibrated to fall24
          4 = calibrated to spring25
Models:   llama (llama3.1:8b), gemma (gemma3:4b)

MCHP has no LLM — pure scripted automation, no model suffix.

Examples:
- M1          = MCHP baseline (no timing)
- M2          = MCHP + summer24 calibrated timing
- B3.gemma    = BrowserUse + gemma + fall24 calibrated timing
- S4.llama    = SmolAgents + llama + spring25 calibrated timing
"""
import warnings
from dataclasses import dataclass
from typing import Optional, Literal

BrainType = Literal["mchp", "browseruse", "smolagents"]
ContentType = Literal["none", "llm"]
ModelType = Literal["llama", "gemma", "deepseek", "lfm", "ministral", "qwen"]

# Version number -> calibration dataset mapping
_VERSION_TO_CALIBRATION = {
    1: None,
    2: "summer24",
    3: "fall24",
    4: "spring25",
}

_CALIBRATION_TO_VERSION = {v: k for k, v in _VERSION_TO_CALIBRATION.items()}


@dataclass
class SUPConfig:
    """Configuration for a SUP agent run."""
    brain: BrainType
    content: ContentType = "none"
    model: Optional[ModelType] = None
    calibration: Optional[str] = None    # "summer24"/"fall24"/"spring25"/None
    cpu_only: bool = False
    seed: int = 42

    # Kept for backward compat with exp-2 code that reads config.phase
    @property
    def phase(self) -> bool:
        """Backward compat: True if any calibration is set."""
        return self.calibration is not None

    @property
    def config_key(self) -> str:
        """Generate the configuration key (e.g., M1, B3.gemma)."""
        version = _CALIBRATION_TO_VERSION.get(self.calibration, 1)

        if self.brain == "mchp":
            prefix = "MC" if self.cpu_only else "M"
            return f"{prefix}{version}"
        elif self.brain == "browseruse":
            prefix = "BC" if self.cpu_only else "B"
            return f"{prefix}{version}.{self.model}"
        elif self.brain == "smolagents":
            prefix = "SC" if self.cpu_only else "S"
            return f"{prefix}{version}.{self.model}"
        return f"{self.brain}-{self.content}-{self.model}"


# ============================================================================
# Primary configurations (22 for exp-3)
# ============================================================================

CONFIGS = {
    # === Controls ===
    "C0": SUPConfig(brain="mchp"),
    "M0": SUPConfig(brain="mchp"),

    # === MCHP (no LLM, no model) ===
    "M1": SUPConfig(brain="mchp"),
    "M2": SUPConfig(brain="mchp", calibration="summer24"),
    "M3": SUPConfig(brain="mchp", calibration="fall24"),
    "M4": SUPConfig(brain="mchp", calibration="spring25"),

    # === BrowserUse ===
    "B1.llama": SUPConfig(brain="browseruse", model="llama"),
    "B1.gemma": SUPConfig(brain="browseruse", model="gemma"),
    "B2.llama": SUPConfig(brain="browseruse", model="llama", calibration="summer24"),
    "B2.gemma": SUPConfig(brain="browseruse", model="gemma", calibration="summer24"),
    "B3.llama": SUPConfig(brain="browseruse", model="llama", calibration="fall24"),
    "B3.gemma": SUPConfig(brain="browseruse", model="gemma", calibration="fall24"),
    "B4.llama": SUPConfig(brain="browseruse", model="llama", calibration="spring25"),
    "B4.gemma": SUPConfig(brain="browseruse", model="gemma", calibration="spring25"),

    # === SmolAgents ===
    "S1.llama": SUPConfig(brain="smolagents", model="llama"),
    "S1.gemma": SUPConfig(brain="smolagents", model="gemma"),
    "S2.llama": SUPConfig(brain="smolagents", model="llama", calibration="summer24"),
    "S2.gemma": SUPConfig(brain="smolagents", model="gemma", calibration="summer24"),
    "S3.llama": SUPConfig(brain="smolagents", model="llama", calibration="fall24"),
    "S3.gemma": SUPConfig(brain="smolagents", model="gemma", calibration="fall24"),
    "S4.llama": SUPConfig(brain="smolagents", model="llama", calibration="spring25"),
    "S4.gemma": SUPConfig(brain="smolagents", model="gemma", calibration="spring25"),
}

# ============================================================================
# Deprecated aliases (exp-2 backward compat)
# ============================================================================

_ALIASES = {
    # MCHP exp-2 keys -> exp-3 (MCHP has no LLM now, map to base M configs)
    "M1a.llama": "M1",
    "M1b.gemma": "M1",
    "M1c.deepseek": "M1",
    "M2a.llama": "M2",
    "M2b.gemma": "M2",
    "M2c.deepseek": "M2",

    # BrowserUse exp-2 keys -> exp-3 (drop variant letter, drop deepseek)
    "B1a.llama": "B1.llama",
    "B1b.gemma": "B1.gemma",
    "B1c.deepseek": "B1.llama",
    "B2a.llama": "B2.llama",
    "B2b.gemma": "B2.gemma",
    "B2c.deepseek": "B2.llama",

    # SmolAgents exp-2 keys -> exp-3 (drop variant letter, drop deepseek)
    "S1a.llama": "S1.llama",
    "S1b.gemma": "S1.gemma",
    "S1c.deepseek": "S1.llama",
    "S2a.llama": "S2.llama",
    "S2b.gemma": "S2.gemma",
    "S2c.deepseek": "S2.llama",

    # CPU variants -> map to base configs (no CPU configs in exp-3)
    "MC1a.llama": "M1",
    "MC1b.gemma": "M1",
    "MC1c.deepseek": "M1",
    "MC1d.lfm": "M1",
    "MC1e.ministral": "M1",
    "MC1f.qwen": "M1",
    "MC2a.llama": "M2",
    "MC2b.gemma": "M2",
    "MC2c.deepseek": "M2",
    "MC2d.lfm": "M2",
    "MC2e.ministral": "M2",
    "MC2f.qwen": "M2",
    "BC1a.llama": "B1.llama",
    "BC1b.gemma": "B1.gemma",
    "BC1c.deepseek": "B1.llama",
    "BC1d.lfm": "B1.llama",
    "BC1e.ministral": "B1.llama",
    "BC1f.qwen": "B1.llama",
    "SC1a.llama": "S1.llama",
    "SC1b.gemma": "S1.gemma",
    "SC1c.deepseek": "S1.llama",
    "SC1d.lfm": "S1.llama",
    "SC1e.ministral": "S1.llama",
    "SC1f.qwen": "S1.llama",
}


def get_config(key: str) -> Optional[SUPConfig]:
    """Look up a config by key, checking aliases with deprecation warning."""
    if key in CONFIGS:
        return CONFIGS[key]

    if key in _ALIASES:
        target = _ALIASES[key]
        warnings.warn(
            f"Config key '{key}' is deprecated, use '{target}' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return CONFIGS[target]

    return None


def list_configs() -> list:
    """List all primary config keys (not aliases)."""
    return list(CONFIGS.keys())


def list_aliases() -> dict:
    """List all deprecated alias mappings."""
    return dict(_ALIASES)


def build_config(brain: BrainType, content: ContentType = "none",
                 model: Optional[ModelType] = None,
                 calibration: Optional[str] = None,
                 cpu_only: bool = False,
                 phase: bool = False,
                 seed: int = 42) -> SUPConfig:
    """Build a config from individual parameters.

    Args:
        phase: Backward compat flag. If True and calibration is None,
               defaults to calibration="summer24" (exp-2 behavior).
        seed: Random seed for deterministic behavior (0 = non-deterministic).
    """
    # Backward compat: --phase without --calibration means summer24
    if phase and calibration is None:
        calibration = "summer24"
    return SUPConfig(brain=brain, content=content, model=model,
                     calibration=calibration, cpu_only=cpu_only, seed=seed)
