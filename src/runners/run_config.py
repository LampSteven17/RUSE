"""
Configuration loader for SUP runners.

Maps configuration keys to brain/augmentation/model combinations.
"""
from dataclasses import dataclass
from typing import Optional, Literal

BrainType = Literal["mchp", "browseruse", "smolagents"]
ControllerType = Literal["none", "smolagents", "browseruse"]
ModelType = Literal["llama", "gemma", "deepseek"]


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
                if self.mechanics == "smolagents":
                    return f"M2.{self.model}"
                elif self.mechanics == "none":
                    return f"M2a.{self.model}"
            elif self.content == "none" and self.mechanics == "smolagents":
                return f"M2b.{self.model}"
            elif self.content == "browseruse":
                if self.mechanics == "browseruse":
                    return f"M3.{self.model}"
                elif self.mechanics == "none":
                    return f"M3a.{self.model}"
            elif self.content == "none" and self.mechanics == "browseruse":
                return f"M3b.{self.model}"

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
