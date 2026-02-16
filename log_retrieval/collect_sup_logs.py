#!/usr/bin/env python3
"""
SUP Log Collection System

Collects JSONL logs from remote SUP VMs and loads them into DuckDB.
Focuses only on structured JSONL logs from AgentLogger - ignores systemd output.

Usage:
    python log_retrieval/collect_sup_logs.py exp-3              # Single experiment
    python log_retrieval/collect_sup_logs.py exp-2 exp-3        # Multiple experiments
    python log_retrieval/collect_sup_logs.py --all              # All with inventory
    python log_retrieval/collect_sup_logs.py --list             # Show configurations
    python log_retrieval/collect_sup_logs.py --dry-run exp-3    # Preview only
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
DEPLOYMENTS_DIR = Path("/home/ubuntu/DOLOS-DEPLOY/deployments")
REMOTE_LOG_BASE = "/opt/dolos-deploy/deployed_sups"
SSH_OPTIONS = "-o ProxyJump=axes -o StrictHostKeyChecking=no -o ConnectTimeout=30 -o BatchMode=yes"
SSH_USER = "ubuntu"


# ============================================================================
# EXPERIMENT CONFIGURATIONS
# ============================================================================
# Each experiment defines a DOLOS deployment to collect logs from.
# Usage: python log_retrieval/collect_sup_logs.py exp-3
#        python log_retrieval/collect_sup_logs.py --experiments exp-2 exp-3

@dataclass
class ExperimentConfig:
    """Configuration for a log collection experiment."""
    name: str
    description: str
    vm_count: int
    behaviors: List[str]
    db_name: Optional[str] = None  # Default: sup-logs-{name}.duckdb


EXPERIMENTS = {
    "test": ExperimentConfig(
        name="test",
        description="Quick log generation test (M1, B1a.llama, S1a.llama)",
        vm_count=3,
        behaviors=["M1", "B1a.llama", "S1a.llama"],
    ),
    "exp-1": ExperimentConfig(
        name="exp-1",
        description="Pre-PHASE experimental deployment (19 VMs, V100/RTX mix)",
        vm_count=19,
        behaviors=[
            "M1", "M2a.llama", "M2b.gemma", "M2c.deepseek",
            "M3a.llama", "M3b.gemma", "M3c.deepseek",
            "B1a.llama", "B1b.gemma", "B1c.deepseek",
            "S1a.llama", "S1b.gemma", "S1c.deepseek",
            "B1a.llama", "B1b.gemma", "B1c.deepseek",  # RTX A duplicates
            "S1a.llama", "S1b.gemma", "S1c.deepseek",
        ],
    ),
    "exp-2": ExperimentConfig(
        name="exp-2",
        description="Simplified architecture deployment (38 VMs, Brain+Content+Model)",
        vm_count=38,
        behaviors=[
            # Controls
            "C0", "M0",
            # MCHP
            "M1", "M1a.llama", "M1b.gemma", "M1c.deepseek",
            "M2a.llama", "M2b.gemma", "M2c.deepseek",
            # BrowserUse
            "B1a.llama", "B1b.gemma", "B1c.deepseek",
            "B2a.llama", "B2b.gemma", "B2c.deepseek",
            # SmolAgents
            "S1a.llama", "S1b.gemma", "S1c.deepseek",
            "S2a.llama", "S2b.gemma", "S2c.deepseek",
            # CPU variants
            "BC1a.llama", "BC1b.gemma", "BC1c.deepseek",
            "BC1d.lfm", "BC1e.ministral", "BC1f.qwen",
            "SC1a.llama", "SC1b.gemma", "SC1c.deepseek",
            "SC1d.lfm", "SC1e.ministral", "SC1f.qwen",
        ],
    ),
    "exp-3": ExperimentConfig(
        name="exp-3",
        description="Calibrated PHASE timing (25 VMs, semester profiles)",
        vm_count=25,
        behaviors=[
            # Controls
            "C0", "M0",
            # MCHP (no LLM)
            "M1", "M2", "M3", "M4",
            # BrowserUse
            "B1.llama", "B1.gemma", "B2.llama", "B2.gemma",
            "B3.llama", "B3.gemma", "B4.llama", "B4.gemma",
            # SmolAgents
            "S1.llama", "S1.gemma", "S2.llama", "S2.gemma",
            "S3.llama", "S3.gemma", "S4.llama", "S4.gemma",
        ],
    ),
    "exp-4": ExperimentConfig(
        name="exp-4",
        description="PHASE feedback engine evaluation (25 VMs, feedback configs)",
        vm_count=25,
        behaviors=[
            # Controls
            "C0", "M0",
            # MCHP (no LLM)
            "M1", "M2", "M3", "M4",
            # BrowserUse
            "B1.llama", "B1.gemma", "B2.llama", "B2.gemma",
            "B3.llama", "B3.gemma", "B4.llama", "B4.gemma",
            # SmolAgents
            "S1.llama", "S1.gemma", "S2.llama", "S2.gemma",
            "S3.llama", "S3.gemma", "S4.llama", "S4.gemma",
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

def discover_experiments(deployments_dir: Path) -> List[str]:
    """Find all experiments with inventory.ini files.

    Discovers both legacy (root inventory.ini) and multi-run (runs/<id>/inventory.ini)
    layouts. Multi-run experiments are returned as 'config/run_id'.
    """
    experiments = []
    for path in deployments_dir.iterdir():
        if not path.is_dir() or path.name in ("playbooks", "lib", "logs"):
            continue

        # Check for legacy root inventory
        if (path / "inventory.ini").exists():
            experiments.append(path.name)

        # Check for multi-run inventories in runs/ subdirs
        runs_dir = path / "runs"
        if runs_dir.is_dir():
            for run_dir in sorted(runs_dir.iterdir()):
                if run_dir.is_dir() and (run_dir / "inventory.ini").exists():
                    experiments.append(f"{path.name}/{run_dir.name}")

    return sorted(experiments)


def list_experiments() -> None:
    """Display available experiment configurations."""
    available = discover_experiments(DEPLOYMENTS_DIR)
    print("\nConfigured experiments:")
    print("-" * 70)
    for key, cfg in EXPERIMENTS.items():
        # Check for legacy (bare name) or multi-run (config/run_id) entries
        matching = [a for a in available if a == key or a.startswith(f"{key}/")]
        if matching:
            runs_str = ", ".join(matching)
            status = "READY"
        else:
            runs_str = ""
            status = "NO INVENTORY"
        print(f"  {key:<12} {cfg.vm_count:>2} VMs  [{status:<12}]  {cfg.description}")
        if runs_str and runs_str != key:
            print(f"  {'':12}         runs: {runs_str}")
    # Show any discovered experiments not in EXPERIMENTS
    known_prefixes = set(EXPERIMENTS.keys())
    extra = [e for e in available if e.split("/")[0] not in known_prefixes]
    if extra:
        print(f"\n  (Also discovered: {', '.join(extra)})")
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

    experiments = discover_experiments(DEPLOYMENTS_DIR)
    if not experiments:
        issues.append(f"No experiments with inventory.ini found in {DEPLOYMENTS_DIR}")

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
# Main
# ============================================================================

def main():
    experiment_names = ", ".join(EXPERIMENTS.keys())
    parser = argparse.ArgumentParser(
        description='Collect SUP JSONL logs from remote VMs into DuckDB',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Configured experiments: {experiment_names}

Examples:
    python log_retrieval/collect_sup_logs.py exp-3                        # Single experiment
    python log_retrieval/collect_sup_logs.py exp-2 exp-3                  # Multiple experiments
    python log_retrieval/collect_sup_logs.py --experiments exp-2 --clean  # Delete and rebuild DB
    python log_retrieval/collect_sup_logs.py --all                        # All with inventory
    python log_retrieval/collect_sup_logs.py --list                       # Show configurations
    python log_retrieval/collect_sup_logs.py --dry-run exp-3              # Preview only
        """
    )
    parser.add_argument('experiments', nargs='*', help='Experiments to collect (e.g., exp-2 exp-3)')
    parser.add_argument('--all', action='store_true', help='Collect from all experiments with inventory')
    parser.add_argument('--list', action='store_true', help='List configured experiments and exit')
    parser.add_argument('--dry-run', action='store_true', help='Preview without collecting')
    parser.add_argument('--parallel', type=int, default=8, help='Parallel SSH connections')
    parser.add_argument('--skip-load', action='store_true', help='Skip DuckDB loading')
    parser.add_argument('--db-name', type=str, default=None, help='Custom database name')
    parser.add_argument('--clean', action='store_true', default=True, help='Delete and rebuild database (default)')
    parser.add_argument('--append', action='store_true', help='Append to existing database instead of rebuilding')
    args = parser.parse_args()

    if args.list:
        list_experiments()
        return 0

    print("=" * 60)
    print("SUP Log Collection (JSONL only)")
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

    # Discover experiments with inventory files
    available_experiments = discover_experiments(DEPLOYMENTS_DIR)

    if args.all:
        experiments = available_experiments
    elif args.experiments:
        experiments = args.experiments
    else:
        print("\nERROR: No experiments specified.")
        print("Usage: python log_retrieval/collect_sup_logs.py exp-3")
        print("       python log_retrieval/collect_sup_logs.py --all")
        print("       python log_retrieval/collect_sup_logs.py --list")
        return 1

    # Validate and expand experiments.
    # Bare names like "exp-4" resolve to the most recent multi-run with an inventory
    # (e.g., "exp-4/0216"). Only one run has an inventory at a time — teardown deletes it.
    resolved = []
    for exp in experiments:
        if exp in available_experiments:
            resolved.append(exp)
        else:
            # "exp-4" -> find "exp-4/XXXX" runs with inventory, pick latest
            runs = sorted(a for a in available_experiments if a.startswith(f"{exp}/"))
            if runs:
                pick = runs[-1]
                print(f"  Resolved {exp} -> {pick}")
                resolved.append(pick)
            elif exp in EXPERIMENTS:
                print(f"\nERROR: Experiment '{exp}' is configured but has no inventory.ini.")
                print(f"  Run provisioning first: cd deployments && ./deploy spinup {exp}")
                return 1
            else:
                print(f"\nERROR: Unknown experiment '{exp}'.")
                print(f"  Configured: {list(EXPERIMENTS.keys())}")
                print(f"  With inventory: {available_experiments}")
                return 1
    experiments = resolved

    # Show experiment info
    print(f"\nExperiments to process:")
    for exp in experiments:
        cfg = EXPERIMENTS.get(exp.split("/")[0])
        if cfg:
            print(f"  {exp}: {cfg.description} ({cfg.vm_count} VMs)")
        else:
            print(f"  {exp}: (no configuration - discovered from inventory)")

    # Setup directories
    collection_date = datetime.now().strftime("%Y-%m-%d")
    raw_dir = BASE_OUTPUT_DIR / "raw" / collection_date

    # Database naming: CLI flag > experiment config > convention
    # Use base experiment name (before /run_id) for config lookup and db naming
    if args.db_name:
        db_name = args.db_name if args.db_name.endswith('.duckdb') else f"{args.db_name}.duckdb"
    elif len(experiments) == 1:
        base_name = experiments[0].split("/")[0]
        cfg = EXPERIMENTS.get(base_name)
        if cfg and cfg.db_name:
            db_name = cfg.db_name if cfg.db_name.endswith('.duckdb') else f"{cfg.db_name}.duckdb"
        else:
            db_name = f"sup-logs-{base_name}.duckdb"
    else:
        db_name = "sup_logs.duckdb"

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

    for experiment in experiments:
        # Resolve inventory path: "config/run_id" -> runs/run_id/inventory.ini
        if "/" in experiment:
            config_name, run_id = experiment.split("/", 1)
            inventory_path = DEPLOYMENTS_DIR / config_name / "runs" / run_id / "inventory.ini"
        else:
            inventory_path = DEPLOYMENTS_DIR / experiment / "inventory.ini"
        vms = parse_inventory(inventory_path)

        print(f"\n[{experiment}] Found {len(vms)} VMs")

        if not vms:
            continue

        for vm in vms:
            vm_info_map[f"{experiment}:{vm.hostname}"] = vm

        with ThreadPoolExecutor(max_workers=args.parallel) as executor:
            futures = {
                executor.submit(collect_logs_from_vm, vm, experiment, raw_dir, args.dry_run): vm
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

    print("\n  Loading JSONL events...")
    total_events = load_events_to_duckdb(conn, raw_dir, experiments, vm_info_map)
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
    print(f"  -- Event counts by type and SUP")
    print(f"  SELECT sup_behavior, event_type, COUNT(*) FROM events GROUP BY 1, 2 ORDER BY 1, 3 DESC;")
    print(f"  -- LLM token usage by model")
    print(f"  SELECT * FROM llm_performance;")
    print(f"  -- Session success rates")
    print(f"  SELECT sup_behavior, COUNT(*), SUM(workflows_succeeded), SUM(error_count) FROM session_summary GROUP BY 1;")

    return 0


if __name__ == '__main__':
    sys.exit(main())
