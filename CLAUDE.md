# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DOLOS-DEPLOY is a deployment system for SUP (Simulated User Profiles) agents. It provides installers and configurations for three types of agents that simulate human-like behavior:

- **MCHP**: Human simulation using Selenium/Firefox with workflows like web browsing, YouTube, Google search, document editing
- **SMOL**: AI agents using Hugging Face's smolagents with Ollama for local LLM inference
- **BU**: Browser Use agents using the browser-use library with Playwright/Chromium

## Installation Commands

Main installer (from repo root):
```bash
# MCHP (Human simulation)
./INSTALL_SUP.sh --mchp

# SMOL agents (requires config: --default, --mchp-like, or --improved)
./INSTALL_SUP.sh --smol --default [--model=MODEL]
./INSTALL_SUP.sh --smol --mchp-like [--model=MODEL]
./INSTALL_SUP.sh --smol --improved [--model=MODEL]

# BU agents (requires config: --default, --mchp-like, or --improved)
./INSTALL_SUP.sh --bu --default [--model=MODEL]
./INSTALL_SUP.sh --bu --mchp-like [--model=MODEL]
./INSTALL_SUP.sh --bu --improved [--model=MODEL]
```

Default Ollama model: `llama3.1:8b` (override with `--model=` or `DEFAULT_OLLAMA_MODEL` env var)

## Testing

Test a deployed agent:
```bash
./src/install_scripts/test_agent.sh --agent=MCHP --path=/path/to/DOLOS-DEPLOY
./src/install_scripts/test_agent.sh --agent=SMOL --path=/path/to/DOLOS-DEPLOY
./src/install_scripts/test_agent.sh --agent=BU --path=/path/to/DOLOS-DEPLOY
```

## Architecture

```
DOLOS-DEPLOY/
├── INSTALL_SUP.sh           # Main entry point - orchestrates agent installation
├── src/
│   ├── MCHP/                # Human simulation agent
│   │   ├── install_mchp.sh  # Installer (Firefox + Geckodriver + Python venv)
│   │   └── default/pyhuman/ # Workflow-based human emulation system
│   │       ├── human.py     # Main loop - randomly executes workflows
│   │       └── app/workflows/  # Individual behaviors (browse_web, google_search, etc.)
│   ├── SMOL/                # smolagents-based AI agents
│   │   ├── install_smol.sh  # Installer (Python venv + smolagents + litellm)
│   │   ├── default/         # Basic CodeAgent with DuckDuckGo search
│   │   ├── mchp-like/       # MCHP-style behavior patterns
│   │   └── PHASE-improved/  # Enhanced configuration
│   ├── BU/                  # browser-use agents
│   │   ├── install_bu.sh    # Installer (Python venv + browser-use + Playwright Chromium)
│   │   ├── default/         # Basic browser automation agent
│   │   ├── mchp-like/       # MCHP-style behavior patterns
│   │   └── PHASE-improved/  # Enhanced configuration
│   └── install_scripts/
│       ├── install_ollama.sh  # Ollama LLM setup for SMOL/BU
│       └── test_agent.sh      # Validates deployed agent installations
```

## Deployment Structure

After installation, agents are deployed to `deployed_sups/`:
```
deployed_sups/
├── MCHP/
│   ├── venv/           # Python virtual environment
│   ├── pyhuman/        # Agent code
│   ├── geckodriver     # Firefox WebDriver
│   ├── run_mchp.sh     # Launcher script
│   └── logs/
├── SMOL/
│   ├── venv/
│   ├── agent.py        # Agent code (varies by config)
│   ├── run_smol.sh
│   └── logs/
└── BU/
    ├── venv/
    ├── agent.py
    ├── run_bu.sh
    └── logs/
```

## Service Management

Each agent runs as a systemd service:
```bash
sudo systemctl {start|stop|restart|status} mchp
sudo systemctl {start|stop|restart|status} smol
sudo systemctl {start|stop|restart|status} bu
sudo journalctl -u {mchp|smol|bu} -f  # View logs
```

## Key Dependencies

- **MCHP**: selenium, pyautogui, Firefox, Geckodriver, xvfb
- **SMOL**: smolagents, litellm, Ollama
- **BU**: browser-use, playwright (Chromium), selenium, Ollama