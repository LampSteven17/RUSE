# SUP Implementation Plan

This document outlines the implementation approach for the experimental configurations defined in `EXPERIMENTAL_PLAN.md`.

---

## Design Principles

1. **Leave MCHP alone** — Stable baseline, minimal changes
2. **Modular architecture** — Brain → Augmentations → Model
3. **Start simple** — Use llama3.1:8b as default, swap models easily later

---

## Architecture: Brain → Augmentations → Model

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CONFIGURATION                                │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   1. BRAIN              2. AUGMENTATIONS           3. MODEL          │
│   ──────────            ────────────────           ─────────         │
│                                                                      │
│   ┌─────────┐           ┌──────────────┐          ┌────────────┐    │
│   │  MCHP   │ ───────►  │  Workflows   │ ──────►  │   None     │    │
│   └─────────┘           │  (llm_content│          │   -or-     │    │
│                         │   for HYBRID)│          │ llama3.1:8b│    │
│   ┌─────────┐           └──────────────┘          └────────────┘    │
│   │ Browser │           ┌──────────────┐          ┌────────────┐    │
│   │   Use   │ ───────►  │   Prompts    │ ──────►  │ llama3.1:8b│    │
│   └─────────┘           │  - Content   │          │ gemma3:4b  │    │
│                         │  - Mechanics │          │ deepseek-r1│    │
│   ┌─────────┐           └──────────────┘          └────────────┘    │
│   │  Smol   │           ┌──────────────┐          ┌────────────┐    │
│   │ Agents  │ ───────►  │   Prompts    │ ──────►  │ llama3.1:8b│    │
│   └─────────┘           │  - Content   │          │ gemma3:4b  │    │
│                         │  - Mechanics │          │ deepseek-r1│    │
│                         └──────────────┘          └────────────┘    │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### How Each Brain Handles Augmentations

| Brain | Augmentation Method | Where It Lives |
|-------|--------------------|-----------------|
| MCHP | Workflows + llm_content.py | `src/MCHP-HYBRID/common/pyhuman/app/utility/llm_content.py` |
| BrowserUse | Content Prompt + Mechanics Prompt | Passed to Agent at runtime |
| SmolAgents | Content Prompt + Mechanics Prompt | Passed to Agent at runtime |

---

## Configuration Mapping

### MCHP Configurations (M Series)

| Config | Brain | Augmentations | Model |
|--------|-------|---------------|-------|
| **M1** | MCHP | None (pure workflows) | None |
| M2.llama | MCHP | SmolAgents via llm_content.py | llama3.1:8b |
| M2a.llama | MCHP | SmolAgents content only | llama3.1:8b |
| M2b.llama | MCHP | SmolAgents mechanics only | llama3.1:8b |
| M3.llama | MCHP | BrowserUse via llm_content.py | llama3.1:8b |
| M3a.llama | MCHP | BrowserUse content only | llama3.1:8b |
| M3b.llama | MCHP | BrowserUse mechanics only | llama3.1:8b |

**M1 = Original MCHP, Unchanged**

M1 runs the existing MCHP code exactly as it was before this refactor:
- Same `human.py` main loop
- Same workflow discovery and execution
- Same `TextLorem` content generation
- Same Selenium/PyAutoGUI mechanics
- Same timing (cluster size, delays, etc.)
- **No LLM, no new abstractions, no changes to logic**

The only difference is file location (`brains/mchp/` instead of `MCHP/default/pyhuman/`).

**M2/M3 Configurations**: MCHP workflows remain unchanged. `llm_content.py` handles content generation (replaces TextLorem calls). Model is passed via environment variable.

### BrowserUse Configurations (B Series)

| Config | Brain | Augmentations | Model |
|--------|-------|---------------|-------|
| B1.llama | BrowserUse | Default prompts | llama3.1:8b |
| B2.gemma | BrowserUse | Default prompts | gemma3:4b |
| B3.deepseek | BrowserUse | Default prompts | deepseek-r1:8b |

**Implementation**: Same prompts, different models. Model is configurable.

### SmolAgents Configurations (S Series)

| Config | Brain | Augmentations | Model |
|--------|-------|---------------|-------|
| S1.llama | SmolAgents | Default prompts | llama3.1:8b |
| S2.gemma | SmolAgents | Default prompts | gemma3:4b |
| S3.deepseek | SmolAgents | Default prompts | deepseek-r1:8b |

**Implementation**: Same prompts, different models. Model is configurable.

---

## Implementation

### Part 1: Model Configuration (All Agents)

Create a simple model config that all agents can use:

**File**: `src/common/config/model_config.py`

```python
"""
Model configuration for SUP agents.
"""
import os

# Default model
DEFAULT_MODEL = "llama3.1:8b"

# Available models for experiments
MODELS = {
    "llama": "llama3.1:8b",
    "gemma": "gemma3:4b",
    "deepseek": "deepseek-r1:8b",
}

def get_model(model_key: str = None) -> str:
    """Get model name from key or environment."""
    if model_key and model_key in MODELS:
        return MODELS[model_key]
    return os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
```

### Part 2: BrowserUse Three-Prompt System

**File**: `src/BU/prompts.py`

```python
"""
Three-prompt configuration for BrowserUse agents.
"""
from dataclasses import dataclass
from typing import Optional

@dataclass
class BUPrompts:
    """Prompt configuration for BrowserUse."""

    task: str
    content: Optional[str] = None
    mechanics: Optional[str] = None

    def build_full_prompt(self) -> str:
        """Combine prompts into single instruction."""
        parts = [self.task]

        if self.content:
            parts.append(f"\n\n[Content Guidelines]\n{self.content}")

        if self.mechanics:
            parts.append(f"\n\n[Interaction Guidelines]\n{self.mechanics}")

        return "".join(parts)


# Default prompts (no augmentation)
DEFAULT_PROMPTS = BUPrompts(
    task="Complete the browsing task.",
    content=None,
    mechanics=None,
)

# PHASE-improved prompts (for POST-PHASE experiments)
PHASE_PROMPTS = BUPrompts(
    task="Complete the browsing task naturally, as a human would.",

    content="""
    When generating any text (searches, form inputs, etc.):
    - Use natural, conversational language
    - Vary your word choices
    - Occasionally include minor typos (realistic)
    """,

    mechanics="""
    When interacting with the browser:
    - Pause 1-3 seconds before clicking
    - Scroll gradually, not instantly
    - Read content before taking action
    - Occasionally explore related links
    """,
)
```

**File**: `src/BU/agent.py` (refactored base)

```python
"""
BrowserUse agent with configurable prompts and model.
"""
import asyncio
from browser_use import Agent, Browser, BrowserConfig
from langchain_ollama import ChatOllama

from common.config.model_config import get_model
from BU.prompts import BUPrompts, DEFAULT_PROMPTS


class BrowserUseAgent:
    """BrowserUse agent with three-prompt support."""

    def __init__(
        self,
        prompts: BUPrompts = DEFAULT_PROMPTS,
        model: str = None,
    ):
        self.prompts = prompts
        self.model = get_model(model)
        self.llm = ChatOllama(model=self.model)

    async def run(self, task: str) -> str:
        """Run a task with configured prompts."""
        # Build full prompt from task + content + mechanics
        full_prompt = BUPrompts(
            task=task,
            content=self.prompts.content,
            mechanics=self.prompts.mechanics,
        ).build_full_prompt()

        config = BrowserConfig(headless=True, disable_security=True)

        async with Browser(config=config) as browser:
            agent = Agent(
                task=full_prompt,
                llm=self.llm,
                browser=browser,
            )
            return await agent.run()
```

### Part 3: SmolAgents Three-Prompt System

**File**: `src/SMOL/prompts.py`

```python
"""
Three-prompt configuration for SmolAgents.
"""
from dataclasses import dataclass
from typing import Optional

@dataclass
class SMOLPrompts:
    """Prompt configuration for SmolAgents."""

    task: str
    content: Optional[str] = None
    mechanics: Optional[str] = None

    def build_system_prompt(self) -> Optional[str]:
        """Build system prompt from content + mechanics."""
        parts = []

        if self.content:
            parts.append(f"[Content Guidelines]\n{self.content}")

        if self.mechanics:
            parts.append(f"\n[Behavior Guidelines]\n{self.mechanics}")

        return "\n".join(parts) if parts else None


# Default prompts
DEFAULT_PROMPTS = SMOLPrompts(
    task="Research and answer the question.",
    content=None,
    mechanics=None,
)

# PHASE-improved prompts
PHASE_PROMPTS = SMOLPrompts(
    task="Research and answer the question thoroughly.",

    content="""
    When searching and generating responses:
    - Use varied, natural search queries
    - Summarize findings conversationally
    - Include relevant details
    """,

    mechanics="""
    When performing research:
    - Try multiple search queries if needed
    - Take time to review results
    - Prefer authoritative sources
    """,
)
```

**File**: `src/SMOL/agent.py` (refactored base)

```python
"""
SmolAgents agent with configurable prompts and model.
"""
from smolagents import CodeAgent, DuckDuckGoSearchTool
from litellm import LiteLLMModel

from common.config.model_config import get_model
from SMOL.prompts import SMOLPrompts, DEFAULT_PROMPTS


class SmolAgent:
    """SmolAgents agent with three-prompt support."""

    def __init__(
        self,
        prompts: SMOLPrompts = DEFAULT_PROMPTS,
        model: str = None,
    ):
        self.prompts = prompts
        self.model = get_model(model)

        model_id = f"ollama/{self.model}"
        self.llm = LiteLLMModel(model_id=model_id)

        # Build agent with system prompt from content+mechanics
        system_prompt = prompts.build_system_prompt()
        self.agent = CodeAgent(
            tools=[DuckDuckGoSearchTool()],
            model=self.llm,
            system_prompt=system_prompt,
        )

    def run(self, task: str) -> str:
        """Run a task."""
        return self.agent.run(task)
```

### Part 4: MCHP (No Changes)

MCHP stays as-is. For HYBRID configurations:
- `llm_content.py` already handles content generation
- Model is set via `OLLAMA_MODEL` or `LITELLM_MODEL` environment variable
- Workflows handle mechanics (no changes needed)

---

## Usage Examples

### Running Different Configurations

```bash
# B1.llama - BrowserUse with llama
OLLAMA_MODEL=llama3.1:8b python -m BU.default.agent

# B2.gemma - BrowserUse with gemma
OLLAMA_MODEL=gemma3:4b python -m BU.default.agent

# B3.deepseek - BrowserUse with deepseek
OLLAMA_MODEL=deepseek-r1:8b python -m BU.default.agent

# S1.llama - SmolAgents with llama
OLLAMA_MODEL=llama3.1:8b python -m SMOL.default.agent

# M1 - Pure MCHP (no model needed)
python -m MCHP.default.pyhuman.human

# M2.llama - MCHP + SmolAgents content
HYBRID_LLM_BACKEND=smol OLLAMA_MODEL=llama3.1:8b python -m MCHP-HYBRID.human
```

### Programmatic Configuration

```python
from BU.agent import BrowserUseAgent
from BU.prompts import DEFAULT_PROMPTS, PHASE_PROMPTS

# B1.llama - default prompts, llama model
agent = BrowserUseAgent(prompts=DEFAULT_PROMPTS, model="llama")

# B1.llama+ (POST-PHASE) - phase prompts, llama model
agent = BrowserUseAgent(prompts=PHASE_PROMPTS, model="llama")

# B2.gemma - default prompts, gemma model
agent = BrowserUseAgent(prompts=DEFAULT_PROMPTS, model="gemma")
```

---

## New Directory Structure

Reorganized to match Brain → Augmentations → Model:

```
src/
├── common/
│   ├── config/
│   │   └── model_config.py       # Model selection (llama, gemma, deepseek)
│   ├── logging/
│   │   └── agent_logger.py       # (existing) JSON-Lines logging
│   └── timing/
│       └── phase_timing.py       # (existing) PHASE timing
│
├── brains/
│   │
│   ├── mchp/
│   │   ├── __init__.py
│   │   ├── agent.py              # Main MCHP agent (human.py logic)
│   │   ├── workflows/            # All workflow implementations
│   │   │   ├── __init__.py
│   │   │   ├── base.py           # BaseWorkflow class
│   │   │   ├── google_search.py
│   │   │   ├── browse_web.py
│   │   │   ├── browse_youtube.py
│   │   │   ├── open_office_writer.py
│   │   │   ├── open_office_calc.py
│   │   │   └── ...
│   │   └── data/                 # Static data files
│   │       ├── websites.txt
│   │       └── google_searches.txt
│   │
│   ├── browseruse/
│   │   ├── __init__.py
│   │   ├── agent.py              # BrowserUse agent with prompt support
│   │   ├── prompts.py            # Three-prompt definitions
│   │   └── tasks.py              # Task lists
│   │
│   └── smolagents/
│       ├── __init__.py
│       ├── agent.py              # SmolAgents agent with prompt support
│       ├── prompts.py            # Three-prompt definitions
│       └── tasks.py              # Task lists
│
├── augmentations/
│   │
│   ├── content/
│   │   ├── __init__.py
│   │   ├── llm_content.py        # LLM content generation for MCHP
│   │   └── prompts/
│   │       ├── default.py        # Default content prompts
│   │       └── phase.py          # PHASE-improved content prompts
│   │
│   └── mechanics/
│       ├── __init__.py
│       └── prompts/
│           ├── default.py        # Default mechanics prompts
│           └── phase.py          # PHASE-improved mechanics prompts
│
└── runners/
    ├── __init__.py
    ├── run_config.py             # Configuration loader
    ├── run_mchp.py               # M1, M2, M3 configurations
    ├── run_browseruse.py         # B1, B2, B3 configurations
    └── run_smolagents.py         # S1, S2, S3 configurations
```

### Directory Explanation

| Directory | Purpose |
|-----------|---------|
| `common/` | Shared utilities (config, logging, timing) |
| `brains/` | The three brain implementations |
| `brains/mchp/` | MCHP brain with workflows |
| `brains/browseruse/` | BrowserUse brain with prompt support |
| `brains/smolagents/` | SmolAgents brain with prompt support |
| `augmentations/` | Content and mechanics augmentation modules |
| `augmentations/content/` | LLM content generation + prompts |
| `augmentations/mechanics/` | Mechanics behavior prompts |
| `runners/` | Entry points for each configuration |

---

## Command Structure

Single unified command that maps directly to the architecture:

```bash
python -m sup --brain <BRAIN> --content <CONTROLLER> --mechanics <CONTROLLER> --model <MODEL>
```

### Arguments

| Argument | Options | Default | Description |
|----------|---------|---------|-------------|
| `--brain` | `mchp`, `browseruse`, `smolagents` | `mchp` | Which brain to use |
| `--content` | `none`, `smolagents`, `browseruse` | `none` | Content controller/augmentation |
| `--mechanics` | `none`, `smolagents`, `browseruse` | `none` | Mechanics controller/augmentation |
| `--model` | `llama`, `gemma`, `deepseek` | `llama` | LLM model (ignored if no augmentation) |
| `--phase` | flag | off | Enable PHASE-improved prompts |

### Configuration Examples

```bash
# === M1: Pure MCHP (default) ===
python -m sup --brain mchp
# Equivalent: --brain mchp --content none --mechanics none

# === M2.llama: MCHP + SmolAgents (both) ===
python -m sup --brain mchp --content smolagents --mechanics smolagents --model llama

# === M2a.llama: MCHP + SmolAgents content only ===
python -m sup --brain mchp --content smolagents --mechanics none --model llama

# === M2b.llama: MCHP + SmolAgents mechanics only ===
python -m sup --brain mchp --content none --mechanics smolagents --model llama

# === M3.llama: MCHP + BrowserUse (both) ===
python -m sup --brain mchp --content browseruse --mechanics browseruse --model llama

# === B1.llama: BrowserUse brain ===
python -m sup --brain browseruse --model llama

# === B2.gemma: BrowserUse with Gemma ===
python -m sup --brain browseruse --model gemma

# === B3.deepseek: BrowserUse with DeepSeek ===
python -m sup --brain browseruse --model deepseek

# === S1.llama: SmolAgents brain ===
python -m sup --brain smolagents --model llama

# === S2.gemma: SmolAgents with Gemma ===
python -m sup --brain smolagents --model gemma

# === POST-PHASE: Add --phase flag ===
python -m sup --brain browseruse --model llama --phase    # B1.llama+
python -m sup --brain smolagents --model gemma --phase    # S2.gemma+
```

### Shorthand (Config Key)

For convenience, also support direct config keys:

```bash
python -m sup M1           # Pure MCHP
python -m sup M2.llama     # MCHP + SmolAgents
python -m sup B1.llama     # BrowserUse + llama
python -m sup S2.gemma     # SmolAgents + gemma
```

---

## Summary

| Component | Changes |
|-----------|---------|
| `brains/mchp/` | Consolidate from `MCHP/default/` - minimal logic changes |
| `brains/browseruse/` | New structure with three-prompt support |
| `brains/smolagents/` | New structure with three-prompt support |
| `augmentations/` | Move `llm_content.py` here, add prompt modules |
| `runners/` | New entry points for each configuration |
| `common/` | Keep existing, add `model_config.py` |

The architecture is:
1. **Pick a Brain** (`brains/mchp`, `brains/browseruse`, `brains/smolagents`)
2. **Configure Augmentations** (`augmentations/content`, `augmentations/mechanics`)
3. **Select Model** (llama3.1:8b default, swap via `--model` flag)