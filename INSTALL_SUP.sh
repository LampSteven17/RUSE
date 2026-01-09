#!/bin/bash

# DOLOS-DEPLOY: Unified SUP Installer
# Matches the configuration matrix from docs/EXPERIMENTAL_PLAN.md
#
# Architecture: Brain → Content Controller → Mechanics Controller → Model
#
# Usage:
#   ./INSTALL_SUP.sh --M1                    # Config key shorthand
#   ./INSTALL_SUP.sh --S1.llama --runner     # Run directly without install
#   ./INSTALL_SUP.sh --brain mchp --content mchp --mechanics mchp --model none

set -e
set -u
set -o pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Error handling
error_handler() {
    local exit_code=$?
    local line_number=$1
    echo ""
    echo -e "${RED}================================${NC}"
    echo -e "${RED}    INSTALLATION FAILED!${NC}"
    echo -e "${RED}================================${NC}"
    echo -e "${RED}Error at line ${line_number}, exit code ${exit_code}${NC}"
    exit $exit_code
}
trap 'error_handler ${LINENO}' ERR

log() { echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"; }
log_error() { echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1" >&2; }
log_info() { echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')] INFO:${NC} $1"; }

usage() {
    cat << 'EOF'
dolos-engine Unified Installer

Usage: ./INSTALL_SUP.sh <CONFIG> [OPTIONS]

=== Configuration Keys ===

  MCHP Series (Baseline):
    --M0                Upstream MITRE pyhuman (control - DO NOT MODIFY)
    --M1                DOLOS MCHP baseline (no LLM)
    --M2.llama          MCHP + SmolAgents content/mechanics
    --M2a.llama         MCHP + SmolAgents content only
    --M2b.llama         MCHP + SmolAgents mechanics only
    --M3.llama          MCHP + BrowserUse content/mechanics
    --M3a.llama         MCHP + BrowserUse content only
    --M3b.llama         MCHP + BrowserUse mechanics only

  MCHP Series (Improved - with PHASE timing):
    --M4.llama          MCHP + SmolAgents content/mechanics + PHASE timing
    --M4a.llama         MCHP + SmolAgents content only + PHASE timing
    --M4b.llama         MCHP + SmolAgents mechanics only + PHASE timing
    --M5.llama          MCHP + BrowserUse content/mechanics + PHASE timing
    --M5a.llama         MCHP + BrowserUse content only + PHASE timing
    --M5b.llama         MCHP + BrowserUse mechanics only + PHASE timing

  BrowserUse Series (Baseline):
    --B1.llama          BrowserUse + llama3.1:8b
    --B2.gemma          BrowserUse + gemma3:4b
    --B3.deepseek       BrowserUse + deepseek-r1:8b

  BrowserUse Series (Improved - Loop mode + PHASE timing):
    --B4.llama          BrowserUseLoop + llama3.1:8b + PHASE timing
    --B5.gemma          BrowserUseLoop + gemma3:4b + PHASE timing
    --B6.deepseek       BrowserUseLoop + deepseek-r1:8b + PHASE timing

  SmolAgents Series (Baseline):
    --S1.llama          SmolAgents + llama3.1:8b
    --S2.gemma          SmolAgents + gemma3:4b
    --S3.deepseek       SmolAgents + deepseek-r1:8b

  SmolAgents Series (Improved - Loop mode + PHASE timing):
    --S4.llama          SmolAgentLoop + llama3.1:8b + PHASE timing
    --S5.gemma          SmolAgentLoop + gemma3:4b + PHASE timing
    --S6.deepseek       SmolAgentLoop + deepseek-r1:8b + PHASE timing

=== Long-Form Options ===

  --brain <TYPE>        Brain type: mchp, smolagents, browseruse
  --content <TYPE>      Content controller: mchp, smolagents, browseruse
  --mechanics <TYPE>    Mechanics controller: mchp, smolagents, browseruse
  --model <MODEL>       Model: none, llama, gemma, deepseek
  --phase               Enable PHASE timing/prompts (adds + suffix)

=== Execution Options ===

  --runner              Run directly without systemd installation
  --task "TASK"         Task for LLM agents (with --runner)
  --stage <1|2>         Staged install for Ansible (1=pre-reboot, 2=post-reboot)
  --list                List all available configurations
  --help                Show this help

=== Examples ===

  ./INSTALL_SUP.sh --M1                           # Install pure MCHP
  ./INSTALL_SUP.sh --S1.llama --runner            # Run SmolAgents directly
  ./INSTALL_SUP.sh --B2.gemma                     # Install BrowserUse + gemma
  ./INSTALL_SUP.sh --brain mchp --content smolagents --mechanics smolagents --model llama
  ./INSTALL_SUP.sh --S1.llama --runner --task "Search for AI news"

EOF
}

# ============================================================================
# Configuration Parsing
# ============================================================================

# Default values
CONFIG_KEY=""
BRAIN=""
CONTENT=""
MECHANICS=""
MODEL=""
PHASE=false
RUNNER=false
TASK=""
STAGE=0  # 0=full install, 1=pre-reboot only, 2=post-reboot only

# Pre-defined configurations matching EXPERIMENTAL_PLAN.md
declare -A CONFIGS
CONFIGS=(
    # M Series - MCHP brain (Baseline)
    ["M0"]="upstream:upstream:upstream:none:false"  # Upstream MITRE pyhuman (control)
    ["M1"]="mchp:mchp:mchp:none:false"
    ["M2.llama"]="mchp:smolagents:smolagents:llama:false"
    ["M2a.llama"]="mchp:smolagents:mchp:llama:false"
    ["M2b.llama"]="mchp:mchp:smolagents:llama:false"
    ["M3.llama"]="mchp:browseruse:browseruse:llama:false"
    ["M3a.llama"]="mchp:browseruse:mchp:llama:false"
    ["M3b.llama"]="mchp:mchp:browseruse:llama:false"

    # M Series - MCHP brain (Improved: with PHASE timing)
    ["M4.llama"]="mchp:smolagents:smolagents:llama:true"
    ["M4a.llama"]="mchp:smolagents:mchp:llama:true"
    ["M4b.llama"]="mchp:mchp:smolagents:llama:true"
    ["M5.llama"]="mchp:browseruse:browseruse:llama:true"
    ["M5a.llama"]="mchp:browseruse:mchp:llama:true"
    ["M5b.llama"]="mchp:mchp:browseruse:llama:true"

    # B Series - BrowserUse brain (Baseline)
    ["B1.llama"]="browseruse:browseruse:browseruse:llama:false"
    ["B2.gemma"]="browseruse:browseruse:browseruse:gemma:false"
    ["B3.deepseek"]="browseruse:browseruse:browseruse:deepseek:false"

    # B Series - BrowserUseLoop (Improved: Loop mode + PHASE timing)
    ["B4.llama"]="browseruse:browseruse:browseruse:llama:true"
    ["B5.gemma"]="browseruse:browseruse:browseruse:gemma:true"
    ["B6.deepseek"]="browseruse:browseruse:browseruse:deepseek:true"

    # S Series - SmolAgents brain (Baseline)
    ["S1.llama"]="smolagents:smolagents:smolagents:llama:false"
    ["S2.gemma"]="smolagents:smolagents:smolagents:gemma:false"
    ["S3.deepseek"]="smolagents:smolagents:smolagents:deepseek:false"

    # S Series - SmolAgentLoop (Improved: Loop mode + PHASE timing)
    ["S4.llama"]="smolagents:smolagents:smolagents:llama:true"
    ["S5.gemma"]="smolagents:smolagents:smolagents:gemma:true"
    ["S6.deepseek"]="smolagents:smolagents:smolagents:deepseek:true"
)

# Model name mappings
declare -A MODEL_NAMES
MODEL_NAMES=(
    ["none"]=""
    ["llama"]="llama3.1:8b"
    ["gemma"]="gemma3:4b"
    ["deepseek"]="deepseek-r1:8b"
)

list_configs() {
    echo "Available configurations:"
    echo ""
    echo "MCHP Series (Baseline):"
    for key in M0 M1 M2.llama M2a.llama M2b.llama M3.llama M3a.llama M3b.llama; do
        IFS=':' read -r brain content mechanics model phase <<< "${CONFIGS[$key]}"
        printf "  %-14s brain=%-10s content=%-12s mechanics=%-12s model=%s\n" \
            "--$key" "$brain" "$content" "$mechanics" "$model"
    done
    echo ""
    echo "MCHP Series (Improved - with PHASE timing):"
    for key in M4.llama M4a.llama M4b.llama M5.llama M5a.llama M5b.llama; do
        IFS=':' read -r brain content mechanics model phase <<< "${CONFIGS[$key]}"
        printf "  %-14s brain=%-10s content=%-12s mechanics=%-12s model=%s phase=true\n" \
            "--$key" "$brain" "$content" "$mechanics" "$model"
    done
    echo ""
    echo "BrowserUse Series (Baseline):"
    for key in B1.llama B2.gemma B3.deepseek; do
        IFS=':' read -r brain content mechanics model phase <<< "${CONFIGS[$key]}"
        printf "  %-14s brain=%-10s content=%-12s mechanics=%-12s model=%s\n" \
            "--$key" "$brain" "$content" "$mechanics" "$model"
    done
    echo ""
    echo "BrowserUse Series (Improved - Loop mode + PHASE timing):"
    for key in B4.llama B5.gemma B6.deepseek; do
        IFS=':' read -r brain content mechanics model phase <<< "${CONFIGS[$key]}"
        printf "  %-14s brain=%-10s content=%-12s mechanics=%-12s model=%s phase=true\n" \
            "--$key" "$brain" "$content" "$mechanics" "$model"
    done
    echo ""
    echo "SmolAgents Series (Baseline):"
    for key in S1.llama S2.gemma S3.deepseek; do
        IFS=':' read -r brain content mechanics model phase <<< "${CONFIGS[$key]}"
        printf "  %-14s brain=%-10s content=%-12s mechanics=%-12s model=%s\n" \
            "--$key" "$brain" "$content" "$mechanics" "$model"
    done
    echo ""
    echo "SmolAgents Series (Improved - Loop mode + PHASE timing):"
    for key in S4.llama S5.gemma S6.deepseek; do
        IFS=':' read -r brain content mechanics model phase <<< "${CONFIGS[$key]}"
        printf "  %-14s brain=%-10s content=%-12s mechanics=%-12s model=%s phase=true\n" \
            "--$key" "$brain" "$content" "$mechanics" "$model"
    done
}

parse_config_key() {
    local key="$1"
    if [[ -v "CONFIGS[$key]" ]]; then
        IFS=':' read -r BRAIN CONTENT MECHANICS MODEL PHASE <<< "${CONFIGS[$key]}"
        CONFIG_KEY="$key"
        [[ "$PHASE" == "true" ]] && PHASE=true || PHASE=false
        return 0
    fi
    return 1
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            # Config key shortcuts (e.g., --M1, --S1.llama, --B4.llama)
            --M0|--M1|--M2.llama|--M2a.llama|--M2b.llama|--M3.llama|--M3a.llama|--M3b.llama|\
            --M4.llama|--M4a.llama|--M4b.llama|--M5.llama|--M5a.llama|--M5b.llama|\
            --B1.llama|--B2.gemma|--B3.deepseek|--B4.llama|--B5.gemma|--B6.deepseek|\
            --S1.llama|--S2.gemma|--S3.deepseek|--S4.llama|--S5.gemma|--S6.deepseek)
                parse_config_key "${1#--}"
                ;;

            # Long-form options (support both --arg value and --arg=value)
            --brain) shift; BRAIN="$1" ;;
            --brain=*) BRAIN="${1#*=}" ;;
            --content) shift; CONTENT="$1" ;;
            --content=*) CONTENT="${1#*=}" ;;
            --mechanics) shift; MECHANICS="$1" ;;
            --mechanics=*) MECHANICS="${1#*=}" ;;
            --model) shift; MODEL="$1" ;;
            --model=*) MODEL="${1#*=}" ;;
            --phase) PHASE=true ;;

            # Execution options
            --runner) RUNNER=true ;;
            --task) shift; TASK="$1" ;;
            --task=*) TASK="${1#*=}" ;;
            --stage) shift; STAGE="$1" ;&
            --stage=*)
                [[ "$1" == --stage=* ]] && STAGE="${1#*=}"
                if [[ "$STAGE" != "1" && "$STAGE" != "2" ]]; then
                    log_error "Invalid stage: $STAGE (must be 1 or 2)"
                    exit 1
                fi
                ;;
            --list)
                list_configs
                exit 0
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
        shift
    done

    # Validate configuration
    if [[ -z "$BRAIN" ]]; then
        log_error "No configuration specified. Use a config key (e.g., --M1) or --brain option."
        echo ""
        usage
        exit 1
    fi

    # Default content/mechanics to brain type if not specified
    [[ -z "$CONTENT" ]] && CONTENT="$BRAIN"
    [[ -z "$MECHANICS" ]] && MECHANICS="$BRAIN"

    # Default model based on brain type
    if [[ -z "$MODEL" ]]; then
        if [[ "$BRAIN" == "mchp" && "$CONTENT" == "mchp" && "$MECHANICS" == "mchp" ]]; then
            MODEL="none"
        else
            MODEL="llama"
        fi
    fi

    # Generate config key if not set
    if [[ -z "$CONFIG_KEY" ]]; then
        generate_config_key
    fi
}

generate_config_key() {
    local suffix=""
    $PHASE && suffix="+"

    if [[ "$BRAIN" == "mchp" ]]; then
        if [[ "$CONTENT" == "mchp" && "$MECHANICS" == "mchp" ]]; then
            CONFIG_KEY="M1"
        elif [[ "$CONTENT" == "smolagents" && "$MECHANICS" == "smolagents" ]]; then
            CONFIG_KEY="M2.${MODEL}${suffix}"
        elif [[ "$CONTENT" == "smolagents" && "$MECHANICS" == "mchp" ]]; then
            CONFIG_KEY="M2a.${MODEL}${suffix}"
        elif [[ "$CONTENT" == "mchp" && "$MECHANICS" == "smolagents" ]]; then
            CONFIG_KEY="M2b.${MODEL}${suffix}"
        elif [[ "$CONTENT" == "browseruse" && "$MECHANICS" == "browseruse" ]]; then
            CONFIG_KEY="M3.${MODEL}${suffix}"
        elif [[ "$CONTENT" == "browseruse" && "$MECHANICS" == "mchp" ]]; then
            CONFIG_KEY="M3a.${MODEL}${suffix}"
        elif [[ "$CONTENT" == "mchp" && "$MECHANICS" == "browseruse" ]]; then
            CONFIG_KEY="M3b.${MODEL}${suffix}"
        else
            CONFIG_KEY="M-custom"
        fi
    elif [[ "$BRAIN" == "browseruse" ]]; then
        case "$MODEL" in
            llama) CONFIG_KEY="B1.llama${suffix}" ;;
            gemma) CONFIG_KEY="B2.gemma${suffix}" ;;
            deepseek) CONFIG_KEY="B3.deepseek${suffix}" ;;
            *) CONFIG_KEY="B-custom" ;;
        esac
    elif [[ "$BRAIN" == "smolagents" ]]; then
        case "$MODEL" in
            llama) CONFIG_KEY="S1.llama${suffix}" ;;
            gemma) CONFIG_KEY="S2.gemma${suffix}" ;;
            deepseek) CONFIG_KEY="S3.deepseek${suffix}" ;;
            *) CONFIG_KEY="S-custom" ;;
        esac
    else
        CONFIG_KEY="custom"
    fi
}

# ============================================================================
# Installation Functions
# ============================================================================

install_ollama() {
    local model_name="${MODEL_NAMES[$MODEL]}"
    [[ -z "$model_name" ]] && return 0  # No model needed

    log "Setting up Ollama with model: $model_name"
    if [ -f "$SCRIPT_DIR/src/install_scripts/install_ollama.sh" ]; then
        chmod +x "$SCRIPT_DIR/src/install_scripts/install_ollama.sh"
        export OLLAMA_MODELS="$model_name"
        "$SCRIPT_DIR/src/install_scripts/install_ollama.sh"
        log "Ollama setup complete"
    else
        log_error "install_ollama.sh not found"
        exit 1
    fi
}

CUDA_INSTALLED=false

install_cuda() {
    # Install NVIDIA drivers and CUDA toolkit from official repository
    # Required for GPU-accelerated Ollama/BrowserUse/SmolAgents
    # Ref: https://docs.nvidia.com/cuda/cuda-installation-guide-linux/
    # Ref: https://developer.nvidia.com/cuda-downloads

    # Check if GPU is present
    if ! lspci | grep -qi nvidia; then
        log "No NVIDIA GPU detected, skipping CUDA installation"
        return 0
    fi

    log "NVIDIA GPU detected: $(lspci | grep -i nvidia | head -1)"

    # Step 1: Install kernel headers and build tools (REQUIRED for DKMS)
    log "Installing kernel headers and build tools for $(uname -r)..."
    sudo apt-get install -y build-essential linux-headers-$(uname -r)

    # Step 2: Setup NVIDIA CUDA repository
    log "Setting up NVIDIA CUDA repository..."
    wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb -O /tmp/cuda-keyring.deb
    sudo dpkg -i /tmp/cuda-keyring.deb
    rm -f /tmp/cuda-keyring.deb
    sudo apt-get update -y

    # Step 3: Install NVIDIA driver (specific version for compatibility)
    log "Installing NVIDIA driver 580..."
    sudo apt-get install -y nvidia-driver-580

    # Step 4: Install CUDA toolkit 12.9
    log "Installing CUDA toolkit 12.9..."
    sudo apt-get install -y cuda-toolkit-12-9

    # Step 5: Enable nvidia-persistenced for better GPU management
    log "Enabling nvidia-persistenced..."
    sudo systemctl enable nvidia-persistenced || true

    # Add CUDA to PATH for current session (use generic cuda symlink)
    export PATH=/usr/local/cuda/bin:${PATH:-}
    export LD_LIBRARY_PATH=/usr/local/cuda/lib64:${LD_LIBRARY_PATH:-}

    # Add to bashrc for future sessions
    if ! grep -q "/usr/local/cuda/bin" ~/.bashrc; then
        echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.bashrc
        echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc
    fi

    CUDA_INSTALLED=true
    log "NVIDIA driver 580 and CUDA toolkit 12.9 installed. Reboot required for driver to load."
}

install_firefox_deb() {
    # Install Firefox from Mozilla's official deb repository (not snap)
    # Snap Firefox causes Selenium/geckodriver timeouts due to sandbox confinement
    # See: https://support.mozilla.org/en-US/kb/install-firefox-linux

    log "Setting up Mozilla APT repository for Firefox..."

    # Remove snap Firefox if present
    if snap list firefox &>/dev/null; then
        log "Removing snap Firefox (causes Selenium compatibility issues)..."
        sudo snap remove firefox 2>/dev/null || true
    fi

    # Create keyring directory
    sudo install -d -m 0755 /etc/apt/keyrings

    # Import Mozilla APT signing key
    wget -q https://packages.mozilla.org/apt/repo-signing-key.gpg -O- | \
        sudo tee /etc/apt/keyrings/packages.mozilla.org.asc > /dev/null

    # Add Mozilla APT repository
    echo "deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main" | \
        sudo tee /etc/apt/sources.list.d/mozilla.list > /dev/null

    # Configure APT to prioritize Mozilla packages
    echo 'Package: *
Pin: origin packages.mozilla.org
Pin-Priority: 1000
' | sudo tee /etc/apt/preferences.d/mozilla > /dev/null

    # Install Firefox from Mozilla repo
    sudo apt-get update -y
    sudo apt-get install -y firefox

    log "Firefox deb installed: $(firefox --version)"
}

install_system_deps() {
    log "Installing system dependencies for $BRAIN..."

    sudo apt-get update -y
    sudo apt-get install -y python3-pip python3-venv python3-dev build-essential

    case "$BRAIN" in
        mchp)
            sudo apt-get install -y xvfb xdg-utils libxml2-dev libxslt-dev python3-tk scrot
            # Install Firefox from Mozilla deb repo (not snap)
            install_firefox_deb
            # Install Geckodriver
            log "Installing Geckodriver..."
            if [[ "$(uname -m)" == "x86_64" ]]; then
                wget -q "https://github.com/mozilla/geckodriver/releases/download/v0.34.0/geckodriver-v0.34.0-linux64.tar.gz" -O /tmp/geckodriver.tar.gz
                sudo tar -xzf /tmp/geckodriver.tar.gz -C /usr/local/bin/
                sudo chmod +x /usr/local/bin/geckodriver
                rm -f /tmp/geckodriver.tar.gz
            fi
            ;;
        browseruse|smolagents)
            sudo apt-get install -y xvfb
            # Install uv (provides uvx for browser-use)
            if ! command -v uvx &> /dev/null; then
                log "Installing uv (provides uvx)..."
                curl -LsSf https://astral.sh/uv/install.sh | sh
                export PATH="$HOME/.local/bin:$PATH"
            fi
            ;;
    esac

    # Install CUDA if GPU is present and model requires it (for LLM acceleration)
    # This applies to ALL brain types when MODEL != none
    if [[ "$MODEL" != "none" ]]; then
        install_cuda
    fi
}

install_python_deps() {
    local venv_path="$1"

    log "Installing Python dependencies..."
    source "$venv_path/bin/activate"
    pip install --upgrade pip

    # Base deps for brain type
    case "$BRAIN" in
        mchp)
            pip install selenium beautifulsoup4 webdriver-manager lxml pyautogui lorem \
                certifi chardet colorama configparser crayons idna requests urllib3
            ;;
        smolagents)
            pip install smolagents litellm torch transformers datasets numpy pandas requests duckduckgo-search ddgs
            ;;
        browseruse)
            pip install uv browser-use langchain-ollama playwright
            playwright install chromium
            playwright install-deps chromium
            ;;
    esac

    # Add LLM deps if content/mechanics uses LLM
    if [[ "$BRAIN" == "mchp" ]]; then
        if [[ "$CONTENT" == "smolagents" || "$MECHANICS" == "smolagents" ]]; then
            pip install smolagents litellm torch transformers
        fi
        if [[ "$CONTENT" == "browseruse" || "$MECHANICS" == "browseruse" ]]; then
            pip install langchain-ollama
        fi
    fi

    deactivate
}

copy_source_code() {
    local dest_dir="$1"
    log "Copying source code to $dest_dir..."

    mkdir -p "$dest_dir/src"
    cp -r "$SCRIPT_DIR/src/brains" "$dest_dir/src/"
    cp -r "$SCRIPT_DIR/src/runners" "$dest_dir/src/"
    cp -r "$SCRIPT_DIR/src/augmentations" "$dest_dir/src/"
    cp -r "$SCRIPT_DIR/src/common" "$dest_dir/src/"
    cp -r "$SCRIPT_DIR/src/sup" "$dest_dir/src/"
    touch "$dest_dir/src/__init__.py"
}

create_run_script() {
    local deploy_dir="$1"
    local run_script="$deploy_dir/run_agent.sh"

    log "Creating run script: $run_script"

    # Map content/mechanics to runner args
    local content_arg="none"
    local mechanics_arg="none"
    [[ "$CONTENT" != "mchp" ]] && content_arg="$CONTENT"
    [[ "$MECHANICS" != "mchp" ]] && mechanics_arg="$MECHANICS"

    local phase_arg=""
    $PHASE && phase_arg="--phase"

    local model_name="${MODEL_NAMES[$MODEL]:-llama3.1:8b}"

    # Build model arg (skip if none)
    local model_arg=""
    [[ "$MODEL" != "none" ]] && model_arg="--model=$MODEL"

    # Build runner command based on brain
    local runner_cmd=""
    local xvfb_prefix=""

    case "$BRAIN" in
        upstream)
            # M0: Run upstream MITRE pyhuman via DOLOS wrapper
            runner_cmd="python3 -m runners.run_m0"
            xvfb_prefix=""  # xvfb is handled inside run_m0.py
            ;;
        mchp)
            local mchp_phase_arg=""
            $PHASE && mchp_phase_arg="--phase-timing"
            runner_cmd="python3 -m runners.run_mchp --content=$content_arg --mechanics=$mechanics_arg $model_arg $mchp_phase_arg"
            xvfb_prefix="xvfb-run -a "
            ;;
        smolagents)
            if $PHASE; then
                # S4-S6: Loop mode with MCHP workflows and PHASE timing
                runner_cmd="python3 -m runners.run_smolagents --loop $model_arg"
            else
                # S1-S3: Single task mode
                runner_cmd="python3 -m runners.run_smolagents \"\$TASK\" $model_arg"
            fi
            ;;
        browseruse)
            if $PHASE; then
                # B4-B6: Loop mode with MCHP workflows and PHASE timing
                runner_cmd="python3 -m runners.run_browseruse --loop $model_arg"
            else
                # B1-B3: Single task mode
                runner_cmd="python3 -m runners.run_browseruse $model_arg"
            fi
            xvfb_prefix="xvfb-run -a "
            ;;
    esac

    cat > "$run_script" << EOF
#!/bin/bash
# $CONFIG_KEY Agent Runner
# Generated by INSTALL_SUP.sh

set -e

cd "$deploy_dir"
source venv/bin/activate

# Configuration
export PATH="\$HOME/.local/bin:/usr/local/cuda/bin:\$PATH"
export LD_LIBRARY_PATH="/usr/local/cuda/lib64:\${LD_LIBRARY_PATH:-}"
export OLLAMA_MODEL="$model_name"
export LITELLM_MODEL="ollama/$model_name"
export PYTHONPATH="$deploy_dir/src:\${PYTHONPATH:-}"
export LOG_DIR="$deploy_dir/logs"

# Task (for LLM agents)
TASK="\${1:-Research the latest technology news}"

cd src
${xvfb_prefix}${runner_cmd}

deactivate
EOF
    chmod +x "$run_script"
}

create_systemd_service() {
    local service_name="$1"
    local deploy_dir="$2"

    log "Creating systemd service: $service_name"

    sudo tee "/etc/systemd/system/${service_name}.service" > /dev/null << EOF
[Unit]
Description=$CONFIG_KEY SUP Agent
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$deploy_dir
ExecStart=/bin/bash $deploy_dir/run_agent.sh
Restart=always
RestartSec=5s
StandardOutput=append:$deploy_dir/logs/systemd.log
StandardError=append:$deploy_dir/logs/systemd_error.log

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable "${service_name}.service"
}

# ============================================================================
# Runner Mode (Direct Execution)
# ============================================================================

run_directly() {
    log "Running $CONFIG_KEY directly (development mode)..."

    # Install Ollama if needed
    if [[ "$MODEL" != "none" ]]; then
        install_ollama
    fi

    # Map content/mechanics to runner args
    local content_arg="none"
    local mechanics_arg="none"
    [[ "$CONTENT" != "mchp" ]] && content_arg="$CONTENT"
    [[ "$MECHANICS" != "mchp" ]] && mechanics_arg="$MECHANICS"

    local phase_arg=""
    $PHASE && phase_arg="--phase"

    # Set environment
    export PYTHONPATH="$SCRIPT_DIR/src:${PYTHONPATH:-}"
    export OLLAMA_MODEL="${MODEL_NAMES[$MODEL]:-llama3.1:8b}"
    export LITELLM_MODEL="ollama/$OLLAMA_MODEL"

    cd "$SCRIPT_DIR/src"

    # Build model arg (skip if none)
    local model_arg=""
    [[ "$MODEL" != "none" ]] && model_arg="--model=$MODEL"

    case "$BRAIN" in
        mchp)
            local mchp_phase_arg=""
            $PHASE && mchp_phase_arg="--phase-timing"
            log "Running MCHP agent..."
            exec xvfb-run -a python3 -m runners.run_mchp --content="$content_arg" --mechanics="$mechanics_arg" $model_arg $mchp_phase_arg
            ;;
        smolagents)
            if $PHASE; then
                log "Running SmolAgents loop mode..."
                exec python3 -m runners.run_smolagents --loop $model_arg
            else
                local task="${TASK:-What is the latest news in technology?}"
                log "Running SmolAgents with task: $task"
                exec python3 -m runners.run_smolagents "$task" $model_arg
            fi
            ;;
        browseruse)
            if $PHASE; then
                log "Running BrowserUse loop mode..."
                exec xvfb-run -a python3 -m runners.run_browseruse --loop $model_arg
            else
                log "Running BrowserUse agent..."
                exec xvfb-run -a python3 -m runners.run_browseruse $model_arg
            fi
            ;;
    esac
}

# ============================================================================
# M0 Upstream MITRE pyhuman Installation
# ============================================================================

install_m0_upstream() {
    log "Installing M0 upstream MITRE pyhuman..."

    # Clone upstream MITRE repo if not already present
    if [[ ! -d "/opt/human" ]]; then
        log "Cloning upstream MITRE pyhuman..."
        sudo git clone https://github.com/mitre/human.git /opt/human
        sudo chown -R "$USER:$USER" /opt/human
    else
        log "Upstream MITRE pyhuman already present at /opt/human"
    fi

    # Create venv and install deps for upstream pyhuman
    if [[ ! -d "/opt/human/pyhuman/venv" ]]; then
        log "Creating upstream pyhuman virtual environment..."
        python3 -m venv /opt/human/pyhuman/venv
        source /opt/human/pyhuman/venv/bin/activate
        pip install --upgrade pip
        pip install -r /opt/human/pyhuman/requirements.txt
        deactivate
    else
        log "Upstream pyhuman venv already exists"
    fi

    log "M0 upstream installation complete"
}

# ============================================================================
# Full Installation
# ============================================================================

install_agent() {
    # Generate deploy name and service name
    local deploy_name="$CONFIG_KEY"
    local deploy_dir="$SCRIPT_DIR/deployed_sups/$deploy_name"
    local service_name=$(echo "$deploy_name" | tr '[:upper:].' '[:lower:]_' | tr '+' 'p')

    log "Installing $CONFIG_KEY..."
    log "  Brain: $BRAIN"
    log "  Content: $CONTENT"
    log "  Mechanics: $MECHANICS"
    log "  Model: $MODEL"
    log "  PHASE: $PHASE"
    log "  Deploy directory: $deploy_dir"
    log "  Service name: $service_name"
    [[ "$STAGE" != "0" ]] && log "  Stage: $STAGE"

    # ========== STAGE 1: System deps and drivers (pre-reboot) ==========
    if [[ "$STAGE" == "0" || "$STAGE" == "1" ]]; then
        log "=== Stage 1: Installing system dependencies ==="

        # Install system dependencies (includes CUDA if GPU present)
        install_system_deps

        # Create deployment directory
        mkdir -p "$deploy_dir/logs"

        # Check if reboot needed for NVIDIA driver
        if $CUDA_INSTALLED; then
            if [[ "$STAGE" == "1" ]]; then
                log "Stage 1 complete. NVIDIA drivers installed - reboot required."
                log "After reboot, run: ./INSTALL_SUP.sh --$CONFIG_KEY --stage=2"
                exit 100  # Special exit code: reboot needed
            else
                log "CUDA drivers installed. Rebooting in 5 seconds..."
                sleep 5
                sudo reboot
            fi
        fi

        if [[ "$STAGE" == "1" ]]; then
            log "Stage 1 complete. No reboot required."
            exit 0
        fi
    fi

    # ========== STAGE 2: Ollama, Python, services (post-reboot) ==========
    if [[ "$STAGE" == "0" || "$STAGE" == "2" ]]; then
        log "=== Stage 2: Installing application components ==="

        # Ensure deploy dir exists (may be running stage 2 after reboot)
        mkdir -p "$deploy_dir/logs"

        # M0 special handling: Install upstream MITRE pyhuman
        if [[ "$CONFIG_KEY" == "M0" ]]; then
            install_m0_upstream
        fi

        # Install Ollama if needed (skip for M0 - no LLM)
        if [[ "$MODEL" != "none" ]]; then
            install_ollama
        fi

        # Create virtual environment
        log "Creating Python virtual environment..."
        python3 -m venv "$deploy_dir/venv"

        # Install Python dependencies
        install_python_deps "$deploy_dir/venv"

        # Copy source code
        copy_source_code "$deploy_dir"

        # Create run script
        create_run_script "$deploy_dir"

        # Create systemd service
        create_systemd_service "$service_name" "$deploy_dir"

        # Start service
        log "Starting $service_name service..."
        sudo systemctl start "${service_name}.service"
    fi

    echo ""
    echo -e "${GREEN}================================${NC}"
    echo -e "${GREEN}  INSTALLATION COMPLETE!${NC}"
    echo -e "${GREEN}================================${NC}"
    echo ""
    echo "Configuration: $CONFIG_KEY"
    echo "  Brain:      $BRAIN"
    echo "  Content:    $CONTENT"
    echo "  Mechanics:  $MECHANICS"
    echo "  Model:      $MODEL"
    echo "  PHASE:      $PHASE"
    echo ""
    echo "Deploy directory: $deploy_dir"
    echo "Service: $service_name"
    echo ""
    echo "Commands:"
    echo "  sudo systemctl status $service_name"
    echo "  sudo systemctl stop $service_name"
    echo "  sudo journalctl -u $service_name -f"
    echo ""
}

# ============================================================================
# Main
# ============================================================================

main() {
    if [[ $# -eq 0 ]]; then
        usage
        exit 1
    fi

    parse_args "$@"

    echo ""
    echo -e "${BLUE}================================${NC}"
    echo -e "${BLUE}  DOLOS-DEPLOY Installer${NC}"
    echo -e "${BLUE}================================${NC}"
    echo ""
    log_info "Config: $CONFIG_KEY"
    log_info "Brain: $BRAIN | Content: $CONTENT | Mechanics: $MECHANICS | Model: $MODEL"
    log_info "PHASE: $PHASE | Mode: $(if $RUNNER; then echo 'Runner'; else echo 'Install'; fi)"
    echo ""

    if $RUNNER; then
        run_directly
    else
        install_agent
    fi
}

main "$@"
