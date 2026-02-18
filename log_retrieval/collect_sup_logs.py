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
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import duckdb
except ImportError as e:
    print(f"ERROR: Missing dependency: {e}. Run: pip install duckdb")
    sys.exit(1)


# ============================================================================
# Configuration
# ============================================================================

BASE_OUTPUT_DIR = Path("/mnt/AXES2U1/SUP_LOGS")
DEPLOYMENTS_DIR = Path("/home/ubuntu/RUSE/deployments")
REMOTE_LOG_BASE = "/opt/ruse/deployed_sups"
SSH_OPTIONS = "-o ProxyJump=axes -o StrictHostKeyChecking=no -o ConnectTimeout=30 -o BatchMode=yes"
SSH_USER = "ubuntu"


# ============================================================================
# EXPERIMENT CONFIGURATIONS
# ============================================================================
# Keys MUST match deployment directory names under deployments/.
# This is metadata only — discovery comes from inventory.ini files.

@dataclass
class ExperimentConfig:
    """Configuration for a log collection experiment."""
    name: str
    description: str
    vm_count: int
    behaviors: List[str]


EXPERIMENTS = {
    "sup-controls": ExperimentConfig(
        name="sup-controls",
        description="Baseline controls across hardware tiers (15 VMs, V100/RTX/CPU)",
        vm_count=15,
        behaviors=[
            # Controls (non-GPU)
            "C0", "M0", "M1",
            # V100 baselines
            "B0.llama", "B0.gemma", "S0.llama", "S0.gemma",
            # CPU baselines (no GPU)
            "B0C.llama", "B0C.gemma", "S0C.llama", "S0C.gemma",
            # RTX baselines (RTX 2080 Ti-A)
            "B0R.llama", "B0R.gemma", "S0R.llama", "S0R.gemma",
        ],
    ),
}


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
class EventMetadata:
    """Metadata to enrich events during loading."""
    experiment_name: str
    vm_hostname: str
    vm_ip: str
    sup_behavior: str
    sup_flavor: str
    source_file: str
    collection_timestamp: datetime


@dataclass
class CollectionResult:
    """Result of collecting logs from a single VM."""
    experiment: str
    vm: VMInfo
    files_collected: int = 0
    events_collected: int = 0
    errors: List[str] = field(default_factory=list)
    success: bool = False


# ============================================================================
# DuckDB Schema - Simplified for JSONL only
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
    line_number          INTEGER,
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
# SSH/SCP Operations
# ============================================================================

def ssh_command(ip: str, remote_cmd: str, timeout: int = 60) -> Tuple[bool, str]:
    """Execute SSH command and return (success, output)."""
    cmd = f"ssh {SSH_OPTIONS} {SSH_USER}@{ip} '{remote_cmd}'"
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "SSH timeout"
    except Exception as e:
        return False, str(e)


def collect_logs_from_vm(
    vm: VMInfo,
    experiment: str,
    output_dir: Path,
    dry_run: bool = False
) -> CollectionResult:
    """Collect JSONL logs from a VM via rsync (single SSH connection)."""
    result = CollectionResult(experiment=experiment, vm=vm)

    remote_log_dir = f"{REMOTE_LOG_BASE}/{vm.sup_behavior}/logs/"

    if dry_run:
        list_cmd = f"ls -1 {remote_log_dir}*.jsonl 2>/dev/null || echo ''"
        success, output = ssh_command(vm.ip, list_cmd)
        if not success:
            result.success = True  # No logs dir is not an error
            return result
        remote_files = [
            f for f in output.strip().split('\n')
            if f.strip() and f.endswith('.jsonl') and 'latest.jsonl' not in f
        ]
        result.files_collected = len(remote_files)
        result.success = True
        return result

    # Create output directory
    vm_output_dir = output_dir / experiment / vm.hostname
    vm_output_dir.mkdir(parents=True, exist_ok=True)

    # Single rsync: 1 SSH connection instead of N separate SCP calls
    rsync_cmd = (
        f'rsync -az '
        f'--exclude="latest.jsonl" --include="*.jsonl" --exclude="*" '
        f'-e "ssh {SSH_OPTIONS}" '
        f'{SSH_USER}@{vm.ip}:{remote_log_dir} {vm_output_dir}/'
    )

    try:
        proc = subprocess.run(
            rsync_cmd, shell=True, capture_output=True, text=True, timeout=300
        )
        if proc.returncode == 0:
            result.files_collected = len(list(vm_output_dir.glob("*.jsonl")))
            result.success = True
        elif proc.returncode in (23, 24):
            # 23=partial transfer (source dir may not exist), 24=files vanished
            result.files_collected = len(list(vm_output_dir.glob("*.jsonl")))
            result.success = True
        else:
            result.errors.append(f"rsync failed (rc={proc.returncode}): {proc.stderr.strip()}")
    except subprocess.TimeoutExpired:
        result.errors.append("rsync timeout")
    except Exception as e:
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
    conn.execute("CREATE SEQUENCE IF NOT EXISTS event_seq START 1")
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
                if not f.is_symlink()
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
                    regexp_extract(filename, '[^/]+$') as source_file,
                    '{collection_ts}'::TIMESTAMP as collection_timestamp,
                    NULL::INTEGER as line_number,
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
                    filename=true,
                    ignore_errors=true
                )
            """

            before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            conn.execute(query)
            after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            loaded = after - before
            event_id += loaded
            total_loaded += loaded

            if loaded > 0:
                print(f"    {vm_dir.name}: {loaded:,} events")

    print(f"  Total: {total_loaded:,} events")
    return total_loaded


def create_analysis_views(conn: duckdb.DuckDBPyConnection):
    """Create analysis views for querying."""

    # Workflow analysis view
    print("  Creating workflow_analysis view...")
    conn.execute("""
        CREATE OR REPLACE VIEW workflow_analysis AS
        SELECT
            id, timestamp, session_id, agent_type, event_type, workflow,
            details, experiment_name, vm_hostname, sup_behavior, sup_flavor,
            duration_ms, success, error_message, model,
            -- Categorize workflows
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

    # LLM analysis view
    print("  Creating llm_analysis view...")
    conn.execute("""
        CREATE OR REPLACE VIEW llm_analysis AS
        SELECT
            id, timestamp, session_id, agent_type, event_type, workflow,
            experiment_name, vm_hostname, sup_behavior, model,
            duration_ms, success, error_message, action,
            input_tokens, output_tokens, total_tokens, llm_output,
            -- Tokens per second
            CASE
                WHEN event_type = 'llm_response' AND duration_ms > 0 AND output_tokens IS NOT NULL
                THEN ROUND(output_tokens * 1000.0 / duration_ms, 2)
                ELSE NULL
            END as tokens_per_second
        FROM events
        WHERE event_type IN ('llm_request', 'llm_response', 'llm_error')
    """)

    # LLM performance summary view
    print("  Creating llm_performance view...")
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

    # Session summary view
    print("  Creating session_summary view...")
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
# Pre-flight Checks
# ============================================================================

def preflight_checks(dry_run: bool = False) -> List[str]:
    """Run pre-flight checks. Returns list of issues."""
    issues = []

    if not dry_run:
        if not BASE_OUTPUT_DIR.parent.exists():
            issues.append(f"NFS mount not accessible: {BASE_OUTPUT_DIR.parent}")
        else:
            test_file = BASE_OUTPUT_DIR.parent / ".write_test"
            try:
                test_file.touch()
                test_file.unlink()
            except Exception as e:
                issues.append(f"NFS mount not writable: {e}")

    result = subprocess.run(
        "ssh -o ConnectTimeout=10 -o BatchMode=yes axes echo ok",
        shell=True, capture_output=True, timeout=15
    )
    if result.returncode != 0:
        issues.append("Cannot SSH to axes jump host")

    all_runs = discover_runs(DEPLOYMENTS_DIR)
    if not all_runs:
        issues.append(f"No deployments with inventory.ini found in {DEPLOYMENTS_DIR}")

    return issues


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
            "total_files": sum(r.files_collected for r in results),
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
            "files_collected": result.files_collected,
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
            if run_id:
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
    parser.add_argument('--clean', action='store_true', default=True, help='Delete and rebuild database (default)')
    parser.add_argument('--append', action='store_true', help='Append to existing database instead of rebuilding')
    args = parser.parse_args()

    if args.list:
        list_experiments()
        return 0

    print("=" * 60)
    print("SUP Log Collection")
    print("=" * 60)

    # Pre-flight checks
    print("\nRunning pre-flight checks...")
    issues = preflight_checks(args.dry_run)
    if issues:
        print("\nPre-flight checks FAILED:")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    print("  All checks passed!")

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

    # --append disables the default --clean behavior
    clean = args.clean and not args.append

    print(f"\nDatabase: {db_path}")
    if db_path.exists():
        if clean:
            print(f"  Status: EXISTS (will rebuild)")
        else:
            print(f"  Status: EXISTS (will append)")

    # Handle --clean (default)
    if clean and not args.dry_run:
        if db_path.exists():
            print(f"\n[--clean] Deleting: {db_path}")
            db_path.unlink()
        if manifest_path.exists():
            manifest_path.unlink()
        local_tmp = Path("/tmp") / db_name
        if local_tmp.exists():
            local_tmp.unlink()

    if not args.dry_run:
        BASE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1: Collect logs
    all_results: List[CollectionResult] = []
    vm_info_map: Dict[str, VMInfo] = {}

    print(f"\n{'='*60}")
    print("Phase 1: Collecting JSONL logs from VMs")
    print("=" * 60)

    for deploy_name, run_id, inv_path in resolved:
        tag = f"{deploy_name}-{run_id}" if run_id else deploy_name
        vms = parse_inventory(inv_path)

        print(f"\n[{tag}] Found {len(vms)} VMs")

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
                files_str = f"{result.files_collected} files" if result.files_collected else "no JSONL"
                print(f"  {result.vm.hostname}: {status} ({files_str})")

                if result.errors:
                    for err in result.errors:
                        print(f"    ERROR: {err}")

    # Summary
    total_files = sum(r.files_collected for r in all_results)
    successful_vms = sum(1 for r in all_results if r.success)

    print(f"\n{'='*60}")
    print("Collection Summary")
    print("=" * 60)
    print(f"  VMs processed: {len(all_results)}")
    print(f"  VMs successful: {successful_vms}")
    print(f"  JSONL files collected: {total_files}")

    if args.dry_run:
        print("\n[DRY RUN] No files were actually collected.")
        return 0

    if total_files == 0:
        print("\nNo JSONL files found. Nothing to load.")
        return 0

    if args.skip_load:
        print("\n[--skip-load] Skipping DuckDB loading.")
        return 0

    # Phase 2: Load into DuckDB
    print(f"\n{'='*60}")
    print("Phase 2: Loading into DuckDB")
    print("=" * 60)

    import shutil
    local_db_path = Path("/tmp") / db_name
    print(f"  Building locally: {local_db_path}")
    print(f"  Final destination: {db_path}")

    conn = init_database(local_db_path)

    # Build experiment tags for loading
    exp_tags = [f"{d}-{r}" if r else d for d, r, _ in resolved]

    print("\n  Loading JSONL events...")
    total_events = load_events_to_duckdb(conn, raw_dir, exp_tags, vm_info_map)
    print(f"  Events loaded: {total_events:,}")

    print("\n  Creating analysis views...")
    create_analysis_views(conn)

    # Copy to NFS
    conn.close()
    print(f"\n  Copying to NFS: {db_path}")
    shutil.copy2(local_db_path, db_path)
    local_db_path.unlink()

    # Write manifest
    write_manifest(manifest_path, all_results, collection_date, total_events)
    print(f"  Manifest: {manifest_path}")

    # Final summary
    print(f"\n{'='*60}")
    print("Collection Complete!")
    print("=" * 60)
    print(f"  Database: {db_path}")
    print(f"  Events: {total_events:,}")
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
