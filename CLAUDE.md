# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DOLOS-DEPLOY (dolos-engine) is a deployment system for SUP (Simulated User Profiles) agents that simulate human-like computer behavior. The system supports scripted automation (MCHP), LLM-powered agents (SMOL/BU), and hybrid approaches combining both.

## Quick Reference

```bash
# Install using config keys (from EXPERIMENTAL_PLAN.md)
./INSTALL_SUP.sh --M1                      # Pure MCHP (no LLM)
./INSTALL_SUP.sh --S1.llama                # SmolAgents + llama3.1:8b
./INSTALL_SUP.sh --B2.gemma                # BrowserUse + gemma3:4b
./INSTALL_SUP.sh --M2.llama                # MCHP + SmolAgents content/mechanics
./INSTALL_SUP.sh --S1.llama+               # SmolAgents + PHASE timing

# Or use long-form options
./INSTALL_SUP.sh --brain mchp --content smolagents --mechanics smolagents --model llama

# Run directly without installation (dev/testing)
./INSTALL_SUP.sh --S1.llama --runner                  # Run directly
./INSTALL_SUP.sh --S1.llama --runner --task "Search"  # With custom task
./INSTALL_SUP.sh --list                               # List all configs

# Or use Python runners directly (from src/)
python3 -m sup M1                                   # Unified CLI
python3 -m sup --brain smolagents --model llama --phase
python3 -m runners.run_smolagents "What is AI?" --model=llama --phase

# Service management (service name = config key, lowercase with underscores)
sudo systemctl {start|stop|status} m1|s1_llama|b2_gemma|m2_llama|s1_llamap
sudo journalctl -u <service> -f
```

## Architecture: Brain → Augmentations → Model

The codebase uses a three-layer architecture:

1. **Brain** (`src/brains/`): Core execution engine
   - `mchp/` - Selenium/pyautogui workflow automation
   - `smolagents/` - HuggingFace smolagents with DuckDuckGo search
   - `browseruse/` - Playwright-based browser automation

2. **Augmentations** (`src/augmentations/`): Optional LLM enhancements
   - `content/` - Content generation (paragraphs, search queries)
   - `mechanics/` - Behavioral prompts (search strategies)

3. **Model** (`src/common/config/model_config.py`): LLM selection
   - `llama` → llama3.1:8b (default)
   - `gemma` → gemma3:4b
   - `deepseek` → deepseek-r1:8b

### Configuration Keys

Configurations encode brain + augmentation + model:

| Series | Pattern | Example |
|--------|---------|---------|
| M (MCHP) | M[1-3][a\|b].[model] | M1, M2.llama, M2a.llama |
| S (SmolAgents) | S[1-3].[model][+] | S1.llama, S2.gemma+ |
| B (BrowserUse) | B[1-3].[model][+] | B1.llama, B3.deepseek+ |

- No suffix = DEFAULT_PROMPTS (PRE-PHASE baseline)
- `+` suffix = PHASE_PROMPTS (POST-PHASE with enhanced prompts)

Pre-defined configurations are in `src/runners/run_config.py`.

### Source Structure (`src/`)

- **brains/** - Core agent implementations
  - `mchp/human.py` - Main loop: random workflows in clusters
  - `mchp/app/workflows/` - Individual behaviors (browse_web, google_search, etc.)
  - `smolagents/agent.py` - SmolAgent class with three-prompt support
  - `browseruse/agent.py` - BrowserUseAgent with async execution
- **augmentations/** - LLM content/mechanics controllers
  - `content/llm_content.py` - LLM abstraction (SmolLLMBackend, BuLLMBackend)
  - `*/prompts/` - Prompt configurations (default.py, phase.py)
- **runners/** - Unified agent runners
  - `run_config.py` - SUPConfig dataclass and CONFIGS registry
  - `run_mchp.py`, `run_smolagents.py`, `run_browseruse.py`
- **sup/** - Unified CLI (`python -m sup M1`, `python -m sup --list`)
- **common/** - Shared modules
  - `logging/agent_logger.py` - JSON-Lines logging framework
  - `timing/phase_timing.py` - Time-of-day aware timing
  - `config/model_config.py` - Model registry

### Deployment Structure (`deployed_sups/`)

Installed agents are deployed to `deployed_sups/<AGENT_NAME>/` with:
- `venv/` - Python virtual environment
- `logs/` - Session logs (JSON-Lines for PHASE/HYBRID)
- `run_*.sh` - Service entry point

## Key Patterns

### MCHP Workflow Pattern

Workflows in `brains/mchp/app/workflows/` export a `load()` function returning a workflow object with `display`, `action(extra)`, and `cleanup()` methods. The main loop (`human.py`) executes random workflows in clusters with configurable timing.

### Three-Prompt Configuration (SmolAgents/BrowserUse)

```python
from brains.smolagents.prompts import SMOLPrompts, PHASE_PROMPTS
from brains.smolagents.agent import SmolAgent

# Prompts structure: task + content + mechanics
prompts = SMOLPrompts(
    task="Research and answer the question.",
    content="[Content guidelines for output formatting]",
    mechanics="[Behavior guidelines for search strategies]",
)

agent = SmolAgent(prompts=PHASE_PROMPTS, model="llama")
result = agent.run("What is AI?")
```

### LLM Content Generation (HYBRID)

```python
from augmentations.content.llm_content import llm_paragraph, llm_search_query, llm_select

text = llm_paragraph()                    # Replaces TextLorem().paragraph()
query = llm_search_query("technology")    # Context-aware search query
choice = llm_select(items, "browsing")    # Intelligent item selection
```

### Logging (PHASE/HYBRID)

```python
from common.logging.agent_logger import AgentLogger

logger = AgentLogger(agent_type="SMOL-PHASE", log_dir="/path/to/logs")
logger.session_start()
logger.workflow_start("google_search")
logger.llm_request(action="generate_query", input_data={"context": "..."})
logger.llm_response(output="query text", duration_ms=1200)
logger.workflow_end("google_search", success=True)
logger.session_end()
```

### PHASE Timing

```python
from common.timing.phase_timing import PhaseTiming

timing = PhaseTiming()
cluster_size = timing.get_cluster_size()   # Adjusted by time-of-day
task_delay = timing.get_task_delay()       # Inter-task delay
cluster_delay = timing.get_cluster_delay() # Inter-cluster break
```

## Key Design Decisions

- **NO FALLBACK**: HYBRID agents raise `LLMUnavailableError` if LLM fails - experiments must be clearly valid or invalid
- **Unified installer**: Single `INSTALL_SUP.sh` with combinatorial flags determines configuration
- **JSON-Lines logging**: One event per line for easy post-hoc analysis
- **PHASE timing**: Hourly activity multipliers (peak 9-11 AM, 2-4 PM; minimal 2-4 AM)

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DEFAULT_OLLAMA_MODEL` | Ollama model for installation | `llama3.1:8b` |
| `OLLAMA_MODEL` | Runtime model (BU backend) | `llama3.1:8b` |
| `LITELLM_MODEL` | Runtime model (SMOL backend) | `ollama/llama3.1:8b` |
| `HYBRID_LLM_BACKEND` | Backend for HYBRID agents (`smol` or `bu`) | `smol` |
| `PYTHONPATH` | Must include `src/` for module imports | Set by runners |

## Dependencies

- **MCHP**: selenium, pyautogui, Firefox, Geckodriver, xvfb
- **SMOL**: smolagents, litellm, Ollama
- **BU**: browser-use, playwright (Chromium), Ollama
- **HYBRID**: MCHP deps + backend-specific LLM deps
- **PHASE**: LLM deps + common logging/timing modules

## Git Conventions

- **No Claude footers**: Do NOT add "Generated with Claude Code" or "Co-Authored-By: Claude" footers to commits or PRs
