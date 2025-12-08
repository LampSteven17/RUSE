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

=== Configuration Keys (from EXPERIMENTAL_PLAN.md) ===

  PRE-PHASE (13 configs):
    --M1                Pure MCHP (no LLM)
    --M2.llama          MCHP + SmolAgents content/mechanics
    --M2a.llama         MCHP + SmolAgents content only
    --M2b.llama         MCHP + SmolAgents mechanics only
    --M3.llama          MCHP + BrowserUse content/mechanics
    --M3a.llama         MCHP + BrowserUse content only
    --M3b.llama         MCHP + BrowserUse mechanics only
    --B1.llama          BrowserUse + llama3.1:8b
    --B2.gemma          BrowserUse + gemma3:4b
    --B3.deepseek       BrowserUse + deepseek-r1:8b
    --S1.llama          SmolAgents + llama3.1:8b
    --S2.gemma          SmolAgents + gemma3:4b
    --S3.deepseek       SmolAgents + deepseek-r1:8b

  POST-PHASE (with + suffix):
    --B1.llama+         BrowserUse + llama + PHASE timing
    --S1.llama+         SmolAgents + llama + PHASE timing
    (etc.)

=== Long-Form Options ===

  --brain <TYPE>        Brain type: mchp, smolagents, browseruse
  --content <TYPE>      Content controller: mchp, smolagents, browseruse
  --mechanics <TYPE>    Mechanics controller: mchp, smolagents, browseruse
  --model <MODEL>       Model: none, llama, gemma, deepseek
  --phase               Enable PHASE timing/prompts (adds + suffix)

=== Execution Options ===

  --runner              Run directly without systemd installation
  --task "TASK"         Task for LLM agents (with --runner)
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

# Pre-defined configurations matching EXPERIMENTAL_PLAN.md
declare -A CONFIGS
CONFIGS=(
    # M Series - MCHP brain
    ["M1"]="mchp:mchp:mchp:none:false"
    ["M2.llama"]="mchp:smolagents:smolagents:llama:false"
    ["M2a.llama"]="mchp:smolagents:mchp:llama:false"
    ["M2b.llama"]="mchp:mchp:smolagents:llama:false"
    ["M3.llama"]="mchp:browseruse:browseruse:llama:false"
    ["M3a.llama"]="mchp:browseruse:mchp:llama:false"
    ["M3b.llama"]="mchp:mchp:browseruse:llama:false"

    # B Series - BrowserUse brain
    ["B1.llama"]="browseruse:browseruse:browseruse:llama:false"
    ["B2.gemma"]="browseruse:browseruse:browseruse:gemma:false"
    ["B3.deepseek"]="browseruse:browseruse:browseruse:deepseek:false"

    # S Series - SmolAgents brain
    ["S1.llama"]="smolagents:smolagents:smolagents:llama:false"
    ["S2.gemma"]="smolagents:smolagents:smolagents:gemma:false"
    ["S3.deepseek"]="smolagents:smolagents:smolagents:deepseek:false"

    # POST-PHASE configurations (+ suffix)
    ["B1.llama+"]="browseruse:browseruse:browseruse:llama:true"
    ["B2.gemma+"]="browseruse:browseruse:browseruse:gemma:true"
    ["B3.deepseek+"]="browseruse:browseruse:browseruse:deepseek:true"
    ["S1.llama+"]="smolagents:smolagents:smolagents:llama:true"
    ["S2.gemma+"]="smolagents:smolagents:smolagents:gemma:true"
    ["S3.deepseek+"]="smolagents:smolagents:smolagents:deepseek:true"
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
    echo "PRE-PHASE:"
    for key in M1 M2.llama M2a.llama M2b.llama M3.llama M3a.llama M3b.llama \
               B1.llama B2.gemma B3.deepseek S1.llama S2.gemma S3.deepseek; do
        IFS=':' read -r brain content mechanics model phase <<< "${CONFIGS[$key]}"
        printf "  %-14s brain=%-10s content=%-12s mechanics=%-12s model=%s\n" \
            "--$key" "$brain" "$content" "$mechanics" "$model"
    done
    echo ""
    echo "POST-PHASE:"
    for key in B1.llama+ B2.gemma+ B3.deepseek+ S1.llama+ S2.gemma+ S3.deepseek+; do
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
            # Config key shortcuts (e.g., --M1, --S1.llama, --B2.gemma+)
            --M1|--M2.llama|--M2a.llama|--M2b.llama|--M3.llama|--M3a.llama|--M3b.llama|\
            --B1.llama|--B2.gemma|--B3.deepseek|--S1.llama|--S2.gemma|--S3.deepseek|\
            --B1.llama+|--B2.gemma+|--B3.deepseek+|--S1.llama+|--S2.gemma+|--S3.deepseek+)
                parse_config_key "${1#--}"
                ;;

            # Long-form options
            --brain)
                shift
                BRAIN="$1"
                ;;
            --content)
                shift
                CONTENT="$1"
                ;;
            --mechanics)
                shift
                MECHANICS="$1"
                ;;
            --model)
                shift
                MODEL="$1"
                ;;
            --phase)
                PHASE=true
                ;;

            # Execution options
            --runner)
                RUNNER=true
                ;;
            --task)
                shift
                TASK="$1"
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

install_cuda() {
    # Install CUDA 12.8 from NVIDIA's official repository
    # Required for GPU-accelerated BrowserUse/SmolAgents
    # Ref: https://developer.nvidia.com/cuda-12-8-0-download-archive

    # Check if GPU is present
    if ! lspci | grep -qi nvidia; then
        log "No NVIDIA GPU detected, skipping CUDA installation"
        return 0
    fi

    log "NVIDIA GPU detected, installing CUDA 12.8..."

    # Install CUDA keyring
    wget -q https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb -O /tmp/cuda-keyring.deb
    sudo dpkg -i /tmp/cuda-keyring.deb
    rm -f /tmp/cuda-keyring.deb

    # Update and install CUDA toolkit + drivers
    # Note: cuda-drivers triggers modprobe which fails before reboot - this is expected
    sudo apt-get update -y
    sudo apt-get install -y cuda-toolkit-12-8
    sudo apt-get install -y cuda-drivers || {
        log "CUDA drivers installed (modprobe fails until reboot - this is normal)"
    }

    # Add CUDA to PATH for current session
    export PATH=/usr/local/cuda-12.8/bin:$PATH
    export LD_LIBRARY_PATH=/usr/local/cuda-12.8/lib64:$LD_LIBRARY_PATH

    # Add to bashrc for future sessions
    if ! grep -q "cuda-12.8" ~/.bashrc; then
        echo 'export PATH=/usr/local/cuda-12.8/bin:$PATH' >> ~/.bashrc
        echo 'export LD_LIBRARY_PATH=/usr/local/cuda-12.8/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc
    fi

    log "CUDA 12.8 installed. Reboot required for driver to load."
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
            # Install CUDA if GPU is present (for LLM acceleration)
            install_cuda
            ;;
    esac
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
            pip install smolagents litellm torch transformers datasets numpy pandas requests duckduckgo-search
            ;;
        browseruse)
            pip install browser-use langchain-ollama playwright
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
        mchp)
            runner_cmd="python3 -m runners.run_mchp --content=$content_arg --mechanics=$mechanics_arg $model_arg $phase_arg"
            xvfb_prefix="xvfb-run -a "
            ;;
        smolagents)
            runner_cmd="python3 -m runners.run_smolagents \"\$TASK\" $model_arg $phase_arg"
            ;;
        browseruse)
            runner_cmd="python3 -m runners.run_browseruse $model_arg $phase_arg"
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
            log "Running MCHP agent..."
            exec xvfb-run -a python3 -m runners.run_mchp --content="$content_arg" --mechanics="$mechanics_arg" $model_arg $phase_arg
            ;;
        smolagents)
            local task="${TASK:-What is the latest news in technology?}"
            log "Running SmolAgents with task: $task"
            exec python3 -m runners.run_smolagents "$task" $model_arg $phase_arg
            ;;
        browseruse)
            log "Running BrowserUse agent..."
            exec xvfb-run -a python3 -m runners.run_browseruse $model_arg $phase_arg
            ;;
    esac
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

    # Install Ollama if needed
    if [[ "$MODEL" != "none" ]]; then
        install_ollama
    fi

    # Install system dependencies
    install_system_deps

    # Create deployment directory
    mkdir -p "$deploy_dir/logs"

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
