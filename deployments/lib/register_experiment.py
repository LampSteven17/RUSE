#!/usr/bin/env python3
"""Register a RUSE deployment in PHASE experiments.json.

Parses ssh_config_snippet.txt and inventory.ini to build an experiment
entry, then upserts it into experiments.json (preserving user-added fields).

Usage:
    python3 register_experiment.py \
        --name sup-controls \
        --snippet runs/0219/ssh_config_snippet.txt \
        --inventory runs/0219/inventory.ini
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Canonical field order for experiment entries
FIELD_ORDER = [
    "ips",
    "output_file",
    "inference_file",
    "interface",
    "start_date",
    "end_date",
    "output_type",
    "description",
    "sup_logs_db",
]


def parse_snippet(snippet_path):
    """Parse ssh_config_snippet.txt -> {ip: vm_name}.

    Uses the actual OpenStack VM name as the label (the Host line),
    unified across all deployment types (r-*, g-*, e-*).
    """
    from vm_naming import ALL_PREFIXES

    text = snippet_path.read_text()

    # Build regex to match any known VM prefix (skip wildcards like Host r-*)
    prefix_alt = "|".join(re.escape(p) for p in ALL_PREFIXES)
    ips = {}
    for m in re.finditer(
        rf"^Host ((?:{prefix_alt})(?!\*)\S+)\s*\n\s+HostName (\S+)", text, re.MULTILINE
    ):
        hostname = m.group(1)
        ip = m.group(2)
        ips[ip] = hostname

    return ips


def parse_start_date(inventory_path):
    """Extract date from '# Generated: 2026-02-19T22:28:29+00:00' line."""
    text = inventory_path.read_text()
    m = re.search(r"^# Generated:\s*(\d{4}-\d{2}-\d{2})", text, re.MULTILINE)
    return m.group(1) if m else None


def order_entry(entry):
    """Return entry dict with keys in canonical order."""
    ordered = {}
    for key in FIELD_ORDER:
        if key in entry:
            ordered[key] = entry[key]
    for key in entry:
        if key not in ordered:
            ordered[key] = entry[key]
    return ordered


def main():
    parser = argparse.ArgumentParser(
        description="Register a RUSE deployment in PHASE experiments.json"
    )
    parser.add_argument(
        "--name", required=True, help="Deployment name (experiments.json key)"
    )
    parser.add_argument(
        "--snippet", required=True, help="Path to ssh_config_snippet.txt"
    )
    parser.add_argument(
        "--inventory", help="Path to inventory.ini (for start_date extraction)"
    )
    parser.add_argument(
        "--run-id", help="Run ID (e.g., 0227) — sets sup_logs_db field for PHASE log collection"
    )
    parser.add_argument(
        "--start-date", help="Explicit start date (YYYY-MM-DD), overrides inventory extraction"
    )
    parser.add_argument(
        "--interface", default="eno2", help="Network interface (default: eno2)"
    )
    parser.add_argument(
        "--experiments-json",
        default="/mnt/AXES2U1/experiments.json",
        help="Path to experiments.json",
    )
    args = parser.parse_args()

    snippet_path = Path(args.snippet)
    if not snippet_path.exists():
        print(f"Snippet file not found: {snippet_path}", file=sys.stderr)
        return 1

    # Parse IPs from snippet
    ips = parse_snippet(snippet_path)
    if not ips:
        print(f"No SUP hosts found in {snippet_path}", file=sys.stderr)
        return 1

    # Parse start_date: explicit flag > inventory extraction
    start_date = args.start_date
    if not start_date and args.inventory:
        inv_path = Path(args.inventory)
        if inv_path.exists():
            start_date = parse_start_date(inv_path)

    # Build entry
    name = args.name
    name_upper = name.upper()

    entry = {
        "ips": ips,
        "output_file": f"bigdisk/TRAINING_DATA/IPs/AXES_{name_upper}.json",
        "inference_file": f"bigdisk/TRAINING_DATA/IPs/axes-{name}_IPs.txt",
        "interface": args.interface,
        "start_date": start_date,
        "end_date": None,
        "output_type": "inference",
    }

    # Read existing experiments.json
    exp_path = Path(args.experiments_json)
    if exp_path.exists():
        data = json.loads(exp_path.read_text())
    else:
        data = {}

    # Set sup_logs_db if run_id provided
    if args.run_id:
        entry["sup_logs_db"] = f"{name}-{args.run_id}.duckdb"

    # Upsert: merge with existing entry, preserving user-added fields
    if name in data:
        existing = data[name]
        for key in (
            "ips",
            "output_file",
            "inference_file",
            "interface",
            "start_date",
            "output_type",
        ):
            existing[key] = entry[key]
        # Set end_date only if not already present
        if "end_date" not in existing:
            existing["end_date"] = None
        # Update sup_logs_db if provided
        if "sup_logs_db" in entry:
            existing["sup_logs_db"] = entry["sup_logs_db"]
        data[name] = order_entry(existing)
        action = "Updated"
    else:
        entry["description"] = ""
        data[name] = order_entry(entry)
        action = "Added"

    # Write back
    exp_path.write_text(json.dumps(data, indent=4) + "\n")

    # Summary
    print(f'{action} "{name}" in {exp_path}')
    print(f"  IPs: {len(ips)}")
    if start_date:
        print(f"  start_date: {start_date}")
    if args.run_id:
        print(f"  sup_logs_db: {name}-{args.run_id}.duckdb")
    print(f"  interface: {args.interface}")

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
