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

### PHASE Feedback Integration
6. `deployments/lib/phase_to_timeline.py` - Translates PHASE behavioral configs into GHOSTS timeline.json (timing windows, weighted URLs, delays, stickiness, DNS noise)
7. `deployments/cli/commands/feedback.py` - Feedback source resolution, `--target` flag, `find_feedback_by_target()`, `DATASET_TARGETS` mapping

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

### PHASE Feedback → GHOSTS Timeline Translation

`phase_to_timeline.py` reads PHASE behavioral configs and generates a GHOSTS timeline:

| PHASE Config | GHOSTS Timeline Effect |
|---|---|
| `timing_profile.json` → `hourly_distribution.mean_fraction` | Split day into peak/normal BrowserFirefox handlers with different UtcTimeOn/Off and DelayAfter |
| `site_config.json` → `domain_categories` + `site_categories` | Weighted URL list (lightweight 6x, medium 3x, heavy 2x repetition) |
| `behavior_modifiers.json` → `page_dwell`, `navigation_clicks` | DelayAfter (ms), stickiness + stickiness-depth |
| `workflow_weights.json` → workflow weights | Handler type mix (BrowserFirefox, Bash, Curl proportions) |
| `activity_pattern.json` → `active_hour_range` | Constrains which hours have active handlers |
| `diversity_injection.json` → `background_services` | DNS lookup bash events (nslookup every ~6min) |

Each config is optional — missing configs fall back to sensible defaults. The translator is a pure function: `generate_timeline(feedback_dir: Path) -> dict`.

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
├── ssh_config_snippet.txt   # SSH access for all VMs
├── deployment_type          # Marker file containing "ghosts"
└── timeline.json            # PHASE-generated timeline (if feedback enabled)
```

### Feedback Config Generation
When `./deploy --ghosts --all` (or any feedback flag) is used with `ghosts-controls`, the CLI auto-generates a `ghosts-feedback-*` deployment directory via `generate_ghosts_feedback_config()` in `feedback.py`. This mirrors `generate_feedback_config()` for RUSE SUPs. The generated config has `type: ghosts` and the same ghosts section as `ghosts-controls`. On teardown, `ghosts-feedback-*` directories are cleaned up entirely (like `ruse-feedback-*`).

After reading these files, provide a brief summary of the current state and any recent changes visible in the code.
