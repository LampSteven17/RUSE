#!/bin/bash
# monitor.sh - Event parsing and status table rendering for RUSE deployments
#
# Sourced by the main deploy script. Provides:
#   - State tracking via bash associative arrays
#   - JSONL event parsing via jq
#   - Status table rendering via printf
#   - monitoring_loop() for live playbook monitoring
#
# State machine per VM:
#   pending → creating → provisioned → installing → preparing → stage1 → rebooting → stage2 → completed
#                ↓           ↓             ↓           ↓           ↓         ↓          ↓
#              failed      failed        failed      failed      failed    failed     failed
#
# Table columns:
#   Prov  = VM created and active in OpenStack
#   SSH   = SSH connectivity confirmed via ProxyJump
#   Prep  = cloud-init + apt + clone repo
#   Deps  = System deps + GPU/CUDA drivers (INSTALL_SUP.sh --stage=1)
#   Boot  = NVIDIA driver reload
#   Agent = Ollama + Python + SUP service (INSTALL_SUP.sh --stage=2)
#
# Step markers:
#   --  not started
#   ..  in progress
#   ok  completed
#   !!  failed

# ─────────────────────────────────────────────────────────────────────────────
# ANSI text helpers (replaces gum style which corrupts terminal onlcr)
# ─────────────────────────────────────────────────────────────────────────────

_bold()       { printf '\033[1;38;5;%dm%s\033[0m\n' "$1" "$2"; }
_color()      { printf '\033[38;5;%dm%s\033[0m\n' "$1" "$2"; }
_faint()      { printf '\033[2m%s\033[0m\n' "$1"; }
_faint_bold() { printf '\033[1;2m%s\033[0m\n' "$1"; }

_box() {
    # Draw a double-bordered box: _box <color> "line1" "line2" ...
    local color="$1"; shift
    local max_len=0
    local line
    for line in "$@"; do
        (( ${#line} > max_len )) && max_len=${#line}
    done
    local w=$((max_len + 4))
    local rule=""
    printf -v rule '═%.0s' $(seq 1 "$w")
    printf '\033[38;5;%dm╔%s╗\033[0m\n' "$color" "$rule"
    for line in "$@"; do
        printf '\033[38;5;%dm║\033[0m  %-*s  \033[38;5;%dm║\033[0m\n' "$color" "$max_len" "$line" "$color"
    done
    printf '\033[38;5;%dm╚%s╝\033[0m\n' "$color" "$rule"
}

_print_table() {
    # Clean aligned table (batch-training style).
    # Widths: comma-separated column widths. Use "|" for a │ group separator.
    # First row = dim header, remaining rows = normal text. No borders.
    local widths_str="$1"; shift
    local -a specs
    IFS=',' read -ra specs <<< "$widths_str"

    local first=true
    local row
    for row in "$@"; do
        local -a cells
        IFS=',' read -ra cells <<< "$row"
        local out="" ci=0 si
        for ((si=0; si<${#specs[@]}; si++)); do
            if [[ "${specs[$si]}" == "|" ]]; then
                out+=" │ "
            else
                local cfmt
                printf -v cfmt "%-${specs[$si]}s " "${cells[$ci]:-}"
                out+="$cfmt"
                ci=$((ci + 1))
            fi
        done
        if $first; then
            printf '\033[2m %s\033[0m\n' "$out"
            first=false
        else
            printf ' %s\n' "$out"
        fi
    done
}

_gum_table() {
    # Wrapper: capture gum table output, restore terminal stty, then print.
    # gum corrupts the terminal onlcr setting; capturing in $() prevents the
    # corrupted output from reaching the terminal, and stty restores after.
    local out
    out=$(gum table "$@") || true
    stty opost onlcr 2>/dev/null || true
    printf '%s\n' "$out"
}

_gum_table_k() {
    # Like _gum_table but appends \033[K (clear to EOL) on each line.
    # Used for in-place table refreshes in the monitoring loop.
    local out
    out=$(gum table "$@") || true
    stty opost onlcr 2>/dev/null || true
    while IFS= read -r line; do
        printf '%s\033[K\n' "$line"
    done <<< "$out"
}

_step_fmt() {
    # Set _SF to a colored, padded step marker (avoids subshell overhead).
    # Usage: _step_fmt "ok" 7; echo "$_SF"
    local s="$1" w="$2" padded
    printf -v padded "%-${w}s" "$s"
    case "$s" in
        ok) _SF=$'\033[38;5;46m'"$padded"$'\033[0m' ;;
        ..) _SF=$'\033[1;38;5;214m'"$padded"$'\033[0m' ;;
        !!) _SF=$'\033[38;5;196m'"$padded"$'\033[0m' ;;
        *)  _SF=$'\033[2m'"$padded"$'\033[0m' ;;
    esac
}

_print_table_k() {
    # Like _print_table but appends \033[K (clear to EOL) on each line.
    # Used for in-place table refresh in the monitoring loop.
    local widths_str="$1"; shift
    local -a specs
    IFS=',' read -ra specs <<< "$widths_str"

    local first=true
    local row
    for row in "$@"; do
        local -a cells
        IFS=',' read -ra cells <<< "$row"
        local out="" ci=0 si
        for ((si=0; si<${#specs[@]}; si++)); do
            if [[ "${specs[$si]}" == "|" ]]; then
                out+=" │ "
            else
                local cfmt
                printf -v cfmt "%-${specs[$si]}s " "${cells[$ci]:-}"
                out+="$cfmt"
                ci=$((ci + 1))
            fi
        done
        if $first; then
            printf '\033[2m %s\033[K\033[0m\n' "$out"
            first=false
        else
            printf ' %s\033[K\n' "$out"
        fi
    done
}

_hrule() {
    # Print a dim full-width horizontal rule with \033[K
    printf '\033[2m────────────────────────────────────────────────────────────────────────────────────────────────\033[K\033[0m\n'
}

# ─────────────────────────────────────────────────────────────────────────────
# State arrays (populated by init_vm_state / process_new_events)
# ─────────────────────────────────────────────────────────────────────────────

declare -gA VM_STATUS           # pending|creating|provisioned|installing|stage1|rebooting|stage2|completed|failed
declare -gA VM_BEHAVIOR         # M3, B2.llama, etc.
declare -gA VM_FLAVOR           # OpenStack flavor
declare -gA VM_HW               # CPU|V100|RTX|RTX-A
declare -gA VM_IP               # IP address
declare -gA VM_ERROR            # error message (truncated)
declare -gA VM_PROVISION_START  # unix timestamp
declare -gA VM_PROVISION_END
declare -gA VM_INSTALL_START
declare -gA VM_INSTALL_END
declare -gA VM_FREEZE_TS            # wall-clock second when all steps became terminal

# Teardown state
declare -gA RESOURCE_NAME       # resource display name
declare -gA RESOURCE_TYPE       # server|volume
declare -gA RESOURCE_STATUS     # pending|deleting|deleted|failed

# Ordered list of VM names for consistent display
declare -ga VM_ORDER=()

# Current deployment phase
MONITOR_PHASE="idle"

# Whether current deployment includes feedback distribution
SHOW_FEEDBACK=false
TEARDOWN_SSH_CLEANED=false

# File position for incremental reads
_EVENT_FILE_POS=0
_LOG_PARSE_POS=0

# Flavor short names
declare -gA FLAVOR_SHORT=(
    ["v100-1gpu.14vcpu.28g"]="V100"
    ["rtx2080ti-A-1gpu.14vcpu.28g"]="RTX-A"
    ["rtx2080ti-1gpu.14vcpu.28g"]="RTX"
    ["v1.14vcpu.28g"]="CPU"
)

# ─────────────────────────────────────────────────────────────────────────────
# Screen layout: header stays at top, content refreshes below
# ─────────────────────────────────────────────────────────────────────────────

# Logo = 16 lines + 1 blank = content starts at line 18
CONTENT_START_LINE=18

clear_content_area() {
    printf '\033[%d;1H\033[J' "$CONTENT_START_LINE"
}

# ─────────────────────────────────────────────────────────────────────────────
# VM sorting
# ─────────────────────────────────────────────────────────────────────────────

_vm_sort_key() {
    local name="$1"
    local stripped="${name#sup-}"
    # If there's a dep_id prefix, skip it: "exp3-M1-0" -> "M1-0"
    # The dep_id is always lowercase before the behavior letter [CMBS]
    if [[ "$stripped" =~ ^[a-z0-9]+-([CMBS].*)$ ]]; then
        stripped="${BASH_REMATCH[1]}"
    fi
    local instance="${stripped##*-}"
    local behavior="${stripped%-*}"

    local cat="" version="" variant=""
    case "$behavior" in
        BC*) cat=3; version="${behavior#BC}"; version="${version%%[a-z]*}" ;;
        SC*) cat=5; version="${behavior#SC}"; version="${version%%[a-z]*}" ;;
        C*)  cat=0; version="${behavior#C}";  version="${version%%[a-z]*}" ;;
        M*)  cat=1; version="${behavior#M}";  version="${version%%[a-z]*}" ;;
        B*)  cat=2; version="${behavior#B}";  version="${version%%[a-z]*}" ;;
        S*)  cat=4; version="${behavior#S}";  version="${version%%[a-z]*}" ;;
        *)   cat=9; version=0 ;;
    esac

    variant="${behavior#*[0-9]}"
    # Strip non-numeric chars from version (e.g. "1-llama" -> "1")
    version="${version%%[^0-9]*}"
    [[ -z "$version" ]] && version=0

    printf '%d.%03d.%s.%03d' "$cat" "$version" "$variant" "$instance"
}

sort_vm_order() {
    local -a pairs=()
    local vm
    for vm in "${VM_ORDER[@]}"; do
        pairs+=("$(_vm_sort_key "$vm") $vm")
    done

    VM_ORDER=()
    while IFS= read -r line; do
        VM_ORDER+=("${line#* }")
    done < <(printf '%s\n' "${pairs[@]}" | sort)
}

# ─────────────────────────────────────────────────────────────────────────────
# Config loading
# ─────────────────────────────────────────────────────────────────────────────

init_vm_state() {
    local config="$1"
    local dep_id="${2:-}"

    VM_STATUS=(); VM_BEHAVIOR=(); VM_FLAVOR=(); VM_HW=(); VM_IP=()
    VM_ERROR=(); VM_PROVISION_START=(); VM_PROVISION_END=()
    VM_INSTALL_START=(); VM_INSTALL_END=(); VM_FREEZE_TS=(); VM_ORDER=()
    RESOURCE_NAME=(); RESOURCE_TYPE=(); RESOURCE_STATUS=()
    _EVENT_FILE_POS=0

    while IFS=$'\t' read -r vm_name behavior flavor; do
        VM_STATUS["$vm_name"]="pending"
        VM_BEHAVIOR["$vm_name"]="$behavior"
        VM_FLAVOR["$vm_name"]="$flavor"
        VM_HW["$vm_name"]="${FLAVOR_SHORT[$flavor]:-${flavor:0:6}}"
        VM_IP["$vm_name"]=""
        VM_ERROR["$vm_name"]=""
        VM_PROVISION_START["$vm_name"]=""
        VM_PROVISION_END["$vm_name"]=""
        VM_INSTALL_START["$vm_name"]=""
        VM_INSTALL_END["$vm_name"]=""
        VM_ORDER+=("$vm_name")
    done < <(python3 -c "
import yaml, sys
dep_id = '$dep_id'
with open('$config') as f:
    cfg = yaml.safe_load(f)
prefix = f'sup-{dep_id}-' if dep_id else 'sup-'
counts = {}
for dep in cfg.get('deployments', []):
    behavior = dep['behavior']
    flavor = dep['flavor']
    for _ in range(dep.get('count', 1)):
        idx = counts.get(behavior, 0)
        counts[behavior] = idx + 1
        vm = prefix + behavior.replace('.', '-') + '-' + str(idx)
        print(f'{vm}\t{behavior}\t{flavor}')
")

    sort_vm_order
}

# ─────────────────────────────────────────────────────────────────────────────
# Event processing
# ─────────────────────────────────────────────────────────────────────────────

process_new_events() {
    local event_file="$1"

    [[ -f "$event_file" ]] || return 0

    local file_size
    file_size=$(stat -c%s "$event_file" 2>/dev/null) || return 0
    (( file_size <= _EVENT_FILE_POS )) && return 0

    # IMPORTANT: Use SOH (\x01) as delimiter instead of tab. Bash's `read`
    # collapses consecutive tabs (tab is IFS-whitespace), which breaks parsing
    # when fields are empty. SOH is non-whitespace so consecutive SOH delimiters
    # correctly produce empty fields.
    while IFS=$'\x01' read -r etype vm_name host ip error ignored playbook stage unix_ts servers_json volumes_json rtype rname; do
        case "$etype" in
            playbook_start)
                case "$playbook" in
                    *provision*)
                        MONITOR_PHASE="provisioning"
                        local vm
                        for vm in "${VM_ORDER[@]}"; do
                            if [[ "${VM_STATUS[$vm]}" == "pending" ]]; then
                                VM_STATUS["$vm"]="creating"
                            fi
                            if [[ -z "${VM_PROVISION_START[$vm]:-}" ]]; then
                                VM_PROVISION_START["$vm"]="$unix_ts"
                            fi
                        done
                        ;;
                    *install*)
                        MONITOR_PHASE="installing"
                        local vm
                        for vm in "${VM_ORDER[@]}"; do
                            if [[ "${VM_STATUS[$vm]}" == "provisioned" || "${VM_STATUS[$vm]}" == "pending" ]]; then
                                VM_STATUS["$vm"]="installing"
                                VM_INSTALL_START["$vm"]="$unix_ts"
                            fi
                        done
                        ;;
                    *teardown*)
                        MONITOR_PHASE="teardown"
                        ;;
                esac
                ;;

            vm_creating|vm_exists)
                if [[ -n "$vm_name" && -n "${VM_STATUS[$vm_name]+x}" ]]; then
                    VM_STATUS["$vm_name"]="creating"
                    if [[ -z "${VM_PROVISION_START[$vm_name]:-}" ]]; then
                        VM_PROVISION_START["$vm_name"]="$unix_ts"
                    fi
                fi
                ;;

            vm_provisioned|vm_active)
                local target="${vm_name:-$host}"
                if [[ -n "$target" && -n "${VM_STATUS[$target]+x}" ]]; then
                    VM_STATUS["$target"]="provisioned"
                    VM_PROVISION_END["$target"]="$unix_ts"
                fi
                ;;

            vm_ip)
                local target="${vm_name:-$host}"
                if [[ -n "$target" && -n "$ip" && "$ip" != "false" && "$ip" != "null" && -n "${VM_STATUS[$target]+x}" ]]; then
                    VM_IP["$target"]="$ip"
                fi
                ;;

            vm_failed)
                if [[ -n "$vm_name" && -n "${VM_STATUS[$vm_name]+x}" ]]; then
                    VM_STATUS["$vm_name"]="failed"
                    VM_ERROR["$vm_name"]="${error:0:60}"
                fi
                ;;

            install_preparing)
                if [[ -n "$host" && -n "${VM_STATUS[$host]+x}" ]]; then
                    case "${VM_STATUS[$host]}" in
                        installing) VM_STATUS["$host"]="preparing" ;;
                    esac
                else
                    local vm
                    for vm in "${VM_ORDER[@]}"; do
                        [[ "${VM_STATUS[$vm]}" == "installing" ]] && VM_STATUS["$vm"]="preparing"
                    done
                fi
                ;;

            install_stage1)
                if [[ -n "$host" && -n "${VM_STATUS[$host]+x}" ]]; then
                    case "${VM_STATUS[$host]}" in
                        installing|preparing) VM_STATUS["$host"]="stage1" ;;
                    esac
                else
                    local vm
                    for vm in "${VM_ORDER[@]}"; do
                        case "${VM_STATUS[$vm]}" in
                            installing|preparing) VM_STATUS["$vm"]="stage1" ;;
                        esac
                    done
                fi
                ;;

            install_stage2)
                if [[ -n "$host" && -n "${VM_STATUS[$host]+x}" ]]; then
                    case "${VM_STATUS[$host]}" in
                        stage1|rebooting|installing|preparing) VM_STATUS["$host"]="stage2" ;;
                    esac
                else
                    local vm
                    for vm in "${VM_ORDER[@]}"; do
                        case "${VM_STATUS[$vm]}" in
                            stage1|rebooting|installing|preparing) VM_STATUS["$vm"]="stage2" ;;
                        esac
                    done
                fi
                ;;

            install_complete)
                if [[ -n "$host" && -n "${VM_STATUS[$host]+x}" ]]; then
                    case "${VM_STATUS[$host]}" in
                        failed|completed) ;;
                        *) VM_STATUS["$host"]="completed"; VM_INSTALL_END["$host"]="$unix_ts" ;;
                    esac
                fi
                ;;

            install_feedback)
                # Feedback play started — move completed non-control VMs to feedback state
                if [[ -n "$host" && -n "${VM_STATUS[$host]+x}" ]]; then
                    case "${VM_BEHAVIOR[$host]}" in
                        C0|M0) ;; # controls skip feedback
                        *) [[ "${VM_STATUS[$host]}" == "completed" ]] && VM_STATUS["$host"]="feedback" ;;
                    esac
                else
                    local vm
                    for vm in "${VM_ORDER[@]}"; do
                        case "${VM_BEHAVIOR[$vm]}" in
                            C0|M0) continue ;;
                        esac
                        [[ "${VM_STATUS[$vm]}" == "completed" ]] && VM_STATUS["$vm"]="feedback"
                    done
                fi
                ;;

            reboot_start)
                if [[ -n "$host" && -n "${VM_STATUS[$host]+x}" ]]; then
                    [[ "${VM_STATUS[$host]}" == "stage1" ]] && VM_STATUS["$host"]="rebooting"
                else
                    local vm
                    for vm in "${VM_ORDER[@]}"; do
                        [[ "${VM_STATUS[$vm]}" == "stage1" ]] && VM_STATUS["$vm"]="rebooting"
                    done
                fi
                ;;

            reboot_complete)
                if [[ -n "$host" && -n "${VM_STATUS[$host]+x}" ]]; then
                    if [[ "${VM_STATUS[$host]}" == "rebooting" ]]; then
                        VM_STATUS["$host"]="stage1"
                    fi
                fi
                ;;

            install_failed)
                if [[ -n "$host" && -n "${VM_STATUS[$host]+x}" ]]; then
                    VM_STATUS["$host"]="failed"
                    VM_ERROR["$host"]="Stage ${stage}: ${error:0:50}"
                fi
                ;;

            task_failed)
                if [[ "$ignored" == "true" ]]; then
                    : # skip
                elif [[ -n "$host" && -n "${VM_STATUS[$host]+x}" ]]; then
                    VM_STATUS["$host"]="failed"
                    VM_ERROR["$host"]="${error:0:60}"
                fi
                ;;

            host_unreachable)
                if [[ -n "$host" && -n "${VM_STATUS[$host]+x}" ]]; then
                    VM_STATUS["$host"]="failed"
                    VM_ERROR["$host"]="Unreachable: ${error:0:50}"
                fi
                ;;

            recap)
                local failures="${error%%|*}"
                local unreachable="${error##*|}"
                if [[ -n "$host" && -n "${VM_STATUS[$host]+x}" ]]; then
                    if (( ${failures:-0} > 0 || ${unreachable:-0} > 0 )) 2>/dev/null; then
                        if [[ "${VM_STATUS[$host]}" != "failed" ]]; then
                            VM_STATUS["$host"]="failed"
                            if [[ -z "${VM_ERROR[$host]:-}" ]]; then
                                VM_ERROR["$host"]="Recap: ${failures} failures"
                            fi
                        fi
                    else
                        if [[ "$MONITOR_PHASE" == "installing" ]]; then
                            case "${VM_STATUS[$host]}" in
                                failed|completed) ;;
                                *) VM_STATUS["$host"]="completed"; VM_INSTALL_END["$host"]="$unix_ts" ;;
                            esac
                        elif [[ "$MONITOR_PHASE" == "provisioning" ]]; then
                            case "${VM_STATUS[$host]}" in
                                failed|provisioned|completed) ;;
                                *) VM_STATUS["$host"]="provisioned"; VM_PROVISION_END["$host"]="$unix_ts" ;;
                            esac
                        fi
                    fi
                fi
                ;;

            discovery_servers)
                if [[ -n "$servers_json" && "$servers_json" != "null" ]]; then
                    while IFS=$'\t' read -r sid sname; do
                        if [[ -n "$sid" && -z "${RESOURCE_STATUS[$sid]+x}" ]]; then
                            RESOURCE_NAME["$sid"]="$sname"
                            RESOURCE_TYPE["$sid"]="server"
                            RESOURCE_STATUS["$sid"]="pending"
                        fi
                        # Populate VM state from discovered servers (teardown-all mode)
                        if [[ -n "$sname" && -z "${VM_STATUS[$sname]+x}" ]]; then
                            local _beh="" _idx=""
                            if [[ "$sname" =~ -([BS][0-9]-(llama|gemma|deepseek))-([0-9]+)$ ]]; then
                                _beh="${BASH_REMATCH[1]}"; _beh="${_beh/-/.}"; _idx="${BASH_REMATCH[3]}"
                            elif [[ "$sname" =~ -([CM][0-9]+)-([0-9]+)$ ]]; then
                                _beh="${BASH_REMATCH[1]}"; _idx="${BASH_REMATCH[2]}"
                            fi
                            if [[ -n "$_beh" ]]; then
                                VM_STATUS["$sname"]="pending"
                                VM_BEHAVIOR["$sname"]="$_beh"
                                case "$_beh" in [BS]*) VM_HW["$sname"]="GPU" ;; *) VM_HW["$sname"]="CPU" ;; esac
                                VM_ORDER+=("$sname")
                            fi
                        fi
                    done < <(echo "$servers_json" | jq -r '.[] | [.id, .name] | @tsv' 2>/dev/null)
                    # Sort VM_ORDER if we just populated it
                    (( ${#VM_ORDER[@]} > 0 )) && sort_vm_order
                fi
                ;;

            discovery_volumes)
                if [[ -n "$volumes_json" && "$volumes_json" != "null" ]]; then
                    while IFS=$'\t' read -r vid; do
                        if [[ -n "$vid" && -z "${RESOURCE_STATUS[$vid]+x}" && -z "${RESOURCE_TYPE[$vid]+x}" ]]; then
                            RESOURCE_NAME["$vid"]="${vid:0:8}"
                            RESOURCE_TYPE["$vid"]="volume"
                            RESOURCE_STATUS["$vid"]="pending"
                        fi
                    done < <(echo "$volumes_json" | jq -r '.[]' 2>/dev/null)
                fi
                ;;

            resource_deleted)
                if [[ "$rtype" == "server" ]]; then
                    local rid found=false
                    for rid in "${!RESOURCE_STATUS[@]}"; do
                        if [[ "${RESOURCE_NAME[$rid]}" == "$rname" || "$rid" == "$rname"* ]]; then
                            RESOURCE_STATUS["$rid"]="deleted"; found=true; break
                        fi
                    done
                    # Fallback: create resource entry if discovery event was missed
                    if [[ "$found" == "false" && -n "$rname" ]]; then
                        RESOURCE_NAME["$rname"]="$rname"
                        RESOURCE_TYPE["$rname"]="server"
                        RESOURCE_STATUS["$rname"]="deleted"
                    fi
                elif [[ "$rtype" == "volume" ]]; then
                    local rid found=false
                    for rid in "${!RESOURCE_STATUS[@]}"; do
                        if [[ "$rid" == "$rname"* || "${RESOURCE_NAME[$rid]}" == "$rname" ]]; then
                            RESOURCE_STATUS["$rid"]="deleted"; found=true; break
                        fi
                    done
                    if [[ "$found" == "false" && -n "$rname" ]]; then
                        RESOURCE_NAME["$rname"]="$rname"
                        RESOURCE_TYPE["$rname"]="volume"
                        RESOURCE_STATUS["$rname"]="deleted"
                    fi
                fi
                ;;

            resource_failed)
                if [[ "$ignored" != "true" ]]; then
                    local rid
                    for rid in "${!RESOURCE_STATUS[@]}"; do
                        if [[ "${RESOURCE_NAME[$rid]}" == "$rname" || "$rid" == "$rname"* ]]; then
                            RESOURCE_STATUS["$rid"]="failed"; break
                        fi
                    done
                fi
                ;;
        esac
    done < <(dd if="$event_file" bs=1 skip="$_EVENT_FILE_POS" 2>/dev/null | jq -r '
        [
            .type // "",
            .data.vm_name // "",
            .data.host // "",
            .data.ip // "",
            (if .type == "recap" then
                ((.data.failures // 0 | tostring) + "|" + (.data.unreachable // 0 | tostring))
            else
                .data.error // ""
            end),
            (.data.ignored // false | tostring),
            .data.playbook // "",
            .data.stage // "",
            (.unix_ts // 0 | tostring),
            (.data.servers // "null" | tostring),
            (.data.volumes // "null" | tostring),
            .data.type // "",
            .data.name // ""
        ] | join("\u0001")
    ' 2>/dev/null)

    _EVENT_FILE_POS="$file_size"
}

_parse_log_teardown() {
    # Fallback for teardown-all: parse ansible log directly for deletion progress.
    # Used when VM_ORDER is empty (no config-based state) and callback events
    # may not be reaching the event file. Tracks server/volume deletions from
    # the default callback's item output lines.
    local log_file="$1"
    [[ -f "$log_file" ]] || return 0

    local file_size
    file_size=$(stat -c%s "$log_file" 2>/dev/null) || return 0
    (( file_size <= _LOG_PARSE_POS )) && return 0

    # Regex patterns stored in variables (bash =~ can't handle inline [^)])
    local _re_sup='item=(sup-[^)]+)'
    local _re_vol='item=([a-f0-9-]{36})'

    local current_task=""
    while IFS= read -r line; do
        # Track current task
        if [[ "$line" == TASK\ \[* ]]; then
            current_task="${line#TASK \[}"
            current_task="${current_task%%\]*}"
            current_task="${current_task,,}"  # lowercase
            continue
        fi

        # Extract server names from loop item results: changed: [axes] => (item=sup-name)
        if [[ "$line" =~ $_re_sup ]]; then
            local name="${BASH_REMATCH[1]}"
            # Register resource if new
            if [[ -z "${RESOURCE_STATUS[$name]+x}" ]]; then
                RESOURCE_NAME["$name"]="$name"
                RESOURCE_TYPE["$name"]="server"
                RESOURCE_STATUS["$name"]="pending"
            fi
            # Mark deleted when confirmed by the "wait for deletion" task
            if [[ "$current_task" == *"wait for"*"delete"* ]]; then
                if [[ "$line" == changed:* || "$line" == ok:* ]]; then
                    RESOURCE_STATUS["$name"]="deleted"
                fi
            fi
        fi

        # Extract volume UUIDs from loop item results: changed: [axes] => (item=uuid)
        if [[ "$current_task" == *volume* ]] && [[ "$line" =~ $_re_vol ]]; then
            local vid="${BASH_REMATCH[1]}"
            if [[ -z "${RESOURCE_STATUS[$vid]+x}" ]]; then
                RESOURCE_NAME["$vid"]="${vid:0:8}"
                RESOURCE_TYPE["$vid"]="volume"
                RESOURCE_STATUS["$vid"]="pending"
            fi
            if [[ "$current_task" == *"wait for"*"delete"* ]]; then
                if [[ "$line" == changed:* || "$line" == ok:* ]]; then
                    RESOURCE_STATUS["$vid"]="deleted"
                fi
            fi
        fi
    done < <(tail -c +"$((_LOG_PARSE_POS + 1))" "$log_file" 2>/dev/null)

    _LOG_PARSE_POS="$file_size"
}

# ─────────────────────────────────────────────────────────────────────────────
# Status counting
# ─────────────────────────────────────────────────────────────────────────────

get_vm_counts() {
    COUNT_TOTAL=${#VM_ORDER[@]}
    COUNT_PENDING=0; COUNT_CREATING=0; COUNT_PROVISIONED=0
    COUNT_INSTALLING=0; COUNT_COMPLETED=0; COUNT_FAILED=0

    local vm
    for vm in "${VM_ORDER[@]}"; do
        case "${VM_STATUS[$vm]}" in
            pending)     COUNT_PENDING=$((COUNT_PENDING + 1)) ;;
            creating)    COUNT_CREATING=$((COUNT_CREATING + 1)) ;;
            provisioned) COUNT_PROVISIONED=$((COUNT_PROVISIONED + 1)) ;;
            installing|preparing|stage1|rebooting|stage2|feedback) COUNT_INSTALLING=$((COUNT_INSTALLING + 1)) ;;
            completed)   COUNT_COMPLETED=$((COUNT_COMPLETED + 1)) ;;
            failed)      COUNT_FAILED=$((COUNT_FAILED + 1)) ;;
        esac
    done
}

get_resource_counts() {
    RCOUNT_SERVERS_TOTAL=0; RCOUNT_SERVERS_DELETED=0; RCOUNT_SERVERS_FAILED=0
    RCOUNT_VOLUMES_TOTAL=0; RCOUNT_VOLUMES_DELETED=0; RCOUNT_VOLUMES_FAILED=0

    local rid
    for rid in "${!RESOURCE_STATUS[@]}"; do
        if [[ "${RESOURCE_TYPE[$rid]}" == "server" ]]; then
            RCOUNT_SERVERS_TOTAL=$((RCOUNT_SERVERS_TOTAL + 1))
            [[ "${RESOURCE_STATUS[$rid]}" == "deleted" ]] && RCOUNT_SERVERS_DELETED=$((RCOUNT_SERVERS_DELETED + 1))
            [[ "${RESOURCE_STATUS[$rid]}" == "failed" ]]  && RCOUNT_SERVERS_FAILED=$((RCOUNT_SERVERS_FAILED + 1))
        elif [[ "${RESOURCE_TYPE[$rid]}" == "volume" ]]; then
            RCOUNT_VOLUMES_TOTAL=$((RCOUNT_VOLUMES_TOTAL + 1))
            [[ "${RESOURCE_STATUS[$rid]}" == "deleted" ]] && RCOUNT_VOLUMES_DELETED=$((RCOUNT_VOLUMES_DELETED + 1))
            [[ "${RESOURCE_STATUS[$rid]}" == "failed" ]]  && RCOUNT_VOLUMES_FAILED=$((RCOUNT_VOLUMES_FAILED + 1))
        fi
    done
}

# ─────────────────────────────────────────────────────────────────────────────
# Time formatting
# ─────────────────────────────────────────────────────────────────────────────

format_timestamp() {
    local ts="${1:-}"
    [[ -z "$ts" || "$ts" == "0" ]] && return 0
    local int_ts="${ts%%.*}"
    date -d "@$int_ts" '+%H:%M:%S' 2>/dev/null
}

format_duration() {
    local secs="${1%%.*}"
    [[ -z "$secs" ]] && secs=0
    printf '%02d:%02d:%02d' $((secs/3600)) $(((secs%3600)/60)) $((secs%60))
}

# ─────────────────────────────────────────────────────────────────────────────
# Step marker derivation
# ─────────────────────────────────────────────────────────────────────────────

# Derive per-step markers from VM status.
# Steps:
#   Prov  = VM created and active in OpenStack
#   SSH   = SSH connectivity confirmed via ProxyJump
#   Prep  = cloud-init + apt + clone repo
#   Deps  = System deps + GPU/CUDA drivers (INSTALL_SUP.sh --stage=1)
#   Boot  = NVIDIA driver reload
#   Agent = Ollama + Python + SUP service (INSTALL_SUP.sh --stage=2)
#   Fdbk  = PHASE feedback config distribution (feedback deployments only)
#
# Sets: _S_PROVISION _S_SSH _S_PREP _S_DRIVERS _S_REBOOT _S_AGENT _S_FEEDBACK
_derive_steps() {
    local status="$1"
    local behavior="${2:-}"

    # Defaults
    _S_PROVISION="--"; _S_SSH="--"; _S_PREP="--"; _S_DRIVERS="--"; _S_REBOOT="--"; _S_AGENT="--"; _S_FEEDBACK="--"

    case "$status" in
        pending)
            ;;
        creating)
            _S_PROVISION=".."
            ;;
        provisioned)
            _S_PROVISION="ok"; _S_SSH="ok"
            ;;
        installing)
            _S_PROVISION="ok"; _S_SSH="ok"; _S_PREP=".."
            ;;
        preparing)
            _S_PROVISION="ok"; _S_SSH="ok"; _S_PREP=".."
            ;;
        stage1)
            _S_PROVISION="ok"; _S_SSH="ok"; _S_PREP="ok"; _S_DRIVERS=".."
            ;;
        rebooting)
            _S_PROVISION="ok"; _S_SSH="ok"; _S_PREP="ok"; _S_DRIVERS="ok"; _S_REBOOT=".."
            ;;
        stage2)
            _S_PROVISION="ok"; _S_SSH="ok"; _S_PREP="ok"; _S_DRIVERS="ok"; _S_REBOOT="ok"; _S_AGENT=".."
            ;;
        feedback)
            _S_PROVISION="ok"; _S_SSH="ok"; _S_PREP="ok"; _S_DRIVERS="ok"; _S_REBOOT="ok"; _S_AGENT="ok"; _S_FEEDBACK=".."
            ;;
        completed)
            _S_PROVISION="ok"; _S_SSH="ok"; _S_PREP="ok"; _S_DRIVERS="ok"; _S_REBOOT="ok"; _S_AGENT="ok"; _S_FEEDBACK="ok"
            ;;
        failed)
            # Figure out which step failed based on what completed
            _S_PROVISION="ok"
            # If never provisioned, failed during create
            if [[ -z "${_STEP_PROV_END:-}" ]]; then
                _S_PROVISION="!!"; return
            fi
            _S_SSH="ok"
            # If never started install, failed after provisioning
            if [[ -z "${_STEP_INST_START:-}" ]]; then
                return
            fi
            _S_PREP="ok"
            # Failed during install - check last known sub-status from error
            local err="${_STEP_ERROR:-}"
            if [[ "$err" == Stage\ 2* ]]; then
                _S_DRIVERS="ok"; _S_REBOOT="ok"; _S_AGENT="!!"
            elif [[ "$err" == *eboot* ]]; then
                _S_DRIVERS="ok"; _S_REBOOT="!!"
            else
                _S_DRIVERS="!!"
            fi
            ;;
    esac

    # C0 (bare Ubuntu control) has no install steps
    if [[ "$behavior" == "C0" ]]; then
        _S_PREP="skip"; _S_DRIVERS="skip"; _S_REBOOT="skip"; _S_AGENT="skip"; _S_FEEDBACK="skip"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Table rendering (pure printf — no gum)
# ─────────────────────────────────────────────────────────────────────────────

render_status_table() {
    # Batch-training-style status table with colored step markers,
    # progress bar, and status tags. Fixed-height output for scroll regions.
    local elapsed="$1"
    local now
    now=$(date +%s)

    get_vm_counts

    # Phase info
    local done_count=0 phase_label="" _install_pct=-1
    case "$MONITOR_PHASE" in
        provisioning)
            done_count=$((COUNT_PROVISIONED + COUNT_INSTALLING + COUNT_COMPLETED))
            phase_label="Provisioning" ;;
        installing)
            done_count=$COUNT_COMPLETED
            phase_label="Installing"
            # Step-weighted progress for the bar: each VM has 5 install steps
            # (SSH, Prep, Deps, Boot, Agent). Bar fills as VMs advance through
            # stages, rather than staying at 0% until the first VM fully completes.
            local _sw=0 _st=$((COUNT_TOTAL * 5)) _vm
            for _vm in "${VM_ORDER[@]}"; do
                case "${VM_BEHAVIOR[$_vm]}" in
                    C0) _sw=$((_sw + 5)) ;;  # no install steps — count as done
                    *)
                        case "${VM_STATUS[$_vm]}" in
                            installing|preparing) _sw=$((_sw + 1)) ;;
                            stage1)              _sw=$((_sw + 2)) ;;
                            rebooting)           _sw=$((_sw + 3)) ;;
                            stage2)              _sw=$((_sw + 4)) ;;
                            completed|feedback)  _sw=$((_sw + 5)) ;;
                        esac ;;
                esac
            done
            (( _st > 0 )) && _install_pct=$((_sw * 100 / _st))
            ;;
        *)
            phase_label="Starting" ;;
    esac

    local pct=0
    if (( _install_pct >= 0 )); then
        pct=$_install_pct
    else
        (( COUNT_TOTAL > 0 )) && pct=$((done_count * 100 / COUNT_TOTAL))
    fi

    local lines_printed=0

    # Column widths
    local W_SUP=12 W_IDX=3 W_HW=6 W_STEP=6 W_TIME=7

    # Determine how many VM rows fit on screen.
    # Layout: header(18) + table_header(1) + VMs + separator(1) + progress(1) + tags(1) + errors(3) + log_separator(1) + min_log(5) = 31 + VMs
    local term_height
    term_height=$(tput lines 2>/dev/null) || term_height=50
    local overhead=$((CONTENT_START_LINE + 13))  # header + separator + progress + tags + errors + log separator + min log lines
    local max_vm_rows=$((term_height - overhead))
    (( max_vm_rows < 5 )) && max_vm_rows=5
    local total_vms=${#VM_ORDER[@]}
    local truncated=false
    local hidden_count=0

    # Build display list: if too many VMs, prioritize active/failed, then show rest
    local display_vms=()
    if (( total_vms <= max_vm_rows )); then
        display_vms=("${VM_ORDER[@]}")
    else
        truncated=true
        # Active/failed VMs first (in-progress or errored)
        local active_vms=() done_vms=()
        for vm in "${VM_ORDER[@]}"; do
            case "${VM_STATUS[$vm]}" in
                completed) done_vms+=("$vm") ;;
                *) active_vms+=("$vm") ;;
            esac
        done
        # Fill with active first, then done
        for vm in "${active_vms[@]}"; do
            if (( ${#display_vms[@]} < max_vm_rows - 1 )); then  # -1 for "N more" line
                display_vms+=("$vm")
            fi
        done
        for vm in "${done_vms[@]}"; do
            if (( ${#display_vms[@]} < max_vm_rows - 1 )); then
                display_vms+=("$vm")
            fi
        done
        hidden_count=$((total_vms - ${#display_vms[@]}))
    fi

    # Header (dim)
    if [[ "$SHOW_FEEDBACK" == "true" ]]; then
        printf '\033[2m %-*s %-*s %-*s %-*s %-*s %-*s %-*s %-*s %-*s %-*s %*s\033[K\033[0m\n' \
            $W_SUP "SUP" $W_IDX "#" $W_HW "HW" \
            $W_STEP "Prov" $W_STEP "SSH" $W_STEP "Prep" $W_STEP "Deps" $W_STEP "Boot" $W_STEP "Agent" $W_STEP "Fdbk" $W_TIME "Time"
    else
        printf '\033[2m %-*s %-*s %-*s %-*s %-*s %-*s %-*s %-*s %-*s %*s\033[K\033[0m\n' \
            $W_SUP "SUP" $W_IDX "#" $W_HW "HW" \
            $W_STEP "Prov" $W_STEP "SSH" $W_STEP "Prep" $W_STEP "Deps" $W_STEP "Boot" $W_STEP "Agent" $W_TIME "Time"
    fi
    lines_printed=$((lines_printed + 1))

    # Data rows with colored step markers
    local vm
    for vm in "${display_vms[@]}"; do
        local behavior="${VM_BEHAVIOR[$vm]}"
        local idx="${vm##*-}"
        local hw="${VM_HW[$vm]}" status="${VM_STATUS[$vm]}"

        _STEP_PROV_END="${VM_PROVISION_END[$vm]:-}"
        _STEP_INST_START="${VM_INSTALL_START[$vm]:-}"
        _STEP_ERROR="${VM_ERROR[$vm]:-}"
        _derive_steps "$status" "$behavior"

        # Per-VM elapsed time (use install start during install phase)
        local vm_time=""
        local vm_start="${VM_PROVISION_START[$vm]:-}"
        if [[ "$MONITOR_PHASE" == "installing" && -n "${VM_INSTALL_START[$vm]:-}" ]]; then
            vm_start="${VM_INSTALL_START[$vm]}"
        fi
        if [[ -n "$vm_start" && "$vm_start" != "0" ]]; then
            local end_ts="$now"

            # Freeze timer when all visible steps are terminal (ok/skip/!!)
            local _all_done=true
            local _step
            for _step in "$_S_PROVISION" "$_S_SSH" "$_S_PREP" "$_S_DRIVERS" "$_S_REBOOT" "$_S_AGENT"; do
                case "$_step" in ok|skip|"!!") ;; *) _all_done=false; break ;; esac
            done
            if [[ "$SHOW_FEEDBACK" == "true" && "$_all_done" == "true" ]]; then
                case "$_S_FEEDBACK" in ok|skip|"!!") ;; *) _all_done=false ;; esac
            fi
            # Failed VMs always freeze (remaining "--" steps won't run)
            [[ "$status" == "failed" ]] && _all_done=true

            if [[ "$_all_done" == "true" ]]; then
                # First render where all steps are terminal → record freeze time
                if [[ -z "${VM_FREEZE_TS[$vm]:-}" ]]; then
                    VM_FREEZE_TS["$vm"]="$now"
                fi
                end_ts="${VM_FREEZE_TS[$vm]}"
            fi
            local vm_secs=$(( end_ts - ${vm_start%%.*} ))
            (( vm_secs < 0 )) && vm_secs=0
            if (( vm_secs >= 3600 )); then
                printf -v vm_time '%dh%02dm' $((vm_secs/3600)) $(((vm_secs%3600)/60))
            elif (( vm_secs >= 60 )); then
                printf -v vm_time '%dm%02ds' $((vm_secs/60)) $((vm_secs%60))
            else
                printf -v vm_time '%ds' "$vm_secs"
            fi
        fi

        # Build colored step markers (no subshell — sets _SF variable)
        _step_fmt "$_S_PROVISION" $W_STEP; local sf_provision="$_SF"
        _step_fmt "$_S_SSH" $W_STEP;       local sf_ssh="$_SF"
        _step_fmt "$_S_PREP" $W_STEP;      local sf_prep="$_SF"
        _step_fmt "$_S_DRIVERS" $W_STEP;   local sf_drivers="$_SF"
        _step_fmt "$_S_REBOOT" $W_STEP;    local sf_reboot="$_SF"
        _step_fmt "$_S_AGENT" $W_STEP;     local sf_agent="$_SF"

        if [[ "$SHOW_FEEDBACK" == "true" ]]; then
            _step_fmt "$_S_FEEDBACK" $W_STEP; local sf_feedback="$_SF"
            printf ' %-*s %-*s %-*s %s %s %s %s %s %s %s %*s\033[K\n' \
                $W_SUP "$behavior" $W_IDX "$idx" $W_HW "$hw" \
                "$sf_provision" "$sf_ssh" "$sf_prep" "$sf_drivers" "$sf_reboot" "$sf_agent" "$sf_feedback" \
                $W_TIME "$vm_time"
        else
            printf ' %-*s %-*s %-*s %s %s %s %s %s %s %*s\033[K\n' \
                $W_SUP "$behavior" $W_IDX "$idx" $W_HW "$hw" \
                "$sf_provision" "$sf_ssh" "$sf_prep" "$sf_drivers" "$sf_reboot" "$sf_agent" \
                $W_TIME "$vm_time"
        fi
        lines_printed=$((lines_printed + 1))
    done

    # Truncation notice
    if [[ "$truncated" == "true" && hidden_count -gt 0 ]]; then
        printf '\033[2m  ... %d more (completed)\033[K\033[0m\n' "$hidden_count"
        lines_printed=$((lines_printed + 1))
    fi

    # Separator
    _hrule
    lines_printed=$((lines_printed + 1))

    # Progress bar
    local bar_len=30
    local filled=$((bar_len * pct / 100))
    local empty=$((bar_len - filled))
    local bar=""
    local i
    for ((i=0; i<filled; i++)); do bar+="█"; done
    for ((i=0; i<empty; i++)); do bar+="░"; done

    if (( _install_pct >= 0 )); then
        if (( done_count > 0 )); then
            printf '\033[1m %s  [%s]  %d%%  %d/%d done   Elapsed: %s\033[K\033[0m\n' \
                "$phase_label" "$bar" "$pct" "$done_count" "$COUNT_TOTAL" "$(format_duration "$elapsed")"
        else
            printf '\033[1m %s  [%s]  %d%%   Elapsed: %s\033[K\033[0m\n' \
                "$phase_label" "$bar" "$pct" "$(format_duration "$elapsed")"
        fi
    else
        printf '\033[1m %s  [%s]  %d/%d   Elapsed: %s\033[K\033[0m\n' \
            "$phase_label" "$bar" "$done_count" "$COUNT_TOTAL" "$(format_duration "$elapsed")"
    fi
    lines_printed=$((lines_printed + 1))

    # Errors (max 3 lines)
    local error_count=0
    for vm in "${VM_ORDER[@]}"; do
        if [[ "${VM_STATUS[$vm]}" == "failed" && -n "${VM_ERROR[$vm]:-}" ]]; then
            printf '\033[38;5;196m  FAIL %s: %s\033[K\033[0m\n' "${VM_BEHAVIOR[$vm]}#${vm##*-}" "${VM_ERROR[$vm]}"
            lines_printed=$((lines_printed + 1))
            error_count=$((error_count + 1))
            (( error_count >= 3 )) && break
        fi
    done

    # Pad to fixed height so table area stays stable between refreshes
    local display_count=${#display_vms[@]}
    [[ "$truncated" == "true" ]] && display_count=$((display_count + 1))
    local target_lines=$((display_count + 5))
    while (( lines_printed < target_lines )); do
        printf '\033[K\n'
        lines_printed=$((lines_printed + 1))
    done
}

render_teardown_table() {
    # Batch-training-style teardown table with colored status markers.
    local elapsed="$1"
    local lines_printed=0

    if (( ${#VM_ORDER[@]} > 0 )); then
        # Header (dim)
        local W_SUP=12 W_IDX=3 W_HW=6 W_ST=8

        # Determine how many VM rows fit on screen
        local term_height
        term_height=$(tput lines 2>/dev/null) || term_height=50
        local overhead=$((CONTENT_START_LINE + 10))
        local max_vm_rows=$((term_height - overhead))
        (( max_vm_rows < 5 )) && max_vm_rows=5
        local total_vms=${#VM_ORDER[@]}
        local truncated=false
        local hidden_count=0

        local display_vms=()
        if (( total_vms <= max_vm_rows )); then
            display_vms=("${VM_ORDER[@]}")
        else
            truncated=true
            # Prioritize VMs not yet deleted
            local active_vms=() done_vms=()
            for vm in "${VM_ORDER[@]}"; do
                local is_deleted=false
                for rid in "${!RESOURCE_STATUS[@]}"; do
                    if [[ "${RESOURCE_NAME[$rid]}" == "$vm" && "${RESOURCE_STATUS[$rid]}" == "deleted" ]]; then
                        is_deleted=true; break
                    fi
                done
                if [[ "$is_deleted" == "true" ]]; then
                    done_vms+=("$vm")
                else
                    active_vms+=("$vm")
                fi
            done
            for vm in "${active_vms[@]}"; do
                if (( ${#display_vms[@]} < max_vm_rows - 1 )); then
                    display_vms+=("$vm")
                fi
            done
            for vm in "${done_vms[@]}"; do
                if (( ${#display_vms[@]} < max_vm_rows - 1 )); then
                    display_vms+=("$vm")
                fi
            done
            hidden_count=$((total_vms - ${#display_vms[@]}))
        fi

        printf '\033[2m %-*s %-*s %-*s %-*s %-*s %-*s\033[K\033[0m\n' \
            $W_SUP "SUP" $W_IDX "#" $W_HW "HW" $W_ST "Server" $W_ST "Vols" $W_ST "SSH"
        lines_printed=$((lines_printed + 1))

        # SSH status is deployment-wide (same for all VMs)
        local ssh_st="--"
        [[ "$TEARDOWN_SSH_CLEANED" == "true" ]] && ssh_st="ok"

        local vm deleted=0
        for vm in "${display_vms[@]}"; do
            local behavior="${VM_BEHAVIOR[$vm]}"
            local idx="${vm##*-}"
            local hw="${VM_HW[$vm]}" srv_st=".." vol_st="--"
            local rid
            for rid in "${!RESOURCE_STATUS[@]}"; do
                if [[ "${RESOURCE_NAME[$rid]}" == "$vm" && "${RESOURCE_TYPE[$rid]}" == "server" ]]; then
                    case "${RESOURCE_STATUS[$rid]}" in
                        deleted) srv_st="ok"; deleted=$((deleted + 1)) ;; failed) srv_st="!!" ;; *) srv_st=".." ;;
                    esac
                fi
                # Volume status: match volumes associated via ID patterns
                if [[ "${RESOURCE_TYPE[$rid]}" == "volume" ]]; then
                    case "${RESOURCE_STATUS[$rid]}" in
                        deleted) vol_st="ok" ;; failed) vol_st="!!" ;;
                        pending|deleting) [[ "$vol_st" == "--" ]] && vol_st=".." ;;
                    esac
                fi
            done

            _step_fmt "$srv_st" $W_ST; local srv_colored="$_SF"
            _step_fmt "$vol_st" $W_ST; local vol_colored="$_SF"
            _step_fmt "$ssh_st" $W_ST; local ssh_colored="$_SF"
            printf ' %-*s %-*s %-*s %s %s %s\033[K\n' \
                $W_SUP "$behavior" $W_IDX "$idx" $W_HW "$hw" "$srv_colored" "$vol_colored" "$ssh_colored"
            lines_printed=$((lines_printed + 1))
        done

        # Count deleted across ALL VMs (not just displayed)
        deleted=0
        for vm in "${VM_ORDER[@]}"; do
            for rid in "${!RESOURCE_STATUS[@]}"; do
                if [[ "${RESOURCE_NAME[$rid]}" == "$vm" && "${RESOURCE_TYPE[$rid]}" == "server" && "${RESOURCE_STATUS[$rid]}" == "deleted" ]]; then
                    deleted=$((deleted + 1)); break
                fi
            done
        done

        # Truncation notice
        if [[ "$truncated" == "true" && hidden_count -gt 0 ]]; then
            printf '\033[2m  ... %d more (deleted)\033[K\033[0m\n' "$hidden_count"
            lines_printed=$((lines_printed + 1))
        fi

        # Separator
        _hrule
        lines_printed=$((lines_printed + 1))

        # Progress
        local total=${#VM_ORDER[@]}
        local pct=0
        (( total > 0 )) && pct=$((deleted * 100 / total))

        local bar_len=30
        local filled=$((bar_len * pct / 100))
        local empty=$((bar_len - filled))
        local bar=""
        local i
        for ((i=0; i<filled; i++)); do bar+="█"; done
        for ((i=0; i<empty; i++)); do bar+="░"; done

        printf '\033[1;38;5;196m Teardown  [%s]  %d/%d   Elapsed: %s\033[K\033[0m\n' \
            "$bar" "$deleted" "$total" "$(format_duration "$elapsed")"
        lines_printed=$((lines_printed + 1))

        # Pad to fixed height
        local display_count=${#display_vms[@]}
        [[ "$truncated" == "true" ]] && display_count=$((display_count + 1))
        local target_lines=$((display_count + 5))
        while (( lines_printed < target_lines )); do
            printf '\033[K\n'
            lines_printed=$((lines_printed + 1))
        done
        return
    fi

    # Fallback: resource-based (teardown-all)
    get_resource_counts
    local total=$((RCOUNT_SERVERS_TOTAL + RCOUNT_VOLUMES_TOTAL))
    local deleted=$((RCOUNT_SERVERS_DELETED + RCOUNT_VOLUMES_DELETED))
    local pct=0
    (( total > 0 )) && pct=$((deleted * 100 / total))

    # Header (dim)
    printf '\033[2m %-28s %-8s %-8s\033[K\033[0m\n' "Resource" "Type" "Status"

    if (( total > 0 )); then
        local rid
        for rid in "${!RESOURCE_STATUS[@]}"; do
            local st=""
            case "${RESOURCE_STATUS[$rid]}" in
                pending)  st="--" ;; deleting) st=".." ;;
                deleted)  st="ok" ;; failed)   st="!!" ;;
            esac
            _step_fmt "$st" 8; local st_colored="$_SF"
            printf ' %-28s %-8s %s\033[K\n' "${RESOURCE_NAME[$rid]}" "${RESOURCE_TYPE[$rid]}" "$st_colored"
        done
    fi

    # Separator + progress
    _hrule

    local bar_len=30
    local filled=$((bar_len * pct / 100))
    local empty=$((bar_len - filled))
    local bar=""
    local i
    for ((i=0; i<filled; i++)); do bar+="█"; done
    for ((i=0; i<empty; i++)); do bar+="░"; done

    printf '\033[1;38;5;196m Teardown  [%s]  %d/%d deleted   Elapsed: %s\033[K\033[0m\n' \
        "$bar" "$deleted" "$total" "$(format_duration "$elapsed")"

    if (( RCOUNT_SERVERS_TOTAL > 0 )); then
        printf '\033[2m Servers: %d/%d  Volumes: %d/%d\033[K\033[0m\n' \
            "$RCOUNT_SERVERS_DELETED" "$RCOUNT_SERVERS_TOTAL" "$RCOUNT_VOLUMES_DELETED" "$RCOUNT_VOLUMES_TOTAL"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Live monitoring loop (scroll regions: table fixed at top, log scrolls below)
# ─────────────────────────────────────────────────────────────────────────────

monitoring_loop() {
    # Full-redraw monitoring: every 0.5s, jump to CONTENT_START_LINE and
    # redraw the table + last N log lines. No scroll regions, no cursor
    # save/restore — just clear and repaint like the batch training TUI.
    local ansible_pid="$1"
    local event_file="$2"
    local render_fn="${3:-render_status_table}"
    local ansible_log="${4:-}"
    local start_time
    start_time=$(date +%s)

    clear_content_area
    _EVENT_FILE_POS=0  # reset for new event file (critical between provision→install)
    _LOG_PARSE_POS=0   # reset for log-based teardown fallback
    printf '\033[?25l'  # hide cursor to prevent flicker during refresh

    # Wait for event file (no gum — pure bash to avoid terminal corruption)
    if ! [[ -f "$event_file" ]]; then
        printf '\033[%d;1H\033[2m  Waiting for ansible to start...\033[0m' "$CONTENT_START_LINE"
        local wait_count=0
        while ! [[ -f "$event_file" ]] && (( wait_count < 100 )); do
            sleep 0.1
            wait_count=$((wait_count + 1))
        done
    fi

    # Layout variables (recalculated each cycle to handle dynamic VM_ORDER growth)
    local term_height vm_count overhead max_vm_rows displayed_vms table_lines log_area_start log_lines_to_show
    term_height=$(tput lines 2>/dev/null) || term_height=50

    # Main loop: render everything each cycle (no scroll regions)
    local running=true
    while $running; do
        # Recalculate layout each cycle (VM_ORDER may grow from discovery events)
        vm_count=${#VM_ORDER[@]}
        (( vm_count < 1 )) && vm_count=1
        overhead=$((CONTENT_START_LINE + 13))
        max_vm_rows=$((term_height - overhead))
        (( max_vm_rows < 5 )) && max_vm_rows=5
        displayed_vms=$vm_count
        (( displayed_vms > max_vm_rows )) && displayed_vms=$((max_vm_rows))
        table_lines=$((displayed_vms + 5))
        log_area_start=$((CONTENT_START_LINE + table_lines + 1))
        log_lines_to_show=$((term_height - log_area_start))
        (( log_lines_to_show < 5 )) && log_lines_to_show=5
        # Defensive: restore terminal output processing in case a backgrounded
        # process (ansible pause module) cleared OPOST/ONLCR via setraw().
        stty opost onlcr 2>/dev/null || true

        process_new_events "$event_file"

        # Fallback for teardown-all: parse ansible log when callback events unavailable
        if [[ "$MONITOR_PHASE" == "teardown" && ${#VM_ORDER[@]} -eq 0 && -n "$ansible_log" ]]; then
            _parse_log_teardown "$ansible_log"
        fi

        local now elapsed
        now=$(date +%s)
        elapsed=$((now - start_time))

        # Jump to content area and redraw everything
        printf '\033[%d;1H' "$CONTENT_START_LINE"
        $render_fn "$elapsed"
        printf '\033[2m──── %s ── q to quit ────────────────────────────────────────────\033[K\033[0m\n' \
            "$(basename "${ansible_log:-}")"
        _render_log_tail "$ansible_log" "$log_lines_to_show"
        printf '\033[J'  # erase any leftover content from previous taller renders

        # Check if ansible is still running
        if ! kill -0 "$ansible_pid" 2>/dev/null; then
            running=false
            continue
        fi

        # Wait 0.5s, check for 'q'
        local key=""
        read -rsn1 -t 0.5 key 2>/dev/null || true
        if [[ "$key" == "q" || "$key" == "Q" ]]; then
            printf '\033[?25h'
            printf '\033[%d;1H\033[J\n' "$term_height"
            # Kill ansible and return — let caller handle cleanup
            kill "$ansible_pid" 2>/dev/null
            wait "$ansible_pid" 2>/dev/null || true
            return 130
        fi
    done

    # Final render
    sleep 0.3
    process_new_events "$event_file"
    if [[ "$MONITOR_PHASE" == "teardown" && ${#VM_ORDER[@]} -eq 0 && -n "$ansible_log" ]]; then
        _parse_log_teardown "$ansible_log"
    fi
    local now elapsed
    now=$(date +%s)
    elapsed=$((now - start_time))
    printf '\033[%d;1H' "$CONTENT_START_LINE"
    $render_fn "$elapsed"
    _hrule
    printf '\033[J'  # clear all stale log output below

    printf '\033[?25h'  # show cursor
    printf '\n'
    _bold 46 "  Playbook complete."
    printf '\n'
    _faint "  Log: ${ansible_log:-}"
    printf '\n'
}

_render_log_tail() {
    # Show last N lines of the ansible log, color-coded.
    # With strategy:free, every VM triggers its own TASK header — so we:
    #   1. Buffer TASK headers and only show them when followed by changed: lines
    #   2. Deduplicate consecutive same-task headers
    #   3. Show changed: [host] lines (actual work completed) with ✓
    #   4. Skip ok: [host] lines (idempotent checks, not interesting)
    # Each line ends with \033[K; finishes with \033[J to clear below.
    local log_file="${1:-}"
    local max_lines="${2:-15}"

    if [[ -z "$log_file" || ! -f "$log_file" ]]; then
        printf '\033[2m  Waiting for output...\033[K\033[0m\n'
        printf '\033[J'
        return 0
    fi

    # Two-pass approach: collect matching lines into an array, then render last N.
    # This avoids the problem of consuming max_lines on old task headers when
    # the recent activity is at the bottom.
    local -a output_lines=()
    local pending_task="" last_shown_task="" _retry_shown=0

    while IFS= read -r line; do
        [[ -z "$line" || "$line" =~ ^[[:space:]]*$ ]] && continue

        if [[ "$line" == TASK\ \[* ]]; then
            local task="${line#TASK \[}"
            task="${task%%\]*}"
            # Skip noisy informational/tracking tasks
            case "$task" in
                Display*|Print*|Read*|Check*for*SSH*|SSH*Config*|Gathering*|Track*) continue ;;
            esac
            # Buffer — only emit when followed by a changed: line
            pending_task="$task"

        elif [[ "$line" == PLAY\ \[* ]]; then
            local play="${line#PLAY \[}"
            play="${play%%\]*}"
            [[ "$play" == "Deployment Summary" ]] && continue
            pending_task=""
            last_shown_task=""
            output_lines+=("$(printf '\033[1;38;5;245m  >> %s\033[K\033[0m' "$play")")

        elif [[ "$line" == PLAY\ RECAP* ]]; then
            continue

        elif [[ "$line" == *"RUSE_RETRY:"* ]]; then
            if (( _retry_shown < 3 )); then
                local retry_rest="${line#*RUSE_RETRY: }"
                output_lines+=("$(printf '\033[38;5;214m  ↻ %s\033[K\033[0m' "${retry_rest:0:110}")")
                _retry_shown=$((_retry_shown + 1))
            fi

        elif [[ "$line" == *"FAILED - RETRYING:"* ]]; then
            if (( _retry_shown < 3 )); then
                local retry_msg="${line#*FAILED - RETRYING: }"
                output_lines+=("$(printf '\033[38;5;214m  ↻ %s\033[K\033[0m' "${retry_msg:0:90}")")
                _retry_shown=$((_retry_shown + 1))
            fi

        elif [[ "$line" == "ASYNC POLL"* || "$line" == "ASYNC FAILED"* ]]; then
            # POLL: extremely noisy during install
            # FAILED: async timeout before retry — not a real error (fatal: shows real errors)
            continue

        elif [[ "$line" == "ASYNC OK on "* ]]; then
            # Async stage completed — extract VM name
            local async_host="${line#ASYNC OK on }"
            async_host="${async_host%%:*}"
            output_lines+=("$(printf '\033[38;5;78m    ✓ %s (async done)\033[K\033[0m' "$async_host")")

        elif [[ "$line" == fatal:* || "$line" == *FAILED* || "$line" == *UNREACHABLE* ]]; then
            output_lines+=("$(printf '\033[38;5;196m  %s\033[K\033[0m' "${line:0:120}")")

        elif [[ "$line" =~ ^[a-zA-Z0-9_.-]+[[:space:]]*:[[:space:]]+(ok|changed|unreachable|failed)= ]]; then
            # PLAY RECAP summary line — already reflected in status table
            continue

        elif [[ "$line" == changed:* ]]; then
            # Actual work completed on a host — this is the interesting stuff
            # Formats: "changed: [sup-M2-0]", "changed: [axes] => (item=sup-name)",
            #          "changed: [axes -> localhost] => (item=sup-name (10.x.x.x))"
            local host_info="${line#changed: }"
            # Extract the bracketed host
            local host="${host_info#\[}"
            host="${host%%\]*}"
            # Strip " -> localhost" delegation suffix
            host="${host% -> localhost}"
            # Check for loop item — only replace host with item when host is
            # a control node (axes) and the item is a VM name (sup-*).
            # When host is already a VM (sup-*), keep it (item may be a filename).
            if [[ "$host_info" == *"(item="* && "$host" != sup-* ]]; then
                local item="${host_info#*\(item=}"
                item="${item%%\)*}"
                # Strip trailing IP like " (10.246.115.64)"
                item="${item%% (*}"
                host="$item"
            fi
            # Emit buffered task header if new (deduplicate)
            if [[ -n "$pending_task" && "$pending_task" != "$last_shown_task" ]]; then
                output_lines+=("$(printf '\033[1;38;5;39m  > %s\033[K\033[0m' "$pending_task")")
                last_shown_task="$pending_task"
            fi
            output_lines+=("$(printf '\033[38;5;78m    ✓ %s\033[K\033[0m' "$host")")

        else
            # Skip everything else: ok: lines, JSON, "host | SUCCESS => {", etc.
            continue
        fi
    # Read a large window: ASYNC POLL lines during install are extremely
    # numerous (~40 per cycle × many cycles) and all get filtered out.
    done < <(tail -n 3000 "$log_file" 2>/dev/null)

    # Render the last max_lines from the collected output
    local total=${#output_lines[@]}
    local start=0
    if (( total > max_lines )); then
        start=$(( total - max_lines ))
    fi
    local rendered=0
    for (( i=start; i<total; i++ )); do
        printf '%s\n' "${output_lines[$i]}"
        rendered=$((rendered + 1))
    done

    if (( rendered == 0 )); then
        printf '\033[2m  Waiting for output...\033[K\033[0m\n'
    fi

    # Clear everything below to remove stale lines from previous frame
    printf '\033[J'
}

# ─────────────────────────────────────────────────────────────────────────────
# Summary rendering
# ─────────────────────────────────────────────────────────────────────────────

render_summary() {
    local name="$1" elapsed="$2"

    get_vm_counts

    echo ""
    _box 39 \
        "DEPLOYMENT COMPLETE: $name" \
        "" \
        "Total VMs:  $COUNT_TOTAL" \
        "Completed:  $COUNT_COMPLETED" \
        "Failed:     $COUNT_FAILED" \
        "Duration:   $(format_duration "$elapsed")"

    if (( COUNT_FAILED > 0 )); then
        echo ""
        _bold 196 "Failed VMs:"
        local vm
        for vm in "${VM_ORDER[@]}"; do
            if [[ "${VM_STATUS[$vm]}" == "failed" ]]; then
                printf '\033[38;5;196m  ERROR %s: %s\033[0m\n' "$vm" "${VM_ERROR[$vm]:-unknown error}"
            fi
        done
    fi

    echo ""
    if (( COUNT_FAILED == 0 && COUNT_COMPLETED == COUNT_TOTAL && COUNT_TOTAL > 0 )); then
        _bold 46 "All $COUNT_TOTAL VMs completed successfully!"
    elif (( COUNT_FAILED > 0 )); then
        _color 214 "Completed with $COUNT_FAILED failure(s)"
    fi
}

render_teardown_summary() {
    local elapsed="$1"

    if (( ${#VM_ORDER[@]} > 0 )); then
        local deleted=0 total=${#VM_ORDER[@]}
        local vm rid
        for vm in "${VM_ORDER[@]}"; do
            for rid in "${!RESOURCE_STATUS[@]}"; do
                if [[ "${RESOURCE_NAME[$rid]}" == "$vm" && "${RESOURCE_STATUS[$rid]}" == "deleted" ]]; then
                    deleted=$((deleted + 1))
                    break
                fi
            done
        done

        echo ""
        _box 39 \
            "TEARDOWN COMPLETE" \
            "" \
            "VMs: $deleted/$total deleted" \
            "Duration: $(format_duration "$elapsed")"
    else
        get_resource_counts

        echo ""
        _box 39 \
            "TEARDOWN COMPLETE" \
            "" \
            "Servers: $RCOUNT_SERVERS_DELETED/$RCOUNT_SERVERS_TOTAL deleted" \
            "Volumes: $RCOUNT_VOLUMES_DELETED/$RCOUNT_VOLUMES_TOTAL deleted" \
            "Failed:  $((RCOUNT_SERVERS_FAILED + RCOUNT_VOLUMES_FAILED))" \
            "Duration: $(format_duration "$elapsed")"
    fi
}

install_ssh_config() {
    # Install SSH config snippet into ~/.ssh/config with managed markers.
    # Replaces any existing block for the same deployment.
    # Usage: install_ssh_config <snippet_file> <deployment_name>
    local snippet_file="$1" deploy_name="$2"
    local ssh_config="${SSH_CONFIG:-$HOME/.ssh/config}"
    local marker_begin="# BEGIN RUSE: ${deploy_name}"
    local marker_end="# END RUSE: ${deploy_name}"

    if [[ ! -f "$snippet_file" ]]; then
        return 0
    fi

    # Ensure ~/.ssh exists
    mkdir -p "$(dirname "$ssh_config")"

    # Read snippet content (strip the old ##### header/footer decorations)
    local snippet
    snippet=$(sed -E '/^#{5,}/d; /^# Add this to your/d' "$snippet_file")

    # Build the managed block
    local block
    block=$(printf '%s\n%s\n%s\n' "$marker_begin" "$snippet" "$marker_end")

    if [[ -f "$ssh_config" ]]; then
        # Remove any existing block for this deployment
        local tmp="${ssh_config}.ruse.tmp"
        awk -v begin="$marker_begin" -v end="$marker_end" '
            $0 == begin { skip=1; next }
            $0 == end   { skip=0; next }
            !skip
        ' "$ssh_config" > "$tmp"

        # Remove trailing blank lines then append new block
        sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$tmp"
        printf '\n\n%s\n' "$block" >> "$tmp"
        mv "$tmp" "$ssh_config"
    else
        printf '%s\n' "$block" > "$ssh_config"
    fi

    chmod 600 "$ssh_config"

    # Display what was installed
    echo ""
    local rule=""
    printf -v rule '═%.0s' {1..64}
    printf '\033[38;5;39m╔%s╗\033[0m\n' "$rule"
    printf '\033[38;5;39m║\033[0m  %-62s\033[38;5;39m║\033[0m\n' "SSH CONFIG — installed to $ssh_config"
    printf '\033[38;5;39m╠%s╣\033[0m\n' "$(printf '═%.0s' {1..64})"

    local line
    while IFS= read -r line || [[ -n "$line" ]]; do
        printf '\033[38;5;39m║\033[0m  %-62s\033[38;5;39m║\033[0m\n' "$line"
    done <<< "$block"

    printf '\033[38;5;39m╚%s╝\033[0m\n' "$rule"
}

remove_ssh_config() {
    # Remove a RUSE-managed SSH config block for a deployment.
    # Usage: remove_ssh_config <deployment_name>
    local deploy_name="$1"
    local ssh_config="${SSH_CONFIG:-$HOME/.ssh/config}"
    local marker_begin="# BEGIN RUSE: ${deploy_name}"
    local marker_end="# END RUSE: ${deploy_name}"

    [[ -f "$ssh_config" ]] || return 0

    # Check if the block exists
    if ! grep -qF "$marker_begin" "$ssh_config"; then
        return 0
    fi

    local tmp="${ssh_config}.ruse.tmp"
    awk -v begin="$marker_begin" -v end="$marker_end" '
        $0 == begin { skip=1; next }
        $0 == end   { skip=0; next }
        !skip
    ' "$ssh_config" > "$tmp"

    # Remove trailing blank lines
    sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$tmp"
    mv "$tmp" "$ssh_config"
    chmod 600 "$ssh_config"

    TEARDOWN_SSH_CLEANED=true
    _faint "Removed SSH config for $deploy_name from $ssh_config"
}

generate_phase_config() {
    # Print PHASE IP config derived from ssh_config_snippet.txt.
    # Usage: generate_phase_config <snippet_file> <deploy_name>
    local snippet_file="$1" deploy_name="$2"

    [[ -f "$snippet_file" ]] || return 0

    echo ""
    python3 -c "
import re, sys

snippet = open('$snippet_file').read()
deploy = '$deploy_name'

# Parse Host / HostName pairs (skip wildcard Host sup-*)
hosts = []
for m in re.finditer(r'^Host (sup-(?!\*)\S+)\s*\n\s+HostName (\S+)', snippet, re.MULTILINE):
    hosts.append((m.group(1), m.group(2)))

if not hosts:
    sys.exit(0)

# Derive config label: 'exp-4' → 'EXP4', 'test/0213' → 'TEST_0213'
label = deploy.upper().replace('-', '').replace('/', '_')

# Map VM hostnames to behavior labels
entries = []  # (ip, behavior_label, brain_type)
for hostname, ip in hosts:
    rest = hostname[4:]  # remove 'sup-'
    parts = rest.rsplit('-', 1)
    if len(parts) != 2 or not parts[1].isdigit():
        continue
    index = parts[1]
    name_part = parts[0]

    beh_match = re.search(r'[A-Z]\d', name_part)
    if not beh_match:
        continue
    behavior_raw = name_part[beh_match.start():]

    dash_parts = behavior_raw.split('-', 1)
    if len(dash_parts) == 2:
        behavior = dash_parts[0] + '.' + dash_parts[1]
    else:
        behavior = dash_parts[0]

    brain = behavior[0]
    entries.append((ip, f'SUP_{behavior}-{index}', brain))

groups = {
    'C': 'Control nodes',
    'M': 'MCHP agents',
    'B': 'BrowserUse agents',
    'S': 'SmolAgents agents',
}

lines = []
lines.append(f'# --- SUPS-{label} MODE CONFIG ---')
lines.append(f\"SUPS_{label}_OUTPUT_FILE = 'bigdisk/TRAINING_DATA/IPs/AXES_SUPs_{label}.json'\")
lines.append(f\"SUPS_{label}_INFERENCE_FILE = 'bigdisk/TRAINING_DATA/IPs/axes-sups-{deploy.replace('/', '-')}_IPs.txt'\")
lines.append(f'SUP_IPS_{label} = {{')

prev_brain = None
for ip, label_name, brain in entries:
    if brain != prev_brain:
        if prev_brain is not None:
            lines.append('')
        comment = groups.get(brain, f'{brain} agents')
        lines.append(f'    # {comment}')
        prev_brain = brain
    lines.append(f'    \"{ip}\": \"{label_name}\",')
lines.append('}')

print('\n'.join(lines))
" 2>/dev/null
}
