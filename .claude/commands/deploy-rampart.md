# Rampart Enterprise Deployment - System Context

Load critical context about the Rampart enterprise deployment system before working on it. This covers the full RUSE CLI integration, the uva-cs-workflow scripts, and autonomous emulation via systemd/scheduled tasks.

## Instructions

Read the following files in order to understand the deployment system:

### RUSE CLI Integration
1. `deployments/cli/commands/rampart.py` - RUSE CLI deploy command (5 steps: venv → provision → post-deploy → simulate → deploy emulation services)
2. `deployments/cli/commands/teardown.py` - Teardown: `_rampart_teardown()` deletes VMs by `e-{hash}-` prefix
3. `deployments/rampart-controls/config.yaml` - Deployment config (type: rampart, workflow_dir, enterprise configs)
4. `deployments/playbooks/install-rampart-emulation.yaml` - Ansible playbook for Linux systemd emulation services
5. `deployments/lib/enterprise_ssh_config.py` - SSH config generation from deploy-output.json (handles enterprise_built.deployed.nodes structure)
6. `deployments/lib/register_experiment.py` - PHASE experiments.json registration (--start-date flag for RAMPART date range)

### uva-cs-workflow Scripts (~/uva-cs-workflow)
6. `~/uva-cs-workflow/deploy-nodes.py` - OpenStack VM provisioning (creates VMs, DNS zones, collects IPs/passwords)
7. `~/uva-cs-workflow/post-deploy.py` - Post-provisioning (register Windows, AD setup, domain join, pyhuman install)
8. `~/uva-cs-workflow/simulate-logins.py` - Generate AD users + login schedule from user-roles.json → logins.json
9. `~/uva-cs-workflow/emulate-logins.py` - Manual/test login emulation (NOT used in deploy — kept for workflow testing)
10. `~/uva-cs-workflow/openstack_cloud.py` - OpenStack API wrapper (Nova, Neutron, Designate, Glance)
11. `~/uva-cs-workflow/shell_handler.py` - SSH/SFTP session manager (password-first, key-fallback, no agent)
12. `~/uva-cs-workflow/role_domains.py` - AD setup (forest, DCs, domain join, CA certs, user deployment)
13. `~/uva-cs-workflow/role_register.py` - Windows adapter rename + license activation
14. `~/uva-cs-workflow/role_human.py` - pyhuman installation on endpoints (Windows + Linux)

### Configuration
15. `~/uva-cs-workflow/cloud-configs/axes-cicd.json` - OpenStack cloud config (keypair: enterprise-key, security group, network, images)
16. `~/uva-cs-workflow/enterprise-configs/enterprise-med.json` - Enterprise topology (3 DCs, 10 Windows, 10 Linux endpoints)
17. `~/uva-cs-workflow/user-roles/user-roles.json` - User behavior profiles (standard/power/admin roles, workflows, activity patterns)

## Architecture

### RUSE CLI Deploy Flow

```
./deploy --rampart                       # uses rampart-controls config
./deploy --rampart --feedback            # + PHASE per-node user roles
./deploy --rampart --feedback --source ~/path  # explicit PHASE source
./deploy --rampart rampart-controls      # explicit config name

Deploy flow (rampart.py):
  [1/5] Setup venv in ~/uva-cs-workflow
  [2/5] Provision VMs (deploy-nodes.py) → deploy-output.json
        Uses per-deployment cloud config with unique enterprise_url ({hash}.{project}.os)
  [3/5] Configure VMs (post-deploy.py) → post-deploy-output.json
        ├── register_windows()           - Adapter rename + license activation
        ├── deploy_domain_controllers()  - AD forest (dc1) + replicas (dc2, dc3)
        │   Uses -DomainNetBIOSName CASTLE{hash} for multi-deploy isolation
        ├── setup_fileservers()          - File server configuration
        ├── join_domains()               - Windows + Linux domain join
        ├── deploy_human()               - pyhuman agent install on all endpoints
        ├── setup_moodle_idps()          - Moodle IdP configuration
        ├── setup_moodle_sps()           - Moodle SP configuration
        └── setup_moodle_idps_part2()    - Moodle IdP finalization
  [---] Assemble PHASE user roles (if --feedback): rampart.py::_generate_feedback_user_roles
        Reads behavior_source/{bare_node}/user-roles.json (Stage 2
        target-native format, written directly by PHASE — no
        translation layer), extracts each file's first role (the
        tuned {bare_node}_user role), renames it to
        e-{hash}-{bare_node}_user for deployment-unique naming,
        combines with the 3 baseline roles (standard/power/admin user)
        from the workflow baseline, and rewrites each fed enterprise
        node's "user" field to point at the renamed role.
        → user-roles-feedback.json (19 tuned + 3 baseline roles)
        → enterprise-config-feedback.json (per-node role references)
  [4/5] Generate users + login schedule (simulate-logins.py) → logins.json
        Uses FQDN domain auth (administrator@castle.{hash}.{project}.os)
  [5/5] Deploy autonomous emulation services
        ├── Linux: Ansible playbook → systemd service (rampart-human)
        └── Windows: Python SSH → scheduled task (RampartHuman)

Post-deploy:
  - SSH config installed in ~/.ssh/config (RUSE markers)
  - PHASE experiments.json registered with start_date = deploy date
  - Deploy finishes, terminal returns — VMs run independently
```

### Teardown Flow

```
./teardown rampart-controls-MMDDYYHHMMSS
./teardown rampart-feedback-stdctrls-sum24-all-MMDDYYHHMMSS

  [1/4] Stop emulation PID (if running centrally)
  [2/4] Delete VMs by e-{hash}- prefix (direct OpenStack delete)
  [3/4] Clean up DNS zone (scoped: reads dns_zone.txt for this deployment only)
  [4/4] Verify 0 VMs remaining

./teardown --all   # catches e-* VMs in teardown-all.yaml
```

### Naming Conventions

```
Baseline:      deployments/rampart-controls/
Feedback:      deployments/rampart-feedback-{preset}-{dataset}-{scope}/
Config type:   type: rampart
VM prefix:     e-{5char-md5-hash}-{node_name}
               e.g., e-bf351-dc1, e-bf351-winep1, e-bf351-linep3
dep_id:        controls{run_id}  (rampart- prefix stripped)
Run dir:       rampart-controls/runs/{MMDDYYHHMMSS}/
DNS zone:      {hash}.vxn3kr-bot-project.os (per-deployment, isolated)
NetBIOS:       CASTLE{hash} (per-deployment, unique on network)
Teardown:      ./teardown rampart-controls-{MMDDYYHHMMSS}
```

### Multi-Deployment Isolation

Each RAMPART deployment gets its own DNS zone and NetBIOS name. Key files:
- `run_dir/cloud-config-prefixed.json` — cloud config with per-deployment enterprise_url
- `run_dir/dns_zone.txt` — zone name for scoped teardown
- `openstack_cloud.py:40` — respects pre-set enterprise_url from cloud config

## Enterprise Topology (enterprise-med.json)

```
Domain: castle.{hash}.{project}.os  (e.g., castle.14a6d.vxn3kr-bot-project.os)

Domain Controllers (Windows Server 2022):
  dc1      - Forest leader (domain_controller_leader)
  dc2, dc3 - Replica DCs (domain_controller)

Windows Endpoints (Windows Server 2022):
  winep1-10  - Domain-joined, personal, standard user

Linux Endpoints (Ubuntu Jammy):
  linep1     - Shared (no user assigned, no emulation)
  linep2     - Shared, standard user
  linep3-8   - Personal, standard user
  linep9     - Personal, admin user
  linep10    - Personal, power user

Total: 23 VMs (3 DC + 10 Windows + 10 Linux)
Emulated: 19 endpoints (linep1 excluded — shared, no user)
```

## Autonomous Emulation

Emulation runs **on the VMs themselves** — mlserv can shut down.

### Linux Endpoints (systemd)
```
Service: rampart-human.service
Binary:  xvfb-run -a /opt/pyhuman/bin/python -u /opt/pyhuman/human.py
Args:    --clustersize 5 --taskinterval 10 --taskgroupinterval 500
         --seed {seed} --workflows {list} --extra passfile /tmp/shib_login.{user}
Config:  /etc/systemd/system/rampart-human.service
Check:   ssh e-XXXXX-linep3 "systemctl status rampart-human"
Logs:    ssh e-XXXXX-linep3 "journalctl -u rampart-human -f"
```

### Windows Endpoints (scheduled task)
```
Task:    RampartHuman (runs at startup, SYSTEM, auto-restart)
Script:  C:\tmp\run-emulation.ps1
Binary:  C:\Python\python.exe -u C:\human\human.py
Args:    Same as Linux (clustersize, taskinterval, seed, workflows, passfile)
Creds:   C:\tmp\shib_login.{username}
Check:   sshpass -p {admin_pass} ssh Administrator@castle.{hash}.vxn3kr-bot-project.os@{ip} "powershell (Get-ScheduledTask -TaskName RampartHuman).State"
```

### Why Not Ansible for Windows?
Ansible's `raw` module strips PowerShell `$` variables (`$action`, `$trigger`, `$false`, etc.). No escape method works reliably (`{{ '$' }}`, `{% raw %}`, cmd echo). Windows emulation is deployed via direct `sshpass` SSH from Python (`_deploy_windows_emulation()` in rampart.py), using the domain admin password for auth.

## PHASE Registration

`_register_phase()` in `rampart.py` calls `register_experiment.py` with:
- `--name rampart-controls` — experiment name
- `--snippet ssh_config_snippet.txt` — SSH config for all 23 VMs
- `--run-id MMDDYYHHMMSS` — deployment timestamp
- `--start-date YYYY-MM-DD` — **critical**: deploy date, used by PHASE to scope Zeek log dredging

Without `--start-date`, PHASE tries to process ALL Zeek logs on eno2 (potentially thousands of files, runs out of disk). The date range scopes it to just the deployment window.

`enterprise_ssh_config.py` generates the snippet by navigating:
```
deploy-output.json → enterprise_built.deployed.nodes[] → addresses[0].addr
```

## Log Collection

Rampart VMs do NOT produce RUSE-format JSONL logs. There is no `/opt/ruse/deployed_sups/{behavior}/logs/*.jsonl` path on these VMs.

- **Linux logs**: `journalctl -u rampart-human` (systemd journal, pyhuman stdout)
- **Windows logs**: Captured by scheduled task, no persistent log file by default
- **RUSE log collector** (`collect_sup_logs.py`): Will SSH in, find no JSONL, and skip each VM — harmless but noisy
- **Network traffic**: Captured by Zeek on eno2 (axes), processed by PHASE pipeline with the `start_date` range

To check emulation health across all VMs:
```bash
# Linux endpoints
for i in 2 3 4 5 6 7 8 9 10; do
  echo -n "linep$i: "
  SSH_AUTH_SOCK="" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -o IdentitiesOnly=yes -i ~/.ssh/id_rsa ubuntu@<IP> \
    "systemctl is-active rampart-human" 2>/dev/null
done

# Windows endpoints
for ip in <winep IPs>; do
  echo -n "$ip: "
  SSH_AUTH_SOCK="" sshpass -p '<admin_pass>' ssh -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -o PreferredAuthentications=password \
    "Administrator@castle@$ip" \
    "powershell -Command (Get-ScheduledTask -TaskName RampartHuman).State" 2>/dev/null
done
```

## Behavioral Configuration

The central orchestrator is **user-roles.json** — equivalent to RUSE's `behavioral_configurations/defaults/` directory:
- Activity timing: hours/day, logins/hour, start hours (per day-of-week)
- Workflow selection: which workflows each role runs
- Session behavior: login duration, recursive logins, terminal count
- Node targeting: fraction to personal vs shared vs random machines

### Baseline: 3 role types
`standard user`, `power user`, `admin user` — static in `~/uva-cs-workflow/user-roles/user-roles.json`.

### With --feedback: 19 per-node roles (post Stage 2, 2026-04-09)
`./deploy --rampart --feedback` assembles per-node roles via
`rampart.py::_generate_feedback_user_roles()`. **There is no longer a
translation layer** — `deployments/lib/phase_to_user_roles.py` was
deleted in Stage 2. PHASE's feedback engine now writes target-native
`user-roles.json` files directly, one per fed endpoint.

**PHASE output layout** (read as-is by the deploy):
```
~/PHASE/feedback_engine/configs/axes-rampart-controls_{dataset}_{preset}/
  linep2/user-roles.json     linep3/user-roles.json   ...  linep10/user-roles.json
  winep1/user-roles.json     winep2/user-roles.json   ...  winep10/user-roles.json
  (19 files total — dc1/dc2/dc3/linep1 are absent; they have user: null in
   enterprise-med.json and don't receive feedback architecturally.)
```

Each per-node file is a **self-contained pyhuman config**:
```json
{
  "roles": [
    {"name": "linep9_user", ...},   // ← tuned role (first entry)
    {"name": "standard user", ...}, // ← baseline clones (for reference)
    {"name": "power user", ...},
    {"name": "admin user", ...}
  ],
  "_phase_metadata": { ... provenance ... }
}
```

**Assembly flow** in `_generate_feedback_user_roles()`:
1. Walks `behavior_source/*/user-roles.json` to discover processed nodes.
2. For each file, extracts the first role (the tuned `{bare_node}_user` role).
3. Clones the tuned role and renames it to `e-{hash}-{bare_node}_user`
   (e.g. `e-14a6d-linep9_user`). This is where the `e-{hash}-` prefix
   gets applied — PHASE writes bare names, deploy maps them to
   hash-prefixed enterprise config node names so concurrent deployments
   of different hashes don't collide.
4. Walks `enterprise-config-prefixed.json` nodes, strips the
   `e-{hash}-` prefix via regex, looks up the matching tuned role, and
   rewrites the node's `"user"` field to the renamed role.
5. Nodes with `user: null` (dc1-3, linep1) are left unchanged.
6. Combines tuned roles with the 3 baseline roles (`standard user`,
   `power user`, `admin user`) loaded from the workflow baseline — so
   any unfed node still has valid role references.
7. Writes `user-roles-feedback.json` (22 roles = 19 tuned + 3 baseline)
   and `enterprise-config-feedback.json` into the run dir.

**Role naming convention matters**: the tuned role for `linep9`
appears in the output as `e-{hash}-linep9_user`, NOT `linep9_user`.
This diverges from the PHASE file's name so the enterprise config can
reference a unique role name per deployment.

**Baseline role assignment** (determined on the PHASE side during
generation, reflected in the `_phase_metadata.baseline_role` field of
each file):
- `winep1..winep10` and `linep2..linep8` → cloned from `standard user`
- `linep9` → cloned from `admin user`
- `linep10` → cloned from `power user`

So e.g. `linep9`'s tuned role inherits the admin's
`fraction_of_logins_to_personal_machine: "0.2"` while receiving
PHASE-supplied `day_start_hour`, `activity_daily_hours`,
`logins_per_hour`, `login_length`, `clustersize`, `taskinterval`,
`taskgroupinterval`. Enterprise-only workflows (`browse_iis`,
`browse_shibboleth`, `moodle`, `build_software`) are retained in the
`workflows` list because PHASE preserves them during role cloning.

## SSH Authentication

| Target | Auth Method | Key/Password | User |
|--------|------------|--------------|------|
| Linux VMs (Ansible) | Key | `~/.ssh/id_rsa` (enterprise-key) | ubuntu |
| Linux VMs (manual) | Key | `~/.ssh/id_rsa` | ubuntu |
| Windows VMs (deploy) | Password via sshpass | Domain admin password | Administrator@{fqdn_domain} |
| Windows VMs (manual) | Password | Domain admin password | Administrator@{fqdn_domain} |

**SSH agent MUST be disabled** (`SSH_AUTH_SOCK=""` / `allow_agent=False`) — too many keys cause auth timeouts.

Note: Enterprise VMs use `enterprise-key` (`~/.ssh/id_rsa`), NOT `bot-desktop` (`~/.ssh/id_ed25519`) which is used by RUSE/GHOSTS VMs.

## Testing Individual Workflows

`emulate-logins.py` is kept for manual testing (not used in deploy):
```bash
cd ~/uva-cs-workflow
source .venv/bin/activate && source ~/vxn3kr-bot-rc

# Test single workflow (fast-debug compresses timings)
python3 emulate-logins.py post-deploy-output.json logins.json \
  --seed 42 --logfile test.ndjson --fast-debug --workflows browse_web

# Test multiple workflows
python3 emulate-logins.py post-deploy-output.json logins.json \
  --seed 42 --logfile test.ndjson --fast-debug \
  --workflows browse_iis moodle google_search
```

Available workflows: `browse_iis`, `browse_shibboleth`, `browse_web`, `browse_youtube`, `build_software`, `download_files`, `google_search`, `moodle`, `spawn_shell`

## Common Issues and Fixes

| Issue | Cause | Fix |
|-------|-------|-----|
| One VM failure kills entire post-deploy step | joblib.Parallel raises on first exception | `_safe_parallel_call()` wrapper catches per-VM exceptions |
| Domain join verification takes 30+ min | 30 outer × 10 inner retries with backoff | Reduced to 15 × 2 retries in `role_domains.py` |
| Ansible strips PowerShell `$` variables | `raw` module treats `$` as Jinja2 | Windows emulation deployed via direct sshpass SSH, not Ansible |
| SSH "Too many auth failures" on enterprise VMs | Wrong key (id_ed25519 vs id_rsa) | Enterprise VMs use `~/.ssh/id_rsa` (enterprise-key keypair) |
| PHASE registration "No SUP hosts found" | enterprise_ssh_config.py couldn't parse deploy-output.json | Fixed to navigate `enterprise_built.deployed.nodes` and read `addresses[0].addr` |
| Output buffering (deploy appears frozen) | Child Python processes buffer stdout | `PYTHONUNBUFFERED=1` + `bufsize=1` in `_ent_run()` |
| Deprecation warnings in output | neutronclient, cryptography libs | `PYTHONWARNINGS=ignore` + `_is_noise()` filter |
| verbose=True in role_domains.py | Dumps every SSH handshake to stdout | Set to `verbose=False` |
| PHASE dredges all Zeek logs (disk full) | No start_date in experiments.json | `--start-date` flag added to register_experiment.py, rampart.py passes deploy date |
| RUSE log collector finds nothing on Rampart VMs | No JSONL logs — pyhuman uses stdout | Expected behavior — collector skips, use journalctl/Zeek instead |
| NetBIOS name collision between RAMPART deployments | `Install-ADDSForest` can't auto-derive NetBIOS when multi-label domain; even when it can, two deploys on same network collide | Fixed: explicit `-DomainNetBIOSName CASTLE{hash}` in role_domains.py |
| Auth fails with "Authentication failed" on DC | `deploy_users()` used bare domain (`administrator@castle`) but NetBIOS is now `CASTLE{hash}` | Fixed: use FQDN (`administrator@castle.{hash}.{project}.os`) in role_domains.py and rampart.py |
| 0 endpoints found for emulation | `user_map` keyed by prefixed names (`e-hash-winep1`) but `node_map` keyed by bare names (`winep1`) | Fixed: strip `ent_prefix` from home_node names in `_generate_emulation_inventory()` and `_deploy_windows_emulation()` |
| DNS zone collision between RAMPART deployments | All deploys shared one zone (`vxn3kr-bot-project.os`) | Fixed: per-deployment zone (`{hash}.vxn3kr-bot-project.os`), scoped teardown via `dns_zone.txt` marker |
| PHASE feedback produces 0 per-node roles | (historical, pre-Stage-2) `phase_to_user_roles.py` used prefixed names to look up PHASE dirs that used bare names | Resolved in Stage 2 (2026-04-09): `phase_to_user_roles.py` was deleted and replaced by `rampart.py::_generate_feedback_user_roles` which reads `{bare_node}/user-roles.json` directly and applies the `e-{hash}-` prefix only when renaming the tuned role in the combined output. No more translation-layer mismatch. |

## Important Constraints

- **Do NOT delete `bot-desktop` keypair from OpenStack** — used by RUSE/GHOSTS VMs
- **`enterprise-key` (`~/.ssh/id_rsa`)** must be RSA in PEM format — used for VM provisioning + Windows password decryption
- **No package installs on axes** — axes is locked down
- **`~/uva-cs-workflow/`** is the active copy (ported from nomod); `~/uva-cs-workflow-old/` is the pre-fix backup; `~/uva-cs-workflow-nomod/` is the reference copy
- **`sshpass` must be installed on mlserv** — required for Windows emulation deployment
