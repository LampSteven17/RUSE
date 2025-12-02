```
 _ .-') _                                        .-')
( (  OO) )                                      ( OO ).
 \     .'_  .-'),-----.  ,--.      .-'),-----. (_)---\_)
 ,`'--..._)( OO'  .-.  ' |  |.-') ( OO'  .-.  '/    _ |
 |  |  \  '/   |  | |  | |  | OO )/   |  | |  |\  :` `.
 |  |   ' |\_) |  |\|  | |  |`-' |\_) |  |\|  | '..`''.)
 |  |   / :  \ |  | |  |(|  '---.'  \ |  | |  |.-._)   \
 |  '--'  /   `'  '-'  ' |      |    `'  '-'  '\       /
 `-------'      `-----'  `------'      `-----'  `-----'
```

# DOLOS-DEPLOY

**Deployment system for Simulated User Profile (SUP) agents**

DOLOS-DEPLOY provides a unified installation and management system for agents that simulate human-like computer behavior. Whether you need scripted automation, LLM-powered browsing, or hybrid approaches combining both, DOLOS has you covered.

---

## Quick Start

```bash
# Clone the repository
git clone https://github.com/LampSteven17/DOLOS-DEPLOY.git
cd DOLOS-DEPLOY

# Install an agent (examples)
./INSTALL_SUP.sh --mchp                    # Human simulation
./INSTALL_SUP.sh --smol --default          # LLM agent
./INSTALL_SUP.sh --mchp --smol             # Hybrid agent
./INSTALL_SUP.sh --smol --phase            # LLM + advanced timing
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

## Configuration Tiers

| Tier | Command | Description |
|------|---------|-------------|
| **DEFAULT** | `--mchp` | Standard MCHP human simulation |
| **DEFAULT** | `--smol --default` | Basic SMOL CodeAgent |
| **DEFAULT** | `--bu --default` | Basic BU browser agent |
| **MCHP-LIKE** | `--smol --mchp-like` | SMOL with MCHP timing patterns |
| **MCHP-LIKE** | `--bu --mchp-like` | BU with MCHP timing patterns |
| **HYBRID** | `--mchp --smol` | MCHP workflows + SMOL LLM content |
| **HYBRID** | `--mchp --bu` | MCHP workflows + BU LLM content |
| **PHASE** | `--smol --phase` | SMOL + time-of-day timing + logging |
| **PHASE** | `--bu --phase` | BU + time-of-day timing + logging |

---

## Installation

### Prerequisites

- Ubuntu 20.04+ (or compatible Linux distribution)
- Python 3.8+
- sudo access (for systemd service installation)
- ~10GB disk space (for models and dependencies)

### Full Installation Commands

```bash
# === DEFAULT Configurations ===

# MCHP - Human simulation with Selenium/Firefox
./INSTALL_SUP.sh --mchp

# SMOL - Basic LLM agent
./INSTALL_SUP.sh --smol --default [--model=MODEL]

# SMOL with MCHP-like timing
./INSTALL_SUP.sh --smol --mchp-like [--model=MODEL]

# BU - Basic browser automation
./INSTALL_SUP.sh --bu --default [--model=MODEL]

# BU with MCHP-like timing
./INSTALL_SUP.sh --bu --mchp-like [--model=MODEL]


# === HYBRID Configurations ===
# (MCHP workflows with LLM-generated content)

# MCHP + SMOL LLM backend
./INSTALL_SUP.sh --mchp --smol [--model=MODEL]

# MCHP + BU LLM backend
./INSTALL_SUP.sh --mchp --bu [--model=MODEL]


# === PHASE Configurations ===
# (LLM agents with advanced timing and logging)

# SMOL with PHASE timing
./INSTALL_SUP.sh --smol --phase [--model=MODEL]

# BU with PHASE timing
./INSTALL_SUP.sh --bu --phase [--model=MODEL]
```

### Model Selection

Default model: `llama3.1:8b`

Override with:
```bash
./INSTALL_SUP.sh --smol --default --model=mistral
./INSTALL_SUP.sh --mchp --smol --model=qwen2.5:7b
```

Or set environment variable:
```bash
export DEFAULT_OLLAMA_MODEL=codellama
./INSTALL_SUP.sh --smol --default
```

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

## Directory Structure

```
DOLOS-DEPLOY/
├── INSTALL_SUP.sh              # Unified installer
├── README.md                   # This file
├── CLAUDE.md                   # Claude Code guidance
├── docs/
│   └── HYBRID_ARCHITECTURE_PLAN.txt
├── src/
│   ├── common/                 # Shared modules
│   │   ├── logging/            # JSON-Lines logging framework
│   │   └── timing/             # PHASE timing system
│   ├── MCHP/                   # Human simulation agent
│   ├── SMOL/                   # smolagents-based agents
│   ├── BU/                     # browser-use agents
│   ├── MCHP-HYBRID/            # Hybrid configurations
│   ├── SMOL-PHASE/             # SMOL + PHASE timing
│   ├── BU-PHASE/               # BU + PHASE timing
│   └── install_scripts/        # Shared installation utilities
└── deployed_sups/              # Deployed agents (created during install)
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
./src/install_scripts/test_agent.sh --agent=MCHP --path=/path/to/DOLOS-DEPLOY
./src/install_scripts/test_agent.sh --agent=SMOL --path=/path/to/DOLOS-DEPLOY
./src/install_scripts/test_agent.sh --agent=BU --path=/path/to/DOLOS-DEPLOY
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
