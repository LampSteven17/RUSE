#!/bin/bash
#
# Interactive SUP Deployment Script
# Runs Ansible playbooks to provision VMs and install SUPs
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$HOME/.ssh/config}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_header() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}  DOLOS-DEPLOY - SUP Deployment Tool${NC}"
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
        if [[ -f "${dir}provision-vms.yaml" ]]; then
            deployment=$(basename "$dir")
            echo "  $i) $deployment"
            ((i++))
        fi
    done
}

# Get deployment directories
get_deployments() {
    local deployments=()
    for dir in "$SCRIPT_DIR"/*/; do
        if [[ -f "${dir}provision-vms.yaml" ]]; then
            deployments+=("$(basename "$dir")")
        fi
    done
    echo "${deployments[@]}"
}

# Check prerequisites
check_prereqs() {
    print_step "Checking prerequisites..."

    if ! command -v ansible-playbook &> /dev/null; then
        print_error "ansible-playbook not found. Please install Ansible."
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

    cd "$deploy_dir"

    if [[ -f "$SSH_CONFIG" ]]; then
        ANSIBLE_SSH_ARGS="-F $SSH_CONFIG" ansible-playbook -i hosts.ini provision-vms.yaml
    else
        ansible-playbook -i hosts.ini provision-vms.yaml
    fi

    if [[ -f "inventory.ini" ]]; then
        print_step "VMs provisioned! Inventory written to: $deploy_dir/inventory.ini"
    fi
}

# Run install playbook
run_install() {
    local deployment=$1
    local deploy_dir="$SCRIPT_DIR/$deployment"

    if [[ ! -f "$deploy_dir/inventory.ini" ]]; then
        print_error "No inventory.ini found. Run provision first."
        return 1
    fi

    print_step "Installing SUPs for: $deployment"
    echo ""

    cd "$deploy_dir"
    ansible-playbook -i inventory.ini install-sups.yaml

    print_step "SUP installation complete!"
}

# Run teardown playbook
run_teardown() {
    local deployment=$1
    local deploy_dir="$SCRIPT_DIR/$deployment"

    if [[ ! -f "$deploy_dir/teardown.yaml" ]]; then
        print_error "No teardown.yaml found for this deployment."
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

    cd "$deploy_dir"

    if [[ -f "$SSH_CONFIG" ]]; then
        ANSIBLE_SSH_ARGS="-F $SSH_CONFIG" ansible-playbook -i hosts.ini teardown.yaml
    else
        ansible-playbook -i hosts.ini teardown.yaml
    fi

    print_step "Teardown complete!"
}

# Show deployment info
show_deployment_info() {
    local deployment=$1
    local deploy_dir="$SCRIPT_DIR/$deployment"

    echo -e "\n${BLUE}Deployment: $deployment${NC}"
    echo "-----------------------------------"

    if [[ -f "$deploy_dir/provision-vms.yaml" ]]; then
        echo -e "${GREEN}Configured SUPs:${NC}"
        grep -A1 "behavior:" "$deploy_dir/provision-vms.yaml" | grep -E "behavior:|flavor:" | head -20
    fi

    if [[ -f "$deploy_dir/inventory.ini" ]]; then
        echo -e "\n${GREEN}Provisioned VMs:${NC}"
        grep -E "^sup-" "$deploy_dir/inventory.ini" 2>/dev/null || echo "  (none yet)"
    else
        echo -e "\n${YELLOW}VMs not yet provisioned${NC}"
    fi
}

# Main menu
main_menu() {
    local deployments=($(get_deployments))

    if [[ ${#deployments[@]} -eq 0 ]]; then
        print_error "No deployments found in $SCRIPT_DIR"
        exit 1
    fi

    while true; do
        print_header

        echo "What would you like to do?"
        echo ""
        echo "  1) Full deploy (provision + install)"
        echo "  2) Provision VMs only (create OpenStack instances)"
        echo "  3) Install SUPs only (on provisioned VMs)"
        echo "  4) Teardown (delete VMs and volumes)"
        echo "  5) Show deployment info"
        echo "  6) Exit"
        echo ""
        read -p "Select option [1-6]: " choice

        case $choice in
            1|2|3|4|5)
                echo ""
                list_deployments
                echo ""
                read -p "Select deployment [1-${#deployments[@]}]: " dep_choice

                if [[ $dep_choice -ge 1 && $dep_choice -le ${#deployments[@]} ]]; then
                    deployment="${deployments[$((dep_choice-1))]}"

                    case $choice in
                        1)
                            run_provision "$deployment"
                            echo ""
                            print_step "SUP installation will begin in 30 seconds (press 'n' to cancel)..."
                            cancelled=false
                            for i in {30..1}; do
                                printf "\r    Starting in %2d seconds... " "$i"
                                read -t 1 -n 1 key 2>/dev/null && {
                                    if [[ "$key" == "n" || "$key" == "N" ]]; then
                                        echo ""
                                        print_warn "SUP installation cancelled."
                                        cancelled=true
                                        break
                                    fi
                                }
                            done
                            echo ""
                            if [[ "$cancelled" == "false" ]]; then
                                run_install "$deployment"
                                echo ""
                                echo "Deployment complete. Goodbye!"
                                exit 0
                            fi
                            ;;
                        2)
                            run_provision "$deployment"
                            ;;
                        3)
                            run_install "$deployment"
                            ;;
                        4)
                            run_teardown "$deployment"
                            echo "Goodbye!"
                            exit 0
                            ;;
                        5)
                            show_deployment_info "$deployment"
                            ;;
                    esac
                else
                    print_error "Invalid selection"
                fi

                echo ""
                read -p "Press Enter to continue..."
                ;;
            6)
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
        provision)
            run_provision "$deployment"
            ;;
        install)
            run_install "$deployment"
            ;;
        deploy)
            run_provision "$deployment"
            run_install "$deployment"
            ;;
        teardown)
            run_teardown "$deployment"
            ;;
        *)
            echo "Usage: $0 [provision|install|deploy|teardown] <deployment-name>"
            echo "       $0  (interactive mode)"
            exit 1
            ;;
    esac
}

# Entry point
check_prereqs

if [[ $# -ge 2 ]]; then
    cli_mode "$1" "$2"
else
    main_menu
fi