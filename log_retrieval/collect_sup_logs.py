#!/usr/bin/env python3
"""
SUP Log Collection System (Simplified)

Collects JSONL logs from remote SUP VMs and loads them into DuckDB.
Focuses only on structured JSONL logs from AgentLogger - ignores systemd output.

Usage:
    python log_retrieval/collect_sup_logs.py                    # All experiments
    python log_retrieval/collect_sup_logs.py --experiments exp-2
    python log_retrieval/collect_sup_logs.py --dry-run          # Preview only
"""

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

try:
    import duckdb
    import pandas as pd
except ImportError as e:
    print(f"ERROR: Missing dependency: {e}. Run: pip install duckdb pandas")
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
    """Find all experiments with inventory.ini files."""
    experiments = []
    for path in deployments_dir.iterdir():
        if path.is_dir() and (path / "inventory.ini").exists():
            if path.name not in ("playbooks",):
                experiments.append(path.name)
    return sorted(experiments)


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


def scp_file(ip: str, remote_path: str, local_path: Path, timeout: int = 300) -> bool:
    """SCP a file from remote to local."""
    cmd = f"scp {SSH_OPTIONS} {SSH_USER}@{ip}:{remote_path} {local_path}"
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout)
        return result.returncode == 0
    except Exception:
        return False


def collect_logs_from_vm(
    vm: VMInfo,
    experiment: str,
    output_dir: Path,
    dry_run: bool = False
) -> CollectionResult:
    """SSH to VM and collect JSONL logs only."""
    result = CollectionResult(experiment=experiment, vm=vm)

    # Only look for .jsonl files (not .log systemd files)
    remote_log_dir = f"{REMOTE_LOG_BASE}/{vm.sup_behavior}/logs"
    list_cmd = f"ls -1 {remote_log_dir}/*.jsonl 2>/dev/null || echo ''"
    success, output = ssh_command(vm.ip, list_cmd)

    if not success:
        result.errors.append(f"SSH failed: {output}")
        return result

    # Parse file list - only .jsonl files
    remote_files = [
        f.strip() for f in output.strip().split('\n')
        if f.strip() and f.endswith('.jsonl')
    ]

    if not remote_files:
        result.success = True  # No logs is not an error
        return result

    # Create output directory
    vm_output_dir = output_dir / experiment / vm.hostname
    if not dry_run:
        vm_output_dir.mkdir(parents=True, exist_ok=True)

    # Copy each file
    for remote_file in remote_files:
        filename = Path(remote_file).name

        # Skip symlinks (latest.jsonl)
        if filename == 'latest.jsonl':
            continue

        if dry_run:
            result.files_collected += 1
            continue

        local_path = vm_output_dir / filename
        if scp_file(vm.ip, remote_file, local_path):
            result.files_collected += 1
        else:
            result.errors.append(f"SCP failed: {filename}")

    result.success = len(result.errors) == 0 or result.files_collected > 0
    return result


# ============================================================================
# JSONL Parsing
# ============================================================================

def parse_jsonl_file(
    file_path: Path,
    metadata: EventMetadata
) -> Generator[Dict[str, Any], None, None]:
    """Parse JSONL file and yield enriched events."""
    with open(file_path, 'r') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  WARNING: {file_path.name}:{line_num}: Invalid JSON: {e}")
                continue

            # Enrich with metadata
            event['experiment_name'] = metadata.experiment_name
            event['vm_hostname'] = metadata.vm_hostname
            event['vm_ip'] = metadata.vm_ip
            event['sup_behavior'] = metadata.sup_behavior
            event['sup_flavor'] = metadata.sup_flavor
            event['source_file'] = metadata.source_file
            event['collection_timestamp'] = metadata.collection_timestamp.isoformat()
            event['line_number'] = line_num

            # Extract common fields from details for indexing
            details = event.get('details', {})
            if isinstance(details, dict):
                event['duration_ms'] = details.get('duration_ms')
                event['success'] = details.get('success')
                event['error_message'] = details.get('error') or details.get('message')
                event['model'] = details.get('model')
                event['action'] = details.get('action')
                event['category'] = details.get('category')
                event['step_name'] = details.get('step_name')
                event['status'] = details.get('status')

                # LLM-specific fields
                tokens = details.get('tokens', {})
                if isinstance(tokens, dict):
                    event['input_tokens'] = tokens.get('input')
                    event['output_tokens'] = tokens.get('output')
                    event['total_tokens'] = tokens.get('total')
                else:
                    event['input_tokens'] = None
                    event['output_tokens'] = None
                    event['total_tokens'] = None
                event['llm_output'] = details.get('output')
            else:
                # Set all extracted fields to None
                for field in ['duration_ms', 'success', 'error_message', 'model', 'action',
                              'category', 'step_name', 'status', 'input_tokens',
                              'output_tokens', 'total_tokens', 'llm_output']:
                    event[field] = None

            yield event


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
    """Load JSONL events into DuckDB."""
    collection_ts = datetime.now()
    total_loaded = 0

    # Get max existing ID
    try:
        result = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
        event_id = result[0] if result else 0
    except Exception:
        event_id = 0

    batch_size = 50000
    rows = []

    for experiment in experiments:
        exp_dir = raw_dir / experiment
        if not exp_dir.exists():
            continue

        for vm_dir in exp_dir.iterdir():
            if not vm_dir.is_dir():
                continue

            vm_key = f"{experiment}:{vm_dir.name}"
            vm = vm_info_map.get(vm_key)

            # Process JSONL files
            for log_file in vm_dir.glob("*.jsonl"):
                if log_file.is_symlink():
                    continue

                metadata = EventMetadata(
                    experiment_name=experiment,
                    vm_hostname=vm_dir.name,
                    vm_ip=vm.ip if vm else "",
                    sup_behavior=vm.sup_behavior if vm else "",
                    sup_flavor=vm.sup_flavor if vm else "",
                    source_file=log_file.name,
                    collection_timestamp=collection_ts
                )

                for event in parse_jsonl_file(log_file, metadata):
                    event_id += 1
                    rows.append({
                        'id': event_id,
                        'timestamp': event.get('timestamp'),
                        'session_id': event.get('session_id'),
                        'agent_type': event.get('agent_type'),
                        'event_type': event.get('event_type'),
                        'workflow': event.get('workflow'),
                        'details': json.dumps(event.get('details')) if event.get('details') else None,
                        'experiment_name': event.get('experiment_name'),
                        'vm_hostname': event.get('vm_hostname'),
                        'vm_ip': event.get('vm_ip'),
                        'sup_behavior': event.get('sup_behavior'),
                        'sup_flavor': event.get('sup_flavor'),
                        'source_file': event.get('source_file'),
                        'collection_timestamp': event.get('collection_timestamp'),
                        'line_number': event.get('line_number'),
                        'duration_ms': event.get('duration_ms'),
                        'success': event.get('success'),
                        'error_message': event.get('error_message'),
                        'model': event.get('model'),
                        'action': event.get('action'),
                        'category': event.get('category'),
                        'step_name': event.get('step_name'),
                        'status': event.get('status'),
                        'input_tokens': event.get('input_tokens'),
                        'output_tokens': event.get('output_tokens'),
                        'total_tokens': event.get('total_tokens'),
                        'llm_output': event.get('llm_output')
                    })

                    if len(rows) >= batch_size:
                        df = pd.DataFrame(rows)
                        conn.execute("INSERT INTO events SELECT * FROM df")
                        total_loaded += len(rows)
                        print(f"  Loaded {total_loaded:,} events...")
                        rows = []

    # Load remaining
    if rows:
        df = pd.DataFrame(rows)
        conn.execute("INSERT INTO events SELECT * FROM df")
        total_loaded += len(rows)

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
    parser = argparse.ArgumentParser(
        description='Collect SUP JSONL logs from remote VMs into DuckDB',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python log_retrieval/collect_sup_logs.py                              # All experiments
    python log_retrieval/collect_sup_logs.py --experiments exp-2          # Single experiment
    python log_retrieval/collect_sup_logs.py --experiments exp-2 --clean  # Delete and rebuild DB
    python log_retrieval/collect_sup_logs.py --dry-run                    # Preview only
        """
    )
    parser.add_argument('--experiments', nargs='+', help='Specific experiments to collect')
    parser.add_argument('--dry-run', action='store_true', help='Preview without collecting')
    parser.add_argument('--parallel', type=int, default=4, help='Parallel SSH connections')
    parser.add_argument('--skip-load', action='store_true', help='Skip DuckDB loading')
    parser.add_argument('--db-name', type=str, default=None, help='Custom database name')
    parser.add_argument('--clean', action='store_true', help='Delete existing database first')
    args = parser.parse_args()

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

    # Discover experiments
    all_experiments = discover_experiments(DEPLOYMENTS_DIR)
    experiments = args.experiments if args.experiments else all_experiments

    for exp in experiments:
        if exp not in all_experiments:
            print(f"\nERROR: Experiment '{exp}' not found. Available: {all_experiments}")
            return 1

    print(f"\nExperiments to process: {experiments}")

    # Setup directories
    collection_date = datetime.now().strftime("%Y-%m-%d")
    raw_dir = BASE_OUTPUT_DIR / "raw" / collection_date

    # Database naming
    if args.db_name:
        db_name = args.db_name if args.db_name.endswith('.duckdb') else f"{args.db_name}.duckdb"
    elif len(experiments) == 1:
        db_name = f"sup-logs-{experiments[0]}.duckdb"
    else:
        db_name = "sup_logs.duckdb"

    db_path = BASE_OUTPUT_DIR / db_name
    manifest_path = BASE_OUTPUT_DIR / db_name.replace('.duckdb', '_manifest.json')

    print(f"\nDatabase: {db_path}")
    if db_path.exists():
        print(f"  Status: EXISTS (will append)")
        if not args.clean:
            print(f"  Tip: Use --clean to rebuild from scratch")

    # Handle --clean
    if args.clean and not args.dry_run:
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
