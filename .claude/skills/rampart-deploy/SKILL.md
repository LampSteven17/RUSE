---
name: rampart-deploy
description: RAMPART enterprise deployment — running ./deploy --rampart [feedback], 5-step orchestrator with AD forest + Windows/Linux pyhuman emulation, multi-deploy isolation, hour-gating wiring. Inputs deployments/rampart-controls/config.yaml + ~/uva-cs-workflow/ + /mnt/AXES2U1/feedback/rampart-controls/{dataset}/{node}/user-roles.json. Outputs deployments/rampart-{controls,feedback-...}/runs/{run_id}/. Does NOT cover DECOY SUPs (see /decoy-deploy) or GHOSTS NPCs (see /ghosts-deploy). Cross-type CLI shape, fail-loud contract, and SSH key matrix live in CLAUDE.md.
type: skill
---

# rampart-deploy

RAMPART = synthetic enterprise: AD forest with Windows + Linux endpoints
running pyhuman workflow emulation. Driven by `user-roles.json` (RAMPART
analog of DECOY's `behavior.json`). Code in `deployment_engine/rampart/spinup.py`
delegates VM provisioning to `~/uva-cs-workflow/`.

| | |
|---|---|
| Inputs | `deployments/rampart-controls/config.yaml`, `~/uva-cs-workflow/cloud-configs/axes-cicd.json` (or `axes-cicd-feedback.json` for feedback flavor bump), `~/uva-cs-workflow/enterprise-configs/enterprise-med.json`, `~/uva-cs-workflow/user-roles/user-roles.json` (3-role baseline), `/mnt/AXES2U1/feedback/rampart-controls/{dataset}/{bare_node}/user-roles.json` (19 per-node feedback files) |
| Outputs | `deployments/rampart-{controls,feedback-...}/runs/{run_id}/` (cloud-config-prefixed.json, dns_zone.txt, deploy-output.json, post-deploy-output.json, enterprise-config-feedback.json, user-roles-feedback.json, logins.json, ssh_config_snippet.txt) |
| Manifest | PHASE source `manifest.json`; same loader as DECOY |
| Upstream | PHASE feedback engine writes target-native per-node `user-roles.json` directly (no translation layer) |
| Downstream | PHASE Zeek pipeline (`PHASE.py --rampart`) scoped to `start_date` from `experiments.json` |
| Narrow exceptions | dc1-3 + linep1 have `user: null` and run no emulation; baseline pyhuman runs 24/7 if hour-gating fields are absent (backward compat) |

## Topology (`enterprise-med.json`)

```
Domain: castle.{hash}.{project}.os  (e.g. castle.14a6d.vxn3kr-bot-project.os)

Domain Controllers (Windows Server 2022):
  dc1      Forest leader (domain_controller_leader)
  dc2, dc3 Replica DCs (domain_controller)

Windows Endpoints (Windows Server 2022):
  winep1-10  Domain-joined, personal, standard user

Linux Endpoints (Ubuntu Jammy):
  linep1     Shared (no user, no emulation)
  linep2     Shared, standard user
  linep3-8   Personal, standard user
  linep9     Personal, admin user
  linep10    Personal, power user

Total: 23 VMs (3 DC + 10 Win + 10 Linux). 19 emulated endpoints.
```

VM names: `r-{md5(dep_id)[:5]}-{node_name}` — 5-char hash for NetBIOS
limit. Example: `r-bf351-dc1`, `r-bf351-winep1`, `r-bf351-linep3`.

## Deploy flow (`rampart/spinup.py`)

```
[1/5] Setup venv in ~/uva-cs-workflow
[2/5] Provision VMs (deploy-nodes.py) → deploy-output.json
      Per-deployment cloud config with unique enterprise_url
      ({hash}.{project}.os)
[3/5] Configure VMs (post-deploy.py) → post-deploy-output.json
      ├── register_windows()           Adapter rename + license activation
      ├── deploy_domain_controllers()  AD forest (dc1) + replicas (dc2, dc3)
      │                                -DomainNetBIOSName CASTLE{hash}
      ├── setup_fileservers()
      ├── join_domains()               Win + Linux domain join
      ├── deploy_human()               pyhuman install on all endpoints
      └── setup_moodle_idps/sps/idps_part2()
[---] If behavior_source set (controls OR feedback):
      rampart.py::_generate_feedback_user_roles
      → user-roles-feedback.json (per-node roles + 3 baseline)
      → enterprise-config-feedback.json
      Mode FATAL gate — see "user-roles.json mode contract" below.
[4/5] simulate-logins.py → logins.json (FQDN auth, tz-aware UTC)
[5/5] Deploy emulation services
      ├── Linux: install-rampart-emulation.yaml → systemd
      └── Windows: rampart.py::_deploy_windows_emulation → scheduled task
```

Post-deploy: SSH config block installed via
`enterprise_ssh_config.py`, PHASE registered with `--start-date $(today)`
(scopes Zeek log dredging — without it PHASE processes ALL eno2 logs and
fills disk).

## Multi-deployment isolation

Each deploy gets its own DNS zone and NetBIOS name:

- `run_dir/cloud-config-prefixed.json` — cloud config with per-deployment
  `enterprise_url={hash}.{project}.os`
- `run_dir/dns_zone.txt` — zone name for scoped teardown
- `openstack_cloud.py:40` respects pre-set `enterprise_url`
- `-DomainNetBIOSName CASTLE{hash}` in `role_domains.py`

Without these, concurrent deploys collided on AD/DNS. Authentication uses
FQDN (`administrator@castle.{hash}.{project}.os`), not bare NetBIOS, in
`role_domains.py` and `role_human.py`.

## Linux emulation (systemd)

```
Service: rampart-human.service
Binary:  xvfb-run -a /opt/pyhuman/bin/python -u /opt/pyhuman/human.py
Args:    --clustersize 5 --clustersize-sigma {0|0.5}
         --taskinterval 10 --taskinterval-sigma {0|0.5}
         --taskgroupinterval 500
         --seed {seed} --workflows {list}
         --extra passfile /tmp/shib_login.{user}
         --day-start-hour-min {N} --day-start-hour-max {N}
         --activity-daily-min-hours {csv} --activity-daily-max-hours {csv}
Config:  /etc/systemd/system/rampart-human.service
Logs:    journalctl -u rampart-human -f
```

`install-rampart-emulation.yaml` asserts `systemctl is-active` AND
`NRestarts ≤ 10` (catches services oscillating between active and crash).

## Windows emulation (scheduled task)

```
Task:    RampartHuman (AtStartup, SYSTEM, RestartCount=999, RestartInterval=1m)
Script:  C:\tmp\run-emulation.ps1
Binary:  C:\Python\python.exe -u C:\human\human.py
Args:    Same as Linux, including 4 hour-gating fields
Creds:   C:\tmp\shib_login.{username}
```

Why not Ansible: Ansible's `raw` module strips PowerShell `$` variables
(`$action`, `$trigger`, `$false`). No escape method works (`{{ '$' }}`,
`{% raw %}`, cmd echo). `_deploy_windows_emulation()` uses direct
`sshpass` SSH from Python — 10 VMs in parallel via `concurrent.futures`,
4 SSH steps each (passfile, ps1 script, register task, start task), each
`subprocess.run` checks rc and raises with stderr. Errors aggregated via
`Counter` ("19x Authentication failed" once, not 19 buried warnings).

Windows SSH options: `PubkeyAuthentication=no` to prevent pubkey burning
Windows sshd's `MaxAuthTries` before password attempt.

## Hour gating (4 PHASE fields, UTC-indexed)

PHASE writes hour-of-day fields into per-node `user-roles.json`'s
`login_profile`. They reach pyhuman via two paths:

| login_profile field | pyhuman flag |
|---|---|
| `day_start_hour_min` | `--day-start-hour-min` |
| `day_start_hour_max` | `--day-start-hour-max` |
| `activity_daily_min_hours[7]` | `--activity-daily-min-hours` (CSV, Mon=0..Sun=6) |
| `activity_daily_max_hours[7]` | `--activity-daily-max-hours` (CSV) |

Wiring:

- `rampart.py::_generate_emulation_inventory` reads verbatim from
  `login_profile`, writes `rampart_day_start_hour_min/_max` and
  `rampart_activity_daily_min/max_hours` host vars
- `rampart.py::_deploy_windows_emulation` threads same 4 fields into
  `run-emulation.ps1` for the Windows scheduled-task path
- `install-rampart-emulation.yaml` ExecStart appends 4 flags to pyhuman
- `workflows.zip::human.py` adds 4 args, computes per-day active UTC hour
  set via `_select_active_hours_for_day` (mirrors
  `simulate-logins.py::simulate_terminal_day` randomization), re-rolls
  window at UTC midnight, sleeps 60s outside active hours

PHASE pre-projects any window logic (block-mode, SHAP-driven top-K bands,
etc.) onto these 4 fields server-side. RAMPART runtime sees only the
projected `day_start_hour_*` + `activity_daily_*_hours` shape. The legacy
`_phase_block_mode` / `--block-window` path was removed 2026-05-08 — PHASE
no longer emits the field; pyhuman no longer accepts the flag.

Backward compat: empty fields → gate disabled → pyhuman runs 24/7.
**`workflows.zip` and the playbook must roll together** — old
workflows.zip crashes on the new flag set.

Verify: `ssh r-XXXXX-linep3 "journalctl -u rampart-human | grep hour-gate"`
prints `[hour-gate] UTC active hours today (YYYY-MM-DD, dow=N): [14, 15, ...]`.

`simulate-logins.py` writes tz-aware UTC `start_date` → `logins.json`
carries `+00:00` ISO timestamps. Those timestamps are **dead code in
production** — the manual-test path `emulate-logins.py` reads them, but
`_start_emulation` in `rampart.py:419` is unused. Hour fields reach
pyhuman directly via inventory host vars.

## D5 sigma flow

PHASE generates `clustersize_sigma` / `taskinterval_sigma` per-node in
each `{bare_node}/user-roles.json`. Wiring:

- `rampart.py::_generate_emulation_inventory` extracts from each user's
  `login_profile`, passes as `rampart_clustersize_sigma` /
  `rampart_taskinterval_sigma` per-host vars
- `install-rampart-emulation.yaml` ExecStart inserts
  `--clustersize-sigma` / `--taskinterval-sigma` into pyhuman command
- Patched `/opt/pyhuman/human.py` (from local `workflows.zip`) applies
  `random.lognormvariate(0, sigma)` per cluster + per task

Controls get `0/0` (no jitter); feedback gets `0.5/0.5` → clusters range
2-15 around mean of 5.

## user-roles.json mode contract (2026-05-08)

PHASE writes one per-node file for each emulated node it processes,
native schema. Each file carries `_phase_metadata.mode ∈ {feedback,
controls}`. Both modes flow through the same loading code in
`_generate_feedback_user_roles`:

```python
mode = data["_phase_metadata"]["mode"]
if mode not in ("feedback", "controls"):
    FATAL  # sys.exit(1) with banner — PHASE schema bumped, RAMPART hasn't
# Same load + rename + enterprise-config rewrite for both modes.
# Difference is purely server-side content of the roles array:
#   - feedback: tuned role (windows pre-projected onto day_start/activity_daily)
#   - controls: pristine baseline (identical shape to canonical user-roles.json)
```

Per-node status from PHASE manifest: `feedback` / `controls` / `skipped`.
Skipped nodes get NO file emitted — RAMPART falls through to the
baseline-trio fallback (no special handling needed).

```
/mnt/AXES2U1/feedback/rampart-controls/{dataset}/
  linep2/user-roles.json    linep3/user-roles.json    ...    linep10/user-roles.json
  winep1/user-roles.json    winep2/user-roles.json    ...    winep10/user-roles.json
  (up to 19 files; dc1/dc2/dc3/linep1 absent — user: null in enterprise-med.json)
```

Each per-node file:

```json
{
  "roles": [
    {"name": "linep9_user", ...},   // tuned/baseline role (first entry)
    {"name": "standard user", ...}, // baseline clones (PHASE preserves for reference)
    {"name": "power user", ...},
    {"name": "admin user", ...}
  ],
  "_phase_metadata": {"mode": "feedback", "baseline_role": "admin user", "timezone": "UTC", ...}
}
```

`_generate_feedback_user_roles` flow:

1. Walk `behavior_source/*/user-roles.json` to discover processed nodes
2. Read `_phase_metadata.mode` → FATAL on unknown (schema-regression gate)
3. Extract first role (the `{bare_node}_user`)
4. Clone and rename to `r-{hash}-{bare_node}_user` (e.g.
   `r-14a6d-linep9_user`) — `r-{hash}-` prefix applied here so concurrent
   deploys don't collide on role names
5. Walk `enterprise-config-prefixed.json` nodes, strip `r-{hash}-` prefix
   via regex, look up matching role, rewrite node's `"user"` field
6. Nodes with `user: null` (dc1-3, linep1) left unchanged
7. Combine per-node roles with 3 baseline roles (`standard user`, `power
   user`, `admin user`) loaded from workflow baseline → fallback for any
   unfed node
8. Write `user-roles-feedback.json` and `enterprise-config-feedback.json`
   to run dir (filename retained for both modes — output content
   reflects whichever mode PHASE emitted)

Baseline role assignment (in `_phase_metadata.baseline_role`):

- `winep1..winep10` and `linep2..linep8` → `standard user`
- `linep9` → `admin user`
- `linep10` → `power user`

So `linep9` inherits admin's
`fraction_of_logins_to_personal_machine: "0.2"` while receiving
PHASE-supplied timing fields. Enterprise-only workflows
(`browse_iis`, `browse_shibboleth`, `moodle`, `build_software`)
preserved by PHASE during cloning.

Feedback flavor bump: `core/feedback.py::generate_rampart_feedback_config`
overrides `enterprise.cloud_config` to `axes-cicd-feedback.json`
(m1.small → m1.medium). Controls stay on m1.small.

## SSH access

Linux:

```bash
ssh r-bf351-linep3 "systemctl status rampart-human"
ssh r-bf351-linep3 "journalctl -u rampart-human -f"
```

Windows:

```bash
SSH_AUTH_SOCK="" sshpass -p '<admin_pass>' ssh \
  -o StrictHostKeyChecking=no \
  -o PreferredAuthentications=password \
  -o PubkeyAuthentication=no \
  Administrator@castle.{hash}.{project}.os@<win_ip> \
  "powershell -Command (Get-ScheduledTask -TaskName RampartHuman).State"
```

## Health check

```bash
# Linux endpoints
for ip in <linep_ips>; do
  echo -n "$ip: "
  SSH_AUTH_SOCK="" ssh -o IdentitiesOnly=yes -i ~/.ssh/id_rsa ubuntu@$ip \
    "systemctl is-active rampart-human" 2>/dev/null
done

# Windows endpoints
for ip in <winep_ips>; do
  echo -n "$ip: "
  SSH_AUTH_SOCK="" sshpass -p '<admin_pass>' ssh \
    -o PreferredAuthentications=password -o PubkeyAuthentication=no \
    Administrator@castle.{hash}.{project}.os@$ip \
    "powershell -Command (Get-ScheduledTask -TaskName RampartHuman).State" 2>/dev/null
done
```

RAMPART VMs do NOT produce DECOY-format JSONL logs. Logs are:

- Linux: `journalctl -u rampart-human` (systemd journal, pyhuman stdout)
- Windows: scheduled task captures, no persistent log file by default
- Network: Zeek on eno2 (axes), processed by PHASE pipeline scoped by
  `start_date`

DECOY's `collect_sup_logs.py` finds nothing on RAMPART VMs and skips
silently — harmless.

## Manual workflow testing (`emulate-logins.py`)

Kept for manual testing; NOT used in deploy.

```bash
cd ~/uva-cs-workflow
source .venv/bin/activate && source ~/vxn3kr-bot-rc

# Single workflow (fast-debug compresses timings)
python3 emulate-logins.py post-deploy-output.json logins.json \
  --seed 42 --logfile test.ndjson --fast-debug --workflows browse_web

# Multiple
python3 emulate-logins.py post-deploy-output.json logins.json \
  --seed 42 --logfile test.ndjson --fast-debug \
  --workflows browse_iis moodle google_search
```

Available: `browse_iis`, `browse_shibboleth`, `browse_web`,
`browse_youtube`, `build_software`, `download_files`, `google_search`,
`moodle`, `spawn_shell`.

## Post-deploy fail-loud (`post-deploy.py::_check_step_results`)

Runs after every parallel batch (register_windows, join_domains,
deploy_human, Moodle, setup_fileservers). Counts errors, prints pattern
summary, `sys.exit(1)` if > 10% fail. Prints
`[step_name] OK — all N succeeded` on success.

Linux domain-join verification: SSH as `ubuntu` (cloud-init creds), run
`sudo realm list`. Window 300s.

DC promotion is sequential. `_wait_for_domain_reachable()` runs
`nltest /dsgetdc:{domain}` from new follower DC's POV in a 15s loop
(timeout 600s) BEFORE the retry loop starts. Common case ~15s; follower
after leader-reboot waits as needed. Timeout → fail loud with "leader DC
mid-reboot or AD service not started" message.

## experiments.json fcntl lock

`register_experiment.py` and `teardown.py::_close_phase_experiment` both
take `fcntl.LOCK_EX` on `/mnt/AXES2U1/experiments.json.lock` for full
read-modify-write cycle, then write via tempfile + fsync + `os.replace`.
Atomic, serialized. `register_experiment.py` re-reads after write to
catch NFS blip silent drops; reports missing IPs + recommends retry.

If batch loses entries (regression), recover by iterating active deploys:

```bash
register_experiment.py --name <dep> --snippet {run}/ssh_config_snippet.txt \
  --inventory {run}/inventory.ini --run-id {run}
```

## Constraints

- Do NOT delete `bot-desktop` or `enterprise-key` keypairs
- `enterprise-key` (`~/.ssh/id_rsa`) must be RSA in PEM format — used for
  VM provisioning + Windows password decryption
- `~/uva-cs-workflow/` is the active copy
- `sshpass` must be installed on mlserv (Windows emulation)
- VMs set EST via `role_domains.py` for log readability; runtime hour
  reads use UTC (contract in CLAUDE.md)
