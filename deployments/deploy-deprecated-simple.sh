#!/bin/bash
#
# SUP Deployment Script
# Two operations: spinup (provision + install) and teardown
#
# Spinup automatically uses behavior-configs-aware install when the deployment's
# config.yaml contains behavior_source.
#
# Usage:
#   ./deploy.sh                          # Interactive mode
#   ./deploy.sh spinup <deployment>      # Provision + install
#   ./deploy.sh teardown <deployment>    # Delete deployment VMs
#   ./deploy.sh teardown-all             # Delete ALL sup-* VMs

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLAYBOOKS_DIR="$SCRIPT_DIR/playbooks"
SSH_CONFIG="${SSH_CONFIG:-$HOME/.ssh/config}"

# Logging setup
LOGS_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOGS_DIR"
LOG_FILE="$LOGS_DIR/deploy-$(date +%Y%m%d-%H%M%S).log"

# Logging functions
log_to_file() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

start_logging() {
    # Redirect stdout and stderr to both console and log file
    exec > >(tee -a "$LOG_FILE") 2>&1
    log_to_file "=== Deployment started ==="
    log_to_file "Command: $0 $*"
    log_to_file "Working directory: $(pwd)"
    log_to_file "User: $(whoami)"
    log_to_file "Host: $(hostname)"
}

end_logging() {
    local exit_code=${1:-0}
    log_to_file "=== Deployment finished with exit code: $exit_code ==="
}

# Clean up logs older than 30 days
cleanup_old_logs() {
    find "$LOGS_DIR" -name "deploy-*.log" -mtime +30 -delete 2>/dev/null || true
}

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_header() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}  RUSE - SUP Deployment Tool${NC}"
    echo -e "${BLUE}========================================${NC}\n"
}

print_step() {
    echo -e "${GREEN}[*]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[!]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# List available deployments
list_deployments() {
    echo -e "${BLUE}Available deployments:${NC}"
    local i=1
    for dir in "$SCRIPT_DIR"/*/; do
        if [[ -f "${dir}config.yaml" ]]; then
            deployment=$(basename "$dir")
            # Check if behavior-configs-aware
            if grep -q "behavior_source:" "${dir}config.yaml" 2>/dev/null; then
                echo -e "  $i) $deployment  ${GREEN}[behavior]${NC}"
            else
                echo "  $i) $deployment"
            fi
            ((i++))
        fi
    done
}

# Get deployment directories
get_deployments() {
    local deployments=()
    for dir in "$SCRIPT_DIR"/*/; do
        if [[ -f "${dir}config.yaml" ]]; then
            deployments+=("$(basename "$dir")")
        fi
    done
    echo "${deployments[@]}"
}

# Check if deployment has behavioral configs
has_behavior_configs() {
    local deploy_dir="$1"
    grep -q "behavior_source:" "$deploy_dir/config.yaml" 2>/dev/null
}

# Check prerequisites
check_prereqs() {
    print_step "Checking prerequisites..."

    if ! command -v ansible-playbook &> /dev/null; then
        print_error "ansible-playbook not found. Please install Ansible."
        exit 1
    fi

    if [[ ! -d "$PLAYBOOKS_DIR" ]]; then
        print_error "Playbooks directory not found at $PLAYBOOKS_DIR"
        exit 1
    fi

    if [[ ! -f "$SSH_CONFIG" ]]; then
        print_warn "SSH config not found at $SSH_CONFIG"
        read -p "Enter path to SSH config (or press Enter to continue without): " custom_config
        if [[ -n "$custom_config" && -f "$custom_config" ]]; then
            SSH_CONFIG="$custom_config"
        fi
    fi

    print_step "Using SSH config: $SSH_CONFIG"
}

# Run provision playbook
run_provision() {
    local deployment=$1
    local deploy_dir="$SCRIPT_DIR/$deployment"

    print_step "Provisioning VMs for: $deployment"
    echo ""

    cd "$PLAYBOOKS_DIR"

    if [[ -f "$SSH_CONFIG" ]]; then
        ANSIBLE_SSH_ARGS="-F $SSH_CONFIG" ansible-playbook \
            -i "$deploy_dir/hosts.ini" \
            -e "deployment_dir=$deploy_dir" \
            provision-vms.yaml
    else
        ansible-playbook \
            -i "$deploy_dir/hosts.ini" \
            -e "deployment_dir=$deploy_dir" \
            provision-vms.yaml
    fi

    if [[ -f "$deploy_dir/inventory.ini" ]]; then
        print_step "VMs provisioned! Inventory written to: $deploy_dir/inventory.ini"
    fi
}

# Run install playbook (auto-selects behavior-configs-aware variant)
run_install() {
    local deployment=$1
    local deploy_dir="$SCRIPT_DIR/$deployment"

    if [[ ! -f "$deploy_dir/inventory.ini" ]]; then
        print_error "No inventory.ini found. Provision failed or was skipped."
        return 1
    fi

    # Auto-select playbook based on behavior config presence
    local playbook="install-sups.yaml"
    if has_behavior_configs "$deploy_dir"; then
        playbook="install-sups-with-behavior-configs.yaml"
        print_step "Installing SUPs with behavioral configs for: $deployment"
    else
        print_step "Installing SUPs for: $deployment"
    fi
    echo ""

    cd "$PLAYBOOKS_DIR"
    ansible-playbook \
        -i "$deploy_dir/inventory.ini" \
        -e "deployment_dir=$deploy_dir" \
        "$playbook"

    print_step "SUP installation complete!"
}

# Spinup: provision + install (with behavioral configs if present)
run_spinup() {
    local deployment=$1
    local deploy_dir="$SCRIPT_DIR/$deployment"

    if [[ ! -f "$deploy_dir/config.yaml" ]]; then
        print_error "No config.yaml found for deployment: $deployment"
        return 1
    fi

    if [[ ! -f "$deploy_dir/hosts.ini" ]]; then
        print_error "No hosts.ini found for deployment: $deployment"
        return 1
    fi

    run_provision "$deployment"

    echo ""
    print_step "SUP installation will begin in 30 seconds (press 'n' to cancel)..."
    cancelled=false
    for i in {30..1}; do
        printf "\r    Starting in %2d seconds... " "$i"
        read -t 1 -n 1 key 2>/dev/null && {
            if [[ "$key" == "n" || "$key" == "N" ]]; then
                echo ""
                print_warn "SUP installation cancelled. VMs are provisioned but not installed."
                cancelled=true
                break
            fi
        }
    done
    echo ""

    if [[ "$cancelled" == "false" ]]; then
        run_install "$deployment"
    fi
}

# Run teardown playbook
run_teardown() {
    local deployment=$1
    local deploy_dir="$SCRIPT_DIR/$deployment"

    if [[ ! -f "$deploy_dir/config.yaml" ]]; then
        print_error "No config.yaml found for this deployment."
        return 1
    fi

    print_warn "This will DELETE all VMs and volumes for: $deployment"
    read -p "Are you sure? [y/N]: " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        print_step "Teardown cancelled."
        return 0
    fi

    print_step "Tearing down: $deployment"
    echo ""

    cd "$PLAYBOOKS_DIR"

    if [[ -f "$SSH_CONFIG" ]]; then
        ANSIBLE_SSH_ARGS="-F $SSH_CONFIG" ansible-playbook \
            -i "$deploy_dir/hosts.ini" \
            -e "deployment_dir=$deploy_dir" \
            teardown.yaml
    else
        ansible-playbook \
            -i "$deploy_dir/hosts.ini" \
            -e "deployment_dir=$deploy_dir" \
            teardown.yaml
    fi

    print_step "Teardown complete!"
}

# Run teardown-all playbook (delete ALL servers and volumes)
run_teardown_all() {
    # Find any hosts.ini to use for the openstack_controller connection
    local hosts_ini=""
    for dir in "$SCRIPT_DIR"/*/; do
        if [[ -f "${dir}hosts.ini" ]]; then
            hosts_ini="${dir}hosts.ini"
            break
        fi
    done

    if [[ -z "$hosts_ini" ]]; then
        print_error "No hosts.ini found in any deployment. Need at least one to connect to OpenStack."
        return 1
    fi

    print_warn "This will DELETE ALL sup-* servers and volumes!"
    print_warn "This is NOT deployment-specific - it will remove EVERYTHING."
    echo ""
    read -p "Type 'DELETE ALL' to confirm: " confirm
    if [[ "$confirm" != "DELETE ALL" ]]; then
        print_step "Teardown cancelled."
        return 0
    fi

    print_step "Deleting ALL SUP servers and volumes..."
    echo ""

    cd "$PLAYBOOKS_DIR"

    if [[ -f "$SSH_CONFIG" ]]; then
        ANSIBLE_SSH_ARGS="-F $SSH_CONFIG" ansible-playbook \
            -i "$hosts_ini" \
            teardown-all.yaml
    else
        ansible-playbook \
            -i "$hosts_ini" \
            teardown-all.yaml
    fi

    print_step "Teardown complete!"
}

# Select a deployment interactively
select_deployment() {
    local deployments=($(get_deployments))

    echo "" >&2
    list_deployments >&2
    echo "" >&2
    read -p "Select deployment [1-${#deployments[@]}]: " dep_choice

    if [[ $dep_choice -ge 1 && $dep_choice -le ${#deployments[@]} ]] 2>/dev/null; then
        echo "${deployments[$((dep_choice-1))]}"
    else
        print_error "Invalid selection" >&2
        return 1
    fi
}

# Main menu
main_menu() {
    local deployments=($(get_deployments))

    if [[ ${#deployments[@]} -eq 0 ]]; then
        print_error "No deployments found in $SCRIPT_DIR"
        print_error "Each deployment needs a config.yaml file"
        exit 1
    fi

    while true; do
        print_header

        echo "  1) Spinup   (provision VMs + install SUPs)"
        echo "  2) Teardown (delete VMs and volumes)"
        echo "  3) Exit"
        echo ""
        read -p "Select option [1-3]: " choice

        case $choice in
            1)
                deployment=$(select_deployment) || { echo ""; read -p "Press Enter to continue..."; continue; }
                run_spinup "$deployment"
                echo ""
                echo "Deployment complete. Goodbye!"
                exit 0
                ;;
            2)
                echo ""
                echo -e "${BLUE}Teardown options:${NC}"
                list_deployments
                echo "  A) Delete ALL servers and volumes"
                echo ""
                read -p "Select deployment or 'A' for all: " teardown_choice

                if [[ "$teardown_choice" == "A" || "$teardown_choice" == "a" ]]; then
                    run_teardown_all
                    echo "Goodbye!"
                    exit 0
                elif [[ $teardown_choice -ge 1 && $teardown_choice -le ${#deployments[@]} ]] 2>/dev/null; then
                    deployment="${deployments[$((teardown_choice-1))]}"
                    run_teardown "$deployment"
                    echo "Goodbye!"
                    exit 0
                else
                    print_error "Invalid selection"
                fi

                echo ""
                read -p "Press Enter to continue..."
                ;;
            3)
                echo "Goodbye!"
                exit 0
                ;;
            *)
                print_error "Invalid option"
                ;;
        esac
    done
}

# CLI mode for non-interactive use
cli_mode() {
    local action=$1
    local deployment=$2

    case $action in
        spinup)
            run_spinup "$deployment"
            ;;
        teardown)
            run_teardown "$deployment"
            ;;
        teardown-all)
            run_teardown_all
            ;;
        *)
            echo "Usage: $0 [spinup|teardown] <deployment-name>"
            echo "       $0 teardown-all"
            echo "       $0  (interactive mode)"
            exit 1
            ;;
    esac
}

# Entry point
check_prereqs

# Start logging and set up cleanup trap
start_logging "$@"
cleanup_old_logs
trap 'end_logging $?' EXIT

print_step "Logging to: $LOG_FILE"

if [[ $# -ge 1 ]]; then
    if [[ "$1" == "teardown-all" ]]; then
        cli_mode "$1"
    elif [[ $# -ge 2 ]]; then
        cli_mode "$1" "$2"
    else
        echo "Usage: $0 [spinup|teardown] <deployment-name>"
        echo "       $0 teardown-all"
        echo "       $0  (interactive mode)"
        exit 1
    fi
else
    main_menu
fi
