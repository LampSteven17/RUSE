"""
Configuration loader for SUP runners.

Maps configuration keys to brain/augmentation/model combinations.
"""
from dataclasses import dataclass
from typing import Optional, Literal

BrainType = Literal["mchp", "browseruse", "smolagents"]
ControllerType = Literal["none", "smolagents", "browseruse"]
ModelType = Literal["llama", "gemma", "deepseek", "lfm", "ministral", "qwen"]


@dataclass
class SUPConfig:
    """Configuration for a SUP agent run."""

    brain: BrainType
    content: ControllerType = "none"
    mechanics: ControllerType = "none"
    model: ModelType = "llama"
    phase: bool = False

    @property
    def config_key(self) -> str:
        """Generate the configuration key (e.g., M1, B2.gemma, S4.llama)."""
        if self.brain == "mchp":
            if self.content == "none" and self.mechanics == "none":
                return "M1"
            elif self.content == "smolagents":
                # M2 = SmolAgents baseline, M4 = SmolAgents + PHASE timing
                base_num = "4" if self.phase else "2"
                if self.mechanics == "smolagents":
                    return f"M{base_num}.{self.model}"
                elif self.mechanics == "none":
                    return f"M{base_num}a.{self.model}"
            elif self.content == "none" and self.mechanics == "smolagents":
                base_num = "4" if self.phase else "2"
                return f"M{base_num}b.{self.model}"
            elif self.content == "browseruse":
                # M3 = BrowserUse baseline, M5 = BrowserUse + PHASE timing
                base_num = "5" if self.phase else "3"
                if self.mechanics == "browseruse":
                    return f"M{base_num}.{self.model}"
                elif self.mechanics == "none":
                    return f"M{base_num}a.{self.model}"
            elif self.content == "none" and self.mechanics == "browseruse":
                base_num = "5" if self.phase else "3"
                return f"M{base_num}b.{self.model}"

        elif self.brain == "browseruse":
            # B1-B3: Baseline, B4-B6: Improved (loop mode + PHASE timing)
            base = "B"
            if self.phase:
                num = {"llama": "4", "gemma": "5", "deepseek": "6"}[self.model]
            else:
                num = {"llama": "1", "gemma": "2", "deepseek": "3"}[self.model]
            return f"{base}{num}.{self.model}"

        elif self.brain == "smolagents":
            # S1-S3: Baseline, S4-S6: Improved (loop mode + PHASE timing)
            base = "S"
            if self.phase:
                num = {"llama": "4", "gemma": "5", "deepseek": "6"}[self.model]
            else:
                num = {"llama": "1", "gemma": "2", "deepseek": "3"}[self.model]
            return f"{base}{num}.{self.model}"

        return f"{self.brain}-{self.content}-{self.mechanics}-{self.model}"


# Pre-defined configuration shortcuts
CONFIGS = {
    # M Series - MCHP brain (Baseline)
    "M0": SUPConfig(brain="mchp"),  # Upstream MITRE pyhuman (control)
    "M1": SUPConfig(brain="mchp"),  # DOLOS MCHP baseline
    "M2.llama": SUPConfig(brain="mchp", content="smolagents", mechanics="smolagents", model="llama"),
    "M2a.llama": SUPConfig(brain="mchp", content="smolagents", mechanics="none", model="llama"),
    "M2b.llama": SUPConfig(brain="mchp", content="none", mechanics="smolagents", model="llama"),
    "M3.llama": SUPConfig(brain="mchp", content="browseruse", mechanics="browseruse", model="llama"),
    "M3a.llama": SUPConfig(brain="mchp", content="browseruse", mechanics="none", model="llama"),
    "M3b.llama": SUPConfig(brain="mchp", content="none", mechanics="browseruse", model="llama"),

    # M Series - MCHP brain (Improved: with PHASE timing)
    "M4.llama": SUPConfig(brain="mchp", content="smolagents", mechanics="smolagents", model="llama", phase=True),
    "M4a.llama": SUPConfig(brain="mchp", content="smolagents", mechanics="none", model="llama", phase=True),
    "M4b.llama": SUPConfig(brain="mchp", content="none", mechanics="smolagents", model="llama", phase=True),
    "M5.llama": SUPConfig(brain="mchp", content="browseruse", mechanics="browseruse", model="llama", phase=True),
    "M5a.llama": SUPConfig(brain="mchp", content="browseruse", mechanics="none", model="llama", phase=True),
    "M5b.llama": SUPConfig(brain="mchp", content="none", mechanics="browseruse", model="llama", phase=True),

    # B Series - BrowserUse brain (Baseline)
    "B1.llama": SUPConfig(brain="browseruse", model="llama"),
    "B2.gemma": SUPConfig(brain="browseruse", model="gemma"),
    "B3.deepseek": SUPConfig(brain="browseruse", model="deepseek"),

    # B Series - BrowserUseLoop (Improved: MCHP workflows + PHASE timing)
    "B4.llama": SUPConfig(brain="browseruse", model="llama", phase=True),
    "B5.gemma": SUPConfig(brain="browseruse", model="gemma", phase=True),
    "B6.deepseek": SUPConfig(brain="browseruse", model="deepseek", phase=True),

    # S Series - SmolAgents brain (Baseline)
    "S1.llama": SUPConfig(brain="smolagents", model="llama"),
    "S2.gemma": SUPConfig(brain="smolagents", model="gemma"),
    "S3.deepseek": SUPConfig(brain="smolagents", model="deepseek"),

    # S Series - SmolAgentLoop (Improved: MCHP workflows + PHASE timing)
    "S4.llama": SUPConfig(brain="smolagents", model="llama", phase=True),
    "S5.gemma": SUPConfig(brain="smolagents", model="gemma", phase=True),
    "S6.deepseek": SUPConfig(brain="smolagents", model="deepseek", phase=True),

    # BC Series - BrowserUse CPU (Baseline, no GPU)
    "BC1.llama": SUPConfig(brain="browseruse", model="llama"),
    "BC2.gemma": SUPConfig(brain="browseruse", model="gemma"),
    "BC3.deepseek": SUPConfig(brain="browseruse", model="deepseek"),
    "BC7.lfm": SUPConfig(brain="browseruse", model="lfm"),
    "BC8.ministral": SUPConfig(brain="browseruse", model="ministral"),
    "BC9.qwen": SUPConfig(brain="browseruse", model="qwen"),

    # SC Series - SmolAgents CPU (Baseline, no GPU)
    "SC1.llama": SUPConfig(brain="smolagents", model="llama"),
    "SC2.gemma": SUPConfig(brain="smolagents", model="gemma"),
    "SC3.deepseek": SUPConfig(brain="smolagents", model="deepseek"),
    "SC7.lfm": SUPConfig(brain="smolagents", model="lfm"),
    "SC8.ministral": SUPConfig(brain="smolagents", model="ministral"),
    "SC9.qwen": SUPConfig(brain="smolagents", model="qwen"),
}


def get_config(key: str) -> Optional[SUPConfig]:
    """Get a pre-defined configuration by key."""
    return CONFIGS.get(key)


def list_configs() -> list:
    """List all available configuration keys."""
    return list(CONFIGS.keys())


def build_config(
    brain: BrainType,
    content: ControllerType = "none",
    mechanics: ControllerType = "none",
    model: ModelType = "llama",
    phase: bool = False,
) -> SUPConfig:
    """Build a configuration from individual parameters."""
    return SUPConfig(
        brain=brain,
        content=content,
        mechanics=mechanics,
        model=model,
        phase=phase,
    )
