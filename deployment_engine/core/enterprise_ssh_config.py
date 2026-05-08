#!/usr/bin/env python3
"""Generate SSH config snippet from enterprise deploy-output.json.

Reads the deployment output and produces an SSH config block for each node,
using the game-network IP.

Usage:
    python3 enterprise_ssh_config.py <deploy-output.json> <deployment-name> <run-id> [-o snippet.txt]
"""

import argparse
import json
import sys


def generate_ssh_config(deploy_output, deploy_name, run_id):
    """Generate SSH config snippet from deploy-output.json."""
    lines = [f"# --- RUSE {deploy_name} run {run_id} ---"]

    # Support multiple deploy-output.json structures
    if isinstance(deploy_output, list):
        nodes = deploy_output
    elif "enterprise_built" in deploy_output:
        nodes = deploy_output["enterprise_built"].get("deployed", {}).get("nodes", [])
    else:
        nodes = deploy_output.get("nodes", deploy_output.get("servers", []))

    for node in nodes:
        name = node.get("name", node.get("hostname", ""))
        if not name:
            continue

        # Extract IP from various formats
        ip = None

        # Format 1: addresses list (OpenStack deploy-nodes.py output)
        addresses = node.get("addresses", [])
        if isinstance(addresses, list) and addresses:
            ip = addresses[0].get("addr") if isinstance(addresses[0], dict) else addresses[0]

        # Format 2: networks dict
        if not ip:
            networks = node.get("networks", {})
            if isinstance(networks, dict):
                for net_name, addrs in networks.items():
                    if "game" in net_name.lower():
                        ip = addrs[0] if isinstance(addrs, list) else addrs
                        break
                if not ip:
                    for net_name, addrs in networks.items():
                        ip = addrs[0] if isinstance(addrs, list) else addrs
                        break

        # Format 3: flat IP fields
        if not ip:
            ip = node.get("ip", node.get("access_ip", node.get("private_ip", "")))

        if not ip:
            continue

        # Determine user — Windows VMs typically don't use key auth
        os_type = node.get("os", node.get("image", "")).lower()
        is_windows = "win" in os_type

        lines.append("")
        lines.append(f"Host {name}")
        lines.append(f"    HostName {ip}")
        if is_windows:
            password = node.get("password", node.get("admin_password", ""))
            lines.append(f"    User Administrator")
            lines.append(f"    # Windows VM — use RDP or password auth")
            if password:
                lines.append(f"    # Password: {password}")
        else:
            lines.append(f"    User ubuntu")
        lines.append(f"    IdentityFile ~/.ssh/id_rsa")

    lines.append("")
    lines.append(f"# --- end RUSE {deploy_name} run {run_id} ---")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate SSH config from enterprise deploy output")
    parser.add_argument("deploy_output", help="Path to deploy-output.json")
    parser.add_argument("deploy_name", help="Deployment name")
    parser.add_argument("run_id", help="Run ID")
    parser.add_argument("-o", "--output", help="Output file (default: stdout)")
    args = parser.parse_args()

    with open(args.deploy_output) as f:
        data = json.load(f)

    snippet = generate_ssh_config(data, args.deploy_name, args.run_id)

    if args.output:
        with open(args.output, "w") as f:
            f.write(snippet + "\n")
    else:
        print(snippet)


if __name__ == "__main__":
    main()
