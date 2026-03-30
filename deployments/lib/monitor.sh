#!/bin/bash
# monitor.sh — Ansible progress streaming + utilities for RUSE deploy.
#
# Provides:
#   - _run_ansible()           Run a playbook with streaming progress output
#   - _parse_ansible_output()  Filter ansible log into readable terminal output
#   - install_ssh_config()     Manage SSH config blocks
#   - remove_ssh_config()
#   - generate_phase_config()  Register in PHASE experiments.json
#   - format_duration()        Pretty-print seconds
#   - _gum()                   gum wrapper with stty fix
#
# Design: all output scrolls normally. No screen clears, no cursor
# positioning, no full-screen redraws. Works over SSH, with pipes, etc.

# ─────────────────────────────────────────────────────────────────────────────
# gum wrapper (fixes terminal corruption)
# ─────────────────────────────────────────────────────────────────────────────

_gum() {
    command gum "$@"
    local rc=$?
    stty opost onlcr 2>/dev/null || true
    return $rc
}

# ─────────────────────────────────────────────────────────────────────────────
# ANSI helpers (for non-gum output)
# ─────────────────────────────────────────────────────────────────────────────

_bold()  { printf '\033[1;38;5;%dm%s\033[0m\n' "$1" "$2"; }
_color() { printf '\033[38;5;%dm%s\033[0m\n' "$1" "$2"; }
_faint() { printf '\033[2m%s\033[0m\n' "$1"; }
_green() { printf '\033[38;5;78m%s\033[0m\n' "$1"; }
_red()   { printf '\033[38;5;196m%s\033[0m\n' "$1"; }
_yellow(){ printf '\033[38;5;214m%s\033[0m\n' "$1"; }
_dim()   { printf '\033[2m%s\033[0m\n' "$1"; }

_box() {
    local color="$1"; shift
    local max_len=0 line
    for line in "$@"; do (( ${#line} > max_len )) && max_len=${#line}; done
    local w=$((max_len + 4)) rule=""
    printf -v rule '═%.0s' $(seq 1 "$w")
    printf '\033[38;5;%dm╔%s╗\033[0m\n' "$color" "$rule"
    for line in "$@"; do
        printf '\033[38;5;%dm║\033[0m  %-*s  \033[38;5;%dm║\033[0m\n' "$color" "$max_len" "$line" "$color"
    done
    printf '\033[38;5;%dm╚%s╝\033[0m\n' "$color" "$rule"
}

# ─────────────────────────────────────────────────────────────────────────────
# Time formatting
# ─────────────────────────────────────────────────────────────────────────────

format_duration() {
    local secs="${1%%.*}"
    [[ -z "$secs" ]] && secs=0
    if (( secs >= 3600 )); then
        printf '%dh%02dm%02ds' $((secs/3600)) $(((secs%3600)/60)) $((secs%60))
    elif (( secs >= 60 )); then
        printf '%dm%02ds' $((secs/60)) $((secs%60))
    else
        printf '%ds' "$secs"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Ansible progress streaming
# ─────────────────────────────────────────────────────────────────────────────

_run_ansible() {
    # Run an ansible playbook with live streaming progress.
    # Output scrolls normally — no screen clearing or cursor tricks.
    #
    # Usage: _run_ansible <playbook> <inventory> [extra_args...]
    #   extra_args are passed directly to ansible-playbook (e.g., -e key=value)
    #
    # Requires: ANSIBLE_LOG set by caller, PLAYBOOKS_DIR set by deploy script.
    local playbook="$1"; shift
    local inventory="$1"; shift

    local playbooks_dir="${PLAYBOOKS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../playbooks" && pwd)}"

    local ssh_args=""
    [[ -f "${SSH_CONFIG:-$HOME/.ssh/config}" ]] && ssh_args="-F ${SSH_CONFIG:-$HOME/.ssh/config}"

    local start_time
    start_time=$(date +%s)

    # Run ansible in background, all output to log file
    env \
        ANSIBLE_FORCE_COLOR=0 \
        ANSIBLE_NOCOLOR=1 \
        ANSIBLE_STDOUT_CALLBACK=default \
        ${ssh_args:+ANSIBLE_SSH_ARGS="$ssh_args"} \
        ansible-playbook \
        -i "$inventory" \
        "$@" \
        "$playbooks_dir/$playbook" \
        < /dev/null >> "$ANSIBLE_LOG" 2>&1 &
    local pid=$!
    _ANSIBLE_PID="$pid"

    # Stream parsed output to terminal
    # --pid makes tail exit when the process dies (GNU coreutils)
    tail -f "$ANSIBLE_LOG" --pid="$pid" 2>/dev/null | _parse_ansible_output

    wait "$pid" 2>/dev/null || true
    local rc=$?
    _ANSIBLE_PID=""

    local elapsed=$(( $(date +%s) - start_time ))
    echo ""
    if (( rc == 0 )); then
        _green "  Done ($(format_duration "$elapsed"))"
    else
        _red "  Failed (exit $rc, $(format_duration "$elapsed"))"
        _dim "  Log: $ANSIBLE_LOG"
    fi

    return $rc
}

_parse_ansible_output() {
    # Filter ansible log into readable streaming output.
    # Reads from stdin (piped from tail -f).
    local current_task=""

    while IFS= read -r line; do
        [[ -z "$line" || "$line" =~ ^[[:space:]]*$ ]] && continue

        if [[ "$line" == TASK\ \[* ]]; then
            local task="${line#TASK \[}"
            task="${task%%\]*}"
            # Skip noisy tasks
            case "$task" in
                Display*|Print*|Read*|Gathering*|Track*|Check*SSH*|SSH*Config*) continue ;;
            esac
            current_task="$task"
            _dim "  > $task"

        elif [[ "$line" == PLAY\ \[* ]]; then
            local play="${line#PLAY \[}"
            play="${play%%\]*}"
            [[ "$play" == "Deployment Summary" ]] && continue
            echo ""
            _bold 39 "[$play]"

        elif [[ "$line" == PLAY\ RECAP* ]]; then
            continue

        elif [[ "$line" == changed:* ]]; then
            local host_info="${line#changed: }"
            local host="${host_info#\[}"
            host="${host%%\]*}"
            host="${host% -> localhost}"
            # Extract loop item name when host is control node
            if [[ "$host_info" == *"(item="* && "$host" != r-* && "$host" != e-* && "$host" != sup-* ]]; then
                local item="${host_info#*\(item=}"
                item="${item%%\)*}"
                item="${item%% (*}"
                host="$item"
            fi
            _green "    ✓ $host"

        elif [[ "$line" == fatal:* || "$line" == *UNREACHABLE* ]]; then
            _red "    ✗ ${line:0:120}"

        elif [[ "$line" == "ASYNC OK on "* ]]; then
            local host="${line#ASYNC OK on }"
            host="${host%%:*}"
            _green "    ✓ $host (async)"

        elif [[ "$line" == "ASYNC POLL"* || "$line" == "ASYNC FAILED"* ]]; then
            continue  # noisy during install

        elif [[ "$line" == *"FAILED - RETRYING:"* ]]; then
            local msg="${line#*FAILED - RETRYING: }"
            _yellow "    ↻ ${msg:0:80}"

        elif [[ "$line" == *"RUSE_RETRY:"* ]]; then
            local msg="${line#*RUSE_RETRY: }"
            _yellow "    ↻ ${msg:0:80}"

        # Skip: ok: lines, JSON output, recap summary lines, etc.
        fi
    done
}

# ─────────────────────────────────────────────────────────────────────────────
# SSH config management
# ─────────────────────────────────────────────────────────────────────────────

install_ssh_config() {
    local snippet_file="$1" deploy_name="$2"
    local ssh_config="${SSH_CONFIG:-$HOME/.ssh/config}"
    local marker_begin="# BEGIN RUSE: ${deploy_name}"
    local marker_end="# END RUSE: ${deploy_name}"

    [[ -f "$snippet_file" ]] || return 0
    mkdir -p "$(dirname "$ssh_config")"

    local snippet
    snippet=$(sed -E '/^#{5,}/d; /^# Add this to your/d' "$snippet_file")

    local block
    block=$(printf '%s\n%s\n%s\n' "$marker_begin" "$snippet" "$marker_end")

    if [[ -f "$ssh_config" ]]; then
        local tmp="${ssh_config}.ruse.tmp"
        awk -v begin="$marker_begin" -v end="$marker_end" '
            $0 == begin { skip=1; next }
            $0 == end   { skip=0; next }
            !skip
        ' "$ssh_config" > "$tmp"
        sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$tmp"
        printf '\n\n%s\n' "$block" >> "$tmp"
        mv "$tmp" "$ssh_config"
    else
        printf '%s\n' "$block" > "$ssh_config"
    fi
    chmod 600 "$ssh_config"

    echo ""
    _green "  SSH config installed to $ssh_config"
    _dim "  Hosts: $(grep -c '^Host ' <<< "$snippet" || echo 0) entries"
}

remove_ssh_config() {
    local deploy_name="$1"
    local ssh_config="${SSH_CONFIG:-$HOME/.ssh/config}"
    local marker_begin="# BEGIN RUSE: ${deploy_name}"
    local marker_end="# END RUSE: ${deploy_name}"

    [[ -f "$ssh_config" ]] || return 0
    grep -qF "$marker_begin" "$ssh_config" || return 0

    local tmp="${ssh_config}.ruse.tmp"
    awk -v begin="$marker_begin" -v end="$marker_end" '
        $0 == begin { skip=1; next }
        $0 == end   { skip=0; next }
        !skip
    ' "$ssh_config" > "$tmp"
    sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$tmp"
    mv "$tmp" "$ssh_config"
    chmod 600 "$ssh_config"

    _dim "  Removed SSH config for $deploy_name"
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE experiment registration
# ─────────────────────────────────────────────────────────────────────────────

generate_phase_config() {
    local snippet_file="$1" deploy_name="$2" run_id="${3:-}"
    [[ -f "$snippet_file" ]] || return 0

    local run_dir script_dir
    run_dir="$(dirname "$snippet_file")"
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    local run_id_args=()
    [[ -n "$run_id" ]] && run_id_args=(--run-id "$run_id")

    python3 "$script_dir/register_experiment.py" \
        --name "$deploy_name" \
        --snippet "$snippet_file" \
        --inventory "$run_dir/inventory.ini" \
        "${run_id_args[@]}" \
        2>/dev/null && _dim "  Registered in PHASE experiments.json" || true
}
