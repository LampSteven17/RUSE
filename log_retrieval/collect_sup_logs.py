#!/usr/bin/env python3
"""
SUP Log Collection System

Collects JSONL logs from remote SUP VMs and loads them into DuckDB.
Focuses only on structured JSONL logs from AgentLogger - ignores systemd output.

Naming convention:
    Deployment dir name = experiment name = DB prefix.
    DB filename includes run ID: {name}-{run_id}.duckdb

Usage:
    python log_retrieval/collect_sup_logs.py sup-controls      # Collect from latest run
    python log_retrieval/collect_sup_logs.py --all              # All with inventory
    python log_retrieval/collect_sup_logs.py --list             # Show configurations
    python log_retrieval/collect_sup_logs.py --dry-run sup-controls
"""

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import duckdb
except ImportError as e:
    print(f"ERROR: Missing dependency: {e}. Run: pip install duckdb")
    sys.exit(1)


# ============================================================================
# Output helpers (child-mode awareness for PHASE.py pipeline)
# ============================================================================

_CHILD_MODE = bool(os.environ.get("PHASE_PIPELINE_STEP"))

def _banner(msg):
    """Top-level script banner. Suppressed in child mode."""
    if not _CHILD_MODE:
        print("=" * 60)
        print(msg)
        print("=" * 60)

def _section(msg):
    """Section header within the script."""
    if _CHILD_MODE:
        print(f"    {msg}")
    else:
        print(f"\n{'='*60}")
        print(msg)
        print("=" * 60)

def _out(msg=""):
    """Standard output line. Indented in child mode."""
    if _CHILD_MODE:
        print(f"    {msg}" if msg else "")
    else:
        print(msg)


# ============================================================================
# Configuration
# ============================================================================

BASE_OUTPUT_DIR = Path("/mnt/AXES2U1/SUP_LOGS")
DEPLOYMENTS_DIR = Path("/home/ubuntu/RUSE/deployments")
PHASE_EXPERIMENTS_FILE = Path("/mnt/AXES2U1/experiments.json")
REMOTE_LOG_BASE = "/opt/ruse/deployed_sups"
SSH_OPTIONS = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=30 -o BatchMode=yes -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519"
SSH_USER = "ubuntu"


# ============================================================================
# EXPERIMENT CONFIGURATIONS
# ============================================================================
# Loaded from shared experiment registry (/mnt/AXES2U1/experiments.json).
# Keys in experiments.json match RUSE deployment directory names directly.
# Only entries with SUP_ IP labels (i.e., RUSE deployments) are included.

@dataclass
class ExperimentConfig:
    """Configuration for a log collection experiment."""
    name: str
    description: str
    vm_count: int
    behaviors: List[str]


def load_experiment_configs(phase_config: Path) -> dict:
    """Load experiment configs from PHASE experiments.json.

    Filters for entries with SUP_ IP labels (i.e., RUSE deployments).
    Keys match RUSE deployment directory names.
    """
    if not phase_config.exists():
        return {}

    try:
        with open(phase_config) as f:
            data = json.load(f)
    except Exception:
        return {}

    configs = {}
    for key, entry in data.items():
        if key.startswith("_"):
            continue

        # Only include entries with agent IPs (r- RUSE, g- GHOSTS, e- Enterprise, or legacy SUP_)
        ips = entry.get("ips", {})
        behaviors = []
        for label in ips.values():
            if label.startswith("r-") or label.startswith("g-") or label.startswith("e-") or label.startswith("SUP_"):
                # Extract behavior: strip prefix+dep_id, take everything before last -N instance
                behaviors.append(label.rsplit("-", 1)[0])
        if not behaviors:
            continue

        configs[key] = ExperimentConfig(
            name=key,
            description=entry.get("description", key),
            vm_count=len(ips),
            behaviors=behaviors,
        )

    return configs


EXPERIMENTS = load_experiment_configs(PHASE_EXPERIMENTS_FILE)


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class VMInfo:
    """Information about a VM from inventory."""
    hostname: str
    ip: str
    sup_behavior: str
    sup_flavor: str


@dataclass
class CollectionResult:
    """Result of collecting logs from a single VM."""
    experiment: str
    vm: VMInfo
    events_collected: int = 0
    errors: List[str] = field(default_factory=list)
    success: bool = False


# ============================================================================
# DuckDB Schema
# ============================================================================

CREATE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS events (
    id                   BIGINT,
    timestamp            TIMESTAMP,
    session_id           VARCHAR,
    agent_type           VARCHAR,
    event_type           VARCHAR,
    workflow             VARCHAR,
    details              JSON,
    experiment_name      VARCHAR,
    vm_hostname          VARCHAR,
    vm_ip                VARCHAR,
    sup_behavior         VARCHAR,
    sup_flavor           VARCHAR,
    source_file          VARCHAR,
    collection_timestamp TIMESTAMP,
    -- Extracted fields for fast querying
    duration_ms          INTEGER,
    success              BOOLEAN,
    error_message        VARCHAR,
    model                VARCHAR,
    action               VARCHAR,
    category             VARCHAR,
    step_name            VARCHAR,
    status               VARCHAR,
    -- LLM-specific columns
    input_tokens         INTEGER,
    output_tokens        INTEGER,
    total_tokens         INTEGER,
    llm_output           VARCHAR
);
"""

CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_events_experiment ON events(experiment_name);
CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_agent_type ON events(agent_type);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_workflow ON events(workflow);
CREATE INDEX IF NOT EXISTS idx_events_sup_behavior ON events(sup_behavior);
CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);
"""


# ============================================================================
# Inventory Parsing
# ============================================================================

def discover_runs(deployments_dir: Path) -> List[Tuple[str, str, Path]]:
    """Find all deployment runs with inventory.ini files.

    Returns list of (deployment_name, run_id, inventory_path) tuples.
    For legacy layouts (root inventory.ini), run_id is empty string.
    """
    runs = []
    for path in deployments_dir.iterdir():
        if not path.is_dir() or path.name in ("playbooks", "lib", "logs"):
            continue

        # Check for legacy root inventory
        inv = path / "inventory.ini"
        if inv.exists():
            runs.append((path.name, "", inv))

        # Check for multi-run inventories in runs/ subdirs
        runs_dir = path / "runs"
        if runs_dir.is_dir():
            for run_dir in sorted(runs_dir.iterdir()):
                inv = run_dir / "inventory.ini"
                if run_dir.is_dir() and inv.exists():
                    runs.append((path.name, run_dir.name, inv))

    return sorted(runs)


def list_experiments() -> None:
    """Display available experiment configurations."""
    all_runs = discover_runs(DEPLOYMENTS_DIR)
    # Group runs by deployment name
    runs_by_deploy = {}
    for deploy_name, run_id, _ in all_runs:
        runs_by_deploy.setdefault(deploy_name, []).append(run_id)

    print("\nConfigured experiments:")
    print("-" * 70)
    for key, cfg in EXPERIMENTS.items():
        run_ids = runs_by_deploy.get(key, [])
        if run_ids:
            runs_str = ", ".join(f"{key}-{r}" if r else key for r in run_ids)
            status = "READY"
        else:
            runs_str = ""
            status = "NO INVENTORY"
        print(f"  {key:<24} {cfg.vm_count:>2} VMs  [{status:<12}]  {cfg.description}")
        if runs_str:
            print(f"  {'':24}         runs: {runs_str}")

    # Show any discovered deployments not in EXPERIMENTS
    known = set(EXPERIMENTS.keys())
    extra = [(d, r) for d, r, _ in all_runs if d not in known]
    if extra:
        names = sorted(set(f"{d}-{r}" if r else d for d, r in extra))
        print(f"\n  (Also discovered: {', '.join(names)})")
    print()


def parse_inventory(inventory_path: Path) -> List[VMInfo]:
    """Parse inventory.ini to extract VM information."""
    vms = []
    in_sup_hosts = False

    with open(inventory_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('['):
                in_sup_hosts = line == '[sup_hosts]'
                continue
            if ':vars]' in line or line.endswith(':vars'):
                in_sup_hosts = False
                continue

            if in_sup_hosts and '=' in line:
                parts = line.split()
                if not parts:
                    continue
                hostname = parts[0]
                attrs = {}
                for part in parts[1:]:
                    if '=' in part:
                        key, value = part.split('=', 1)
                        attrs[key] = value

                if 'ansible_host' in attrs:
                    vms.append(VMInfo(
                        hostname=hostname,
                        ip=attrs.get('ansible_host', ''),
                        sup_behavior=attrs.get('sup_behavior', ''),
                        sup_flavor=attrs.get('sup_flavor', '')
                    ))
    return vms


# ============================================================================
# SSH/Rsync Operations
# ============================================================================

def collect_logs_from_vm(
    vm: VMInfo,
    experiment: str,
    output_dir: Path,
    dry_run: bool = False
) -> CollectionResult:
    """Collect JSONL logs from a VM via SSH cat into a single file."""
    result = CollectionResult(experiment=experiment, vm=vm)

    remote_log_dir = f"{REMOTE_LOG_BASE}/{vm.sup_behavior}/logs/"

    if dry_run:
        cmd = f"ssh {SSH_OPTIONS} {SSH_USER}@{vm.ip} 'ls -1 {remote_log_dir}*.jsonl 2>/dev/null || echo \"\"'"
        try:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                result.success = True  # No logs dir is not an error
                return result
            remote_files = [
                f for f in proc.stdout.strip().split('\n')
                if f.strip() and f.endswith('.jsonl') and 'latest.jsonl' not in f
            ]
            result.events_collected = len(remote_files)  # approximate: 1 session file ≈ many events
        except (subprocess.TimeoutExpired, Exception):
            pass
        result.success = True
        return result

    # Create output directory
    vm_output_dir = output_dir / experiment / vm.hostname
    vm_output_dir.mkdir(parents=True, exist_ok=True)

    # Single SSH, single stream: concatenate all JSONL on remote, write one file to NFS.
    # Identical path for 5 files or 22K files — no per-file overhead on either end.
    out_file = vm_output_dir / "events.jsonl"
    cat_cmd = (
        f'ssh {SSH_OPTIONS} {SSH_USER}@{vm.ip} '
        f'"find {remote_log_dir} -maxdepth 1 -name \'*.jsonl\' ! -name \'latest.jsonl\' -print0 '
        f'| xargs -0 cat 2>/dev/null" '
        f'> {out_file}'
    )

    try:
        proc = subprocess.run(
            cat_cmd, shell=True, capture_output=True, text=True
        )
        if proc.returncode == 0 and out_file.exists() and out_file.stat().st_size > 0:
            # Count events (lines) in the combined file
            with open(out_file) as f:
                result.events_collected = sum(1 for _ in f)
            result.success = True
        elif proc.returncode == 0:
            out_file.unlink(missing_ok=True)
            result.success = True
        else:
            out_file.unlink(missing_ok=True)
            stderr = proc.stderr.strip()
            if "No such file" in stderr or "No match" in stderr:
                result.success = True
            else:
                result.errors.append(f"ssh cat failed (rc={proc.returncode}): {stderr}")
    except Exception as e:
        out_file.unlink(missing_ok=True)
        result.errors.append(str(e))

    result.success = result.success or result.files_collected > 0
    return result


# ============================================================================
# DuckDB Operations
# ============================================================================

def init_database(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Initialize DuckDB database with schema."""
    conn = duckdb.connect(str(db_path))
    conn.execute(CREATE_EVENTS_TABLE)
    conn.execute(CREATE_INDEXES)
    return conn


def load_events_to_duckdb(
    conn: duckdb.DuckDBPyConnection,
    raw_dir: Path,
    experiments: List[str],
    vm_info_map: Dict[str, VMInfo]
) -> int:
    """Load JSONL events into DuckDB using native JSON reader."""
    collection_ts = datetime.now().isoformat()
    total_loaded = 0

    # Get max existing ID
    try:
        result = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
        event_id = result[0] if result else 0
    except Exception:
        event_id = 0

    for experiment in experiments:
        exp_dir = raw_dir / experiment
        if not exp_dir.exists():
            continue

        for vm_dir in sorted(exp_dir.iterdir()):
            if not vm_dir.is_dir():
                continue

            jsonl_files = sorted([
                str(f) for f in vm_dir.glob("*.jsonl")
                if not f.is_symlink() and f.name != "latest.jsonl"
            ])
            if not jsonl_files:
                continue

            vm_key = f"{experiment}:{vm_dir.name}"
            vm = vm_info_map.get(vm_key)
            vm_ip = (vm.ip if vm else "").replace("'", "''")
            sup_behavior = (vm.sup_behavior if vm else "").replace("'", "''")
            sup_flavor = (vm.sup_flavor if vm else "").replace("'", "''")
            vm_hostname = vm_dir.name.replace("'", "''")
            exp_escaped = experiment.replace("'", "''")

            file_list_sql = ", ".join(f"'{f}'" for f in jsonl_files)

            query = f"""
                INSERT INTO events
                SELECT
                    ROW_NUMBER() OVER () + {event_id} as id,
                    TRY_CAST(timestamp AS TIMESTAMP) as timestamp,
                    session_id,
                    agent_type,
                    event_type,
                    workflow,
                    details,
                    '{exp_escaped}' as experiment_name,
                    '{vm_hostname}' as vm_hostname,
                    '{vm_ip}' as vm_ip,
                    '{sup_behavior}' as sup_behavior,
                    '{sup_flavor}' as sup_flavor,
                    NULL as source_file,
                    '{collection_ts}'::TIMESTAMP as collection_timestamp,
                    TRY_CAST(json_extract_string(details, '$.duration_ms') AS INTEGER) as duration_ms,
                    TRY_CAST(json_extract_string(details, '$.success') AS BOOLEAN) as success,
                    COALESCE(
                        json_extract_string(details, '$.error'),
                        json_extract_string(details, '$.message')
                    ) as error_message,
                    json_extract_string(details, '$.model') as model,
                    json_extract_string(details, '$.action') as action,
                    json_extract_string(details, '$.category') as category,
                    json_extract_string(details, '$.step_name') as step_name,
                    json_extract_string(details, '$.status') as status,
                    TRY_CAST(json_extract_string(details, '$.tokens.input') AS INTEGER) as input_tokens,
                    TRY_CAST(json_extract_string(details, '$.tokens.output') AS INTEGER) as output_tokens,
                    TRY_CAST(json_extract_string(details, '$.tokens.total') AS INTEGER) as total_tokens,
                    json_extract_string(details, '$.output') as llm_output
                FROM read_json(
                    [{file_list_sql}],
                    format='newline_delimited',
                    columns={{
                        timestamp: 'VARCHAR',
                        session_id: 'VARCHAR',
                        agent_type: 'VARCHAR',
                        event_type: 'VARCHAR',
                        workflow: 'VARCHAR',
                        details: 'JSON'
                    }},
                    ignore_errors=true
                )
            """

            before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            conn.execute(query)
            after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            loaded = after - before
            event_id += loaded
            total_loaded += loaded

            if loaded > 0 and not _CHILD_MODE:
                print(f"    {vm_dir.name}: {loaded:,} events")

    if not _CHILD_MODE:
        print(f"  Total: {total_loaded:,} events")
    return total_loaded


def create_analysis_views(conn: duckdb.DuckDBPyConnection):
    """Create analysis views for querying."""

    if not _CHILD_MODE:
        print("  Creating analysis views...")

    conn.execute("""
        CREATE OR REPLACE VIEW workflow_analysis AS
        SELECT
            id, timestamp, session_id, agent_type, event_type, workflow,
            details, experiment_name, vm_hostname, sup_behavior, sup_flavor,
            duration_ms, success, error_message, model,
            CASE
                WHEN lower(workflow) LIKE '%search%' OR lower(workflow) LIKE '%google%' THEN 'web_search'
                WHEN lower(workflow) LIKE '%youtube%' OR lower(workflow) LIKE '%video%' THEN 'youtube'
                WHEN lower(workflow) LIKE '%download%' OR lower(workflow) LIKE '%file%' THEN 'file_ops'
                WHEN lower(workflow) LIKE '%command%' OR lower(workflow) LIKE '%shell%' THEN 'shell'
                WHEN lower(workflow) LIKE '%document%' OR lower(workflow) LIKE '%writer%'
                     OR lower(workflow) LIKE '%spreadsheet%' THEN 'document'
                WHEN lower(workflow) LIKE '%weather%' OR lower(workflow) LIKE '%explain%'
                     OR lower(workflow) LIKE '%what is%' THEN 'research'
                WHEN lower(workflow) LIKE '%browse%' OR lower(workflow) LIKE '%visit%'
                     OR lower(workflow) LIKE '%wikipedia%' THEN 'web_browse'
                ELSE 'other'
            END as workflow_category
        FROM events
        WHERE workflow IS NOT NULL
    """)

    conn.execute("""
        CREATE OR REPLACE VIEW llm_analysis AS
        SELECT
            id, timestamp, session_id, agent_type, event_type, workflow,
            experiment_name, vm_hostname, sup_behavior, model,
            duration_ms, success, error_message, action,
            input_tokens, output_tokens, total_tokens, llm_output,
            CASE
                WHEN event_type = 'llm_response' AND duration_ms > 0 AND output_tokens IS NOT NULL
                THEN ROUND(output_tokens * 1000.0 / duration_ms, 2)
                ELSE NULL
            END as tokens_per_second
        FROM events
        WHERE event_type IN ('llm_request', 'llm_response', 'llm_error')
    """)

    conn.execute("""
        CREATE OR REPLACE VIEW llm_performance AS
        SELECT
            experiment_name,
            sup_behavior,
            model,
            COUNT(*) FILTER (WHERE event_type = 'llm_request') as request_count,
            COUNT(*) FILTER (WHERE event_type = 'llm_response') as response_count,
            COUNT(*) FILTER (WHERE event_type = 'llm_error') as error_count,
            ROUND(AVG(duration_ms) FILTER (WHERE event_type = 'llm_response'), 0) as avg_duration_ms,
            MIN(duration_ms) FILTER (WHERE event_type = 'llm_response') as min_duration_ms,
            MAX(duration_ms) FILTER (WHERE event_type = 'llm_response') as max_duration_ms,
            SUM(input_tokens) FILTER (WHERE event_type = 'llm_response') as total_input_tokens,
            SUM(output_tokens) FILTER (WHERE event_type = 'llm_response') as total_output_tokens,
            ROUND(AVG(input_tokens) FILTER (WHERE event_type = 'llm_response'), 0) as avg_input_tokens,
            ROUND(AVG(output_tokens) FILTER (WHERE event_type = 'llm_response'), 0) as avg_output_tokens,
            ROUND(AVG(
                CASE WHEN duration_ms > 0 AND output_tokens IS NOT NULL
                THEN output_tokens * 1000.0 / duration_ms ELSE NULL END
            ) FILTER (WHERE event_type = 'llm_response'), 2) as avg_tokens_per_second
        FROM events
        WHERE event_type IN ('llm_request', 'llm_response', 'llm_error')
        GROUP BY experiment_name, sup_behavior, model
    """)

    conn.execute("""
        CREATE OR REPLACE VIEW session_summary AS
        SELECT
            session_id,
            experiment_name,
            vm_hostname,
            sup_behavior,
            model,
            MIN(timestamp) as session_start,
            MAX(timestamp) as session_end,
            COUNT(*) as total_events,
            COUNT(*) FILTER (WHERE event_type = 'workflow_start') as workflows_started,
            COUNT(*) FILTER (WHERE event_type = 'workflow_end' AND success = true) as workflows_succeeded,
            COUNT(*) FILTER (WHERE event_type = 'workflow_end' AND success = false) as workflows_failed,
            COUNT(*) FILTER (WHERE event_type = 'llm_response') as llm_calls,
            SUM(input_tokens) FILTER (WHERE event_type = 'llm_response') as total_input_tokens,
            SUM(output_tokens) FILTER (WHERE event_type = 'llm_response') as total_output_tokens,
            COUNT(*) FILTER (WHERE event_type LIKE '%error%') as error_count
        FROM events
        GROUP BY session_id, experiment_name, vm_hostname, sup_behavior, model
    """)


# ============================================================================
# Manifest Generation
# ============================================================================

def write_manifest(
    manifest_path: Path,
    results: List[CollectionResult],
    collection_date: str,
    total_events: int
) -> None:
    """Write collection manifest JSON."""
    manifest = {
        "collection_date": collection_date,
        "collection_timestamp": datetime.now().isoformat(),
        "total_events_loaded": total_events,
        "experiments": {},
        "summary": {
            "total_vms": len(results),
            "successful_vms": sum(1 for r in results if r.success),
            "total_events_collected": sum(r.events_collected for r in results),
            "total_errors": sum(len(r.errors) for r in results)
        }
    }

    for result in results:
        if result.experiment not in manifest["experiments"]:
            manifest["experiments"][result.experiment] = {"vms": []}

        manifest["experiments"][result.experiment]["vms"].append({
            "hostname": result.vm.hostname,
            "ip": result.vm.ip,
            "sup_behavior": result.vm.sup_behavior,
            "events_collected": result.events_collected,
            "success": result.success,
            "errors": result.errors
        })

    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)


# ============================================================================
# Resolution
# ============================================================================

def resolve_experiments(
    requested: List[str],
    all_runs: List[Tuple[str, str, Path]]
) -> Optional[List[Tuple[str, str, Path]]]:
    """Resolve requested experiment names to (deployment, run_id, inventory_path) tuples.

    Accepts bare deployment names (picks latest run) or explicit deployment/run_id.
    Returns None on error (after printing message).
    """
    # Index runs by deployment name
    runs_by_deploy = {}
    for deploy_name, run_id, inv_path in all_runs:
        runs_by_deploy.setdefault(deploy_name, []).append((run_id, inv_path))

    resolved = []
    for exp in requested:
        if "/" in exp:
            # Explicit: "sup-controls/0218"
            deploy_name, run_id = exp.split("/", 1)
            candidates = runs_by_deploy.get(deploy_name, [])
            match = [(r, p) for r, p in candidates if r == run_id]
            if match:
                resolved.append((deploy_name, match[0][0], match[0][1]))
            else:
                print(f"\nERROR: No inventory for '{exp}'.")
                return None
        elif exp in runs_by_deploy:
            # Bare name — pick latest run
            candidates = runs_by_deploy[exp]
            run_id, inv_path = candidates[-1]  # sorted, so last = latest
            if run_id and not _CHILD_MODE:
                print(f"  Resolved {exp} -> {exp}/{run_id}")
            resolved.append((exp, run_id, inv_path))
        elif exp in EXPERIMENTS:
            print(f"\nERROR: '{exp}' is configured but has no inventory.ini.")
            print(f"  Run provisioning first: cd deployments && ./deploy spinup {exp}")
            return None
        else:
            available = sorted(runs_by_deploy.keys())
            print(f"\nERROR: Unknown deployment '{exp}'.")
            print(f"  Configured: {list(EXPERIMENTS.keys())}")
            print(f"  With inventory: {available}")
            return None

    return resolved


# ============================================================================
# Main
# ============================================================================

def main():
    experiment_names = ", ".join(EXPERIMENTS.keys())
    parser = argparse.ArgumentParser(
        description='Collect SUP JSONL logs from remote VMs into DuckDB',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Configured: {experiment_names}

DB naming: {{deployment}}-{{run_id}}.duckdb (e.g., sup-controls-0218.duckdb)

Examples:
    python log_retrieval/collect_sup_logs.py sup-controls           # Latest run
    python log_retrieval/collect_sup_logs.py sup-controls/0218      # Specific run
    python log_retrieval/collect_sup_logs.py --all                  # All with inventory
    python log_retrieval/collect_sup_logs.py --list                 # Show configurations
    python log_retrieval/collect_sup_logs.py --dry-run sup-controls # Preview only
        """
    )
    parser.add_argument('experiments', nargs='*', help='Deployment names to collect (e.g., sup-controls)')
    parser.add_argument('--all', action='store_true', help='Collect from all deployments with inventory')
    parser.add_argument('--list', action='store_true', help='List configured experiments and exit')
    parser.add_argument('--dry-run', action='store_true', help='Preview without collecting')
    parser.add_argument('--parallel', type=int, default=8, help='Parallel SSH connections')
    parser.add_argument('--skip-load', action='store_true', help='Skip DuckDB loading')
    parser.add_argument('--db-name', type=str, default=None, help='Custom database name (overrides convention)')
    parser.add_argument('--append', action='store_true', help='Append to existing database instead of rebuilding')
    args = parser.parse_args()

    if args.list:
        list_experiments()
        return 0

    _banner("SUP Log Collection")

    # Discover all deployment runs
    all_runs = discover_runs(DEPLOYMENTS_DIR)

    if args.all:
        requested = sorted(set(d for d, _, _ in all_runs))
    elif args.experiments:
        requested = args.experiments
    else:
        print("\nERROR: No deployment specified.")
        print("Usage: python log_retrieval/collect_sup_logs.py sup-controls")
        print("       python log_retrieval/collect_sup_logs.py --all")
        print("       python log_retrieval/collect_sup_logs.py --list")
        return 1

    # Resolve to concrete (deployment, run_id, inventory_path) tuples
    resolved = resolve_experiments(requested, all_runs)
    if resolved is None:
        return 1

    # Show what we're collecting
    if not _CHILD_MODE:
        print(f"\nDeployments to process:")
        for deploy_name, run_id, _ in resolved:
            tag = f"{deploy_name}-{run_id}" if run_id else deploy_name
            cfg = EXPERIMENTS.get(deploy_name)
            if cfg:
                print(f"  {tag}: {cfg.description} ({cfg.vm_count} VMs)")
            else:
                print(f"  {tag}: (discovered from inventory)")

    # Setup directories
    collection_date = datetime.now().strftime("%Y-%m-%d")
    raw_dir = BASE_OUTPUT_DIR / "raw" / collection_date

    # DB naming: {deployment}-{run_id}.duckdb
    if args.db_name:
        db_name = args.db_name if args.db_name.endswith('.duckdb') else f"{args.db_name}.duckdb"
    elif len(resolved) == 1:
        deploy_name, run_id, _ = resolved[0]
        if run_id:
            db_name = f"{deploy_name}-{run_id}.duckdb"
        else:
            db_name = f"{deploy_name}.duckdb"
    else:
        # Multiple deployments — join names
        names = sorted(set(d for d, _, _ in resolved))
        db_name = f"{'_'.join(names)}.duckdb"

    db_path = BASE_OUTPUT_DIR / db_name
    manifest_path = BASE_OUTPUT_DIR / db_name.replace('.duckdb', '_manifest.json')

    rebuild = not args.append

    if not _CHILD_MODE:
        print(f"\nDatabase: {db_path}")
        if db_path.exists():
            print(f"  Status: EXISTS ({'will rebuild' if rebuild else 'will append'})")

    # Clean existing DB if rebuilding
    if rebuild and not args.dry_run:
        for p in (db_path, manifest_path):
            if p.exists():
                p.unlink()

    if not args.dry_run:
        BASE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)

    # Collect logs from VMs
    all_results: List[CollectionResult] = []
    vm_info_map: Dict[str, VMInfo] = {}

    _section("Collecting JSONL logs from VMs")

    for deploy_name, run_id, inv_path in resolved:
        tag = f"{deploy_name}-{run_id}" if run_id else deploy_name
        vms = parse_inventory(inv_path)

        _out(f"[{tag}] Found {len(vms)} VMs")

        if not vms:
            continue

        for vm in vms:
            vm_info_map[f"{tag}:{vm.hostname}"] = vm

        with ThreadPoolExecutor(max_workers=args.parallel) as executor:
            futures = {
                executor.submit(collect_logs_from_vm, vm, tag, raw_dir, args.dry_run): vm
                for vm in vms
            }

            for future in as_completed(futures):
                result = future.result()
                all_results.append(result)

                status = "OK" if result.success else "FAILED"
                events_str = f"{result.events_collected:,} events" if result.events_collected else "no JSONL"
                _out(f"  {result.vm.hostname}: {status} ({events_str})")

                if result.errors:
                    for err in result.errors:
                        _out(f"    ERROR: {err}")

    # Summary
    total_events_collected = sum(r.events_collected for r in all_results)
    successful_vms = sum(1 for r in all_results if r.success)

    if _CHILD_MODE:
        _out(f"{successful_vms}/{len(all_results)} VMs, {total_events_collected:,} events collected")
    else:
        _section("Collection Summary")
        print(f"  VMs processed: {len(all_results)}")
        print(f"  VMs successful: {successful_vms}")
        print(f"  Events collected: {total_events_collected:,}")

    if args.dry_run:
        print("\n[DRY RUN] No files were actually collected.")
        return 0

    if total_events_collected == 0:
        print("\nNo JSONL events found. Nothing to load.")
        return 0

    if args.skip_load:
        print("\n[--skip-load] Skipping DuckDB loading.")
        return 0

    # Load into DuckDB (directly on NFS)
    _section("Loading into DuckDB")

    conn = init_database(db_path)

    # Build experiment tags for loading
    exp_tags = [f"{d}-{r}" if r else d for d, r, _ in resolved]

    if not _CHILD_MODE:
        print(f"\n  Loading JSONL events...")
    total_events = load_events_to_duckdb(conn, raw_dir, exp_tags, vm_info_map)
    _out(f"  Events loaded: {total_events:,}")

    create_analysis_views(conn)
    conn.close()

    # Write manifest
    write_manifest(manifest_path, all_results, collection_date, total_events)

    # Final summary
    if _CHILD_MODE:
        _out(f"  Database: {db_path.name} ({total_events:,} events)")
    else:
        _section("Collection Complete!")
        print(f"  Database: {db_path}")
        print(f"  Events: {total_events:,}")
        print(f"  Manifest: {manifest_path}")
        print(f"\nViews available:")
        print(f"  workflow_analysis  - Workflows with categories")
        print(f"  llm_analysis       - LLM events with tokens/sec")
        print(f"  llm_performance    - Aggregate LLM metrics by model/SUP")
        print(f"  session_summary    - Session-level metrics")
        print(f"\nExample queries:")
        print(f"  duckdb {db_path}")
        print(f"  SELECT sup_behavior, event_type, COUNT(*) FROM events GROUP BY 1, 2 ORDER BY 1, 3 DESC;")
        print(f"  SELECT * FROM llm_performance;")

    return 0


if __name__ == '__main__':
    sys.exit(main())
