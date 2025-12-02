# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DOLOS-DEPLOY is a deployment system for SUP (Simulated User Profiles) agents. It provides installers and configurations for various agent types that simulate human-like behavior.

## Configuration Tiers

| Tier | Configurations | Description |
|------|----------------|-------------|
| **DEFAULT** | MCHP, SMOL, BU | Base implementations |
| **MCHP-LIKE** | SMOL --mchp-like, BU --mchp-like | LLM agents with MCHP timing patterns |
| **HYBRID** | MCHP-SMOL, MCHP-BU | MCHP workflows + LLM content generation |
| **PHASE** | SMOL-PHASE, BU-PHASE | LLM agents + time-of-day aware timing + logging |

## Installation Commands

```bash
# === DEFAULT Configurations ===
./INSTALL_SUP.sh --mchp                    # Standard MCHP (human simulation)
./INSTALL_SUP.sh --smol                    # Standard SMOL agent
./INSTALL_SUP.sh --bu                      # Standard BU agent

# === MCHP-LIKE Configurations ===
./INSTALL_SUP.sh --smol --mchp-like        # SMOL with MCHP-like behavior
./INSTALL_SUP.sh --bu --mchp-like          # BU with MCHP-like behavior

# === HYBRID Configurations (MCHP workflows + LLM content) ===
./INSTALL_SUP.sh --mchp --smol             # MCHP-SMOL hybrid
./INSTALL_SUP.sh --mchp --bu               # MCHP-BU hybrid

# === PHASE Configurations (LLM + improved timing + logging) ===
./INSTALL_SUP.sh --smol --phase            # SMOL-PHASE agent
./INSTALL_SUP.sh --bu --phase              # BU-PHASE agent

# === Options ===
--model=MODEL                              # Override Ollama model (default: llama3.1:8b)
```

## Architecture

```
DOLOS-DEPLOY/
├── INSTALL_SUP.sh              # Unified installer for all configurations
├── docs/
│   └── HYBRID_ARCHITECTURE_PLAN.txt  # Detailed implementation plan
├── src/
│   ├── common/                 # Shared components
│   │   ├── logging/
│   │   │   └── agent_logger.py   # Unified JSON-Lines logging framework
│   │   └── timing/
│   │       └── phase_timing.py   # PHASE timing with time-of-day awareness
│   ├── MCHP/                   # DEFAULT: Human simulation agent
│   │   ├── install_mchp.sh
│   │   └── default/pyhuman/    # Workflow-based human emulation
│   │       ├── human.py        # Main loop - randomly executes workflows
│   │       └── app/workflows/  # Individual behaviors
│   ├── SMOL/                   # DEFAULT: smolagents-based AI agents
│   │   ├── install_smol.sh
│   │   ├── default/            # Basic CodeAgent
│   │   └── mchp-like/          # MCHP timing patterns
│   ├── BU/                     # DEFAULT: browser-use agents
│   │   ├── install_bu.sh
│   │   ├── default/            # Basic browser automation
│   │   └── mchp-like/          # MCHP timing patterns
│   ├── MCHP-HYBRID/            # HYBRID: MCHP + LLM content
│   │   ├── common/pyhuman/     # Augmented workflows
│   │   │   └── app/utility/
│   │   │       └── llm_content.py  # LLM abstraction (NO FALLBACK)
│   │   ├── smol-backend/       # SMOL backend config
│   │   └── bu-backend/         # BU backend config
│   ├── SMOL-PHASE/             # PHASE: SMOL + timing + logging
│   │   └── agent.py
│   ├── BU-PHASE/               # PHASE: BU + timing + logging
│   │   └── agent.py
│   └── install_scripts/
│       ├── install_ollama.sh   # Ollama LLM setup
│       └── test_agent.sh       # Validates installations
```

## Deployment Structure

```
deployed_sups/
├── MCHP/              # Standard MCHP
├── SMOL/              # Standard SMOL
├── BU/                # Standard BU
├── MCHP-SMOL/         # HYBRID with SMOL backend
├── MCHP-BU/           # HYBRID with BU backend
├── SMOL-PHASE/        # SMOL + PHASE timing + logging
└── BU-PHASE/          # BU + PHASE timing + logging
```

## Service Management

```bash
# Service names by configuration
sudo systemctl {start|stop|status} mchp        # MCHP
sudo systemctl {start|stop|status} smol        # SMOL
sudo systemctl {start|stop|status} bu          # BU
sudo systemctl {start|stop|status} mchp_smol   # MCHP-SMOL
sudo systemctl {start|stop|status} mchp_bu     # MCHP-BU
sudo systemctl {start|stop|status} smol_phase  # SMOL-PHASE
sudo systemctl {start|stop|status} bu_phase    # BU-PHASE

# View logs
sudo journalctl -u <service> -f
```

## Logging Framework

PHASE and HYBRID agents use unified JSON-Lines logging:

```
logs/
└── session_2025-12-02_14-30-45_abc123.jsonl
```

Event types: `session_start`, `session_end`, `workflow_start`, `workflow_end`,
`llm_request`, `llm_response`, `llm_error`, `decision`, `browser_action`,
`gui_action`, `timing_delay`, `error`, `warning`, `info`

## Key Design Decisions

- **NO FALLBACK**: HYBRID agents fail loudly if LLM unavailable (experiments must be valid)
- **Unified installer**: Single INSTALL_SUP.sh with combinatorial flags
- **JSON-Lines logging**: One event per line for easy parsing
- **PHASE timing**: Time-of-day awareness for realistic activity patterns

## Key Dependencies

- **MCHP**: selenium, pyautogui, Firefox, Geckodriver, xvfb
- **SMOL**: smolagents, litellm, Ollama
- **BU**: browser-use, playwright (Chromium), Ollama
- **HYBRID**: All MCHP deps + backend-specific LLM deps
- **PHASE**: LLM deps + common logging/timing modules
