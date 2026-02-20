"""
Configuration loader for SUP runners.

Maps configuration keys to brain/content/model combinations.

ARCHITECTURE: Brain + Content (MCHP only) + Model

NAMING SCHEME:
[Brain][Version].[Model]

Brain:    M = MCHP, B = BrowserUse, S = SmolAgents
Version:  0 = baseline (B/S only, no behavioral configs)
          1 = baseline (MCHP only, no behavioral configs)
          2+ = iteration number (behavior from behavioral_configurations/)
Models:   llama (llama3.1:8b), gemma (gemma3:1b)

MCHP has no LLM — pure scripted automation, no model suffix.
Baselines (v0/v1) run clean with no behavioral configs.
Iteration configs (v2+) get ALL behavior from behavioral_configurations/
directory — either shipped defaults or PHASE feedback engine overrides.

Examples:
- M1          = MCHP baseline (no behavioral configs)
- M2          = MCHP iteration 2
- B0.llama    = BrowserUse + llama baseline (no behavioral configs)
- B3.gemma    = BrowserUse + gemma iteration 3
- S4.llama    = SmolAgents + llama iteration 4
"""
import warnings
from dataclasses import dataclass
from typing import Optional, Literal

BrainType = Literal["mchp", "browseruse", "smolagents"]
ContentType = Literal["none", "llm"]
ModelType = Literal["llama", "gemma", "deepseek", "lfm", "ministral", "qwen"]

@dataclass
class SUPConfig:
    """Configuration for a SUP agent run."""
    brain: BrainType
    content: ContentType = "none"
    model: Optional[ModelType] = None
    calibration: Optional[str] = None    # "summer24"/"fall24"/"spring25"/None
    cpu_only: bool = False
    seed: int = 42
    _key_override: Optional[str] = None  # Explicit key for non-standard naming (e.g., B0R.llama)

    # Kept for backward compat with exp-2 code that reads config.phase
    @property
    def phase(self) -> bool:
        """Backward compat: True if any calibration is set."""
        return self.calibration is not None

    @property
    def config_key(self) -> str:
        """Generate the configuration key (e.g., M1, B0.llama, B3.gemma)."""
        if self._key_override:
            return self._key_override
        # Baseline version: MCHP=1, BrowserUse/SmolAgents=0
        version = 1 if self.brain == "mchp" else 0

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
    "M2": SUPConfig(brain="mchp", _key_override="M2"),
    "M3": SUPConfig(brain="mchp", _key_override="M3"),
    "M4": SUPConfig(brain="mchp", _key_override="M4"),

    # === BrowserUse ===
    "B0.llama": SUPConfig(brain="browseruse", model="llama"),
    "B0.gemma": SUPConfig(brain="browseruse", model="gemma"),
    "B2.llama": SUPConfig(brain="browseruse", model="llama", _key_override="B2.llama"),
    "B2.gemma": SUPConfig(brain="browseruse", model="gemma", _key_override="B2.gemma"),
    "B3.llama": SUPConfig(brain="browseruse", model="llama", _key_override="B3.llama"),
    "B3.gemma": SUPConfig(brain="browseruse", model="gemma", _key_override="B3.gemma"),
    "B4.llama": SUPConfig(brain="browseruse", model="llama", _key_override="B4.llama"),
    "B4.gemma": SUPConfig(brain="browseruse", model="gemma", _key_override="B4.gemma"),

    # === SmolAgents ===
    "S0.llama": SUPConfig(brain="smolagents", model="llama"),
    "S0.gemma": SUPConfig(brain="smolagents", model="gemma"),
    "S2.llama": SUPConfig(brain="smolagents", model="llama", _key_override="S2.llama"),
    "S2.gemma": SUPConfig(brain="smolagents", model="gemma", _key_override="S2.gemma"),
    "S3.llama": SUPConfig(brain="smolagents", model="llama", _key_override="S3.llama"),
    "S3.gemma": SUPConfig(brain="smolagents", model="gemma", _key_override="S3.gemma"),
    "S4.llama": SUPConfig(brain="smolagents", model="llama", _key_override="S4.llama"),
    "S4.gemma": SUPConfig(brain="smolagents", model="gemma", _key_override="S4.gemma"),

    # === CPU baselines (no GPU — Ollama runs on CPU) ===
    "B0C.llama": SUPConfig(brain="browseruse", model="llama", cpu_only=True, _key_override="B0C.llama"),
    "B0C.gemma": SUPConfig(brain="browseruse", model="gemma", cpu_only=True, _key_override="B0C.gemma"),
    "S0C.llama": SUPConfig(brain="smolagents", model="llama", cpu_only=True, _key_override="S0C.llama"),
    "S0C.gemma": SUPConfig(brain="smolagents", model="gemma", cpu_only=True, _key_override="S0C.gemma"),

    # === CPU iteration 2 (no GPU) ===
    "B2C.llama": SUPConfig(brain="browseruse", model="llama", cpu_only=True, _key_override="B2C.llama"),
    "B2C.gemma": SUPConfig(brain="browseruse", model="gemma", cpu_only=True, _key_override="B2C.gemma"),
    "S2C.llama": SUPConfig(brain="smolagents", model="llama", cpu_only=True, _key_override="S2C.llama"),
    "S2C.gemma": SUPConfig(brain="smolagents", model="gemma", cpu_only=True, _key_override="S2C.gemma"),

    # === RTX baselines (same as B0/S0 but deployed on RTX 2080 Ti) ===
    "B0R.llama": SUPConfig(brain="browseruse", model="llama", _key_override="B0R.llama"),
    "B0R.gemma": SUPConfig(brain="browseruse", model="gemma", _key_override="B0R.gemma"),
    "S0R.llama": SUPConfig(brain="smolagents", model="llama", _key_override="S0R.llama"),
    "S0R.gemma": SUPConfig(brain="smolagents", model="gemma", _key_override="S0R.gemma"),

    # === RTX iteration 2 (deployed on RTX 2080 Ti) ===
    "B2R.llama": SUPConfig(brain="browseruse", model="llama", _key_override="B2R.llama"),
    "B2R.gemma": SUPConfig(brain="browseruse", model="gemma", _key_override="B2R.gemma"),
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

    # BrowserUse exp-2 keys (drop variant letter, drop deepseek)
    "B1a.llama": "B0.llama",
    "B1b.gemma": "B0.gemma",
    "B1c.deepseek": "B0.llama",
    "B2a.llama": "B2.llama",
    "B2b.gemma": "B2.gemma",
    "B2c.deepseek": "B2.llama",

    # SmolAgents exp-2 keys (drop variant letter, drop deepseek)
    "S1a.llama": "S0.llama",
    "S1b.gemma": "S0.gemma",
    "S1c.deepseek": "S0.llama",
    "S2a.llama": "S2.llama",
    "S2b.gemma": "S2.gemma",
    "S2c.deepseek": "S2.llama",

    # Old B1/S1 baseline keys -> B0/S0
    "B1.llama": "B0.llama",
    "B1.gemma": "B0.gemma",
    "S1.llama": "S0.llama",
    "S1.gemma": "S0.gemma",

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
    "BC1a.llama": "B0.llama",
    "BC1b.gemma": "B0.gemma",
    "BC1c.deepseek": "B0.llama",
    "BC1d.lfm": "B0.llama",
    "BC1e.ministral": "B0.llama",
    "BC1f.qwen": "B0.llama",
    "SC1a.llama": "S0.llama",
    "SC1b.gemma": "S0.gemma",
    "SC1c.deepseek": "S0.llama",
    "SC1d.lfm": "S0.llama",
    "SC1e.ministral": "S0.llama",
    "SC1f.qwen": "S0.llama",
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
