---
name: ghosts-deploy
description: GHOSTS NPC deployment — running ./deploy --ghosts [feedback], 5-phase spinup of 1 API + N .NET clients with per-NPC timeline.json routing, cgroup memcap, Docker Hub auth. Inputs deployments/ghosts-controls/config.yaml + /mnt/AXES2U1/feedback/ghosts-controls/{preset}_v{version}/{dataset}/npc-{N}/timeline.json (feedback namespaced 2026-06, needs --preset). Outputs deployments/ghosts-{controls,feedback-...}/runs/{run_id}/. Does NOT cover DECOY SUPs (see /decoy-deploy) or RAMPART AD (see /rampart-deploy). Cross-type CLI shape, fail-loud contract, and SSH key matrix live in CLAUDE.md.
type: skill
---

# ghosts-deploy

GHOSTS = CMU SEI NPC traffic generators. Upstream `cmu-sei/GHOSTS`
provides a .NET 10 client that registers with an API server and runs
behavioral timelines (BrowserFirefox, Bash, Curl handlers).

## CLI scope flags

```bash
# --preset {preset}_v{version} REQUIRED whenever feedback is in scope (2026-06).
./deploy --ghosts --preset std-ctrls_v7.1.2                   # controls + ALL feedback (default)
./deploy --ghosts --controls                                 # controls only (no --preset)
./deploy --ghosts --feedback --preset std-ctrls_v7.1.2       # all feedback (no controls)
./deploy --ghosts --feedback --preset std-ctrls_v7.1.2 --target sum25   # single dataset
./deploy --ghosts --feedback --source /path                  # explicit source (path encodes ns; no --preset)
./deploy --ghosts --controls --preset std-ctrls_v7.1.2 --target sum25   # controls + single feedback
```

`--preset NS` (2026-06): feedback lives under
`/mnt/AXES2U1/feedback/ghosts-controls/{preset}_v{version}/{dataset}/`. REQUIRED
for any feedback deploy; missing/not-found aborts fail-loud. Skip for
`--controls`-only / `--source`. The full ns (version incl.) is stamped into the
deploy NAME → distinct lineages/versions coexist. Spinup lineage-asserts each
per-NPC timeline's `_phase_metadata.model_preset`/`model_version` == the deployed
ns (reads the source copy; the on-VM .NET client strips it). See CLAUDE.md
"Feedback namespace".

`--feedback` is a boolean switch, NOT a value flag. Single-dataset
selection uses `--target NAME` (or `--source /path`). Typing
`./deploy --ghosts --feedback axes-summer25` parses `axes-summer25` as
a positional `config_name`, the filter is silently ignored, and the
deploy runs ALL feedback datasets.

Dataset target aliases live in `core/feedback.py::DATASET_TARGETS`:
`sum25` → `axes-summer25`, `vt50g` → `vt-fall22-50gb`, `axall` →
`axes-all`, etc. Full-name forms (`axes-summer25`, `vt-fall22-50gb`)
also work.

| | |
|---|---|
| Inputs | `deployments/ghosts-controls/config.yaml`, `~/GHOSTS/` (clone of `cmu-sei/GHOSTS` master), `/mnt/AXES2U1/feedback/ghosts-controls/{preset}_v{version}/{dataset}/npc-{N}/timeline.json` (5 per-NPC tuned timelines; namespaced 2026-06 — feedback needs `--preset`), `~/.docker-hub-token` + `~/.docker-hub-token-user` (optional) |
| Outputs | `deployments/ghosts-{controls,feedback-...}/runs/{run_id}/` (config.yaml snapshot, inventory.ini with `[ghosts_api]` + `[ghosts_clients]` host vars, ssh_config_snippet.txt, deployment_type, deploy_status.json, timelines/g-{hash}-npc-N.json) |
| Manifest | PHASE source `manifest.json`; same loader as DECOY/RAMPART |
| Upstream | PHASE feedback engine writes target-native per-NPC `timeline.json` directly (no translation layer) |
| Downstream | PHASE Zeek pipeline (`PHASE.py --ghosts`) scoped by `start_date` |
| Narrow exceptions | api-0 absent from PHASE source (server VM, no timeline). Both controls and feedback flow through the same per-NPC routing pipeline — controls are not a separate code path. |

## Topology

```
g-{hash}-api-0    Docker stack: ghosts-api(:5000), frontend(:4200),
                                postgres(:5432), n8n(:5678), grafana(:3000)
                          │
                          │ HTTP/SignalR :5000/api
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
  g-{hash}-npc-0   g-{hash}-npc-1   g-{hash}-npc-N
  .NET 10 client   .NET 10 client   .NET 10 client
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
  ghosts_branch: v9.0.0   # PINNED (was master) — see "Version pinning" below
```

## Version pinning + Firefox runtime dependency (2026-06-09)

GHOSTS was tracking `master` (`ghosts_branch: master`) — **unpinned**. The git
clone task passes `ghosts_branch` straight to the git module's `version:`, which
accepts a tag, so pinning is just `ghosts_branch: v9.0.0` (the `v9.0.0` release
tag; tags list: `git -C ~/GHOSTS tag -l`).

**Rollout (2026-06-09):** BOTH controls (`ghosts-controls/config.yaml`) and the
feedback generator (`core/feedback.py::generate_ghosts_feedback_config`) pinned to
`v9.0.0` — controls and feedback build the **same GHOSTS version** so the only
intended difference is the PHASE `timeline.json` (the independent variable). NOTE:
the client build only honors the pin because `ghosts/spinup.py` now passes
`ghosts_repo`/`ghosts_branch` from config into the client playbook — previously the
client defaulted to its own hardcoded `master` while only the API got the ref.

**What matches vs differs (final, 2026-06-09):**
- **Firefox install + GHOSTS version `v9.0.0` → BOTH controls and feedback.** These
  determine *what traffic is generated*, so they must match or the control isn't a
  baseline. Decisive evidence: the controls `timeline.json` is **browser-only** (a
  single `BrowserFirefox` handler, ~1h/day), so a controls NPC with no Firefox
  emits **~zero traffic** — an inert control, not a degraded one. (The v9.0.0
  controls canary `g-1e273` confirmed `libgtk-3` absent + browser-only timeline;
  Firefox was ungated to all clients before waiting for the window.)
- **memcap drop-in (`MemoryMax=3G`) + NPC flavor (`m1.medium`) → FEEDBACK-ONLY.**
  Those are survivability / sizing, not traffic-generating behavior, so controls
  stay on the big flavor with no cap (pristine). Set to m1.medium 2026-06-10 after
  the v9.0.0 leak canary passed (client flat ~160 MB over 12h → the big-RAM
  headroom is unneeded). 4 GB is tight for Firefox (~3 GB under load), so the cap
  is now an sshd-protection guard, not leak mitigation; if NRestarts climbs on
  feedback, move to m1.large (8 GB).
- **Only the PHASE `timeline.json` differs** between controls and feedback — the
  intended independent variable.

A controls deploy from *before* this ungating (e.g. `g-1e273`) has NO Firefox —
**redeploy controls** to pick it up.

**Why it matters — silent fleet regression:** on 2026-06-06 a controls NPC's
`BrowserFirefox` handler started failing every cycle with `XPCOMGlueLoad error …
libgtk-3.so.0: cannot open shared object file` → `Couldn't load XPCOM`. Root
cause: `BrowserFirefox.cs` expects a system Firefox at `/usr/bin/firefox`; the
install playbook **never installs one** (only `git/curl/wget` + .NET SDK), so
Selenium Manager auto-downloads the *latest* Firefox (151.0.4) into
`/root/.cache/selenium/…`, and that bare binary needs system GTK libs that aren't
present. NPCs silently stopped browsing fleet-wide (only Bash/Curl handlers left)
with `NRestarts=0`, `svc=active` — the audit didn't catch it. **Two independent
drift sources:** the GHOSTS source (pin via tag) AND the Selenium-fetched Firefox
(needs a system Firefox install, ideally version-pinned).

**Canary caveat:** a pure-upstream controls NPC showing flat memory over days is
NOT evidence the .NET memleak is fixed — verify Firefox is actually *running*
(`pgrep -f firefox`, no `Couldn't load XPCOM` in `journalctl -u ghosts-client`).
With Firefox dead the memory-heavy leak path never executes, so the soak proves
nothing about the leak. A valid leak soak needs working browser traffic first.

## Spinup phases (`ghosts/spinup.py`)

0. `_teardown_matching_prior_runs` (idempotent same-deploy refresh) — for
   each prior `runs/{old_rid}/` whose saved `config.yaml` has the SAME ghosts
   topology (`_ghosts_topology` = client_count + api/client flavor + repo +
   branch) as the new config, openstack-delete its VMs under the prior g-
   prefix (`wait_until_zero`) and `safe_rmtree` the prior run_dir. Re-running
   `./deploy` against an existing config_name no longer piles orphan VMs (each
   run_id hashes to a different g- prefix → no collision, just accumulation).
   A hand-edited ghosts block (e.g. client_count bumped) = different topology
   → prior run left intact; clean it up with explicit `./teardown`. Mirrors
   `decoy/spinup.py` (which keys on gpu_tier + deployments[]).
1. Provision VMs (OpenStack Python wrapper, NOT Ansible) — tracks ACTIVE
   state, IP-extraction audit
2. SSH connectivity test (parallel, 20 workers) — abort if < 90%
3. Per-NPC timeline routing (feedback only, between provision and
   inventory write)
4. Install GHOSTS API (`install-ghosts-api.yaml` — Docker + docker
   compose up)
5. Install GHOSTS clients (`install-ghosts-clients.yaml` — .NET 10 SDK +
   build + systemd)
6. Finalize: SSH config, `deployment_type` marker, PHASE register
   (fail-loud)

Run outcome stamp (2026-06-08): `run_dir/deploy_status.json` is written
`failed` right after the config snapshot is copied, flipped to `ok` only on
the final clean `return 0`. Any phase abort / exception / kill leaves it
`failed` → `./teardown --ghosts --failed` (or `--failed` alone) targets it.
Same `core/run_status.py` contract as DECOY + RAMPART; pre-2026-06-08 GHOSTS
runs are unstamped (`unknown`) and not matched by `--failed`.

## Quota-exceeded partial-provision recovery

OpenStack cores are a **hard project quota** (2000, raised to 2500 on
2026-06-08; check:
`source ~/vxn3kr-bot-rc && openstack limits show --absolute -c Name -c Value | grep -i core`).
Feedback flavor split (2026-06-10): **API on `v1.14vcpu.28g` (14 vCPU)**, the 5
**NPC clients on `m1.medium` (2 vCPU / 4 GB)** — so a feedback dataset =
14 + 5×2 = **24 cores** (was 84 at 14-vCPU NPCs, 54 at the interim m1.xlarge); a
full 13-dataset batch = ~312 cores. **Controls stay all-`v1.14vcpu.28g`** (84
cores; pristine, no memcap). m1.medium chosen after the v9.0.0 leak canary (client
flat ~160 MB); 4 GB is tight for Firefox (~3 GB) → bump to m1.large if NRestarts
climbs. The cluster routinely runs near the cap (observed 2026-06-08: 1991/2000
used — d-/r-/g- ≈ 978/621/392 cores).

When a batch hits the wall mid-run, `_provision_vms` aborts that dataset (<90%
ACTIVE) but the VMs it *did* create before the `Quota exceeded for cores` error
stay alive as **orphans** (e.g. api+npc-0/1/2 up, npc-3/4 rejected). The deploy
stamps that run `failed`. Recovery:

1. `./teardown --ghosts --failed` — deletes every failed run's orphan/partial
   VMs + run_dirs (frees their cores), leaves the `ok` ones alone.
2. Re-deploy only the datasets that didn't land, via `--target` (comma list of
   the failed datasets) — NOT a full re-run, which would idempotent-refresh
   (tear down + redeploy) the already-`ok` datasets too.

Pre-flight before a big batch: confirm `(maxTotalCores − totalCoresUsed) ≥
84 × n_datasets`, or expect a partial-landing + `--failed` cleanup cycle.

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
`/mnt/AXES2U1/feedback/ghosts-controls/{preset}_v{version}/{dataset}/npc-{N}/timeline.json`
(namespaced 2026-06 — feedback needs `--preset`, see /decoy-deploy "Feedback namespace").
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
   uses `{{ ghosts_timeline_file }}` per-host.

Both controls and feedback go through this same flow. Post 2026-05-09,
`ghosts-controls/config.yaml` declares
`behavior_source: /mnt/AXES2U1/feedback/ghosts-controls/controls/`,
where PHASE writes 5 per-NPC `timeline.json` files with
`_phase_metadata.mode == "controls"`. Feedback datasets emit the same
shape with `mode == "feedback"`. The deploy code doesn't branch on mode
— the difference is purely in the timeline contents PHASE emits.

Fail-loud (G6): if `behavior_source` has no `npc-*/timeline.json` files,
deploy exits early. Partial coverage (some VMs missing timelines, or VMs
without `npc-N` naming) raises `RuntimeError` — caller aborts. No silent
fallback to upstream default.

API VM never targeted: `install-ghosts-clients.yaml` has
`hosts: ghosts_clients`; API VM is in `[ghosts_api]`.

## Memcap drop-in (FEEDBACK ONLY)

Historically the upstream `cmu-sei/GHOSTS` .NET client leaked memory until the
kernel OOM-killer took out sshd (unrunnable past 2-3h on `master`). **The v9.0.0
canary disproved this for the pinned version** (2026-06-10): the client held flat
at ~160 MB over 12h of continuous browsing. So on v9.0.0 the memcap is no longer
leak mitigation — it's an **sshd-protection guard** sized to the (now small) NPC
flavor, in case Firefox (the real ~3 GB footprint) runs away.

Mitigation: systemd drop-in at
`/etc/systemd/system/ghosts-client.service.d/memcap.conf`:

```ini
[Service]
MemoryMax=3G
MemorySwapMax=0
```

`MemoryMax` is sized to the NPC flavor's RAM leaving ~1 GB for the OS: **3G for
m1.medium (4 GB)** (2026-06-10; was 12G on the interim m1.xlarge/16 GB, 20G on the
original 28 GB). When the cgroup hits the cap the kernel kills a process **inside
its cgroup ONLY**; systemd respawns via `Restart=always` within `RestartSec=10`.
sshd / cron / system services stay alive — VM remains usable indefinitely even
as leak recurs. Lower RAM → faster respawn (~hourly at 12G vs ~2h at 20G); stays
under the audit `NRestarts ≤ 50` healthy threshold. The audit reads `MemoryMax`
live from the unit, so it tracks the cap automatically.

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
4. **Firefox runtime deps (FIXED 2026-06-09, FEEDBACK-ONLY)** —
   `install-ghosts-clients.yaml` installs only `git/curl/wget` + .NET; it does NOT
   install a system Firefox or its GTK libs. `BrowserFirefox.cs` wants
   `/usr/bin/firefox`; absent it, Selenium Manager fetches the latest Firefox which
   fails on missing `libgtk-3.so.0` (silent fleet browser death, 2026-06-06). Fix
   (runs for ALL clients — controls + feedback, since the controls timeline is
   browser-only): add Mozilla's APT repo + pin-priority pref (beats the Ubuntu
   snap), `apt install firefox-esr={{ ghosts_firefox_version }}` (default
   `140.11.0esr~build2`, pulls the GTK closure), symlink
   `/usr/bin/firefox`→`firefox-esr`, then assert `firefox --version` + libgtk
   present. See "Version pinning".

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
├── deploy_status.json        # run outcome stamp (failed→ok); --failed teardown filter
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
