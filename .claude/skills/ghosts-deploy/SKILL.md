---
name: ghosts-deploy
description: GHOSTS NPC deployment — running ./deploy --ghosts [feedback], 5-phase spinup of 1 API + N .NET clients with per-NPC timeline.json routing, cgroup memcap, Docker Hub auth. Inputs deployments/ghosts-controls/config.yaml + /mnt/AXES2U1/feedback/ghosts-controls/{dataset}/npc-{N}/timeline.json. Outputs deployments/ghosts-{controls,feedback-...}/runs/{run_id}/. Does NOT cover DECOY SUPs (see /decoy-deploy) or RAMPART AD (see /rampart-deploy). Cross-type CLI shape, fail-loud contract, and SSH key matrix live in CLAUDE.md.
type: skill
---

# ghosts-deploy

GHOSTS = CMU SEI NPC traffic generators. Upstream `cmu-sei/GHOSTS`
provides a .NET 9 client that registers with an API server and runs
behavioral timelines (BrowserFirefox, Bash, Curl handlers).

| | |
|---|---|
| Inputs | `deployments/ghosts-controls/config.yaml`, `~/GHOSTS/` (clone of `cmu-sei/GHOSTS` master), `/mnt/AXES2U1/feedback/ghosts-controls/{dataset}/npc-{N}/timeline.json` (5 per-NPC tuned timelines), `~/.docker-hub-token` + `~/.docker-hub-token-user` (optional) |
| Outputs | `deployments/ghosts-{controls,feedback-...}/runs/{run_id}/` (config.yaml snapshot, inventory.ini with `[ghosts_api]` + `[ghosts_clients]` host vars, ssh_config_snippet.txt, deployment_type, timelines/g-{hash}-npc-N.json) |
| Manifest | PHASE source `manifest.json`; same loader as DECOY/RAMPART |
| Upstream | PHASE feedback engine writes target-native per-NPC `timeline.json` directly (no translation layer) |
| Downstream | PHASE Zeek pipeline (`PHASE.py --ghosts`) scoped by `start_date` |
| Narrow exceptions | api-0 absent from feedback source (server VM, no timeline); baseline deploys with no `ghosts_timeline_file` host var fall back to playbook's default timeline |

## Topology

```
g-{hash}-api-0    Docker stack: ghosts-api(:5000), frontend(:4200),
                                postgres(:5432), n8n(:5678), grafana(:3000)
                          │
                          │ HTTP/SignalR :5000/api
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
  g-{hash}-npc-0   g-{hash}-npc-1   g-{hash}-npc-N
  .NET 9 client    .NET 9 client    .NET 9 client
  systemd          systemd          systemd
  timeline.json    timeline.json    timeline.json
```

`ghosts-controls` baseline: 1 API + 5 NPC clients (`v1.14vcpu.28g`),
`cmu-sei/GHOSTS` master.

## Deploy config

```yaml
deployment_name: ghosts-controls
type: ghosts

ghosts:
  api_flavor: v1.14vcpu.28g
  client_flavor: v1.14vcpu.28g
  client_count: 5
  ghosts_repo: https://github.com/cmu-sei/GHOSTS.git
  ghosts_branch: master
```

## Spinup phases (`ghosts/spinup.py`)

1. Provision VMs (OpenStack Python wrapper, NOT Ansible) — tracks ACTIVE
   state, IP-extraction audit
2. SSH connectivity test (parallel, 20 workers) — abort if < 90%
3. Per-NPC timeline routing (feedback only, between provision and
   inventory write)
4. Install GHOSTS API (`install-ghosts-api.yaml` — Docker + docker
   compose up)
5. Install GHOSTS clients (`install-ghosts-clients.yaml` — .NET 9 SDK +
   build + systemd)
6. Finalize: SSH config, `deployment_type` marker, PHASE register
   (fail-loud)

## Inventory format (two host groups)

```ini
[ghosts_api]
g-{hash}-api-0 ansible_host=10.x.x.x

[ghosts_clients]
g-{hash}-npc-0 ansible_host=10.x.x.y ghosts_api_ip=10.x.x.x ghosts_timeline_file=/abs/path/timelines/g-{hash}-npc-0.json
g-{hash}-npc-1 ansible_host=10.x.x.z ghosts_api_ip=10.x.x.x ghosts_timeline_file=/abs/path/timelines/g-{hash}-npc-1.json
```

Host vars:

- `ghosts_api_ip` → each client knows API VM's address
- `ghosts_timeline_file` (feedback only) → per-NPC tuned timeline path

## Client config (`/opt/ghosts-client/config/`)

- **application.json** — API connection: `ApiRootUrl: http://{api_ip}:5000/api`, SignalR WebSocket, polling intervals
- **timeline.json** — Behavioral definition: handler types
  (BrowserFirefox, Bash, Curl), URLs, commands, delays, active hours

## Client registration

1. Client starts → connects to API via SignalR WebSocket (`/clientHub`)
2. Sends machine ID, hostname, IP, version via headers
3. API registers in PostgreSQL `Machines` table
4. Client polls for timeline updates, reports activity results
5. Verify: `curl localhost:5000/api/machines` on API VM

## PHASE feedback → per-NPC timeline routing

PHASE writes one tuned `timeline.json` per NPC at
`/mnt/AXES2U1/feedback/ghosts-controls/{dataset}/npc-{N}/timeline.json`.
Native GHOSTS schema: `{"Status": "Run", "TimeLineHandlers": [...],
"_phase_metadata": {...}}`. Per-VM tuning (different DelayAfter, handler
mixes, lognormal sigmas).

Routing flow (`ghosts.py::run_ghosts_spinup`):

1. After provision, before inventory write, call
   `_build_npc_timeline_mapping(source, client_vms, run_dir)`. Walks
   `source/npc-*/timeline.json`, matches each to a client VM by extracting
   trailing `npc-N` from VM name (`g-{hash}-npc-0` → `npc-0`), copies
   each to `run_dir/timelines/{vm_name}.json` for self-contained run dir,
   returns `{vm_name: Path}`
2. `_write_inventory()` accepts mapping, appends per-host
   `ghosts_timeline_file=/abs/path/{vm_name}.json` to each client line
3. `install-ghosts-clients.yaml::Deploy PHASE-generated timeline` task
   uses `{{ ghosts_timeline_file }}` per-host. Baseline deploys with no
   var → playbook's default-timeline fallback runs

Fail-loud (G6): if `behavior_source` has no `npc-*/timeline.json` files,
deploy exits early. Partial coverage (some VMs missing timelines, or VMs
without `npc-N` naming) raises `RuntimeError` — caller aborts. No silent
fallback to default.

API VM never targeted: `install-ghosts-clients.yaml` has
`hosts: ghosts_clients`; API VM is in `[ghosts_api]`.

## Memcap drop-in (FEEDBACK ONLY)

Upstream `cmu-sei/GHOSTS` .NET client leaks memory until kernel OOM-killer
takes out sshd before the leaky process — pure-upstream clients
unrunnable past 2-3h without hard-reboot.

Mitigation: systemd drop-in at
`/etc/systemd/system/ghosts-client.service.d/memcap.conf`:

```ini
[Service]
MemoryMax=20G
MemorySwapMax=0
```

When .NET RSS hits cap, kernel kills process **inside its cgroup ONLY**;
systemd respawns via `Restart=always` within `RestartSec=10`. sshd / cron
/ system services stay alive — VM remains usable indefinitely even as
leak recurs every ~2h.

Scope: feedback ONLY. Controls keep pure upstream so they remain
experimentally pristine.

Wiring: `ghosts.py` passes `is_feedback={true,false}` extra_var to
`install-ghosts-clients.yaml`, set from `behavior_source is not None`.
Playbook conditionally creates drop-in via
`when: is_feedback | default(false) | bool`. Drop-in pattern (vs editing
base unit) keeps diff reversible — delete to remove cap.

Audit signal: feedback NPCs may show `NRestarts > 0` as cgroup OOM cycle
fires — expected and healthy. Pre-cap, NPCs went SSH-fail entirely;
post-cap they cycle gracefully and stay reachable.

## Memleak hard-reboot

If the cap doesn't catch leak fast enough OR running a control VM:

```bash
source ~/vxn3kr-bot-rc
openstack server reboot --hard g-<hash>-npc-N
```

Not patching upstream code. If experiments hit this regularly, lower
`MemoryMax` in the drop-in or add daily restart cron mirroring MCHP
pattern.

## Build issues (patched in playbooks)

1. **Frontend npm conflict** — GHOSTS Angular frontend has peer dep
   mismatch. `sed` replaces `RUN npm ci` → `RUN npm ci --legacy-peer-deps`
   in Dockerfile. Handles Windows line endings (`\r\n`)
2. **Client NLog version** — `Ghosts.Domain` wants NLog ≥ 6.0.6, client
   pins 6.0.5. Patched with `/p:NoWarn=NU1605` in `dotnet publish`
3. **Client DLL casing** — Published DLL is PascalCase
   `Ghosts.Client.Universal.dll`. Systemd ExecStart must match

## Docker Hub rate-limit auth

Unauthenticated Docker Hub pulls capped at 100/6hr per source IP.
Multi-deploy batches hit limit pulling `postgres:16.8`, `grafana/grafana`,
`n8nio/n8n`.

`install-ghosts-api.yaml`:

- Reads `~/.docker-hub-token` + `~/.docker-hub-token-user` on mlserv if
  present, copies to VM `/tmp/.dh-token` + `/tmp/.dh-user`, runs
  `docker login`, then deletes staged creds. Missing files = unauth pulls
- Retries `docker compose up` once after 60s for transient flakes
- Dedicated `Detect Docker Hub rate-limit` assertion surfaces specific
  error with remediation ("wait 6h, or add PAT")

Setup (one-time):

```bash
echo 'YOUR_PAT' > ~/.docker-hub-token && chmod 600 ~/.docker-hub-token
echo 'YOUR_USER' > ~/.docker-hub-token-user && chmod 600 ~/.docker-hub-token-user
```

API health probe: `/api/machines` (upstream removed `/api/home`).

## Fail-loud assertions

- `install-ghosts-api.yaml` — `set -euo pipefail` on Docker install,
  Dockerfile stat-then-sed, explicit `fail:` on API health timeout,
  docker compose stdout ERROR detection
- `install-ghosts-clients.yaml` — `set -euo pipefail` on dotnet publish,
  `Ghosts.Client.Universal.dll` stat assertion, `systemctl is-active`
  (no ignore_errors)

## SSH access

```bash
ssh g-14a6d-api-0 "curl -s localhost:5000/api/machines | jq length"
ssh g-14a6d-npc-0 "systemctl status ghosts-client"
ssh g-14a6d-npc-0 "journalctl -u ghosts-client -f"
```

## Run dir contents

```
deployments/ghosts-{controls,feedback-...}/runs/<run_id>/
├── config.yaml              # Snapshot
├── inventory.ini            # [ghosts_api] + [ghosts_clients] (with per-host vars)
├── ssh_config_snippet.txt
├── deployment_type          # "ghosts"
└── timelines/               # Per-NPC PHASE timelines (feedback only)
    ├── g-{hash}-npc-0.json
    ├── g-{hash}-npc-1.json
    └── ...
```

## Constraints

- `bot-desktop` keypair (same as DECOY)
- API VM never gets client install (split host groups in inventory)
- Feedback gets memcap; controls don't
- Hour-of-day reads use `DateTime.UtcNow` (UTC contract in CLAUDE.md)
