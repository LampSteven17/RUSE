# RUSE Deploy TUI - System Context

Load critical context about the RUSE deployment TUI system before working on it. Read all the files listed below, then summarize the current state for the user.

## Instructions

Read the following files in order to understand the deployment system:

### Core TUI Files
1. `deployments/deploy` - Main bash TUI orchestrator (gum-based interactive menu, CLI commands, run_playbook, do_spinup/do_install/do_teardown)
2. `deployments/lib/monitor.sh` - Event parsing, state tracking, status table rendering, monitoring_loop

### Ansible Integration
3. `deployments/playbooks/callback_plugins/ruse_events.py` - Callback plugin that emits structured JSONL events for the TUI to consume
4. `deployments/playbooks/provision-vms.yaml` - VM provisioning playbook
5. `deployments/playbooks/install-sups.yaml` - SUP installation playbook (stage1 → reboot → stage2)
6. `deployments/playbooks/install-sups-with-feedback.yaml` - Installation with PHASE feedback configs
7. `deployments/playbooks/teardown.yaml` - Per-deployment teardown
8. `deployments/playbooks/teardown-all.yaml` - Global teardown

### Deployment Configs
9. `deployments/exp-4/config.yaml` - Latest experiment config (25 VMs, feedback evaluation)
10. `deployments/test/config.yaml` - Test deployment config (4 VMs)

## Architecture Summary

The deploy system has three layers:

```
┌──────────────────────────────────────────────────┐
│  deploy (bash)                                   │
│  - Interactive gum menu OR CLI subcommands       │
│  - Manages run IDs (MMDD auto-increment)         │
│  - Creates run directories with inventory/SSH    │
│  - Launches ansible-playbook in background       │
├──────────────────────────────────────────────────┤
│  monitor.sh (bash, sourced by deploy)            │
│  - State machine per VM via associative arrays   │
│  - Incremental JSONL event parsing (dd + jq)     │
│  - Status table rendering (printf, no ncurses)   │
│  - 0.5s refresh monitoring_loop                  │
├──────────────────────────────────────────────────┤
│  ruse_events.py (Ansible callback plugin)        │
│  - Intercepts Ansible task results               │
│  - Parses stdout for VM names, IPs, errors       │
│  - Writes JSON events to RUSE_EVENT_FILE         │
│  - Bridge between Ansible and TUI                │
└──────────────────────────────────────────────────┘
```

## Key Concepts

### VM State Machine
```
pending → creating → provisioned → installing → preparing → stage1 → rebooting → stage2 → completed
             ↓           ↓             ↓           ↓           ↓         ↓          ↓
           failed      failed        failed      failed      failed    failed     failed
```

### State Arrays (bash associative arrays in monitor.sh)
- `VM_STATUS` - Current state in the state machine
- `VM_BEHAVIOR` - SUP config (M3, B2.llama, etc.)
- `VM_FLAVOR` / `VM_HW` - OpenStack flavor / hardware type
- `VM_IP` - Assigned IP address
- `VM_ERROR` - Error message (truncated to 60 chars)
- `VM_PROVISION_START/END` - Timing for provisioning phase
- `VM_INSTALL_START/END` - Timing for installation phase
- `VM_FREEZE_TS` - Wall-clock second when steps became terminal

### Event Flow
```
Ansible task completes
  → ruse_events.py intercepts via v2_runner_on_ok/failed
  → Parses task name + stdout for context
  → Writes JSON event to $RUSE_EVENT_FILE (JSONL)
  → monitor.sh reads new bytes via dd + jq (incremental)
  → Updates VM_STATUS arrays
  → Renders status table via printf
```

### Event Types (from callback plugin)
- **Provisioning**: `vm_creating`, `vm_exists`, `vm_active`, `vm_provisioned`, `vm_ip`, `vm_failed`
- **Installation**: `install_preparing`, `install_stage1`, `install_stage2`, `install_feedback`, `reboot_start`, `reboot_complete`, `install_failed`
- **Teardown**: `discovery_servers`, `discovery_volumes`, `resource_deleted`, `resource_failed`
- **Lifecycle**: `playbook_start`, `playbook_end`, `play_start`, `task_start`, `task_ok`, `task_failed`, `recap`

### Status Table Columns
```
SUP  #  HW   │ Prov  SSH  Prep  Deps  Boot  Agent  [Fdbk]  │ Time
```
Step markers: -- (not started), .. (in progress), ok (done), !! (failed)

### CLI Interface
```bash
./deploy                              # Interactive menu
./deploy spinup <deployment>          # Provision + install
./deploy spinup <deployment> --run ID # Explicit run ID
./deploy install <deployment>/<run>   # Install on existing VMs
./deploy teardown <deployment>/<run>  # Teardown specific run
./deploy teardown-all                 # Delete all sup-* VMs
./deploy list                         # List active deployments
./deploy preview <deployment>         # Show config preview
```

### Run ID System
- Auto-generated: `MMDD` (e.g., `0216`)
- Same-day collisions: letter suffixes (`0216a`, `0216b`)
- Directory structure: `deployments/<name>/runs/<run_id>/`
- Contains: `inventory.ini`, `ssh_config_snippet.txt`, `phase_ips_config.py`

### SSH Config Management
- ProxyJump through `axes` control node
- Managed blocks in `~/.ssh/config` with markers:
  ```
  # BEGIN RUSE: <deployment>/<run_id>
  ...
  # END RUSE: <deployment>/<run_id>
  ```

### Config Format (config.yaml)
```yaml
deployment_name: exp-4
flavor_capacity:
  v1.14vcpu.28g: 9
  v100-1gpu.14vcpu.28g: 19
deployments:
  - behavior: B3.gemma
    flavor: v100-1gpu.14vcpu.28g
    count: 1
```

### Terminal Rendering
- Uses raw ANSI escape codes (no ncurses/curses)
- `gum` used only for interactive menus (with stty workaround for onlcr corruption)
- Fixed layout: 16-line RUSE logo + scroll region below
- `\033[K` (clear to EOL) on every line for clean refresh
- Scroll region managed via `\033[18;${LINES}r`

After reading these files, provide a brief summary of the current state and any recent changes visible in the code.
