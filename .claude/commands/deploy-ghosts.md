# GHOSTS NPC Deployment - System Context

Load critical context about the GHOSTS NPC traffic generator deployment system within RUSE before working on it. Read all the files listed below, then summarize the current state for the user.

## Instructions

Read the following files in order to understand the GHOSTS deployment system:

### Core GHOSTS Deploy Files
1. `deployments/cli/commands/ghosts.py` - Main GHOSTS deployment orchestration (spinup lifecycle, VM provisioning, feedback timeline generation)
2. `deployments/cli/config.py` - DeploymentConfig with `is_ghosts()`, ghosts helpers (api_flavor, client_count, etc.)
3. `deployments/ghosts-controls/config.yaml` - Baseline GHOSTS deployment config (1 API + 5 NPC clients, no feedback)

### Ansible Playbooks
4. `deployments/playbooks/install-ghosts-api.yaml` - API VM: Docker install, GHOSTS repo clone, docker compose up (postgres, api, frontend, n8n, grafana)
5. `deployments/playbooks/install-ghosts-clients.yaml` - Client VMs: .NET 9 SDK, build GHOSTS universal client, configure application.json + timeline.json, systemd service

### PHASE Feedback Integration (post Stage 2, 2026-04-09)
6. `deployments/cli/commands/ghosts.py::_build_npc_timeline_mapping()` - Walks `behavior_source/npc-*/timeline.json`, matches each PHASE timeline to a client VM by parsing the trailing npc-N from the VM name, copies each to `run_dir/timelines/{vm_name}.json`, returns `{vm_name: Path}` mapping
7. `deployments/cli/commands/feedback.py` - Feedback source resolution, `--target` flag, `find_feedback_by_target()`, `DATASET_TARGETS` mapping, `_is_valid_feedback_source()` glob validator (no more manifest.json)

**phase_to_timeline.py was deleted in Stage 2.** PHASE now writes
target-native per-NPC `timeline.json` files directly; RUSE reads them
as-is and routes each to its target VM via per-host Ansible inventory
variables. No translation layer.

### CLI Integration
8. `deployments/cli/__main__.py` - CLI routing: `--ghosts` flag, `--target` flag, feedback flag passthrough to ghosts spinup

### Teardown & List
9. `deployments/cli/commands/teardown.py` - `_ghosts_teardown()`: delete VMs by `g-{hash}-` prefix, OpenStack verification
10. `deployments/cli/commands/list_cmd.py` - GHOSTS active detection via `g-{hash}-` prefix, VM summary display

### GHOSTS Framework (external)
11. `~/GHOSTS/src/Ghosts.Api/docker-compose.yml` - GHOSTS API Docker stack (5 services)
12. `~/GHOSTS/src/Ghosts.Client.Universal/config/application.json` - Client config format (ApiRootUrl, Sockets, Timeline location)
13. `~/GHOSTS/src/Ghosts.Client.Universal/config/timeline.example.yaml` - Example timeline (Firefox with 500+ URLs)

## Architecture

GHOSTS is the third deployment type in RUSE alongside SUP (`type: sup`) and Enterprise (`type: enterprise`).

```
┌─────────────────────────────────────────────────────────────────────────┐
│  ./deploy --ghosts [--all|--timing|--sites] [--target summer24]        │
│  Python CLI: deployments/cli/__main__.py                                │
├─────────────────────────────────────────────────────────────────────────┤
│  ghosts.py orchestrator                                                 │
│  [1/5] Provision VMs (OpenStack Python wrapper, not Ansible)           │
│  [2/5] Test SSH connectivity (parallel, 20 workers)                    │
│  [3/5] Install GHOSTS API (Ansible: Docker + docker compose up)        │
│  [---] Generate PHASE timeline (if --all/--timing/etc. provided)       │
│  [4/5] Install GHOSTS clients (Ansible: .NET 9 + build + systemd)      │
│  [5/5] Finalize (SSH config, deployment_type marker)                   │
└─────────────────────────────────────────────────────────────────────────┘

VM Topology:
  g-{hash}-api-0    Docker: ghosts-api(:5000), frontend(:4200),
                     postgres(:5432), n8n(:5678), grafana(:3000)
                          │
                          │ HTTP/WebSocket :5000/api
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
  g-{hash}-npc-0   g-{hash}-npc-1   g-{hash}-npc-N
  .NET 9 client     .NET 9 client     .NET 9 client
  systemd service   systemd service   systemd service
  timeline.json     timeline.json     timeline.json
```

## Key Concepts

### Deployment Type Detection
`type: ghosts` in config.yaml → `config.is_ghosts()` → routes to `ghosts.py` for spinup, `_ghosts_teardown()` for teardown.

### VM Naming
- Prefix: `g-{hash}-` where hash = MD5(dep_id)[:5]
- API: `g-{hash}-api-0`
- Clients: `g-{hash}-npc-{i}`
- `teardown-all.yaml` catches all prefixes: `(r-|e-|g-|sup-)`

### GHOSTS Config Format
```yaml
deployment_name: ghosts-controls
type: ghosts

ghosts:
  api_flavor: v1.14vcpu.28g        # Flavor for API VM (Docker stack)
  client_flavor: v1.14vcpu.28g     # Flavor for NPC client VMs
  client_count: 5                   # Number of NPC clients
  ghosts_repo: https://github.com/cmu-sei/GHOSTS.git
  ghosts_branch: master
```

### Inventory Format (two host groups)
```ini
[ghosts_api]
g-{hash}-api-0 ansible_host=10.x.x.x

[ghosts_clients]
g-{hash}-npc-0 ansible_host=10.x.x.y ghosts_api_ip=10.x.x.x
g-{hash}-npc-1 ansible_host=10.x.x.z ghosts_api_ip=10.x.x.x
```

`ghosts_api_ip` host var is how each client knows the API VM's address.

### GHOSTS Client Configuration
Each NPC client has two config files at `/opt/ghosts-client/config/`:
- `application.json` — API connection: `ApiRootUrl: http://{api_ip}:5000/api`, SignalR WebSocket, polling intervals
- `timeline.json` — Behavioral definition: handler types, URLs, commands, delays, active hours

### Client Registration Flow
1. Client starts → connects to API via SignalR WebSocket (`/clientHub`)
2. Sends machine ID, hostname, IP, version via headers
3. API registers in PostgreSQL `Machines` table
4. Client polls for timeline updates, reports activity results
5. Verify: `curl localhost:5000/api/machines` on API VM

### PHASE Feedback → Per-NPC Timeline Routing (Stage 2, 2026-04-09)

PHASE's feedback engine writes one tuned `timeline.json` per NPC, at
`~/PHASE/feedback_engine/configs/axes-ghosts-*/npc-{N}/timeline.json`.
Each timeline is already in the native GHOSTS schema expected by
`install-ghosts-clients.yaml` — `{"Status": "Run", "TimeLineHandlers":
[...], "_phase_metadata": {...}}` — with per-VM tuning (different
browser delays, handler mixes, lognormal sigmas). There is no
translation layer; RUSE reads these files as-is and routes each to its
target VM.

**Routing flow** in `ghosts.py::run_ghosts_spinup`:

1. After provisioning but **before** writing the inventory, call
   `_build_npc_timeline_mapping(source, client_vms, run_dir)`. This
   walks `source/npc-*/timeline.json`, matches each file to a client
   VM by extracting the trailing `npc-N` from the VM name
   (`g-{hash}-npc-0` → `npc-0`), copies each timeline to
   `run_dir/timelines/{vm_name}.json` for a self-contained run dir,
   and returns a `{vm_name: Path}` mapping.

2. `_write_inventory()` accepts that mapping and appends a per-host
   `ghosts_timeline_file=/abs/path/to/{vm_name}.json` variable to each
   client VM's inventory line, alongside the existing `ghosts_api_ip=`
   variable. Example inventory output:
   ```
   [ghosts_clients]
   g-14a6d-npc-0 ansible_host=10.0.0.10 ghosts_api_ip=10.0.0.5 ghosts_timeline_file=/abs/path/timelines/g-14a6d-npc-0.json
   g-14a6d-npc-1 ansible_host=10.0.0.11 ghosts_api_ip=10.0.0.5 ghosts_timeline_file=/abs/path/timelines/g-14a6d-npc-1.json
   ...
   ```

3. `install-ghosts-clients.yaml` is unchanged — the existing
   `{{ ghosts_timeline_file }}` reference in the "Deploy PHASE-generated
   timeline" task is now resolved per-host via standard Ansible
   inventory variable lookup. Baseline (no-feedback) deploys have no
   `ghosts_timeline_file` variable in the inventory, so the playbook's
   default-timeline fallback path runs instead.

**Fail-loud semantics**: if `behavior_source` has no
`npc-*/timeline.json` files, the deploy exits early with a clear error
naming the expected layout. No silent fallback to a shared timeline.

**API VM is never a target**: `install-ghosts-clients.yaml` has
`hosts: ghosts_clients`, and the API VM lives in `[ghosts_api]` — it
doesn't run the client playbook at all, so it's never routed a
timeline.

### CLI Usage
```bash
# Baseline (hardcoded default timeline) → deploys ghosts-controls
./deploy --ghosts

# With PHASE feedback → auto-generates ghosts-feedback-{preset}-{dataset}-{scope}
./deploy --ghosts --feedback                         # → ghosts-feedback-stdctrls-sum24-all
./deploy --ghosts --feedback --target summer24       # → ghosts-feedback-stdctrls-sum24-all

# Explicit source path
./deploy --ghosts --feedback --source ~/PHASE/feedback_engine/configs/axes-ghosts-controls_axes-summer24_std-ctrls

# Teardown (feedback dirs auto-cleaned on teardown)
./teardown ghosts-controls-MMDDYYHHMMSS
./teardown ghosts-feedback-stdctrls-sum24-all-MMDDYYHHMMSS

# List
./list
```

### Deployment Naming (mirrors RUSE pattern)
- `ghosts-controls` — Baseline GHOSTS NPCs (no feedback, default timeline)
- `ghosts-feedback-{preset}-{dataset}-{scope}` — Auto-generated feedback deployments
  - Example: `ghosts-feedback-stdctrls-sum24-all` (all configs, summer24 target)
  - Example: `ghosts-feedback-stdctrls-sum24-timing` (timing only)
  - Cleaned up on teardown (entire directory removed, like `ruse-feedback-*`)

### Known Build Issues (patched in playbooks)
1. **Frontend npm conflict**: GHOSTS Angular frontend has peer dep mismatch. Patched by `sed` replacing `RUN npm ci` → `RUN npm ci --legacy-peer-deps` in Dockerfile. Handles Windows line endings (`\r\n`).
2. **Client NLog version**: `Ghosts.Domain` wants NLog >= 6.0.6, client pins 6.0.5. Patched with `/p:NoWarn=NU1605` in `dotnet publish`.
3. **Client DLL casing**: Published DLL is PascalCase `Ghosts.Client.Universal.dll`. Systemd ExecStart must match.

### Memleak Mitigation — Cgroup Memory Cap (2026-04-27, FEEDBACK-ONLY)

Upstream `cmu-sei/GHOSTS` .NET client leaks memory until the kernel
OOM-killer takes out sshd before the leaky process — 23/40 NPCs were
SSH-unreachable 3h post-deploy on 2026-04-27 audit. **Pure-upstream
clients are unrunnable past 2-3h without external rescue (hard-reboot).**

Mitigation lives in a systemd drop-in at:
```
/etc/systemd/system/ghosts-client.service.d/memcap.conf
```

```ini
[Service]
MemoryMax=20G
MemorySwapMax=0
```

When .NET RSS hits the cap, the kernel kills the process **inside its
cgroup** ONLY; systemd respawns it via the existing `Restart=always`
within `RestartSec=10`. sshd / cron / system services stay alive — VM
remains usable indefinitely even as the leak recurs every ~2h.

**Scope: feedback deploys ONLY.** Controls keep the pure upstream unit
so they remain experimentally pristine (leaky-as-designed). Treated as
a feedback-cycle improvement, not a baseline change.

**Wiring**: `ghosts.py` passes `is_feedback={true,false}` extra_var
to `install-ghosts-clients.yaml`, set from `behavior_source is not None`.
Playbook conditionally creates the drop-in dir + memcap.conf via
`when: is_feedback | default(false) | bool` after the base unit is
written. Drop-in pattern (vs editing the base unit) keeps the diff
reversible — delete the file to remove the cap.

**Audit signal**: Feedback NPCs may show `NRestarts > 0` as the cgroup
OOM cycle fires — that's expected and healthy. Pre-cap, NPCs would have
gone SSH-fail entirely; post-cap they cycle gracefully and stay reachable.

### PHASE Feedback Engine Integration

To generate GHOSTS-specific feedback:
1. Deploy GHOSTS NPCs → collect traffic on AXES network
2. Register experiment in `/mnt/AXES2U1/experiments.json` with GHOSTS VM IPs
3. Run: `poetry run python LAUNCH_FEEDBACK.py -e axes-ghosts-deployment -t axes-summer24`
4. Redeploy: `./deploy --ghosts --all --target summer24`

**Dataset targets** (in `feedback.py`):
```python
DATASET_TARGETS = {
    "summer24": "summer24", "sum24": "summer24",
    "fall24": "fall24",
    "spring25": "spring25", "spr25": "spring25",
}
```

### Run Directory Contents
```
deployments/ghosts-controls/runs/<run_id>/          # Baseline
deployments/ghosts-feedback-stdctrls-sum24-all/runs/<run_id>/  # Feedback
├── config.yaml              # Snapshot of deployment config
├── inventory.ini            # Two groups: [ghosts_api] + [ghosts_clients]
│                            #   client lines carry per-host ghosts_timeline_file=
├── ssh_config_snippet.txt   # SSH access for all VMs
├── deployment_type          # Marker file containing "ghosts"
└── timelines/               # Per-NPC PHASE timelines (if feedback enabled)
    ├── g-{hash}-npc-0.json  # Tuned timeline for npc-0 (distinct DelayAfter,
    ├── g-{hash}-npc-1.json  # handler mix, lognormal sigmas)
    ├── g-{hash}-npc-2.json
    ├── g-{hash}-npc-3.json
    └── g-{hash}-npc-4.json
```

### Feedback Config Generation
When `./deploy --ghosts --feedback` (or `--all`) is used with `ghosts-controls`, the CLI auto-generates a `ghosts-feedback-*` deployment directory via `generate_ghosts_feedback_config()` in `feedback.py`. Since Stage 2 (2026-04-09), validity is checked via `_is_valid_feedback_source(source_dir, "ghosts")` which globs for `npc-*/timeline.json`, and `preset`/`dataset` are parsed from the source dir name via `_parse_source_name()`. The generated config has `type: ghosts`, `behavior_source` (PHASE dir path), `behavior_configs` ("all" or list of filenames), and the same ghosts section as `ghosts-controls`. On teardown, `ghosts-feedback-*` directories are cleaned up entirely (like `ruse-feedback-*`).

### VM Provisioning Safety
`_provision_vms()` in ghosts.py tracks which VMs reach ACTIVE state and only includes those in the inventory. VMs that reach ERROR state are excluded — preventing confusing downstream Ansible failures on broken VMs.

### Feedback Source Validation
Since Stage 2, `_is_valid_feedback_source(source_dir, "ghosts")` in `feedback.py` checks that the source contains `npc-*/timeline.json` files (the PHASE GHOSTS generator output). There is no more `manifest.json` marker, and no more "find any JSON subdir" fallback — if the expected layout is missing, the deploy fails loud with a clear message. See `_build_npc_timeline_mapping()` in `ghosts.py` for the VM → timeline routing logic.

## Stage 3 Fail-Loud Assertions (2026-04-14)

`ghosts.py::run_ghosts_spinup` now enforces strict success contracts
at every step (was previously silently continuing past failures):

- **G1 — API install failure aborts**: `if api_result.rc != 0: return api_result.rc`.
  Previously the deploy continued to install clients against a dead API.
- **G2 — Client install failure aborts**: same pattern. Previously the
  final return only surfaced `api_result.rc`, so client failures on a
  healthy API were swallowed.
- **G3 — SSH 90% threshold**: if fewer than 90% of VMs respond to SSH
  after provisioning, the deploy aborts with a specific error. Previously
  a warning and continued against unreachable hosts.
- **G4 — VM→ACTIVE threshold + IP audit**: `_provision_vms` fails if
  < 90% VMs reach ACTIVE. Additionally any ACTIVE VM whose IP can't be
  extracted via `openstack server show -c addresses` is flagged as a
  FAIL instead of silently dropped from the inventory.
- **G6 — per-NPC timeline coverage**: `_build_npc_timeline_mapping`
  now `raise RuntimeError` if any client VM can't be matched to a
  `npc-*/timeline.json` file. Caller catches and aborts. Previously
  unmatched VMs silently fell back to the default timeline, losing
  PHASE tuning with no warning.

`install-ghosts-api.yaml` assertions (was using `ignore_errors: yes`):
- **C3 — API health**: explicit `fail:` task when the `/api/machines`
  health check doesn't return 200 after 5 minutes of retries (switched
  from `/api/home` on 2026-04-17 — upstream CMU-SEI removed that
  endpoint; Kestrel now returns 404 even when the API is healthy).
  Previously logged "NOT RESPONDING (may still be starting)" as an
  INFO and continued.
- **G7 — shell `set -euo pipefail`**: Docker install shell now aborts
  mid-script on curl/apt failures. Previously any mid-script failure
  was silently skipped.
- **G8 — file existence asserts**: stat + assert that
  `src/Ghosts.Frontend/Dockerfile` exists before sed-patching it.
  Previously sed against a missing file just failed obscurely.
- **G9 — docker compose error detection**: asserts `rc == 0 AND no
  "ERROR" in stdout`. Compose sometimes exits 0 while build phase
  emits ERROR to stdout.

`install-ghosts-clients.yaml` assertions:
- **C4 — client service**: explicit `fail:` when `systemctl is-active
  ghosts-client` returns anything other than `active`. Previously
  `ignore_errors: yes` swallowed failures.
- **G10 — dotnet publish output**: stat + assert
  `Ghosts.Client.Universal.dll` exists at the publish location.
  Previously the systemd service could start against a missing binary
  and crash-loop silently.

### Teardown improvements
- **Orphan volume cleanup** — every teardown sweeps nameless/200GB/
  available volumes. Previously GHOSTS teardowns left boot volumes
  behind.
- **experiments.json closure** — `_close_phase_experiment(config_name)`
  sets `end_date` on the matching PHASE registration. Previously
  `PHASE.py --ghosts` batch pipeline picked up torn-down deploys as
  active and tried to dredge their IPs.

### Docker Hub rate-limit auth (2026-04-17)

Unauthenticated Docker Hub pulls are capped at 100/6hr per source
IP. A 7-deploy batch run hit that limit on deploy #7 pulling
`postgres:16.8`, `grafana/grafana`, and `docker.n8n.io/n8nio/n8n`.
`install-ghosts-api.yaml` now:
- Reads `~/.docker-hub-token` + `~/.docker-hub-token-user` on mlserv
  if present, copies to VM `/tmp/.dh-token` + `/tmp/.dh-user`, runs
  `docker login`, then deletes the staged creds. Missing files = no
  login = unauth pulls (same behavior as before).
- Retries `docker compose up` once after 60s for transient flakes.
- Dedicated `Detect Docker Hub rate-limit` assertion surfaces the
  specific error with remediation ("wait 6h, or add PAT").

Setup (one-time):
```bash
echo 'YOUR_PAT' > ~/.docker-hub-token && chmod 600 ~/.docker-hub-token
echo 'YOUR_USER' > ~/.docker-hub-token-user && chmod 600 ~/.docker-hub-token-user
```

### Known: ghosts-client .NET memory leak

Upstream `Ghosts.Client.Universal.dll` grows unbounded in RAM. On a
28GB VM the process reaches ~25GB (89%) in a few hours. Once memory
pressure starts, sshd becomes unresponsive and the VM looks hung
(ACTIVE on OpenStack, responds to ICMP, SSH connects drop or hang).

Remediation:
```bash
source ~/vxn3kr-bot-rc
openstack server reboot --hard g-<hash>-npc-N
```

Not patching locally — this is upstream CMU-SEI's code. If experiments
start hitting this regularly, add `MemoryMax=8G` to
`install-ghosts-clients.yaml`'s systemd unit + daily restart cron
(mirrors MCHP pattern in install-sups.yaml S5). See
`memory/project_ghosts_client_memleak.md` for full notes.

After reading these files, provide a brief summary of the current state and any recent changes visible in the code.
