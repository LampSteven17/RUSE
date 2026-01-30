"""
Configuration loader for SUP runners.

Maps configuration keys to brain/content/model combinations.

SIMPLIFIED ARCHITECTURE (v2):
- Removed "mechanics" concept (redundant with brain)
- Content augmentation only applies to MCHP brain (for text generation)
- BrowserUse and SmolAgents don't need content augmentation (LLM handles everything)

NAMING SCHEME:
[Brain][Version][Variant].[Model]

Brain:    M = MCHP, B = BrowserUse, S = SmolAgents
          MC = MCHP CPU, BC = BrowserUse CPU, SC = SmolAgents CPU
Version:  1 = Baseline, 2 = PHASE timing
Variant:  a = llama, b = gemma, c = deepseek
          d = lfm, e = ministral, f = qwen (CPU only)

Examples:
- M1          = MCHP baseline (no LLM)
- M1a.llama   = MCHP + llama content
- M2c.deepseek = MCHP + deepseek + PHASE timing
- B1b.gemma   = BrowserUse + gemma
- S2a.llama   = SmolAgents + llama + PHASE timing
"""
from dataclasses import dataclass
from typing import Optional, Literal

BrainType = Literal["mchp", "browseruse", "smolagents"]
ContentType = Literal["none", "llm"]  # Simplified: none or LLM-augmented
ModelType = Literal["llama", "gemma", "deepseek", "lfm", "ministral", "qwen"]


@dataclass
class SUPConfig:
    """Configuration for a SUP agent run."""

    brain: BrainType
    content: ContentType = "none"  # Only relevant for MCHP brain
    model: ModelType = "llama"
    phase: bool = False

    @property
    def config_key(self) -> str:
        """Generate the configuration key (e.g., M1, M1a.llama, B1b.gemma)."""
        if self.brain == "mchp":
            if self.content == "none":
                return "M1"
            else:
                # M1a/M1b/M1c = baseline, M2a/M2b/M2c = PHASE
                base_num = "2" if self.phase else "1"
                variant = _model_to_variant(self.model)
                return f"M{base_num}{variant}.{self.model}"

        elif self.brain == "browseruse":
            # B1a/B1b/B1c = baseline, B2a/B2b/B2c = PHASE
            base_num = "2" if self.phase else "1"
            variant = _model_to_variant(self.model)
            return f"B{base_num}{variant}.{self.model}"

        elif self.brain == "smolagents":
            # S1a/S1b/S1c = baseline, S2a/S2b/S2c = PHASE
            base_num = "2" if self.phase else "1"
            variant = _model_to_variant(self.model)
            return f"S{base_num}{variant}.{self.model}"

        return f"{self.brain}-{self.content}-{self.model}"


def _model_to_variant(model: ModelType) -> str:
    """Map model name to variant letter."""
    return {
        "llama": "a",
        "gemma": "b",
        "deepseek": "c",
        "lfm": "d",
        "ministral": "e",
        "qwen": "f",
    }.get(model, "a")


# Pre-defined configuration shortcuts
CONFIGS = {
    # =========================================================================
    # M Series - MCHP brain
    # =========================================================================
    # Controls (CPU-only, no LLM)
    "M0": SUPConfig(brain="mchp"),  # Upstream MITRE pyhuman (control - DO NOT MODIFY)
    "M1": SUPConfig(brain="mchp"),  # DOLOS MCHP baseline (no LLM)

    # With LLM content (GPU recommended)
    "M1a.llama": SUPConfig(brain="mchp", content="llm", model="llama"),
    "M1b.gemma": SUPConfig(brain="mchp", content="llm", model="gemma"),
    "M1c.deepseek": SUPConfig(brain="mchp", content="llm", model="deepseek"),

    # PHASE timing enabled
    "M2a.llama": SUPConfig(brain="mchp", content="llm", model="llama", phase=True),
    "M2b.gemma": SUPConfig(brain="mchp", content="llm", model="gemma", phase=True),
    "M2c.deepseek": SUPConfig(brain="mchp", content="llm", model="deepseek", phase=True),

    # =========================================================================
    # MC Series - MCHP brain (CPU-only)
    # =========================================================================
    # Baseline (no PHASE timing)
    "MC1a.llama": SUPConfig(brain="mchp", content="llm", model="llama"),
    "MC1b.gemma": SUPConfig(brain="mchp", content="llm", model="gemma"),
    "MC1c.deepseek": SUPConfig(brain="mchp", content="llm", model="deepseek"),
    "MC1d.lfm": SUPConfig(brain="mchp", content="llm", model="lfm"),
    "MC1e.ministral": SUPConfig(brain="mchp", content="llm", model="ministral"),
    "MC1f.qwen": SUPConfig(brain="mchp", content="llm", model="qwen"),

    # PHASE timing enabled
    "MC2a.llama": SUPConfig(brain="mchp", content="llm", model="llama", phase=True),
    "MC2b.gemma": SUPConfig(brain="mchp", content="llm", model="gemma", phase=True),
    "MC2c.deepseek": SUPConfig(brain="mchp", content="llm", model="deepseek", phase=True),
    "MC2d.lfm": SUPConfig(brain="mchp", content="llm", model="lfm", phase=True),
    "MC2e.ministral": SUPConfig(brain="mchp", content="llm", model="ministral", phase=True),
    "MC2f.qwen": SUPConfig(brain="mchp", content="llm", model="qwen", phase=True),

    # =========================================================================
    # B Series - BrowserUse brain (GPU)
    # =========================================================================
    # Baseline (no PHASE timing)
    "B1a.llama": SUPConfig(brain="browseruse", model="llama"),
    "B1b.gemma": SUPConfig(brain="browseruse", model="gemma"),
    "B1c.deepseek": SUPConfig(brain="browseruse", model="deepseek"),

    # PHASE timing enabled
    "B2a.llama": SUPConfig(brain="browseruse", model="llama", phase=True),
    "B2b.gemma": SUPConfig(brain="browseruse", model="gemma", phase=True),
    "B2c.deepseek": SUPConfig(brain="browseruse", model="deepseek", phase=True),

    # =========================================================================
    # BC Series - BrowserUse brain (CPU-only)
    # =========================================================================
    "BC1a.llama": SUPConfig(brain="browseruse", model="llama"),
    "BC1b.gemma": SUPConfig(brain="browseruse", model="gemma"),
    "BC1c.deepseek": SUPConfig(brain="browseruse", model="deepseek"),
    "BC1d.lfm": SUPConfig(brain="browseruse", model="lfm"),
    "BC1e.ministral": SUPConfig(brain="browseruse", model="ministral"),
    "BC1f.qwen": SUPConfig(brain="browseruse", model="qwen"),

    # =========================================================================
    # S Series - SmolAgents brain (GPU)
    # =========================================================================
    # Baseline (no PHASE timing)
    "S1a.llama": SUPConfig(brain="smolagents", model="llama"),
    "S1b.gemma": SUPConfig(brain="smolagents", model="gemma"),
    "S1c.deepseek": SUPConfig(brain="smolagents", model="deepseek"),

    # PHASE timing enabled
    "S2a.llama": SUPConfig(brain="smolagents", model="llama", phase=True),
    "S2b.gemma": SUPConfig(brain="smolagents", model="gemma", phase=True),
    "S2c.deepseek": SUPConfig(brain="smolagents", model="deepseek", phase=True),

    # =========================================================================
    # SC Series - SmolAgents brain (CPU-only)
    # =========================================================================
    "SC1a.llama": SUPConfig(brain="smolagents", model="llama"),
    "SC1b.gemma": SUPConfig(brain="smolagents", model="gemma"),
    "SC1c.deepseek": SUPConfig(brain="smolagents", model="deepseek"),
    "SC1d.lfm": SUPConfig(brain="smolagents", model="lfm"),
    "SC1e.ministral": SUPConfig(brain="smolagents", model="ministral"),
    "SC1f.qwen": SUPConfig(brain="smolagents", model="qwen"),

    # =========================================================================
    # C Series - Control (bare VM)
    # =========================================================================
    "C0": SUPConfig(brain="mchp"),  # Bare Ubuntu VM (no software installed)
}


def get_config(key: str) -> Optional[SUPConfig]:
    """Get a pre-defined configuration by key."""
    return CONFIGS.get(key)


def list_configs() -> list:
    """List all available configuration keys."""
    return list(CONFIGS.keys())


def build_config(
    brain: BrainType,
    content: ContentType = "none",
    model: ModelType = "llama",
    phase: bool = False,
) -> SUPConfig:
    """Build a configuration from individual parameters."""
    return SUPConfig(
        brain=brain,
        content=content,
        model=model,
        phase=phase,
    )
