#!/bin/bash

# DOLOS-DEPLOY: Unified SUP Installer
# Supports MCHP, SMOL, BU and all combinations

set -e  # Exit on any command failure
set -u  # Exit on undefined variables
set -o pipefail  # Exit on pipe failures

# Configuration: Default model for LLM agents
DEFAULT_OLLAMA_MODEL="${DEFAULT_OLLAMA_MODEL:-llama3.1:8b}"

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
    echo ""
    echo -e "${RED}Error at line ${line_number}, exit code ${exit_code}${NC}"
    echo -e "${RED}Command: ${BASH_COMMAND}${NC}"
    exit $exit_code
}
trap 'error_handler ${LINENO}' ERR

log() { echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"; }
log_error() { echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1" >&2; }
log_info() { echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')] INFO:${NC} $1"; }

usage() {
    echo "DOLOS-DEPLOY Unified Installer"
    echo ""
    echo "Usage: $0 [FLAGS] [OPTIONS]"
    echo ""
    echo "=== DEFAULT Configurations ==="
    echo "  $0 --mchp                         Install standard MCHP (human simulation)"
    echo "  $0 --smol --default               Install standard SMOL agent"
    echo "  $0 --smol --mchp-like             Install SMOL with MCHP-like behavior"
    echo "  $0 --bu --default                 Install standard BU agent"
    echo "  $0 --bu --mchp-like               Install BU with MCHP-like behavior"
    echo ""
    echo "=== HYBRID Configurations (MCHP workflows + LLM content) ==="
    echo "  $0 --mchp --smol                  Install MCHP-SMOL hybrid"
    echo "  $0 --mchp --bu                    Install MCHP-BU hybrid"
    echo ""
    echo "=== PHASE Configurations (LLM + improved timing) ==="
    echo "  $0 --smol --phase                 Install SMOL-PHASE agent"
    echo "  $0 --bu --phase                   Install BU-PHASE agent"
    echo ""
    echo "=== Options ==="
    echo "  --model=MODEL                     Override Ollama model (default: $DEFAULT_OLLAMA_MODEL)"
    echo "  --help                            Display this help message"
    echo ""
    echo "=== Configuration Matrix ==="
    echo ""
    echo "  Tier      | Flags               | Description"
    echo "  ----------|---------------------|------------------------------------------"
    echo "  DEFAULT   | --mchp              | Human simulation (Selenium + pyautogui)"
    echo "  DEFAULT   | --smol --default    | Basic CodeAgent with DuckDuckGo search"
    echo "  DEFAULT   | --bu --default      | Basic browser automation agent"
    echo "  MCHP-LIKE | --smol --mchp-like  | SMOL with MCHP timing patterns"
    echo "  MCHP-LIKE | --bu --mchp-like    | BU with MCHP timing patterns"
    echo "  HYBRID    | --mchp --smol       | MCHP workflows + SMOL LLM content"
    echo "  HYBRID    | --mchp --bu         | MCHP workflows + BU LLM content"
    echo "  PHASE     | --smol --phase      | SMOL + time-of-day timing + logging"
    echo "  PHASE     | --bu --phase        | BU + time-of-day timing + logging"
    echo ""
}

# Parse flags into variables
FLAG_MCHP=false
FLAG_SMOL=false
FLAG_BU=false
FLAG_PHASE=false
FLAG_DEFAULT=false
FLAG_MCHP_LIKE=false
FLAG_IMPROVED=false

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --mchp) FLAG_MCHP=true ;;
            --smol) FLAG_SMOL=true ;;
            --bu) FLAG_BU=true ;;
            --phase) FLAG_PHASE=true ;;
            --default) FLAG_DEFAULT=true ;;
            --mchp-like) FLAG_MCHP_LIKE=true ;;
            --improved) FLAG_IMPROVED=true ;;
            --model=*) DEFAULT_OLLAMA_MODEL="${1#*=}" ;;
            --help|-h) usage; exit 0 ;;
            *) log_error "Unknown option: $1"; usage; exit 1 ;;
        esac
        shift
    done
}

# Determine configuration based on flags
determine_config() {
    # HYBRID: --mchp --smol
    if $FLAG_MCHP && $FLAG_SMOL && ! $FLAG_BU && ! $FLAG_PHASE; then
        echo "MCHP-SMOL"
        return
    fi

    # HYBRID: --mchp --bu
    if $FLAG_MCHP && $FLAG_BU && ! $FLAG_SMOL && ! $FLAG_PHASE; then
        echo "MCHP-BU"
        return
    fi

    # PHASE: --smol --phase
    if $FLAG_SMOL && $FLAG_PHASE && ! $FLAG_MCHP && ! $FLAG_BU; then
        echo "SMOL-PHASE"
        return
    fi

    # PHASE: --bu --phase
    if $FLAG_BU && $FLAG_PHASE && ! $FLAG_MCHP && ! $FLAG_SMOL; then
        echo "BU-PHASE"
        return
    fi

    # DEFAULT: --mchp alone
    if $FLAG_MCHP && ! $FLAG_SMOL && ! $FLAG_BU; then
        echo "MCHP"
        return
    fi

    # SMOL with config
    if $FLAG_SMOL && ! $FLAG_MCHP && ! $FLAG_BU && ! $FLAG_PHASE; then
        if $FLAG_DEFAULT; then
            echo "SMOL-DEFAULT"
        elif $FLAG_MCHP_LIKE; then
            echo "SMOL-MCHP-LIKE"
        elif $FLAG_IMPROVED; then
            echo "SMOL-IMPROVED"
        else
            log_error "SMOL requires a configuration: --default, --mchp-like, or --improved"
            exit 1
        fi
        return
    fi

    # BU with config
    if $FLAG_BU && ! $FLAG_MCHP && ! $FLAG_SMOL && ! $FLAG_PHASE; then
        if $FLAG_DEFAULT; then
            echo "BU-DEFAULT"
        elif $FLAG_MCHP_LIKE; then
            echo "BU-MCHP-LIKE"
        elif $FLAG_IMPROVED; then
            echo "BU-IMPROVED"
        else
            log_error "BU requires a configuration: --default, --mchp-like, or --improved"
            exit 1
        fi
        return
    fi

    # Invalid combination
    log_error "Invalid flag combination"
    usage
    exit 1
}

# ============================================================================
# Installation Functions
# ============================================================================

install_ollama() {
    log "Setting up Ollama with model: $DEFAULT_OLLAMA_MODEL"
    if [ -f "$SCRIPT_DIR/src/install_scripts/install_ollama.sh" ]; then
        chmod +x "$SCRIPT_DIR/src/install_scripts/install_ollama.sh"
        export OLLAMA_MODELS="$DEFAULT_OLLAMA_MODEL"
        "$SCRIPT_DIR/src/install_scripts/install_ollama.sh"
        log "Ollama setup complete"
    else
        log_error "install_ollama.sh not found"
        exit 1
    fi
}

install_common_modules() {
    local dest_dir="$1"
    log "Installing common modules to $dest_dir"
    mkdir -p "$dest_dir/common"
    cp -r "$SCRIPT_DIR/src/common/logging" "$dest_dir/common/"
    cp -r "$SCRIPT_DIR/src/common/timing" "$dest_dir/common/"
    cp "$SCRIPT_DIR/src/common/__init__.py" "$dest_dir/common/"
}

create_systemd_service() {
    local service_name="$1"
    local work_dir="$2"
    local run_script="$3"
    local description="$4"

    log "Creating systemd service: $service_name"

    sudo tee "/etc/systemd/system/${service_name}.service" > /dev/null << EOF
[Unit]
Description=$description
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$work_dir
ExecStart=/bin/bash $run_script
Restart=always
RestartSec=5s
StandardOutput=append:$work_dir/logs/systemd.log
StandardError=append:$work_dir/logs/systemd_error.log

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable "${service_name}.service"
}

# ============================================================================
# Config-specific Installation Functions
# ============================================================================

install_mchp() {
    log "Installing MCHP (standard human simulation)..."

    if [ -f "$SCRIPT_DIR/src/MCHP/install_mchp.sh" ]; then
        cd "$SCRIPT_DIR/src/MCHP"
        chmod +x install_mchp.sh
        ./install_mchp.sh --installpath="$SCRIPT_DIR"

        # Test
        if [ -f "$SCRIPT_DIR/src/install_scripts/test_agent.sh" ]; then
            chmod +x "$SCRIPT_DIR/src/install_scripts/test_agent.sh"
            "$SCRIPT_DIR/src/install_scripts/test_agent.sh" --agent=MCHP --path="$SCRIPT_DIR"
        fi

        # Start service
        sudo systemctl start mchp.service
        log "MCHP installation complete"
    else
        log_error "MCHP installer not found"
        exit 1
    fi
}

install_mchp_hybrid() {
    local backend="$1"  # "smol" or "bu"
    local deploy_name="MCHP-${backend^^}"  # MCHP-SMOL or MCHP-BU
    local service_name=$(echo "$deploy_name" | tr '[:upper:]' '[:lower:]' | tr '-' '_')

    log "Installing $deploy_name (MCHP + ${backend^^} LLM content)..."

    # Install Ollama
    install_ollama

    # Create directories
    mkdir -p "$SCRIPT_DIR/deployed_sups/$deploy_name/logs"

    # Install system dependencies
    log "Installing system dependencies..."
    sudo apt-get update -y
    sudo apt-get install -y python3-pip python3-venv python3-dev build-essential \
        xvfb xdg-utils firefox libxml2-dev libxslt-dev python3-tk scrot

    # Install Geckodriver
    log "Installing Geckodriver..."
    ARCH=$(uname -m)
    if [[ "$ARCH" == "x86_64" ]]; then
        wget -q "https://github.com/mozilla/geckodriver/releases/download/v0.34.0/geckodriver-v0.34.0-linux64.tar.gz" -O /tmp/geckodriver.tar.gz
        tar -xzf /tmp/geckodriver.tar.gz -C "$SCRIPT_DIR/deployed_sups/$deploy_name/"
        rm -f /tmp/geckodriver.tar.gz
    fi

    # Create venv
    log "Creating Python environment..."
    python3 -m venv "$SCRIPT_DIR/deployed_sups/$deploy_name/venv"
    source "$SCRIPT_DIR/deployed_sups/$deploy_name/venv/bin/activate"
    pip install --upgrade pip

    # Install base deps
    pip install selenium beautifulsoup4 webdriver-manager lxml pyautogui lorem \
        certifi chardet colorama configparser crayons idna requests urllib3

    # Install backend-specific deps
    if [[ "$backend" == "smol" ]]; then
        pip install smolagents litellm torch transformers
    else
        pip install langchain-ollama
    fi
    deactivate

    # Copy code
    log "Copying HYBRID code..."
    cp -r "$SCRIPT_DIR/src/MCHP-HYBRID/common/pyhuman" "$SCRIPT_DIR/deployed_sups/$deploy_name/"
    cp "$SCRIPT_DIR/src/MCHP-HYBRID/${backend}-backend/llm_config.py" "$SCRIPT_DIR/deployed_sups/$deploy_name/"
    install_common_modules "$SCRIPT_DIR/deployed_sups/$deploy_name"

    # Create run script
    cat > "$SCRIPT_DIR/deployed_sups/$deploy_name/run_hybrid.sh" << EOF
#!/bin/bash
cd "$SCRIPT_DIR/deployed_sups/$deploy_name"
source venv/bin/activate
export HYBRID_LLM_BACKEND="$backend"
export OLLAMA_MODEL="$DEFAULT_OLLAMA_MODEL"
export LITELLM_MODEL="ollama/$DEFAULT_OLLAMA_MODEL"
export PYTHONPATH="$SCRIPT_DIR/deployed_sups/$deploy_name:\$PYTHONPATH"
python3 -c "import llm_config; llm_config.test_connection()" || exit 1
xvfb-run -a python3 pyhuman/human.py
deactivate
EOF
    chmod +x "$SCRIPT_DIR/deployed_sups/$deploy_name/run_hybrid.sh"

    # Create service
    create_systemd_service "$service_name" "$SCRIPT_DIR/deployed_sups/$deploy_name" \
        "$SCRIPT_DIR/deployed_sups/$deploy_name/run_hybrid.sh" "$deploy_name HYBRID Agent"

    log "$deploy_name installation complete"
    echo "Service: $service_name"
}

install_smol_default() {
    local config="$1"  # "default", "mchp", or "improved"
    log "Installing SMOL with $config configuration..."

    install_ollama

    if [ -f "$SCRIPT_DIR/src/SMOL/install_smol.sh" ]; then
        cd "$SCRIPT_DIR/src/SMOL"
        chmod +x install_smol.sh
        ./install_smol.sh --installpath="$SCRIPT_DIR" --config="$config"

        # Test
        if [ -f "$SCRIPT_DIR/src/install_scripts/test_agent.sh" ]; then
            chmod +x "$SCRIPT_DIR/src/install_scripts/test_agent.sh"
            "$SCRIPT_DIR/src/install_scripts/test_agent.sh" --agent=SMOL --path="$SCRIPT_DIR"
        fi

        # Create and start service
        create_systemd_service "smol" "$SCRIPT_DIR/deployed_sups/SMOL" \
            "$SCRIPT_DIR/deployed_sups/SMOL/run_smol.sh" "SMOL Agent Service"
        sudo systemctl start smol.service

        log "SMOL ($config) installation complete"
    else
        log_error "SMOL installer not found"
        exit 1
    fi
}

install_bu_default() {
    local config="$1"  # "default", "mchp", or "improved"
    log "Installing BU with $config configuration..."

    install_ollama

    if [ -f "$SCRIPT_DIR/src/BU/install_bu.sh" ]; then
        cd "$SCRIPT_DIR/src/BU"
        chmod +x install_bu.sh
        export OLLAMA_MODEL_DEFAULT="$DEFAULT_OLLAMA_MODEL"
        ./install_bu.sh --installpath="$SCRIPT_DIR" --config="$config"

        # Test
        if [ -f "$SCRIPT_DIR/src/install_scripts/test_agent.sh" ]; then
            chmod +x "$SCRIPT_DIR/src/install_scripts/test_agent.sh"
            "$SCRIPT_DIR/src/install_scripts/test_agent.sh" --agent=BU --path="$SCRIPT_DIR"
        fi

        # Create and start service
        create_systemd_service "bu" "$SCRIPT_DIR/deployed_sups/BU" \
            "$SCRIPT_DIR/deployed_sups/BU/run_bu.sh" "BU Agent Service"
        sudo systemctl start bu.service

        log "BU ($config) installation complete"
    else
        log_error "BU installer not found"
        exit 1
    fi
}

install_smol_phase() {
    log "Installing SMOL-PHASE (SMOL + PHASE timing + logging)..."

    install_ollama

    local deploy_name="SMOL-PHASE"
    mkdir -p "$SCRIPT_DIR/deployed_sups/$deploy_name/logs"

    # Install deps
    log "Installing dependencies..."
    sudo apt-get update -y
    sudo apt-get install -y python3-pip python3-venv python3-dev build-essential

    python3 -m venv "$SCRIPT_DIR/deployed_sups/$deploy_name/venv"
    source "$SCRIPT_DIR/deployed_sups/$deploy_name/venv/bin/activate"
    pip install --upgrade pip
    pip install smolagents litellm torch transformers datasets numpy pandas requests ddgs
    deactivate

    # Copy code
    cp "$SCRIPT_DIR/src/SMOL-PHASE/agent.py" "$SCRIPT_DIR/deployed_sups/$deploy_name/"
    install_common_modules "$SCRIPT_DIR/deployed_sups/$deploy_name"

    # Create run script
    cat > "$SCRIPT_DIR/deployed_sups/$deploy_name/run_smol_phase.sh" << EOF
#!/bin/bash
cd "$SCRIPT_DIR/deployed_sups/$deploy_name"
source venv/bin/activate
export LITELLM_MODEL="ollama/$DEFAULT_OLLAMA_MODEL"
export SMOL_PHASE_LOG_DIR="$SCRIPT_DIR/deployed_sups/$deploy_name/logs"
export PYTHONPATH="$SCRIPT_DIR/deployed_sups/$deploy_name:\$PYTHONPATH"
python3 agent.py
deactivate
EOF
    chmod +x "$SCRIPT_DIR/deployed_sups/$deploy_name/run_smol_phase.sh"

    create_systemd_service "smol_phase" "$SCRIPT_DIR/deployed_sups/$deploy_name" \
        "$SCRIPT_DIR/deployed_sups/$deploy_name/run_smol_phase.sh" "SMOL-PHASE Agent Service"

    log "SMOL-PHASE installation complete"
    echo "Service: smol_phase"
}

install_bu_phase() {
    log "Installing BU-PHASE (BU + PHASE timing + logging)..."

    install_ollama

    local deploy_name="BU-PHASE"
    mkdir -p "$SCRIPT_DIR/deployed_sups/$deploy_name/logs"

    # Install deps
    log "Installing dependencies..."
    sudo apt-get update -y
    sudo apt-get install -y python3-pip python3-venv python3-dev build-essential xvfb

    python3 -m venv "$SCRIPT_DIR/deployed_sups/$deploy_name/venv"
    source "$SCRIPT_DIR/deployed_sups/$deploy_name/venv/bin/activate"
    pip install --upgrade pip
    pip install browser-use langchain-ollama playwright
    playwright install chromium
    playwright install-deps chromium
    deactivate

    # Copy code
    cp "$SCRIPT_DIR/src/BU-PHASE/agent.py" "$SCRIPT_DIR/deployed_sups/$deploy_name/"
    install_common_modules "$SCRIPT_DIR/deployed_sups/$deploy_name"

    # Create run script
    cat > "$SCRIPT_DIR/deployed_sups/$deploy_name/run_bu_phase.sh" << EOF
#!/bin/bash
cd "$SCRIPT_DIR/deployed_sups/$deploy_name"
source venv/bin/activate
export OLLAMA_MODEL="$DEFAULT_OLLAMA_MODEL"
export BU_PHASE_LOG_DIR="$SCRIPT_DIR/deployed_sups/$deploy_name/logs"
export PYTHONPATH="$SCRIPT_DIR/deployed_sups/$deploy_name:\$PYTHONPATH"
xvfb-run -a python3 agent.py
deactivate
EOF
    chmod +x "$SCRIPT_DIR/deployed_sups/$deploy_name/run_bu_phase.sh"

    create_systemd_service "bu_phase" "$SCRIPT_DIR/deployed_sups/$deploy_name" \
        "$SCRIPT_DIR/deployed_sups/$deploy_name/run_bu_phase.sh" "BU-PHASE Agent Service"

    log "BU-PHASE installation complete"
    echo "Service: bu_phase"
}

# ============================================================================
# Main
# ============================================================================

if [[ $# -eq 0 ]]; then
    usage
    exit 1
fi

parse_args "$@"
CONFIG=$(determine_config)

echo ""
echo -e "${BLUE}================================${NC}"
echo -e "${BLUE}  DOLOS-DEPLOY Installer${NC}"
echo -e "${BLUE}================================${NC}"
echo ""
log_info "Configuration: $CONFIG"
log_info "Model: $DEFAULT_OLLAMA_MODEL"
echo ""

case $CONFIG in
    "MCHP")
        install_mchp
        ;;
    "MCHP-SMOL")
        install_mchp_hybrid "smol"
        ;;
    "MCHP-BU")
        install_mchp_hybrid "bu"
        ;;
    "SMOL-DEFAULT")
        install_smol_default "default"
        ;;
    "SMOL-MCHP-LIKE")
        install_smol_default "mchp"
        ;;
    "SMOL-IMPROVED")
        install_smol_default "improved"
        ;;
    "BU-DEFAULT")
        install_bu_default "default"
        ;;
    "BU-MCHP-LIKE")
        install_bu_default "mchp"
        ;;
    "BU-IMPROVED")
        install_bu_default "improved"
        ;;
    "SMOL-PHASE")
        install_smol_phase
        ;;
    "BU-PHASE")
        install_bu_phase
        ;;
    *)
        log_error "Unknown configuration: $CONFIG"
        exit 1
        ;;
esac

echo ""
echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}  INSTALLATION COMPLETE!${NC}"
echo -e "${GREEN}================================${NC}"
echo ""
echo "Configuration: $CONFIG"
echo "Model: $DEFAULT_OLLAMA_MODEL"
echo ""
echo "Service commands:"
echo "  sudo systemctl status <service>"
echo "  sudo systemctl start <service>"
echo "  sudo systemctl stop <service>"
echo "  sudo journalctl -u <service> -f"
echo ""
