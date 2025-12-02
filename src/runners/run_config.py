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
        """Generate the configuration key (e.g., M1, B2.gemma, S1.llama)."""
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
            base = "B"
            num = {"llama": "1", "gemma": "2", "deepseek": "3"}[self.model]
            suffix = "+" if self.phase else ""
            return f"{base}{num}.{self.model}{suffix}"

        elif self.brain == "smolagents":
            base = "S"
            num = {"llama": "1", "gemma": "2", "deepseek": "3"}[self.model]
            suffix = "+" if self.phase else ""
            return f"{base}{num}.{self.model}{suffix}"

        return f"{self.brain}-{self.content}-{self.mechanics}-{self.model}"


# Pre-defined configuration shortcuts
CONFIGS = {
    # M Series - MCHP brain
    "M1": SUPConfig(brain="mchp"),
    "M2.llama": SUPConfig(brain="mchp", content="smolagents", mechanics="smolagents", model="llama"),
    "M2a.llama": SUPConfig(brain="mchp", content="smolagents", mechanics="none", model="llama"),
    "M2b.llama": SUPConfig(brain="mchp", content="none", mechanics="smolagents", model="llama"),
    "M3.llama": SUPConfig(brain="mchp", content="browseruse", mechanics="browseruse", model="llama"),
    "M3a.llama": SUPConfig(brain="mchp", content="browseruse", mechanics="none", model="llama"),
    "M3b.llama": SUPConfig(brain="mchp", content="none", mechanics="browseruse", model="llama"),

    # B Series - BrowserUse brain
    "B1.llama": SUPConfig(brain="browseruse", model="llama"),
    "B2.gemma": SUPConfig(brain="browseruse", model="gemma"),
    "B3.deepseek": SUPConfig(brain="browseruse", model="deepseek"),

    # S Series - SmolAgents brain
    "S1.llama": SUPConfig(brain="smolagents", model="llama"),
    "S2.gemma": SUPConfig(brain="smolagents", model="gemma"),
    "S3.deepseek": SUPConfig(brain="smolagents", model="deepseek"),

    # POST-PHASE configurations (+ suffix)
    "B1.llama+": SUPConfig(brain="browseruse", model="llama", phase=True),
    "B2.gemma+": SUPConfig(brain="browseruse", model="gemma", phase=True),
    "B3.deepseek+": SUPConfig(brain="browseruse", model="deepseek", phase=True),
    "S1.llama+": SUPConfig(brain="smolagents", model="llama", phase=True),
    "S2.gemma+": SUPConfig(brain="smolagents", model="gemma", phase=True),
    "S3.deepseek+": SUPConfig(brain="smolagents", model="deepseek", phase=True),
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
