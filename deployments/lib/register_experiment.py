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
import fcntl
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def _locked_read_write(path: Path):
    """Advisory file lock + atomic replace.

    Opens a sibling lock file, takes an exclusive fcntl lock, then yields
    (read_fn, write_fn). write_fn does a rename-based atomic replace so a
    crash mid-write never leaves a truncated experiments.json.

    Without this, register_experiment.py's read-modify-write raced with
    itself during batch deploys: 14 deploys got wiped down to 2 on
    2026-04-17 because concurrent writers clobbered each other's views.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        def _read():
            if path.exists():
                return json.loads(path.read_text())
            return {}

        def _write(data):
            # Atomic replace: write to temp in same dir, fsync, rename
            tmp = tempfile.NamedTemporaryFile(
                mode="w",
                dir=str(path.parent),
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            )
            try:
                tmp.write(json.dumps(data, indent=4) + "\n")
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp.close()
                os.replace(tmp.name, path)
            except Exception:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
                raise

        yield _read, _write
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

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
    parser.add_argument(
        "--extra-ip",
        action="append",
        default=[],
        help=(
            "Additional IP=HOSTNAME pair to include in the entry's ips dict, "
            "beyond what the snippet provides. Repeatable. Used e.g. to register "
            "the RUSE neighborhood sidecar VM which lives in a separate inventory "
            "file and won't appear in ssh_config_snippet.txt. Silent registration "
            "gaps (today's neighborhood case) were the reason this was added."
        ),
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

    # Merge any --extra-ip pairs (neighborhood, etc.)
    for pair in args.extra_ip:
        if "=" not in pair:
            print(f"--extra-ip must be IP=HOSTNAME (got: {pair})", file=sys.stderr)
            return 1
        ip, host = pair.split("=", 1)
        ip = ip.strip()
        host = host.strip()
        if ip and host:
            ips[ip] = host

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

    # Set sup_logs_db if run_id provided
    if args.run_id:
        entry["sup_logs_db"] = f"{name}-{args.run_id}.duckdb"

    # Atomic read-modify-write under a file lock. Without the lock, two
    # deploys running register_experiment.py concurrently could each
    # load their own stale view and write back — the later writer's
    # view wins and clobbers entries it didn't know about. This actually
    # happened 2026-04-17: a batch of rampart + ruse deploys interleaved
    # and 14 entries got wiped to 2.
    exp_path = Path(args.experiments_json)
    with _locked_read_write(exp_path) as (read, write):
        data = read()

        # Upsert: merge with existing entry, preserving user-added fields.
        # end_date is ALWAYS reset to None on re-registration: a fresh
        # spinup means the deploy is active again, regardless of any
        # stale end_date left by a prior teardown. Previously this was
        # conditional on "end_date not in existing", which never fired
        # after the first teardown — resulting in inverted ranges like
        # start=2026-04-17, end=2026-04-16 on every re-deploy.
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
            existing["end_date"] = None
            if "sup_logs_db" in entry:
                existing["sup_logs_db"] = entry["sup_logs_db"]
            data[name] = order_entry(existing)
            action = "Updated"
        else:
            entry["description"] = ""
            data[name] = order_entry(entry)
            action = "Added"

        write(data)

    # Post-write verification. The atomic-replace inside _locked_read_write
    # returns successfully when the local rename succeeds, but that doesn't
    # prove the data landed on a healthy network mount. On 2026-04-20 the
    # /mnt/AXES2U1 NFS mount blipped and silently discarded ghosts+ruse
    # registrations — deploys reported "Registered in PHASE" while the
    # persisted file still showed pre-blip run IDs. Re-open and confirm
    # the entry we just wrote is there, matches what we intended, and
    # that the ips dict hasn't been reduced.
    try:
        persisted = json.loads(exp_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: post-write re-read failed: {e}", file=sys.stderr)
        return 1

    saved = persisted.get(name)
    if not isinstance(saved, dict):
        print(f"ERROR: post-write verify — entry '{name}' missing after write. "
              f"Mount may have blipped; retry.", file=sys.stderr)
        return 1
    saved_ips = saved.get("ips", {})
    missing = [ip for ip in ips if ip not in saved_ips]
    if missing:
        print(f"ERROR: post-write verify — {len(missing)} IPs missing from "
              f"'{name}' after write (e.g. {missing[:3]}). "
              f"Mount may have blipped; retry.", file=sys.stderr)
        return 1

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
