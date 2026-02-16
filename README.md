```
RRRRRRRRRRRRRRRRR    UUUUUUUU     UUUUUUUU    SSSSSSSSSSSSSSS  EEEEEEEEEEEEEEEEEEEEEE
R::::::::::::::::R   U::::::U     U::::::U  SS:::::::::::::::S E::::::::::::::::::::E
R::::::RRRRRR:::::R  U::::::U     U::::::U S:::::SSSSSS::::::S E::::::::::::::::::::E
RR:::::R     R:::::R UU:::::U     U:::::UU S:::::S     SSSSSSS EE::::::EEEEEEEEE::::E
  R::::R     R:::::R  U:::::U     U:::::U  S:::::S               E:::::E       EEEEEE
  R::::R     R:::::R  U:::::D     D:::::U  S:::::S               E:::::E
  R::::RRRRRR:::::R   U:::::D     D:::::U   S::::SSSS            E::::::EEEEEEEEEE
  R:::::::::::::RR    U:::::D     D:::::U    SS::::::SSSSS       E:::::::::::::::E
  R::::RRRRRR:::::R   U:::::D     D:::::U      SSS::::::::SS     E:::::::::::::::E
  R::::R     R:::::R  U:::::D     D:::::U         SSSSSS::::S    E::::::EEEEEEEEEE
  R::::R     R:::::R  U:::::D     D:::::U              S:::::S   E:::::E
  R::::R     R:::::R  U::::::U   U::::::U              S:::::S   E:::::E       EEEEEE
RR:::::R     R:::::R  U:::::::UUU:::::::U  SSSSSSS     S:::::S EE::::::EEEEEEEE:::::E
R::::::R     R:::::R   UU:::::::::::::UU   S::::::SSSSSS:::::S E::::::::::::::::::::E
R::::::R     R:::::R     UU:::::::::UU     S:::::::::::::::SS  E::::::::::::::::::::E
RRRRRRRR     RRRRRRR       UUUUUUUUU       SSSSSSSSSSSSSSS    EEEEEEEEEEEEEEEEEEEEEE
```

# RUSE — Realistic User Simulation Engine

**Deployment system for Synthetic User Persona (SUP) agents**

RUSE provides a unified installation and management system for agents that simulate human-like computer behavior. Whether you need scripted automation, LLM-powered browsing, or hybrid approaches combining both, RUSE has you covered.

---

## Quick Start

```bash
# Clone the repository
git clone https://github.com/LampSteven17/RUSE.git
cd RUSE

# Install using config keys (creates systemd service)
./INSTALL_SUP.sh --M1                      # Pure MCHP (no LLM)
./INSTALL_SUP.sh --S1.llama                # SmolAgents + llama3.1:8b
./INSTALL_SUP.sh --B2.gemma                # BrowserUse + gemma3:4b
./INSTALL_SUP.sh --M2.llama                # MCHP + SmolAgents augmentation
./INSTALL_SUP.sh --S1.llama+               # SmolAgents + PHASE timing

# Or use long-form options
./INSTALL_SUP.sh --brain mchp --content smolagents --mechanics smolagents --model llama

# Run directly without installation (development/testing)
./INSTALL_SUP.sh --S1.llama --runner                      # Run SmolAgents directly
./INSTALL_SUP.sh --S1.llama --runner --task "Search AI"   # With custom task
./INSTALL_SUP.sh --B2.gemma+ --runner                     # BrowserUse + PHASE

# List all configurations
./INSTALL_SUP.sh --list

# Or use Python runners directly (from src/)
cd src
python3 -m sup M1                          # Unified CLI with config key
python3 -m sup --brain smolagents --model llama --phase
python3 -m runners.run_smolagents "What is AI?" --model=llama --phase
```

---

## Agent Types

### MCHP (Human Simulation)
Scripted automation using Selenium and pyautogui. Executes predefined workflows that mimic human computer usage patterns.

**Workflows include:**
- Web browsing (random site navigation)
- Google searching
- YouTube browsing
- Document creation (OpenOffice Writer/Calc)
- File downloads

### SMOL (smolagents)
AI agents powered by Hugging Face's smolagents library with local LLM inference via Ollama.

**Features:**
- CodeAgent with tool execution
- DuckDuckGo search integration
- Local model support (llama3.1, mistral, etc.)

### BU (Browser Use)
Browser automation agents using the browser-use library with Playwright/Chromium.

**Features:**
- LLM-driven browser navigation
- Intelligent task completion
- Headless Chromium operation

---

## Configuration Matrix

All configurations from `docs/EXPERIMENTAL_PLAN.md`:

### PRE-PHASE (13 configurations)

| Config | Brain | Content | Mechanics | Model |
|--------|-------|---------|-----------|-------|
| `--M1` | MCHP | MCHP | MCHP | None |
| `--M2.llama` | MCHP | SmolAgents | SmolAgents | llama3.1:8b |
| `--M2a.llama` | MCHP | SmolAgents | MCHP | llama3.1:8b |
| `--M2b.llama` | MCHP | MCHP | SmolAgents | llama3.1:8b |
| `--M3.llama` | MCHP | BrowserUse | BrowserUse | llama3.1:8b |
| `--M3a.llama` | MCHP | BrowserUse | MCHP | llama3.1:8b |
| `--M3b.llama` | MCHP | MCHP | BrowserUse | llama3.1:8b |
| `--B1.llama` | BrowserUse | BrowserUse | BrowserUse | llama3.1:8b |
| `--B2.gemma` | BrowserUse | BrowserUse | BrowserUse | gemma3:4b |
| `--B3.deepseek` | BrowserUse | BrowserUse | BrowserUse | deepseek-r1:8b |
| `--S1.llama` | SmolAgents | SmolAgents | SmolAgents | llama3.1:8b |
| `--S2.gemma` | SmolAgents | SmolAgents | SmolAgents | gemma3:4b |
| `--S3.deepseek` | SmolAgents | SmolAgents | SmolAgents | deepseek-r1:8b |

### POST-PHASE (+ suffix = PHASE timing + enhanced prompts)

| Config | Brain | Model | Description |
|--------|-------|-------|-------------|
| `--B1.llama+` | BrowserUse | llama3.1:8b | PHASE timing + prompts |
| `--S1.llama+` | SmolAgents | llama3.1:8b | PHASE timing + prompts |
| (etc.) | | | |

---

## Installation

### Prerequisites

- Ubuntu 20.04+ (or compatible Linux distribution)
- Python 3.8+
- sudo access (for systemd service installation)
- ~10GB disk space (for models and dependencies)

### Installation Examples

```bash
# Using config keys (recommended)
./INSTALL_SUP.sh --M1                      # Pure MCHP
./INSTALL_SUP.sh --S1.llama                # SmolAgents + llama
./INSTALL_SUP.sh --B2.gemma                # BrowserUse + gemma
./INSTALL_SUP.sh --M2.llama                # MCHP + SmolAgents content/mechanics
./INSTALL_SUP.sh --S1.llama+               # SmolAgents + PHASE

# Using long-form options
./INSTALL_SUP.sh --brain mchp --content mchp --mechanics mchp --model none
./INSTALL_SUP.sh --brain smolagents --model gemma --phase
./INSTALL_SUP.sh --brain mchp --content smolagents --mechanics smolagents --model llama

# List all available configurations
./INSTALL_SUP.sh --list
```

### Model Options

| Key | Model | Used By |
|-----|-------|---------|
| `none` | (no LLM) | M1 |
| `llama` | llama3.1:8b | Default for LLM configs |
| `gemma` | gemma3:4b | B2, S2 series |
| `deepseek` | deepseek-r1:8b | B3, S3 series |

---

## Service Management

Each agent runs as a systemd service:

```bash
# Service names by configuration
mchp         # MCHP
smol         # SMOL
bu           # BU
mchp_smol    # MCHP-SMOL hybrid
mchp_bu      # MCHP-BU hybrid
smol_phase   # SMOL-PHASE
bu_phase     # BU-PHASE

# Commands
sudo systemctl start <service>
sudo systemctl stop <service>
sudo systemctl restart <service>
sudo systemctl status <service>

# View logs
sudo journalctl -u <service> -f
```

---

## Architecture

The codebase uses a **Brain → Augmentations → Model** architecture:

1. **Brain**: Core execution engine (MCHP, SmolAgents, BrowserUse)
2. **Augmentations**: Optional LLM content/mechanics controllers
3. **Model**: LLM selection (llama3.1:8b, gemma3:4b, deepseek-r1:8b)

### Configuration Keys

| Series | Pattern | Example |
|--------|---------|---------|
| M (MCHP) | M[1-3][a\|b].[model] | M1, M2.llama, M2a.llama |
| S (SmolAgents) | S[1-3].[model][+] | S1.llama, S2.gemma+ |
| B (BrowserUse) | B[1-3].[model][+] | B1.llama, B3.deepseek+ |

- No suffix = DEFAULT_PROMPTS (baseline)
- `+` suffix = PHASE_PROMPTS (enhanced prompts)

## Directory Structure

```
RUSE/
├── INSTALL_SUP.sh              # Unified installer
├── README.md                   # This file
├── CLAUDE.md                   # Claude Code guidance
├── docs/
│   └── EXPERIMENTAL_PLAN.md    # 16-configuration experiment matrix
├── src/
│   ├── brains/                 # Core agent implementations
│   │   ├── mchp/               # MCHP agent (Selenium/pyautogui)
│   │   │   ├── agent.py        # MCHPAgent class
│   │   │   ├── human.py        # Workflow main loop
│   │   │   └── app/workflows/  # Individual behaviors
│   │   ├── smolagents/         # SmolAgents (HuggingFace)
│   │   │   ├── agent.py        # SmolAgent class
│   │   │   └── prompts.py      # Three-prompt configuration
│   │   └── browseruse/         # BrowserUse (Playwright)
│   │       ├── agent.py        # BrowserUseAgent class
│   │       └── prompts.py      # Three-prompt configuration
│   ├── augmentations/          # LLM content/mechanics controllers
│   │   ├── content/            # Content generation (llm_content.py)
│   │   └── mechanics/          # Behavioral prompts
│   ├── runners/                # Unified runners
│   │   ├── run_config.py       # SUPConfig + CONFIGS registry
│   │   ├── run_mchp.py         # python3 -m runners.run_mchp
│   │   ├── run_smolagents.py   # python3 -m runners.run_smolagents
│   │   └── run_browseruse.py   # python3 -m runners.run_browseruse
│   ├── common/                 # Shared modules
│   │   ├── logging/            # JSON-Lines logging (agent_logger.py)
│   │   ├── timing/             # PHASE timing (phase_timing.py)
│   │   └── config/             # Model registry (model_config.py)
│   ├── sup/                    # Unified CLI (python -m sup)
│   └── install_scripts/        # Installation utilities (ollama, tests)
└── deployed_sups/              # Deployed agents (created by installer)
    ├── MCHP/
    ├── SMOL/
    ├── BU/
    ├── MCHP-SMOL/
    ├── MCHP-BU/
    ├── SMOL-PHASE/
    └── BU-PHASE/
```

---

## Logging

### PHASE and HYBRID Agents

These agents use a unified JSON-Lines logging format for experiment analysis:

```
deployed_sups/<agent>/logs/
├── session_2025-12-02_14-30-45_abc123.jsonl
└── latest.jsonl -> session_2025-12-02_14-30-45_abc123.jsonl
```

**Event Types:**
- `session_start` / `session_end` - Session lifecycle
- `workflow_start` / `workflow_end` - Workflow execution
- `llm_request` / `llm_response` / `llm_error` - LLM interactions
- `decision` - Agent choices (site selection, link clicks)
- `browser_action` / `gui_action` - Automation events
- `timing_delay` - Sleep/delay events
- `error` / `warning` / `info` - Status messages

**Example Log Entry:**
```json
{
  "timestamp": "2025-12-02T14:30:45.123456",
  "session_id": "abc123",
  "agent_type": "MCHP-SMOL",
  "event_type": "llm_response",
  "workflow": "google_search",
  "details": {
    "output": "best python tutorials 2024",
    "duration_ms": 1523,
    "model": "llama3.1:8b"
  }
}
```

---

## Key Features

### HYBRID Agents (MCHP + LLM)

Combines MCHP's scripted workflow structure with LLM-generated content:

- **Preserves timing**: MCHP's human-like delays and clustering
- **Intelligent content**: LLM generates search queries, documents, comments
- **No fallback**: If LLM fails, agent fails loudly (experiment validity)

**What gets LLM-augmented:**
| Workflow | LLM Replaces |
|----------|--------------|
| Google Search | Search query generation |
| Web Browse | Site and link selection |
| YouTube | Video search queries |
| OpenOffice Writer | Document content, comments, filenames |
| OpenOffice Calc | Spreadsheet headers, comments |

### PHASE Timing

Time-of-day aware activity patterns:

- **Peak hours (9-11 AM, 2-4 PM)**: More tasks, shorter delays
- **Off-peak (night, lunch)**: Fewer tasks, longer breaks
- **Activity clustering**: Tasks grouped with inter-cluster delays
- **Human-like variance**: Randomized delays within ranges

---

## Testing

Validate an installation:

```bash
./src/install_scripts/test_agent.sh --agent=MCHP --path=/path/to/RUSE
./src/install_scripts/test_agent.sh --agent=SMOL --path=/path/to/RUSE
./src/install_scripts/test_agent.sh --agent=BU --path=/path/to/RUSE
```

---

## Troubleshooting

### Ollama Connection Issues

```bash
# Check if Ollama is running
systemctl status ollama

# Pull the model manually
ollama pull llama3.1:8b

# Test model
ollama run llama3.1:8b "Hello"
```

### Service Won't Start

```bash
# Check service status
sudo systemctl status <service>

# View detailed logs
sudo journalctl -u <service> -n 100

# Try running manually
cd /path/to/deployed_sups/<agent>
./run_<agent>.sh
```

### Browser Issues (BU agents)

```bash
# Install Playwright dependencies
cd deployed_sups/BU
source venv/bin/activate
playwright install chromium
playwright install-deps chromium
```

---

## Requirements

### System
- Ubuntu 20.04+ / Debian 11+
- 4GB+ RAM (8GB+ recommended for LLM agents)
- 10GB+ disk space

### MCHP
- Firefox
- Xvfb (headless display)

### SMOL / BU
- Ollama (installed automatically)
- CUDA optional (for GPU acceleration)

---

## License

[Add your license here]

---

## Contributing

[Add contribution guidelines here]

---

## Acknowledgments

- [smolagents](https://huggingface.co/docs/smolagents) by Hugging Face
- [browser-use](https://docs.browser-use.com/)
- [Ollama](https://ollama.ai/) for local LLM inference
