#!/usr/bin/env python3
"""
Centralized VM naming for the RUSE deployment system.

Single source of truth for:
  - VM name prefixes (d- for DECOY SUPs, r- for RAMPART, g- for GHOSTS)
  - Deployment ID computation
  - VM name construction and parsing
  - VM sort ordering

All other components (deploy script, ansible playbooks, callback plugin,
monitor.sh) should reference this module instead of reimplementing naming.

CLI usage (for bash/ansible integration):
  python3 lib/vm_naming.py dep_id "decoy-controls"
  python3 lib/vm_naming.py run_dep_id "decoy-controls" "031726152824"
  python3 lib/vm_naming.py vm_name "controls031726" "M1" "0"
  python3 lib/vm_naming.py vm_prefix "controls031726"
  python3 lib/vm_naming.py expand "config.yaml" "controls031726"
  python3 lib/vm_naming.py sort_key "d-controls031726-M1-0"
"""

import re
import sys


# ── Prefixes ─────────────────────────────────────────────────────────

SUP_VM_PREFIX = "d-"       # DECOY SUP agents
ENT_VM_PREFIX = "r-"       # RAMPART enterprise workflows
GHOSTS_VM_PREFIX = "g-"    # GHOSTS NPC traffic generators
ALL_PREFIXES = ("d-", "r-", "g-")


# ── Deployment ID ────────────────────────────────────────────────────

def make_dep_id(deployment_name: str) -> str:
    """Convert deployment directory name to deployment ID component.

    Strips deploy-type prefix and removes hyphens.
    Examples: 'decoy-controls' → 'controls', 'rampart-med' → 'med', 'exp-3' → 'exp3'
    """
    name = deployment_name
    for prefix in ("decoy-", "rampart-", "ghosts-", "enterprise-"):
        name = name.removeprefix(prefix)
    return name.replace("-", "")


def make_run_dep_id(deployment_name: str, run_id: str) -> str:
    """Return deployment ID for a specific run: {dep_id}{run_id}.

    Examples: ('decoy-controls', '031726') → 'controls031726'
    """
    dep_id = make_dep_id(deployment_name)
    if run_id == "orphan":
        return dep_id
    return f"{dep_id}{run_id}"


# ── VM name construction ─────────────────────────────────────────────

def make_vm_prefix(dep_id: str) -> str:
    """Build the VM prefix for a deployment: 'd-{dep_id}-'."""
    return f"{SUP_VM_PREFIX}{dep_id}-"


def make_vm_name(dep_id: str, behavior: str, index: int) -> str:
    """Build a full VM name: d-{dep_id}-{behavior_sanitized}-{index}.

    The behavior's '.' is replaced with '-' for OpenStack compatibility.
    Example: make_vm_name('controls031726', 'B2.llama', 0) → 'd-controls031726-B2-llama-0'
    """
    return f"{SUP_VM_PREFIX}{dep_id}-{behavior.replace('.', '-')}-{index}"


def make_ent_vm_prefix(dep_id: str) -> str:
    """Build RAMPART enterprise VM prefix: 'r-{hash}-' (5-char MD5 for NetBIOS limit)."""
    import hashlib
    ent_hash = hashlib.md5(dep_id.encode()).hexdigest()[:5]
    return f"{ENT_VM_PREFIX}{ent_hash}-"


def make_ghosts_vm_prefix(dep_id: str) -> str:
    """Build GHOSTS VM prefix: 'g-{hash}-' (5-char MD5 to match RAMPART convention)."""
    import hashlib
    g_hash = hashlib.md5(dep_id.encode()).hexdigest()[:5]
    return f"{GHOSTS_VM_PREFIX}{g_hash}-"


# ── VM name parsing ──────────────────────────────────────────────────

_VM_NAME_RE = re.compile(
    r'^(?P<prefix>d-|r-|g-)'
    r'(?P<rest>.+)-'
    r'(?P<index>\d+)$'
)

_BEHAVIOR_RE = re.compile(r'[A-Z]\d')

# GHOSTS VM pattern: g-{hash}-{role}-{index} where role is api or npc
_GHOSTS_VM_RE = re.compile(
    r'^g-(?P<hash>[a-f0-9]+)-(?P<role>api|npc)-(?P<index>\d+)$'
)


def parse_vm_name(vm_name: str) -> dict | None:
    """Parse a VM name into components.

    Returns {'prefix', 'dep_id', 'behavior', 'index'} or None if unparseable.
    For GHOSTS VMs: behavior is 'GHOSTS-api' or 'GHOSTS-npc'.
    """
    # GHOSTS VMs: g-{hash}-api-0 or g-{hash}-npc-0
    gm = _GHOSTS_VM_RE.match(vm_name)
    if gm:
        return {
            'prefix': 'g-',
            'dep_id': gm.group('hash'),
            'behavior': f"GHOSTS-{gm.group('role')}",
            'index': int(gm.group('index')),
        }

    m = _VM_NAME_RE.match(vm_name)
    if not m:
        return None

    prefix = m.group('prefix')
    rest = m.group('rest')
    index = int(m.group('index'))

    # Find where the behavior code starts (first [A-Z]\d pattern)
    beh_match = _BEHAVIOR_RE.search(rest)
    if not beh_match:
        return None

    dep_id_part = rest[:beh_match.start()].rstrip('-')
    behavior_raw = rest[beh_match.start():]

    # Convert first dash back to dot: B2-llama → B2.llama
    dash_parts = behavior_raw.split('-', 1)
    if len(dash_parts) == 2 and dash_parts[1].isalpha():
        behavior = f"{dash_parts[0]}.{dash_parts[1]}"
    else:
        behavior = dash_parts[0]

    return {
        'prefix': prefix,
        'dep_id': dep_id_part,
        'behavior': behavior,
        'index': index,
    }


# ── VM sort key ──────────────────────────────────────────────────────

_BRAIN_CATEGORIES = {
    'BC': 3, 'SC': 5, 'C': 0, 'M': 1, 'B': 2, 'S': 4,
}


def vm_sort_key(vm_name: str) -> str:
    """Return a sortable string key for VM ordering.

    Orders by: brain category → version → variant → instance index.
    """
    parsed = parse_vm_name(vm_name)
    if not parsed:
        # Fallback: try stripping prefix and parsing directly
        stripped = vm_name
        for pfx in ALL_PREFIXES:
            if vm_name.startswith(pfx):
                stripped = vm_name[len(pfx):]
                break

        # Skip dep_id prefix (lowercase before behavior letter)
        beh_match = _BEHAVIOR_RE.search(stripped)
        if not beh_match:
            return f"9.000..000"
        stripped = stripped[beh_match.start():]
        parts = stripped.rsplit('-', 1)
        if len(parts) == 2 and parts[1].isdigit():
            behavior_raw, instance = parts[0], int(parts[1])
        else:
            return f"9.000..000"
    else:
        behavior_raw = parsed['behavior'].replace('.', '-')
        instance = parsed['index']

    # Determine category and version
    cat = 9
    version = 0
    for prefix, cat_num in _BRAIN_CATEGORIES.items():
        if behavior_raw.startswith(prefix):
            cat = cat_num
            version_str = behavior_raw[len(prefix):]
            version_str = version_str.split('-')[0].split('.')[0]
            version_str = re.sub(r'[^0-9]', '', version_str)
            version = int(version_str) if version_str else 0
            break

    variant = behavior_raw[behavior_raw.index(str(version)) + len(str(version)):] if str(version) in behavior_raw else ""

    return f"{cat}.{version:03d}.{variant}.{instance:03d}"


# ── Deployment expansion ─────────────────────────────────────────────

def expand_deployments(deployments: list, dep_id: str) -> list:
    """Expand deployment config entries into a flat VM list.

    Input:  [{'behavior': 'M1', 'flavor': 'v1.14vcpu.28g', 'count': 2}, ...]
    Output: [{'name': 'd-dep-M1-0', 'behavior': 'M1', 'flavor': '...', 'index': 0}, ...]
    """
    vms = []
    counts = {}
    for dep in deployments:
        behavior = dep['behavior']
        flavor = dep['flavor']
        for _ in range(dep.get('count', 1)):
            idx = counts.get(behavior, 0)
            counts[behavior] = idx + 1
            vms.append({
                'name': make_vm_name(dep_id, behavior, idx),
                'behavior': behavior,
                'flavor': flavor,
                'index': idx,
            })
    return vms


# ── CLI entry point (for bash/ansible) ───────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <command> [args...]", file=sys.stderr)
        print("Commands: dep_id, run_dep_id, vm_name, vm_prefix, ent_prefix, sort_key, expand", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "dep_id":
        print(make_dep_id(sys.argv[2]))
    elif cmd == "run_dep_id":
        print(make_run_dep_id(sys.argv[2], sys.argv[3]))
    elif cmd == "vm_name":
        print(make_vm_name(sys.argv[2], sys.argv[3], int(sys.argv[4])))
    elif cmd == "vm_prefix":
        print(make_vm_prefix(sys.argv[2]))
    elif cmd == "ent_prefix":
        print(make_ent_vm_prefix(sys.argv[2]))
    elif cmd == "sort_key":
        print(vm_sort_key(sys.argv[2]))
    elif cmd == "sup_prefix":
        print(SUP_VM_PREFIX)
    elif cmd == "all_prefixes_regex":
        # For teardown-all grep: (d-|r-|g-)
        print("(" + "|".join(ALL_PREFIXES) + ")")
    elif cmd == "expand":
        import yaml
        with open(sys.argv[2]) as f:
            cfg = yaml.safe_load(f)
        dep_id = sys.argv[3]
        for vm in expand_deployments(cfg.get('deployments', []), dep_id):
            print(f"{vm['name']}\t{vm['behavior']}\t{vm['flavor']}")
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
