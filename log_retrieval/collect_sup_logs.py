#!/usr/bin/env python3
"""
SUP Log Collection System

Collects JSONL logs from remote SUP VMs, preserves raw files on NFS,
and loads them into a DuckDB database for semantic analysis.

Usage:
    python log_retrieval/collect_sup_logs.py                    # All experiments
    python log_retrieval/collect_sup_logs.py --experiments exp-1
    python log_retrieval/collect_sup_logs.py --dry-run          # Preview only
"""

import argparse
import json
import re
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
# Workflow Normalization
# ============================================================================
# Maps MCHP workflow names (class names and descriptions) to normalized task format
# This allows comparing MCHP workflows with SmolAgents/BrowserUse tasks

# MCHP class name → normalized task description (matching S/B style)
MCHP_WORKFLOW_MAP = {
    # Class names (from JSONL logging)
    'GoogleSearcher': 'Search for something on Google',
    'WebBrowser': 'Visit a random website and browse',
    'YoutubeBrowser': 'Go to YouTube and browse videos',
    'DownloadFiles': 'Download files from the internet',
    'ExecuteCommand': 'Execute shell commands',
    'ListFiles': 'List files in the current directory',
    'OpenOfficeWriter': 'Create a document with OpenOffice Writer',
    'OpenOfficeCalc': 'Create a spreadsheet with OpenOffice Calc',
    'MicrosoftPaint': 'Create an image with MS Paint',
    # Plain text descriptions (from systemd logs) - map to same normalized form
    'Search for something on Google': 'Search for something on Google',
    'Select a random website and browse': 'Visit a random website and browse',
    'Browse Youtube': 'Go to YouTube and browse videos',
    'Download files': 'Download files from the internet',
    'Execute custom commands': 'Execute shell commands',
    'List files in the current directory': 'List files in the current directory',
    'Create documents with Apache OpenOffice Writer (Windows)': 'Create a document with OpenOffice Writer',
    'Create spreadsheets with Apache OpenOffice Calc (Windows)': 'Create a spreadsheet with OpenOffice Calc',
    'Create a blank MS Paint file (Windows)': 'Create an image with MS Paint',
}

# Workflow categories for high-level analysis
# Maps workflow patterns to categories
WORKFLOW_CATEGORIES = {
    'web_search': ['search', 'google', 'duckduckgo', 'bing'],
    'web_browse': ['visit', 'browse', 'go to', 'navigate', 'website', 'reddit', 'wikipedia', 'news'],
    'youtube': ['youtube', 'video'],
    'file_ops': ['download', 'file', 'list files'],
    'shell': ['command', 'shell', 'execute', 'terminal'],
    'document': ['document', 'writer', 'spreadsheet', 'calc', 'paint', 'office'],
    'research': ['weather', 'explain', 'what is', 'what are', 'summarize', 'describe', 'history'],
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
    duration_ms          INTEGER,
    success              BOOLEAN,
    error_message        VARCHAR,
    model                VARCHAR,
    action               VARCHAR,
    source_format        VARCHAR,
    line_number          INTEGER,
    -- New columns for step-based logging framework
    category             VARCHAR,      -- Step category: browser, video, office, shell, etc.
    step_name            VARCHAR,      -- Name of the step (navigate, click, login, etc.)
    status               VARCHAR       -- Step status: start, success, error
);
"""

# Table for raw systemd logs (plain text with timestamps)
CREATE_RAW_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS raw_logs (
    id                   BIGINT,
    timestamp            TIMESTAMP,
    line_number          INTEGER,
    content              VARCHAR,
    log_type             VARCHAR,        -- 'stdout' or 'stderr'
    experiment_name      VARCHAR,
    vm_hostname          VARCHAR,
    vm_ip                VARCHAR,
    sup_behavior         VARCHAR,
    sup_flavor           VARCHAR,
    source_file          VARCHAR,
    collection_timestamp TIMESTAMP
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
CREATE INDEX IF NOT EXISTS idx_events_source_format ON events(source_format);
CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);
CREATE INDEX IF NOT EXISTS idx_events_step_name ON events(step_name);

CREATE INDEX IF NOT EXISTS idx_raw_logs_experiment ON raw_logs(experiment_name);
CREATE INDEX IF NOT EXISTS idx_raw_logs_timestamp ON raw_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_raw_logs_vm ON raw_logs(vm_hostname);
CREATE INDEX IF NOT EXISTS idx_raw_logs_behavior ON raw_logs(sup_behavior);
"""


# ============================================================================
# Inventory Parsing
# ============================================================================

def discover_experiments(deployments_dir: Path) -> List[str]:
    """Find all experiments with inventory.ini files."""
    experiments = []
    for path in deployments_dir.iterdir():
        if path.is_dir() and (path / "inventory.ini").exists():
            # Skip directories that are clearly not experiments
            if path.name not in ("playbooks",):
                experiments.append(path.name)
    return sorted(experiments)


def parse_inventory(inventory_path: Path) -> List[VMInfo]:
    """
    Parse inventory.ini to extract VM information.

    Format:
        [sup_hosts]
        sup-M1-0 ansible_host=10.246.118.157 sup_behavior=M1 sup_flavor=v1.14vcpu.28g
    """
    vms = []
    in_sup_hosts = False

    with open(inventory_path, 'r') as f:
        for line in f:
            line = line.strip()

            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue

            # Check for section headers
            if line.startswith('['):
                in_sup_hosts = line == '[sup_hosts]'
                continue

            # Skip vars section
            if ':vars]' in line or line.endswith(':vars'):
                in_sup_hosts = False
                continue

            # Parse VM lines in [sup_hosts] section
            if in_sup_hosts and '=' in line:
                # Extract hostname (first token)
                parts = line.split()
                if not parts:
                    continue

                hostname = parts[0]

                # Extract ansible variables
                attrs = {}
                for part in parts[1:]:
                    if '=' in part:
                        key, value = part.split('=', 1)
                        attrs[key] = value

                # Create VMInfo if we have required fields
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
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode == 0, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "SSH timeout"
    except Exception as e:
        return False, str(e)


def scp_file(ip: str, remote_path: str, local_path: Path, timeout: int = 300) -> bool:
    """SCP a file from remote to local."""
    cmd = f"scp {SSH_OPTIONS} {SSH_USER}@{ip}:{remote_path} {local_path}"
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            timeout=timeout
        )
        return result.returncode == 0
    except Exception:
        return False


def collect_logs_from_vm(
    vm: VMInfo,
    experiment: str,
    output_dir: Path,
    dry_run: bool = False
) -> CollectionResult:
    """SSH to VM and collect all JSONL logs."""
    result = CollectionResult(experiment=experiment, vm=vm)

    # The log directory is based on sup_behavior
    remote_log_dir = f"{REMOTE_LOG_BASE}/{vm.sup_behavior}/logs"

    # List remote log files - both systemd logs and any JSONL files
    list_cmd = f"ls -1 {remote_log_dir}/*.log {remote_log_dir}/*.jsonl 2>/dev/null || echo ''"
    success, output = ssh_command(vm.ip, list_cmd)

    if not success:
        result.errors.append(f"SSH failed: {output}")
        return result

    # Parse file list - accept both .log and .jsonl files
    remote_files = [
        f.strip() for f in output.strip().split('\n')
        if f.strip() and (f.endswith('.log') or f.endswith('.jsonl'))
    ]

    if not remote_files:
        # No logs is not an error - VM may not have run yet
        result.success = True
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

def parse_timestamp_from_line(line: str) -> Optional[str]:
    """Extract timestamp from log line like '[2025-12-19 21:23:11] ...'"""
    match = re.match(r'\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]', line)
    if match:
        return match.group(1).replace(' ', 'T')
    return None


# ============================================================================
# Raw Log to JSONL Converter
# ============================================================================

# Pre-compiled patterns for converting raw logs to structured events (MUCH faster)
# Format: (compiled_regex, event_type, extractor_func, fast_prefix_check)
_RAW_PATTERNS_SOURCE = [
    # Workflow start: [2025-12-19 21:23:11] Running Task: Browse Youtube
    (r'^\[[\d\-\s:]+\]\s*Running Task:\s*(.+)$', 'workflow_start', lambda m: {'workflow': m.group(1).strip()}, '['),

    # Browser navigation
    (r'^Browsing to\s+(.+)$', 'browser_action', lambda m: {'action': 'navigate', 'target': m.group(1).strip()}, 'Browsing to'),
    (r'^\.\.\.\s*(\d+)\.\s*Navigated to\s+(.+)$', 'browser_action', lambda m: {'action': 'navigate', 'step': int(m.group(1)), 'target': m.group(2).strip()}, '...'),

    # Google search actions
    (r'^\.+\s*Googling:\s*(.+)$', 'browser_action', lambda m: {'action': 'search', 'query': m.group(1).strip()}, '.'),
    (r'^\.+\s*Browsing search results', 'browser_action', lambda m: {'action': 'browse_results'}, '.'),
    (r'^\.+\s*Clicking on search\s*result', 'browser_action', lambda m: {'action': 'click_result'}, '.'),
    (r'^\.+\s*Hovering.*[Ff]eeling [Ll]ucky', 'browser_action', lambda m: {'action': 'feeling_lucky'}, '.'),
    (r'^\.+\s*Navigating and highlighting.*?(\d+)\s*times?', 'browser_action', lambda m: {'action': 'navigate_page', 'clicks': int(m.group(1))}, '.'),
    (r'^\.+\s*successful navigation', 'info', lambda m: {'message': 'navigation_success'}, '.'),
    (r'^\.+\s*X\s*unsuccessful navigation', 'warning', lambda m: {'message': 'navigation_failed'}, '.'),

    # Errors and timeouts
    (r'^Timeout loading\s+(.+?):\s*(.*)$', 'error', lambda m: {'action': 'timeout', 'target': m.group(1).strip(), 'error': m.group(2).strip()}, 'Timeout'),
    (r'^Error loading\s+(.+?):\s*(.*)$', 'error', lambda m: {'action': 'load_error', 'target': m.group(1).strip(), 'error': m.group(2).strip()}, 'Error load'),
    (r'^Error performing google search\s+(.+?):\s*(.*)$', 'error', lambda m: {'action': 'search_error', 'query': m.group(1).strip(), 'error': m.group(2).strip()}, 'Error perf'),

    # Invalid URL
    (r'^\.\.\.\s*(\d+)\.\s*Invalid URL', 'warning', lambda m: {'action': 'invalid_url', 'step': int(m.group(1))}, '...'),
    (r'^\.\.\.\s*(\d+)\.\s*No clickable elements', 'info', lambda m: {'action': 'no_clickables', 'step': int(m.group(1))}, '...'),

    # SmolAgents / BrowserUse patterns
    (r'^Starting (SmolAgents|BrowserUse) agent with model:\s*(.+)$', 'session_start', lambda m: {'agent_framework': m.group(1), 'model': m.group(2).strip()}, 'Starting'),
    (r'^\[[\d\-\s:]+\]\s*Task:\s*(.+)$', 'workflow_start', lambda m: {'workflow': m.group(1).strip()}, '['),
    (r'^Task completed successfully', 'workflow_end', lambda m: {'success': True}, 'Task com'),
    (r'^Error running agent:\s*(.+)$', 'error', lambda m: {'action': 'agent_error', 'error': m.group(1).strip()}, 'Error run'),
]

# Pre-compile all patterns at module load time
RAW_LOG_PATTERNS = [
    (re.compile(pattern), etype, extractor, prefix)
    for pattern, etype, extractor, prefix in _RAW_PATTERNS_SOURCE
]


def convert_raw_line_to_event(
    content: str,
    timestamp: Optional[str],
    metadata: 'EventMetadata',
    line_number: int
) -> Optional[Dict[str, Any]]:
    """
    Convert a raw log line to a structured JSONL event.

    Returns None if the line is empty or just whitespace.
    """
    content = content.strip()
    if not content:
        return None

    # Try each pattern
    event_type = 'info'  # Default
    details = {'raw_content': content}

    for compiled_re, etype, extractor, prefix in RAW_LOG_PATTERNS:
        # Fast prefix check before regex (string ops are ~10x faster)
        if not content.startswith(prefix):
            continue
        match = compiled_re.match(content)
        if match:
            event_type = etype
            details.update(extractor(match))
            break

    # Build the event
    return {
        'timestamp': timestamp,
        'session_id': f"{metadata.vm_hostname}_{metadata.source_file.replace('.log', '')}",
        'agent_type': metadata.sup_behavior,
        'event_type': event_type,
        'workflow': details.get('workflow'),
        'details': details,
        'experiment_name': metadata.experiment_name,
        'vm_hostname': metadata.vm_hostname,
        'vm_ip': metadata.vm_ip,
        'sup_behavior': metadata.sup_behavior,
        'sup_flavor': metadata.sup_flavor,
        'source_file': metadata.source_file,
        'collection_timestamp': metadata.collection_timestamp.isoformat(),
        'source_format': 'converted',  # Mark as converted from raw
        'line_number': line_number,
    }


def parse_raw_log_file_as_events(
    file_path: Path,
    metadata: 'EventMetadata'
) -> Generator[Dict[str, Any], None, None]:
    """Parse raw systemd log file and yield structured JSONL events."""
    current_timestamp = None
    current_workflow = None

    with open(file_path, 'r', errors='replace') as f:
        for line_num, line in enumerate(f, 1):
            content = line.rstrip('\n\r')

            # Try to extract timestamp from line
            ts = parse_timestamp_from_line(content)
            if ts:
                current_timestamp = ts

            # Convert to event
            event = convert_raw_line_to_event(content, current_timestamp, metadata, line_num)
            if event:
                # Track current workflow for context
                if event['event_type'] == 'workflow_start' and event.get('details', {}).get('workflow'):
                    current_workflow = event['details']['workflow']

                # Add workflow context to non-workflow events
                if event['workflow'] is None and current_workflow:
                    event['workflow'] = current_workflow

                yield event


def parse_raw_log_file(
    file_path: Path,
    metadata: EventMetadata
) -> Generator[Dict[str, Any], None, None]:
    """Parse raw systemd log file (plain text with timestamps)."""
    # Determine log type from filename
    log_type = 'stderr' if 'error' in file_path.name.lower() else 'stdout'

    current_timestamp = None
    with open(file_path, 'r', errors='replace') as f:
        for line_num, line in enumerate(f, 1):
            content = line.rstrip('\n\r')

            # Try to extract timestamp from line
            ts = parse_timestamp_from_line(content)
            if ts:
                current_timestamp = ts

            yield {
                'timestamp': current_timestamp,
                'line_number': line_num,
                'content': content,
                'log_type': log_type,
                'experiment_name': metadata.experiment_name,
                'vm_hostname': metadata.vm_hostname,
                'vm_ip': metadata.vm_ip,
                'sup_behavior': metadata.sup_behavior,
                'sup_flavor': metadata.sup_flavor,
                'source_file': metadata.source_file,
                'collection_timestamp': metadata.collection_timestamp.isoformat()
            }


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
            event['source_format'] = 'native'  # Native JSONL from AgentLogger
            event['line_number'] = line_num

            # Extract common fields from details for indexing
            details = event.get('details', {})
            if isinstance(details, dict):
                event['duration_ms'] = details.get('duration_ms')
                event['success'] = details.get('success')
                event['error_message'] = details.get('error') or details.get('message')
                event['model'] = details.get('model')
                event['action'] = details.get('action')
                # New step-based logging fields
                event['category'] = details.get('category')
                event['step_name'] = details.get('step_name')
                event['status'] = details.get('status')
            else:
                event['duration_ms'] = None
                event['success'] = None
                event['error_message'] = None
                event['model'] = None
                event['action'] = None
                event['category'] = None
                event['step_name'] = None
                event['status'] = None

            yield event


def parse_all_logs(
    raw_dir: Path,
    experiments: List[str],
    vm_info_map: Dict[str, VMInfo],
    file_type: str = 'jsonl'  # 'jsonl', 'raw', or 'raw_as_events'
) -> Generator[Dict[str, Any], None, None]:
    """Parse all collected log files and yield enriched events.

    Args:
        file_type: 'jsonl' for structured logs, 'raw' for systemd plain text logs,
                   'raw_as_events' for raw logs converted to JSONL event format
    """
    collection_ts = datetime.now()

    pattern = "*.jsonl" if file_type == 'jsonl' else "*.log"

    for experiment in experiments:
        exp_dir = raw_dir / experiment
        if not exp_dir.exists():
            continue

        for vm_dir in exp_dir.iterdir():
            if not vm_dir.is_dir():
                continue

            # Get VM info from map
            vm_key = f"{experiment}:{vm_dir.name}"
            vm = vm_info_map.get(vm_key)

            for log_file in vm_dir.glob(pattern):
                # Skip symlinks
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

                if file_type == 'jsonl':
                    yield from parse_jsonl_file(log_file, metadata)
                elif file_type == 'raw_as_events':
                    yield from parse_raw_log_file_as_events(log_file, metadata)
                else:
                    yield from parse_raw_log_file(log_file, metadata)


# ============================================================================
# DuckDB Operations
# ============================================================================

def init_database(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Initialize DuckDB database with schema."""
    conn = duckdb.connect(str(db_path))
    conn.execute(CREATE_EVENTS_TABLE)
    conn.execute(CREATE_RAW_LOGS_TABLE)
    conn.execute("CREATE SEQUENCE IF NOT EXISTS raw_log_seq START 1")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS event_seq START 1")
    conn.execute(CREATE_INDEXES)
    return conn


def load_events_to_duckdb(
    conn: duckdb.DuckDBPyConnection,
    events: Generator[Dict[str, Any], None, None],
    batch_size: int = 100000
) -> int:
    """Load JSONL events into DuckDB using bulk insert."""
    total_loaded = 0

    # Get max existing ID
    try:
        result = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
        event_id = result[0] if result else 0
    except Exception:
        event_id = 0

    # Collect all events into lists for bulk insert
    rows = []
    for event in events:
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
            'duration_ms': event.get('duration_ms'),
            'success': event.get('success'),
            'error_message': event.get('error_message'),
            'model': event.get('model'),
            'action': event.get('action'),
            'source_format': event.get('source_format', 'native'),
            'line_number': event.get('line_number'),
            # New step-based logging fields
            'category': event.get('category'),
            'step_name': event.get('step_name'),
            'status': event.get('status')
        })

        if len(rows) >= batch_size:
            # Bulk insert using pandas DataFrame
            df = pd.DataFrame(rows)
            conn.execute("INSERT INTO events SELECT * FROM df")
            total_loaded += len(rows)
            print(f"  Loaded {total_loaded:,} JSONL events...")
            rows = []

    # Load remaining
    if rows:
        df = pd.DataFrame(rows)
        conn.execute("INSERT INTO events SELECT * FROM df")
        total_loaded += len(rows)

    return total_loaded


def load_raw_logs_to_duckdb(
    conn: duckdb.DuckDBPyConnection,
    logs: Generator[Dict[str, Any], None, None],
    batch_size: int = 100000
) -> int:
    """Load raw systemd logs into DuckDB using bulk insert."""
    total_loaded = 0

    # Get max existing ID
    try:
        result = conn.execute("SELECT COALESCE(MAX(id), 0) FROM raw_logs").fetchone()
        log_id = result[0] if result else 0
    except Exception:
        log_id = 0

    # Collect rows for bulk insert
    rows = []
    for log_entry in logs:
        log_id += 1
        rows.append({
            'id': log_id,
            'timestamp': log_entry.get('timestamp'),
            'line_number': log_entry.get('line_number'),
            'content': log_entry.get('content'),
            'log_type': log_entry.get('log_type'),
            'experiment_name': log_entry.get('experiment_name'),
            'vm_hostname': log_entry.get('vm_hostname'),
            'vm_ip': log_entry.get('vm_ip'),
            'sup_behavior': log_entry.get('sup_behavior'),
            'sup_flavor': log_entry.get('sup_flavor'),
            'source_file': log_entry.get('source_file'),
            'collection_timestamp': log_entry.get('collection_timestamp')
        })

        if len(rows) >= batch_size:
            # Bulk insert using pandas DataFrame
            df = pd.DataFrame(rows)
            conn.execute("INSERT INTO raw_logs SELECT * FROM df")
            total_loaded += len(rows)
            print(f"  Loaded {total_loaded:,} raw log lines...")
            rows = []

    # Load remaining
    if rows:
        df = pd.DataFrame(rows)
        conn.execute("INSERT INTO raw_logs SELECT * FROM df")
        total_loaded += len(rows)

    return total_loaded


# ============================================================================
# Pre-flight Checks
# ============================================================================

def preflight_checks(dry_run: bool = False) -> List[str]:
    """Run pre-flight checks. Returns list of issues."""
    issues = []

    # Check NFS mount
    if not dry_run:
        if not BASE_OUTPUT_DIR.parent.exists():
            issues.append(f"NFS mount not accessible: {BASE_OUTPUT_DIR.parent}")
        else:
            # Test write access
            test_file = BASE_OUTPUT_DIR.parent / ".write_test"
            try:
                test_file.touch()
                test_file.unlink()
            except Exception as e:
                issues.append(f"NFS mount not writable: {e}")

    # Check SSH to axes
    result = subprocess.run(
        "ssh -o ConnectTimeout=10 -o BatchMode=yes axes echo ok",
        shell=True,
        capture_output=True,
        timeout=15
    )
    if result.returncode != 0:
        issues.append("Cannot SSH to axes jump host")

    # Check for inventory files
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
        description='Collect SUP logs from remote VMs into DuckDB',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python log_retrieval/collect_sup_logs.py                              # All experiments
    python log_retrieval/collect_sup_logs.py --experiments exp-1          # Specific experiment
    python log_retrieval/collect_sup_logs.py --db-name exp1_2026-01-07    # Custom DB name
    python log_retrieval/collect_sup_logs.py --dry-run                    # Preview only
        """
    )
    parser.add_argument(
        '--experiments', nargs='+',
        help='Specific experiments to collect (default: all)'
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Show what would be collected without actually collecting'
    )
    parser.add_argument(
        '--parallel', type=int, default=4,
        help='Number of parallel SSH connections (default: 4)'
    )
    parser.add_argument(
        '--skip-load', action='store_true',
        help='Skip loading into DuckDB (only collect raw files)'
    )
    parser.add_argument(
        '--db-name', type=str, default='sup_logs.duckdb',
        help='Name for the DuckDB file (default: sup_logs.duckdb)'
    )
    args = parser.parse_args()

    print("=" * 60)
    print("SUP Log Collection System")
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

    # Validate requested experiments exist
    for exp in experiments:
        if exp not in all_experiments:
            print(f"\nERROR: Experiment '{exp}' not found. Available: {all_experiments}")
            return 1

    print(f"\nExperiments to process: {experiments}")

    # Setup directories
    collection_date = datetime.now().strftime("%Y-%m-%d")
    raw_dir = BASE_OUTPUT_DIR / "raw" / collection_date

    # Ensure db name ends with .duckdb
    db_name = args.db_name if args.db_name.endswith('.duckdb') else f"{args.db_name}.duckdb"
    db_path = BASE_OUTPUT_DIR / db_name

    # Manifest named to match db
    manifest_name = db_name.replace('.duckdb', '_manifest.json')
    manifest_path = BASE_OUTPUT_DIR / manifest_name

    if not args.dry_run:
        BASE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)

    # Collect VM info for all experiments
    all_results: List[CollectionResult] = []
    vm_info_map: Dict[str, VMInfo] = {}

    print(f"\n{'='*60}")
    print("Phase 1: Collecting logs from VMs")
    print("=" * 60)

    for experiment in experiments:
        inventory_path = DEPLOYMENTS_DIR / experiment / "inventory.ini"
        vms = parse_inventory(inventory_path)

        print(f"\n[{experiment}] Found {len(vms)} VMs")

        if not vms:
            continue

        # Store VM info for later use
        for vm in vms:
            vm_info_map[f"{experiment}:{vm.hostname}"] = vm

        # Collect from VMs in parallel
        with ThreadPoolExecutor(max_workers=args.parallel) as executor:
            futures = {
                executor.submit(
                    collect_logs_from_vm, vm, experiment, raw_dir, args.dry_run
                ): vm for vm in vms
            }

            for future in as_completed(futures):
                result = future.result()
                all_results.append(result)

                # Print progress
                status = "OK" if result.success else "FAILED"
                files_str = f"{result.files_collected} files" if result.files_collected else "no logs"
                print(f"  {result.vm.hostname}: {status} ({files_str})")

                if result.errors:
                    for err in result.errors:
                        print(f"    ERROR: {err}")

    # Summary of collection phase
    total_files = sum(r.files_collected for r in all_results)
    successful_vms = sum(1 for r in all_results if r.success)

    print(f"\n{'='*60}")
    print("Collection Summary")
    print("=" * 60)
    print(f"  VMs processed: {len(all_results)}")
    print(f"  VMs successful: {successful_vms}")
    print(f"  Total files collected: {total_files}")

    if args.dry_run:
        print("\n[DRY RUN] No files were actually collected.")
        return 0

    if total_files == 0:
        print("\nNo log files found. Nothing to load into DuckDB.")
        return 0

    if args.skip_load:
        print("\n[--skip-load] Skipping DuckDB loading.")
        return 0

    # Phase 2: Load into DuckDB
    print(f"\n{'='*60}")
    print("Phase 2: Loading into DuckDB")
    print("=" * 60)

    # Write to local /tmp first (much faster than NFS), then copy at the end
    import shutil
    local_db_path = Path("/tmp") / db_name
    print(f"  Building locally: {local_db_path}")
    print(f"  Final destination: {db_path}")
    conn = init_database(local_db_path)

    # Collect file paths with metadata (Python just finds files, DuckDB reads them)
    print("\n  Scanning for log files...")
    log_files = []
    for experiment in experiments:
        exp_dir = raw_dir / experiment
        if not exp_dir.exists():
            continue
        for vm_dir in exp_dir.iterdir():
            if not vm_dir.is_dir():
                continue
            vm_key = f"{experiment}:{vm_dir.name}"
            vm = vm_info_map.get(vm_key)
            for log_file in vm_dir.glob("*.log"):
                if log_file.is_symlink():
                    continue
                log_files.append({
                    'file_path': str(log_file),
                    'experiment_name': experiment,
                    'vm_hostname': vm_dir.name,
                    'vm_ip': vm.ip if vm else '',
                    'sup_behavior': vm.sup_behavior if vm else '',
                    'sup_flavor': vm.sup_flavor if vm else '',
                    'source_file': log_file.name,
                    'log_type': 'stderr' if 'error' in log_file.name.lower() else 'stdout'
                })
    print(f"  Found {len(log_files)} log files")

    # Load raw logs using DuckDB's native file reading (FAST!)
    print("\n  Loading raw logs with DuckDB (native file read)...")
    total_raw_logs = 0
    for i, lf in enumerate(log_files):
        # DuckDB reads the file directly - no Python processing!
        # Using newline_split to get each line as a row
        file_path = lf['file_path'].replace("'", "''")  # Escape single quotes
        conn.execute(f"""
            INSERT INTO raw_logs
            SELECT
                nextval('raw_log_seq') as id,
                NULL as timestamp,
                row_number() OVER () as line_number,
                line as content,
                '{lf['log_type']}' as log_type,
                '{lf['experiment_name']}' as experiment_name,
                '{lf['vm_hostname']}' as vm_hostname,
                '{lf['vm_ip']}' as vm_ip,
                '{lf['sup_behavior']}' as sup_behavior,
                '{lf['sup_flavor']}' as sup_flavor,
                '{lf['source_file']}' as source_file,
                CURRENT_TIMESTAMP as collection_timestamp
            FROM (
                SELECT unnest(string_split(content, chr(10))) as line
                FROM read_text('{file_path}')
            )
            WHERE line IS NOT NULL AND trim(line) != ''
        """)
        if (i + 1) % 10 == 0:
            print(f"    Processed {i + 1}/{len(log_files)} files...")

    total_raw_logs = conn.execute("SELECT COUNT(*) FROM raw_logs").fetchone()[0]
    print(f"  Raw log lines loaded: {total_raw_logs:,}")

    total_native_events = 0  # No native JSONL in old experiments

    # Create a VIEW that converts raw_logs to event format using SQL (instant)
    # This handles both legacy plain-text logs AND the new AgentLogger JSONL format
    print("\n  Creating events_from_raw view...")
    conn.execute("""
        CREATE OR REPLACE VIEW events_from_raw AS
        SELECT
            id,
            CASE
                WHEN content LIKE '[____-__-__ __:__:__]%'
                THEN CAST(regexp_extract(content, '\\[(\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2})\\]', 1) AS TIMESTAMP)
                ELSE NULL
            END as timestamp,
            vm_hostname || '_' || replace(source_file, '.log', '') as session_id,
            sup_behavior as agent_type,
            -- Event type detection (includes new step_* and session_* events)
            CASE
                -- Session events
                WHEN content LIKE '%Starting SmolAgents agent%' THEN 'session_start'
                WHEN content LIKE '%Starting BrowserUse agent%' THEN 'session_start'
                WHEN content LIKE '%Session completed successfully%' THEN 'session_success'
                WHEN content LIKE '%Session failed%' OR content LIKE '%Session ended with exception%' THEN 'session_fail'
                -- Workflow events
                WHEN content LIKE '%Running Task:%' THEN 'workflow_start'
                WHEN content LIKE '[%] Task:%' AND content NOT LIKE '%Task completed%' THEN 'workflow_start'
                WHEN content LIKE '%Task completed successfully%' THEN 'workflow_end'
                -- Step events (new logging framework)
                WHEN content LIKE '%step_start%' OR content LIKE '%Step starting:%' THEN 'step_start'
                WHEN content LIKE '%step_success%' OR content LIKE '%Step succeeded:%' THEN 'step_success'
                WHEN content LIKE '%step_error%' OR content LIKE '%Step failed:%' THEN 'step_error'
                -- Legacy browser actions (map to step events for consistency)
                WHEN content LIKE 'Browsing to %' THEN 'step_start'
                WHEN content LIKE '%Navigated to %' THEN 'step_success'
                WHEN content LIKE '%Googling:%' THEN 'step_start'
                WHEN content LIKE '%Browsing search results%' THEN 'step_success'
                -- Errors
                WHEN content LIKE 'Timeout loading%' THEN 'step_error'
                WHEN content LIKE 'Error loading%' THEN 'step_error'
                WHEN content LIKE 'Error running agent%' THEN 'step_error'
                WHEN content LIKE 'Error %' THEN 'step_error'
                ELSE 'info'
            END as event_type,
            -- Workflow extraction
            CASE
                WHEN content LIKE '%Running Task:%'
                THEN trim(regexp_extract(content, 'Running Task:\\s*(.+)$', 1))
                WHEN content LIKE '[%] Task:%' AND content NOT LIKE '%Task completed%'
                THEN trim(regexp_extract(content, 'Task:\\s*(.+)$', 1))
                ELSE NULL
            END as workflow,
            content as raw_content,
            -- Extract success from plain text
            CASE
                WHEN content LIKE '%Task completed successfully%' THEN true
                WHEN content LIKE '%successful navigation%' THEN true
                WHEN content LIKE '%step_success%' OR content LIKE '%Step succeeded%' THEN true
                WHEN content LIKE '%Session completed successfully%' THEN true
                WHEN content LIKE 'Error %' OR content LIKE 'Timeout %' THEN false
                WHEN content LIKE '%unsuccessful%' THEN false
                WHEN content LIKE '%step_error%' OR content LIKE '%Step failed%' THEN false
                WHEN content LIKE '%Session failed%' THEN false
                ELSE NULL
            END as success_extracted,
            -- Extract error message from plain text
            CASE
                WHEN content LIKE 'Error running agent:%'
                THEN trim(regexp_extract(content, 'Error running agent:\\s*(.+)$', 1))
                WHEN content LIKE 'Error loading%:%'
                THEN trim(regexp_extract(content, 'Error loading\\s+.+?:\\s*(.+)$', 1))
                WHEN content LIKE 'Error performing google search%:%'
                THEN trim(regexp_extract(content, 'Error performing google search\\s+.+?:\\s*(.+)$', 1))
                WHEN content LIKE 'Timeout loading%:%'
                THEN trim(regexp_extract(content, 'Timeout loading\\s+.+?:\\s*(.+)$', 1))
                WHEN content LIKE '%Step failed:%'
                THEN trim(regexp_extract(content, 'Step failed:\\s*(.+)$', 1))
                ELSE NULL
            END as error_message_extracted,
            -- Extract model from session_start lines
            CASE
                WHEN content LIKE '%Starting SmolAgents agent with model:%'
                THEN trim(regexp_extract(content, 'with model:\\s*(.+)$', 1))
                WHEN content LIKE '%Starting BrowserUse agent with model:%'
                THEN trim(regexp_extract(content, 'with model:\\s*(.+)$', 1))
                -- For MCHP, extract model hint from sup_behavior (e.g., M2.llama -> llama)
                WHEN sup_behavior LIKE '%.llama%' THEN 'llama3.1:8b'
                WHEN sup_behavior LIKE '%.gemma%' THEN 'gemma3:4b'
                WHEN sup_behavior LIKE '%.deepseek%' THEN 'deepseek-r1:8b'
                ELSE NULL
            END as model_extracted,
            -- Extract action type (for step events)
            CASE
                WHEN content LIKE 'Browsing to %' THEN 'navigate'
                WHEN content LIKE '%Navigated to %' THEN 'navigate'
                WHEN content LIKE '%Googling:%' THEN 'search'
                WHEN content LIKE '%Browsing search results%' THEN 'browse_results'
                WHEN content LIKE '%Clicking on search%' THEN 'click_result'
                WHEN content LIKE '%Feeling lucky%' THEN 'feeling_lucky'
                ELSE NULL
            END as action_extracted,
            -- NEW: Extract step category from content
            CASE
                WHEN content LIKE '%category%browser%' OR content LIKE 'Browsing%' OR content LIKE '%Navigat%'
                     OR content LIKE '%Googling%' OR content LIKE '%search%' THEN 'browser'
                WHEN content LIKE '%category%video%' OR content LIKE '%YouTube%' OR content LIKE '%video%' THEN 'video'
                WHEN content LIKE '%category%office%' OR content LIKE '%OpenOffice%' OR content LIKE '%Writer%'
                     OR content LIKE '%Calc%' OR content LIKE '%Paint%' THEN 'office'
                WHEN content LIKE '%category%shell%' OR content LIKE '%command%' OR content LIKE '%Execute%' THEN 'shell'
                WHEN content LIKE '%category%authentication%' OR content LIKE '%login%' OR content LIKE '%Shibboleth%' THEN 'authentication'
                ELSE 'other'
            END as category_extracted,
            -- NEW: Extract step name from content
            CASE
                WHEN content LIKE 'Browsing to %' THEN 'navigate'
                WHEN content LIKE '%Navigated to %' THEN 'navigate'
                WHEN content LIKE '%Googling:%' THEN 'google_search'
                WHEN content LIKE '%Browsing search results%' THEN 'browse_results'
                WHEN content LIKE '%Clicking%' THEN 'click'
                WHEN content LIKE '%login%' THEN 'login'
                WHEN content LIKE 'Timeout loading%' THEN 'page_load'
                WHEN content LIKE 'Error loading%' THEN 'page_load'
                ELSE NULL
            END as step_name_extracted,
            -- NEW: Extract step status
            CASE
                WHEN content LIKE '%step_start%' OR content LIKE '%Step starting%' OR content LIKE 'Browsing to %'
                     OR content LIKE '%Googling:%' THEN 'start'
                WHEN content LIKE '%step_success%' OR content LIKE '%Step succeeded%' OR content LIKE '%Navigated to %'
                     OR content LIKE '%successful%' THEN 'success'
                WHEN content LIKE '%step_error%' OR content LIKE '%Step failed%' OR content LIKE 'Error %'
                     OR content LIKE 'Timeout %' THEN 'error'
                ELSE NULL
            END as status_extracted,
            experiment_name,
            vm_hostname,
            vm_ip,
            sup_behavior,
            sup_flavor,
            source_file,
            collection_timestamp,
            'converted' as source_format,
            line_number
        FROM raw_logs
        WHERE content IS NOT NULL AND trim(content) != ''
    """)
    print("  View created: events_from_raw")

    # Create unified view combining native + converted
    print("  Creating unified_events view...")
    conn.execute("""
        CREATE OR REPLACE VIEW unified_events AS
        SELECT * FROM events
        UNION ALL
        SELECT
            id, timestamp, session_id, agent_type, event_type, workflow,
            json_object('raw_content', raw_content) as details,
            experiment_name, vm_hostname, vm_ip, sup_behavior, sup_flavor,
            source_file, collection_timestamp,
            NULL as duration_ms,
            success_extracted as success,
            error_message_extracted as error_message,
            model_extracted as model,
            action_extracted as action,
            source_format, line_number,
            -- New columns for step-based logging
            category_extracted as category,
            step_name_extracted as step_name,
            status_extracted as status
        FROM events_from_raw
    """)
    print("  View created: unified_events")

    # Create workflow normalization view with MCHP mapping and categories
    print("  Creating workflow_normalized view...")
    conn.execute("""
        CREATE OR REPLACE VIEW workflow_normalized AS
        SELECT
            *,
            -- Normalize MCHP workflow names to S/B style descriptions
            CASE
                -- MCHP class names (from JSONL)
                WHEN workflow = 'GoogleSearcher' THEN 'Search for something on Google'
                WHEN workflow = 'WebBrowser' THEN 'Visit a random website and browse'
                WHEN workflow = 'YoutubeBrowser' THEN 'Go to YouTube and browse videos'
                WHEN workflow = 'DownloadFiles' THEN 'Download files from the internet'
                WHEN workflow = 'ExecuteCommand' THEN 'Execute shell commands'
                WHEN workflow = 'ListFiles' THEN 'List files in the current directory'
                WHEN workflow = 'OpenOfficeWriter' THEN 'Create a document with OpenOffice Writer'
                WHEN workflow = 'OpenOfficeCalc' THEN 'Create a spreadsheet with OpenOffice Calc'
                WHEN workflow = 'MicrosoftPaint' THEN 'Create an image with MS Paint'
                -- MCHP descriptions (from plain text logs)
                WHEN workflow = 'Search for something on Google' THEN 'Search for something on Google'
                WHEN workflow = 'Select a random website and browse' THEN 'Visit a random website and browse'
                WHEN workflow = 'Browse Youtube' THEN 'Go to YouTube and browse videos'
                WHEN workflow = 'Download files' THEN 'Download files from the internet'
                WHEN workflow = 'Execute custom commands' THEN 'Execute shell commands'
                WHEN workflow = 'List files in the current directory' THEN 'List files in the current directory'
                WHEN workflow LIKE '%OpenOffice Writer%' THEN 'Create a document with OpenOffice Writer'
                WHEN workflow LIKE '%OpenOffice Calc%' THEN 'Create a spreadsheet with OpenOffice Calc'
                WHEN workflow LIKE '%MS Paint%' OR workflow LIKE '%Paint file%' THEN 'Create an image with MS Paint'
                -- S/B tasks pass through as-is (already normalized)
                ELSE workflow
            END as workflow_normalized,
            -- Categorize workflows for high-level analysis
            CASE
                WHEN lower(workflow) LIKE '%search%' OR lower(workflow) LIKE '%google%'
                     OR lower(workflow) LIKE '%duckduckgo%' OR lower(workflow) LIKE '%bing%'
                     OR workflow = 'GoogleSearcher' THEN 'web_search'
                WHEN lower(workflow) LIKE '%youtube%' OR lower(workflow) LIKE '%video%'
                     OR workflow = 'YoutubeBrowser' THEN 'youtube'
                WHEN lower(workflow) LIKE '%download%' OR lower(workflow) LIKE '%file%'
                     OR workflow = 'DownloadFiles' OR workflow = 'ListFiles' THEN 'file_ops'
                WHEN lower(workflow) LIKE '%command%' OR lower(workflow) LIKE '%shell%'
                     OR lower(workflow) LIKE '%execute%' OR workflow = 'ExecuteCommand' THEN 'shell'
                WHEN lower(workflow) LIKE '%document%' OR lower(workflow) LIKE '%writer%'
                     OR lower(workflow) LIKE '%spreadsheet%' OR lower(workflow) LIKE '%calc%'
                     OR lower(workflow) LIKE '%paint%' OR lower(workflow) LIKE '%office%'
                     OR workflow IN ('OpenOfficeWriter', 'OpenOfficeCalc', 'MicrosoftPaint') THEN 'document'
                WHEN lower(workflow) LIKE '%weather%' OR lower(workflow) LIKE '%explain%'
                     OR lower(workflow) LIKE '%what is%' OR lower(workflow) LIKE '%what are%'
                     OR lower(workflow) LIKE '%summarize%' OR lower(workflow) LIKE '%describe%'
                     OR lower(workflow) LIKE '%history%' THEN 'research'
                WHEN lower(workflow) LIKE '%visit%' OR lower(workflow) LIKE '%browse%'
                     OR lower(workflow) LIKE '%go to%' OR lower(workflow) LIKE '%navigate%'
                     OR lower(workflow) LIKE '%website%' OR lower(workflow) LIKE '%reddit%'
                     OR lower(workflow) LIKE '%wikipedia%' OR lower(workflow) LIKE '%news%'
                     OR workflow = 'WebBrowser' THEN 'web_browse'
                ELSE 'other'
            END as workflow_category
        FROM unified_events
        WHERE workflow IS NOT NULL
    """)
    print("  View created: workflow_normalized")

    # Create workflow_durations view that calculates duration from start/end pairs
    print("  Creating workflow_durations view...")
    conn.execute("""
        CREATE OR REPLACE VIEW workflow_durations AS
        WITH workflow_starts AS (
            SELECT
                id,
                timestamp as start_time,
                session_id,
                workflow,
                workflow_normalized,
                workflow_category,
                sup_behavior,
                experiment_name,
                vm_hostname,
                model,
                source_format,
                -- Get the next workflow_end timestamp in the same session
                LEAD(timestamp) OVER (
                    PARTITION BY session_id
                    ORDER BY timestamp, line_number
                ) as potential_end_time,
                LEAD(event_type) OVER (
                    PARTITION BY session_id
                    ORDER BY timestamp, line_number
                ) as next_event_type,
                LEAD(success) OVER (
                    PARTITION BY session_id
                    ORDER BY timestamp, line_number
                ) as next_success
            FROM workflow_normalized
            WHERE event_type = 'workflow_start'
        )
        SELECT
            id,
            start_time,
            CASE
                WHEN next_event_type = 'workflow_end' THEN potential_end_time
                ELSE NULL
            END as end_time,
            session_id,
            workflow,
            workflow_normalized,
            workflow_category,
            sup_behavior,
            experiment_name,
            vm_hostname,
            model,
            source_format,
            -- Calculate duration in milliseconds
            CASE
                WHEN next_event_type = 'workflow_end' AND potential_end_time IS NOT NULL AND start_time IS NOT NULL
                THEN CAST(EXTRACT(EPOCH FROM (potential_end_time - start_time)) * 1000 AS INTEGER)
                ELSE NULL
            END as duration_ms,
            -- Use success from the paired workflow_end event
            CASE
                WHEN next_event_type = 'workflow_end' THEN next_success
                ELSE NULL
            END as success
        FROM workflow_starts
    """)
    print("  View created: workflow_durations")

    # Create step_analysis view for step-level metrics by category
    print("  Creating step_analysis view...")
    conn.execute("""
        CREATE OR REPLACE VIEW step_analysis AS
        SELECT
            id,
            timestamp,
            session_id,
            agent_type,
            event_type,
            workflow,
            experiment_name,
            vm_hostname,
            sup_behavior,
            model,
            source_format,
            -- Step-specific fields
            category,
            step_name,
            status,
            duration_ms,
            success,
            error_message
        FROM unified_events
        WHERE event_type IN ('step_start', 'step_success', 'step_error')
    """)
    print("  View created: step_analysis")

    # Create step_durations view that pairs step_start with step_success/step_error
    print("  Creating step_durations view...")
    conn.execute("""
        CREATE OR REPLACE VIEW step_durations AS
        WITH step_starts AS (
            SELECT
                id,
                timestamp as start_time,
                session_id,
                workflow,
                category,
                step_name,
                sup_behavior,
                experiment_name,
                vm_hostname,
                model,
                source_format,
                -- Get the next step event in the same session
                LEAD(timestamp) OVER (
                    PARTITION BY session_id
                    ORDER BY timestamp, line_number
                ) as potential_end_time,
                LEAD(event_type) OVER (
                    PARTITION BY session_id
                    ORDER BY timestamp, line_number
                ) as next_event_type,
                LEAD(success) OVER (
                    PARTITION BY session_id
                    ORDER BY timestamp, line_number
                ) as next_success,
                LEAD(error_message) OVER (
                    PARTITION BY session_id
                    ORDER BY timestamp, line_number
                ) as next_error
            FROM unified_events
            WHERE event_type = 'step_start'
        )
        SELECT
            id,
            start_time,
            CASE
                WHEN next_event_type IN ('step_success', 'step_error') THEN potential_end_time
                ELSE NULL
            END as end_time,
            session_id,
            workflow,
            category,
            step_name,
            sup_behavior,
            experiment_name,
            vm_hostname,
            model,
            source_format,
            -- Calculate duration in milliseconds
            CASE
                WHEN next_event_type IN ('step_success', 'step_error')
                     AND potential_end_time IS NOT NULL AND start_time IS NOT NULL
                THEN CAST(EXTRACT(EPOCH FROM (potential_end_time - start_time)) * 1000 AS INTEGER)
                ELSE NULL
            END as duration_ms,
            -- Determine success/failure
            CASE
                WHEN next_event_type = 'step_success' THEN true
                WHEN next_event_type = 'step_error' THEN false
                ELSE NULL
            END as success,
            CASE
                WHEN next_event_type = 'step_error' THEN next_error
                ELSE NULL
            END as error_message
        FROM step_starts
    """)
    print("  View created: step_durations")

    # Create session_outcomes view for session-level success/failure tracking
    print("  Creating session_outcomes view...")
    conn.execute("""
        CREATE OR REPLACE VIEW session_outcomes AS
        WITH session_events AS (
            SELECT
                session_id,
                experiment_name,
                vm_hostname,
                sup_behavior,
                model,
                source_format,
                MIN(CASE WHEN event_type = 'session_start' THEN timestamp END) as session_start_time,
                MAX(CASE WHEN event_type = 'session_end' THEN timestamp END) as session_end_time,
                MAX(CASE WHEN event_type = 'session_success' THEN timestamp END) as success_time,
                MAX(CASE WHEN event_type = 'session_fail' THEN timestamp END) as fail_time,
                MAX(CASE WHEN event_type = 'session_fail' THEN error_message END) as fail_error,
                COUNT(*) FILTER (WHERE event_type = 'workflow_start') as workflows_started,
                COUNT(*) FILTER (WHERE event_type = 'workflow_end' AND success = true) as workflows_succeeded,
                COUNT(*) FILTER (WHERE event_type = 'workflow_end' AND success = false) as workflows_failed,
                COUNT(*) FILTER (WHERE event_type = 'step_start') as steps_started,
                COUNT(*) FILTER (WHERE event_type = 'step_success') as steps_succeeded,
                COUNT(*) FILTER (WHERE event_type = 'step_error') as steps_failed,
                COUNT(*) FILTER (WHERE event_type = 'llm_request') as llm_requests,
                COUNT(*) FILTER (WHERE event_type = 'llm_error') as llm_errors
            FROM unified_events
            GROUP BY session_id, experiment_name, vm_hostname, sup_behavior, model, source_format
        )
        SELECT
            session_id,
            experiment_name,
            vm_hostname,
            sup_behavior,
            model,
            source_format,
            session_start_time,
            session_end_time,
            -- Determine session outcome
            CASE
                WHEN success_time IS NOT NULL THEN 'success'
                WHEN fail_time IS NOT NULL THEN 'fail'
                WHEN workflows_failed > 0 OR steps_failed > 0 OR llm_errors > 0 THEN 'fail'
                WHEN workflows_succeeded > 0 OR steps_succeeded > 0 THEN 'success'
                ELSE 'unknown'
            END as outcome,
            fail_error,
            -- Calculate session duration
            CASE
                WHEN session_end_time IS NOT NULL AND session_start_time IS NOT NULL
                THEN CAST(EXTRACT(EPOCH FROM (session_end_time - session_start_time)) * 1000 AS INTEGER)
                ELSE NULL
            END as duration_ms,
            -- Counts
            workflows_started,
            workflows_succeeded,
            workflows_failed,
            steps_started,
            steps_succeeded,
            steps_failed,
            llm_requests,
            llm_errors
        FROM session_events
    """)
    print("  View created: session_outcomes")

    # Create llm_analysis view for LLM event metrics
    print("  Creating llm_analysis view...")
    conn.execute("""
        CREATE OR REPLACE VIEW llm_analysis AS
        SELECT
            id,
            timestamp,
            session_id,
            agent_type,
            event_type,
            workflow,
            experiment_name,
            vm_hostname,
            sup_behavior,
            model,
            source_format,
            duration_ms,
            success,
            error_message,
            action,
            -- Extract LLM-specific fields from details
            CASE
                WHEN details IS NOT NULL THEN json_extract_string(details, '$.tokens.input')
                ELSE NULL
            END as input_tokens,
            CASE
                WHEN details IS NOT NULL THEN json_extract_string(details, '$.tokens.output')
                ELSE NULL
            END as output_tokens,
            CASE
                WHEN details IS NOT NULL THEN json_extract_string(details, '$.fatal')
                ELSE NULL
            END as fatal
        FROM unified_events
        WHERE event_type IN ('llm_request', 'llm_response', 'llm_error')
    """)
    print("  View created: llm_analysis")

    total_converted_events = 0  # Conversion happens at query time now

    total_events = total_native_events + total_converted_events

    # Close connection and copy to NFS
    conn.close()
    print(f"\n  Copying database to NFS: {db_path}")
    shutil.copy2(local_db_path, db_path)
    local_db_path.unlink()
    print(f"  Done!")

    # Generate manifest
    write_manifest(manifest_path, all_results, collection_date, total_events + total_raw_logs)
    print(f"\n  Manifest written: {manifest_path}")

    # Final summary
    print(f"\n{'='*60}")
    print("Collection Complete!")
    print("=" * 60)
    print(f"  Raw logs: {raw_dir}")
    print(f"  Database: {db_path}")
    print(f"  Manifest: {manifest_path}")
    print(f"\nDuckDB tables & views:")
    print(f"  events            - Native JSONL events ({total_native_events:,} rows)")
    print(f"  raw_logs          - Raw systemd logs ({total_raw_logs:,} rows)")
    print(f"  events_from_raw   - VIEW: raw logs as events (with category/step_name/status)")
    print(f"  unified_events    - VIEW: all events combined (native + converted)")
    print(f"  workflow_normalized - VIEW: workflows with normalized names & categories")
    print(f"  workflow_durations  - VIEW: workflow start/end pairs with calculated duration_ms")
    print(f"  step_analysis       - VIEW: step events filtered (step_start/success/error)")
    print(f"  step_durations      - VIEW: step start/end pairs with duration_ms by category")
    print(f"  session_outcomes    - VIEW: session-level success/fail with workflow/step counts")
    print(f"  llm_analysis        - VIEW: LLM events (request/response/error) with tokens")
    print(f"\nExample queries:")
    print(f"  duckdb {db_path}")
    print(f"  -- Event type breakdown")
    print(f"  SELECT event_type, COUNT(*) FROM unified_events GROUP BY 1 ORDER BY 2 DESC;")
    print(f"  -- Compare workflows across M/S/B using normalized names")
    print(f"  SELECT workflow_category, sup_behavior, COUNT(*) FROM workflow_normalized")
    print(f"    WHERE event_type = 'workflow_start' GROUP BY 1, 2 ORDER BY 1, 3 DESC;")
    print(f"  -- Success rate by SUP behavior")
    print(f"  SELECT sup_behavior, COUNT(*) FILTER (WHERE success = true) as succeeded,")
    print(f"         COUNT(*) FILTER (WHERE success = false) as failed FROM workflow_durations GROUP BY 1;")
    print(f"  -- Step success rate by category and SUP")
    print(f"  SELECT category, sup_behavior, COUNT(*) FILTER (WHERE success) as succeeded,")
    print(f"         COUNT(*) FILTER (WHERE NOT success) as failed FROM step_durations GROUP BY 1, 2;")
    print(f"  -- Session outcomes by SUP behavior")
    print(f"  SELECT sup_behavior, outcome, COUNT(*) FROM session_outcomes GROUP BY 1, 2 ORDER BY 1, 3 DESC;")
    print(f"  -- Average step duration by category")
    print(f"  SELECT category, sup_behavior, AVG(duration_ms) as avg_ms, COUNT(*) as n")
    print(f"    FROM step_durations WHERE duration_ms IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 3;")
    print(f"  -- LLM error rate by SUP")
    print(f"  SELECT sup_behavior, COUNT(*) FILTER (WHERE event_type = 'llm_error') as errors,")
    print(f"         COUNT(*) FILTER (WHERE event_type = 'llm_request') as requests FROM llm_analysis GROUP BY 1;")

    return 0


if __name__ == '__main__':
    sys.exit(main())
