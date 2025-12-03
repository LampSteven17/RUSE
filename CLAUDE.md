# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DOLOS-DEPLOY (dolos-engine) is a deployment system for SUP (Simulated User Profiles) agents that simulate human-like computer behavior. The system supports scripted automation (MCHP), LLM-powered agents (SMOL/BU), and hybrid approaches combining both.

## Quick Reference

```bash
# Install an agent
./INSTALL_SUP.sh --mchp                    # Human simulation (Selenium + pyautogui)
./INSTALL_SUP.sh --smol                    # smolagents + Ollama
./INSTALL_SUP.sh --bu                      # browser-use + Playwright
./INSTALL_SUP.sh --mchp --smol             # HYBRID: MCHP workflows + SMOL LLM content
./INSTALL_SUP.sh --smol --phase            # PHASE: SMOL + time-of-day timing + logging
./INSTALL_SUP.sh --model=mistral           # Override default model (llama3.1:8b)

# Validate installation
./src/install_scripts/test_agent.sh --agent=MCHP --path=/path/to/DOLOS-DEPLOY

# Service management
sudo systemctl {start|stop|status} mchp|smol|bu|mchp_smol|mchp_bu|smol_phase|bu_phase
sudo journalctl -u <service> -f
```

## Configuration Tiers

| Tier | Flags | Description |
|------|-------|-------------|
| DEFAULT | `--mchp`, `--smol`, `--bu` | Base implementations |
| MCHP-LIKE | `--smol --mchp-like`, `--bu --mchp-like` | LLM agents with MCHP timing patterns |
| HYBRID | `--mchp --smol`, `--mchp --bu` | MCHP workflows + LLM content generation |
| PHASE | `--smol --phase`, `--bu --phase` | LLM agents + time-of-day aware timing + logging |

## Architecture

### Source Structure (`src/`)

- **common/** - Shared modules used by PHASE/HYBRID agents
  - `logging/agent_logger.py` - JSON-Lines logging framework
  - `timing/phase_timing.py` - Time-of-day aware timing with activity clustering
- **MCHP/** - Human simulation using Selenium/pyautogui
  - `default/pyhuman/human.py` - Main loop: randomly selects workflows from a cluster
  - `default/pyhuman/app/workflows/` - Individual behaviors (browse_web, google_search, open_office_writer, etc.)
- **SMOL/** - smolagents-based AI agents with DuckDuckGo search
- **BU/** - browser-use agents with Playwright/Chromium
- **MCHP-HYBRID/** - MCHP workflows augmented with LLM content
  - `common/pyhuman/app/utility/llm_content.py` - LLM abstraction layer (SmolLLMBackend, BuLLMBackend)
  - `smol-backend/`, `bu-backend/` - Backend-specific LLM configs
- **SMOL-PHASE/**, **BU-PHASE/** - LLM agents with PHASE timing integration

### Deployment Structure (`deployed_sups/`)

Installed agents are deployed to `deployed_sups/<AGENT_NAME>/` with:
- `venv/` - Python virtual environment
- `logs/` - Session logs (JSON-Lines for PHASE/HYBRID)
- `run_*.sh` - Service entry point

## Key Patterns

### MCHP Workflow Pattern

Workflows in `app/workflows/` export a `load()` function returning a workflow object with `display`, `action(extra)`, and `cleanup()` methods. The main loop (`human.py`) executes random workflows in clusters with configurable timing.

### LLM Content Generation (HYBRID)

```python
from app.utility.llm_content import llm_paragraph, llm_search_query, llm_select

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
| `HYBRID_LLM_BACKEND` | Backend for HYBRID agents | `smol` |
| `LITELLM_MODEL` | Model for SMOL backend | `ollama/llama3.1:8b` |
| `OLLAMA_MODEL` | Model for BU backend | `llama3.1:8b` |

## Dependencies

- **MCHP**: selenium, pyautogui, Firefox, Geckodriver, xvfb
- **SMOL**: smolagents, litellm, Ollama
- **BU**: browser-use, playwright (Chromium), Ollama
- **HYBRID**: MCHP deps + backend-specific LLM deps
- **PHASE**: LLM deps + common logging/timing modules

## Git Conventions

- **No Claude footers**: Do NOT add "Generated with Claude Code" or "Co-Authored-By: Claude" footers to commits or PRs
