# RUSE Deploy System - Full Context

Load full context for the RUSE deploy CLI and all three deployment types
(RUSE SUPs, RAMPART Enterprise, GHOSTS NPCs). Read the files listed below
silently — do not produce a summary unless the user explicitly asks.

## Files to read

### Shared CLI (deployments/cli/)
1. `deployments/cli/__main__.py` - Entry point, argparse, command routing (deploy/teardown/list/shrink/audit)
2. `deployments/cli/config.py` - DeploymentConfig dataclass (sup/rampart/ghosts)
3. `deployments/cli/openstack.py` - OpenStack CLI wrapper with caching
4. `deployments/cli/ansible_runner.py` - Playbook runner, stateful main-thread streaming parser
5. `deployments/cli/output.py` - Monochrome terminal output + session log tee
6. `deployments/cli/ssh_config.py` - ~/.ssh/config block management with RUSE markers
7. `deployments/cli/commands/teardown.py` - All-types teardown + filter flags
8. `deployments/cli/commands/list_cmd.py` - Active deployments list
9. `deployments/cli/commands/feedback.py` - PHASE feedback resolution + config gen + batch discovery
10. `deployments/cli/commands/shrink.py` - In-place VM removal
11. `deployments/cli/commands/audit.py` - Health audit

### Shared libraries + playbooks
12. `deployments/lib/vm_naming.py`
13. `deployments/lib/register_experiment.py`
14. `deployments/lib/enterprise_ssh_config.py` (RAMPART helper)
15. `deployments/playbooks/provision-vms.yaml`
16. `deployments/playbooks/teardown.yaml`
17. `deployments/playbooks/teardown-all.yaml`

### RUSE SUP type
18. `deployments/cli/commands/spinup.py` - 5-phase orchestrator
19. `deployments/ruse-controls/config.yaml` - 7-VM lean baseline (gemma-only)
20. `INSTALL_SUP.sh` - Per-VM installer (cloned from github)
21. `src/common/config/model_config.py` - MODELS dict + get_num_ctx()
22. `src/runners/run_config.py` - SUPConfig registry
23. `src/brains/browseruse/agent.py` - BrowserUse wrapper + Agent tuning + num_ctx injection
24. `src/brains/smolagents/agent.py` - SmolAgents + LiteLLM kwargs
25. `src/brains/{smolagents,browseruse,mchp}/workflows/loader.py` - is_feedback gating
26. `src/common/network/whois.py`, `src/common/network/downloader.py`
27. `deployments/playbooks/install-sups.yaml`
28. `deployments/playbooks/distribute-behavior-configs.yaml`
29. `deployments/playbooks/install-neighborhood.yaml` - Topology-mimicry sidecar

### RAMPART Enterprise type
30. `deployments/cli/commands/rampart.py` - 5-step orchestrator + feedback role assembly + Windows emu deploy
31. `deployments/rampart-controls/config.yaml`
32. `deployments/playbooks/install-rampart-emulation.yaml`
33. `~/uva-cs-workflow/deploy-nodes.py` - OpenStack VM provisioning
34. `~/uva-cs-workflow/post-deploy.py` - register_windows / domain join / pyhuman install / Moodle
35. `~/uva-cs-workflow/simulate-logins.py` - AD users + login schedule → logins.json
36. `~/uva-cs-workflow/role_domains.py` - AD forest, DCs, domain join, CA, user deployment
37. `~/uva-cs-workflow/role_human.py` - pyhuman install on endpoints (Win + Linux)
38. `~/uva-cs-workflow/role_register.py` - Windows adapter rename + license activation
39. `~/uva-cs-workflow/openstack_cloud.py`, `shell_handler.py`
40. `~/uva-cs-workflow/cloud-configs/axes-cicd.json` (controls), `axes-cicd-feedback.json` (feedback m1.xlarge)
41. `~/uva-cs-workflow/enterprise-configs/enterprise-med.json` - 3 DC + 10 win + 10 linux
42. `~/uva-cs-workflow/user-roles/user-roles.json` - standard/power/admin baseline

### GHOSTS NPC type
43. `deployments/cli/commands/ghosts.py` - 5-phase orchestrator + per-NPC timeline routing
44. `deployments/ghosts-controls/config.yaml` - 1 API + 5 NPC clients
45. `deployments/playbooks/install-ghosts-api.yaml`
46. `deployments/playbooks/install-ghosts-clients.yaml`
47. `~/GHOSTS/src/Ghosts.Api/docker-compose.yml`
48. `~/GHOSTS/src/Ghosts.Client.Universal/config/application.json`
49. `~/GHOSTS/src/Ghosts.Client.Universal/config/timeline.example.yaml`

---

## Architecture

```
deployments/
  deploy                    # → exec python3 -m cli deploy "$@"
  teardown                  # → exec python3 -m cli teardown "$@"
  list                      # → exec python3 -m cli list "$@"
  shrink                    # → exec python3 -m cli shrink "$@"
  audit                     # → exec python3 -m cli audit "$@"
  deploy.legacy             # Old bash script (preserved for reference)

  cli/
    __main__.py             # argparse routing
    config.py               # DeploymentConfig
    openstack.py            # OpenStack CLI wrapper
    ansible_runner.py       # Playbook runner + streaming parser (main thread)
    output.py               # Monochrome terminal output
    ssh_config.py           # SSH config management
    commands/
      spinup.py             # ./deploy --ruse
      rampart.py            # ./deploy --rampart
      ghosts.py             # ./deploy --ghosts
      teardown.py
      list_cmd.py
      feedback.py
      shrink.py
      audit.py

  playbooks/                # Ansible (infrastructure only, no display)
    provision-vms.yaml
    install-sups.yaml                # RUSE
    distribute-behavior-configs.yaml # RUSE
    install-neighborhood.yaml        # RUSE topology sidecar
    install-rampart-emulation.yaml   # RAMPART Linux emulation
    install-ghosts-api.yaml          # GHOSTS API
    install-ghosts-clients.yaml      # GHOSTS NPC clients
    teardown.yaml
    teardown-all.yaml

  lib/
    vm_naming.py
    register_experiment.py
    enterprise_ssh_config.py
```

## Three Deployment Types

| Type | Flag | Prefix | Config type | Spinup module |
|------|------|--------|-------------|---------------|
| RUSE SUPs | `--ruse` | `r-` | `sup` | `commands/spinup.py` |
| RAMPART Enterprise | `--rampart` | `e-` (hashed) | `rampart` | `commands/rampart.py` |
| GHOSTS NPCs | `--ghosts` | `g-` (hashed) | `ghosts` | `commands/ghosts.py` |

Stage 2 (2026-04-09): `phase_to_timeline.py` and `phase_to_user_roles.py` were
deleted. PHASE feedback engine writes target-native formats directly — RAMPART
per-node `user-roles.json`, GHOSTS per-NPC `timeline.json`. No reverse-translation.

## CLI Usage

```bash
# Deploy
./deploy --ruse                              # SUP baseline
./deploy --rampart                           # Enterprise baseline
./deploy --ghosts                            # GHOSTS NPCs baseline

# Default (no scope flag) = controls + ALL feedback datasets (per type)
./deploy --ruse                              # controls + all RUSE feedback datasets
./deploy --ruse --controls                   # controls only
./deploy --ruse --feedback                   # all feedback only (no controls)
./deploy --ruse --feedback --target sum24    # single dataset (no controls)
./deploy --ruse --feedback --source /path    # explicit PHASE source (single)
./deploy --ruse --controls --target sum24    # controls + single feedback

# RUSE-only granular feedback flags (each implies --feedback)
./deploy --ruse --timing                     # batch: timing-only across datasets
./deploy --ruse --workflow                   # workflow_weights.json
./deploy --ruse --modifiers                  # behavior_modifiers.json
./deploy --ruse --sites --prompts            # combine any
./deploy --ruse --activity --diversity --variance

# List active deployments
./list

# Teardown — three forms
./teardown ruse-controls-040226205037        # specific deployment
./teardown --ruse --feedback                 # filter: all RUSE feedback deploys
./teardown --rampart                         # filter: all RAMPART
./teardown --ghosts --feedback               # filter: GHOSTS feedback only
./teardown --all                             # nuclear (requires confirmation)

# Shrink in place (no teardown/redeploy) — diffs run snapshot vs config.yaml
./shrink ruse-controls-040226205037

# Health audit
./audit                                      # all 9 per-VM checks across all VMs
```

**Batch is the DEFAULT** when `--feedback` (or any granular flag) is given without
a single-target selector (`--target`/`--source`/positional name). The CLI scans
`/mnt/AXES2U1/feedback/{type}-controls/`, shows discovered datasets, prompts for
confirmation, then deploys each in sequence with a final summary.

## VM Naming

- Run ID: `MMDDYYHHmmss` (second precision)
- dep_id: `{name_no_hyphens}{run_id}` — strips type prefix (`ruse-`/`rampart-`/`ghosts-`/`sup-`)
- RUSE SUP VMs: `r-{dep_id}-{behavior}-{index}` (e.g. `r-controls040226-M1-0`, `r-controls040226-B0-gemma-0`)
- RAMPART VMs: `e-{md5(dep_id)[:5]}-{node_name}` (e.g. `e-bf351-dc1`, `e-bf351-winep1`, `e-bf351-linep3`). 5-char hash for NetBIOS limit.
- GHOSTS VMs: `g-{md5(dep_id)[:5]}-{role}-{index}` (e.g. `g-14a6d-api-0`, `g-14a6d-npc-0`)
- Neighborhood sidecar (RUSE feedback only): `r-{dep_id}-neighborhood-0`
- `teardown-all.yaml` regex: `(r-|e-|g-|sup-)`

## SSH Keys + auth

| Type | OpenStack keypair | Local key | User |
|------|-------------------|-----------|------|
| RUSE SUPs | `bot-desktop` | `~/.ssh/id_ed25519` | ubuntu (key) |
| GHOSTS NPCs | `bot-desktop` | `~/.ssh/id_ed25519` | ubuntu (key) |
| RAMPART Linux | `enterprise-key` | `~/.ssh/id_rsa` (PEM RSA) | ubuntu (key) |
| RAMPART Windows (deploy) | `enterprise-key` | sshpass + domain admin password | `Administrator@{fqdn_domain}` |

**SSH agent MUST be disabled everywhere** — `SSH_AUTH_SOCK=""` + `IdentitiesOnly=yes`.
Agent offers too many keys → auth timeouts. The CLI sets this in `subprocess.run` envs
+ Ansible `ansible_ssh_common_args` everywhere.

## Key Design Decisions

- **Monochrome output** — no ANSI colors, ASCII `####` banners, `[HH:MM:SS]` wall-clock timestamps, `OK`/`FAIL`/`..` markers
- **Ansible for infrastructure only** — all display in Python, playbooks stripped of pause/display tasks
- **Stateful Ansible parser** — `_LineParser` tracks current task, only emits whitelisted task names, suppresses noise
- **Single-thread streaming parser** — main thread reads subprocess stdout (no parser-thread/file-close race)
- **Python SSH test** — replaced Ansible retry loop (silent hang) with `concurrent.futures` + real-time per-VM print
- **No teardown confirmation** — if you `./teardown <target>`, you mean it (except `--all`)
- **Three separate scripts** — `./deploy`, `./teardown`, `./list` (not subcommands)
- **Session log** — every CLI invocation writes `deployments/logs/session-{cmd}-{ts}.log` (path printed last line, even on abort)
- **Raw Ansible log** — separate `deployments/logs/ansible-{playbook}-{ts}.log` (unfiltered)

## OpenStack / SSH details

- Local on mlserv (10.246.118.30) — same network as OpenStack API
- Credentials: `~/vxn3kr-bot-rc` (OS_AUTH_URL, OS_PROJECT_ID, …)
- SSL: `~/openstack_vault_ca.pem` (custom CA)
- DO NOT delete `bot-desktop` keypair (RUSE/GHOSTS use it)
- DO NOT delete `enterprise-key` keypair (RAMPART uses it; key must stay PEM RSA for Windows password decryption)
- No package installs on axes — locked down. mlserv is the orchestrator.

## Behavioral Config System (shared)

### Unified feedback flag
- `--feedback` → all behavioral configs for the chosen type

### Feedback-only divergence (2026-04-27/28)

Code paths branch on `is_feedback` (presence of `behavior.json` in deployed config dir,
OR `behavior_source` extra_var). Lets feedback deploys run extra functionality without
polluting controls. Live examples:

- **RUSE workflows** — Smol/BU/MCHP each gain `whois_lookup` + `download_files` workflows on feedback only. Implemented via `is_feedback` flag on `load_workflows()` (Smol/BU) or `FEEDBACK_ONLY_WORKFLOWS` set in `mchp/agent.py`. Controls keep their original workflow set.
- **GHOSTS memcap** — feedback NPCs get systemd drop-in `/etc/systemd/system/ghosts-client.service.d/memcap.conf` (cgroup `MemoryMax=20G`). Controls run pure upstream. `when: is_feedback | default(false) | bool` gate in `install-ghosts-clients.yaml`.
- **RAMPART flavor bump** — feedback configs override `enterprise.cloud_config` to `axes-cicd-feedback.json` (m1.small → m1.xlarge). Controls stay on m1.small. `feedback.py::generate_rampart_feedback_config` applies at config-gen time.

Pattern: same playbook for controls + feedback; feedback gets extra layered on via
conditional task / loader gate. No separate control playbook, no behavioral drift in controls.

### Shared network helpers (`src/common/network/`)
- `whois.py` — `whois_lookup(domain)` over TCP/43 to whois.iana.org + `FALLBACK_DOMAINS`
- `downloader.py` — `download_file(url)` streaming fetch with 10MB cap + `FALLBACK_URLS`
- `probes.py` — neighborhood-sidecar inbound probes (10 protocols)
- `neighborhood_traffic.py` — sidecar daemon

No cross-brain imports. Each brain's workflow file imports from `common/network/` directly.

### Deployment naming pattern
- `{type}-controls` — Baseline (no feedback) — committed to git
- `{type}-feedback-{preset}-{dataset}-{scope}` — Auto-generated, NOT committed (in `.gitignore`)
- On teardown, `*-feedback-*` directories cleaned up entirely once last run is torn down

### Dataset targets (`feedback.py::DATASET_TARGETS`)
Maps short aliases → canonical PHASE names (`sum24` → `summer24`, `spr25` → `spring25`,
`vt1g` → `vt-fall22-1gb`, `vt50g` → `vt-fall22-50gb`, `cptc8` → `cptc8-23`, `axall` →
`axes-all`, `2025` → `axes-2025`, etc.). `find_feedback_by_target()` resolves with
substring match against `/mnt/AXES2U1/feedback/{type}-controls/`.

### PHASE feedback source layouts (post Stage 2, 2026-04-09)

```
/mnt/AXES2U1/feedback/                              # NEW location (was ~/PHASE/feedback_engine/configs/)
  ruse-controls/{dataset}/
    {behavior}/{sup}/behavior.json                  # consolidated per-SUP file
                                                    # validator: */*/behavior.json
                                                    # (2026-04-16 consolidation: was 8 JSONs)

  rampart-controls/{dataset}/
    {bare_node}/user-roles.json                     # 19 per-node files (linep2-10, winep1-10)
                                                    # dc1-3 + linep1 absent (user: null)
                                                    # validator: */user-roles.json

  ghosts-controls/{dataset}/
    npc-{N}/timeline.json                           # 5 per-NPC tuned timelines
                                                    # api-0 absent (server VM)
                                                    # validator: npc-*/timeline.json
```

manifest.json is back as a provenance index (post 2026-04-23). Loaded by
`feedback.py::load_manifest`, surfaced at confirm time via `manifest_summary_lines`,
validated against deploy type via `validate_manifest_target`. Missing manifest still
OK — `_is_valid_feedback_source()` falls back to file-glob detection so legacy/dev
sources work.

## Stage 3 fail-loud semantics (2026-04-14)

After 161 RAMPART Windows endpoints silently failed across 7 "successful" deploys
(`_safe_parallel_call` swallowed every per-VM auth failure as WARNING and continued),
the entire deploy system was overhauled to fail loud at every silent-failure point.
`DONE` now means every VM is verified functional.

### Core principles
1. No broad `except Exception:` without logging the actual error.
2. No `ignore_errors: yes` or `failed_when: false` on readiness checks.
3. No aggregate metric ("5 VMs succeeded") without a pass/fail threshold.
4. Deploys emit a success contract — complete = every VM in expected state.
5. Shell blocks use `set -euo pipefail` so mid-script failures don't silently skip subsequent steps.

### Canonical threshold: 90%
Below that, deploy is not usable for experiments → step aborts with summary of failure patterns.

### Per-step assertions (cross-cutting)
- **provision-vms.yaml** — abort if < 90% VMs reach ACTIVE
- **spinup.py / rampart.py / ghosts.py SSH tests** — abort if < 90% reachable
- **distribute-behavior-configs.yaml** — abort if behavior source missing, no config files matched, or any `behavior.json` fails `python3 -m json.tool` on localhost
- **install-sups.yaml** — assert stage2 rc=0, `systemctl is-active {behavior}.service` (M0 exempted), MCHP cron count ≥ 2
- **install-ghosts-api.yaml** — `set -euo pipefail` on Docker install, Dockerfile stat-then-sed, explicit `fail:` on API health timeout, docker compose stdout ERROR detection
- **install-ghosts-clients.yaml** — `set -euo pipefail` on dotnet publish, `Ghosts.Client.Universal.dll` stat assertion, `systemctl is-active` (no ignore_errors)
- **install-rampart-emulation.yaml** — `systemctl is-active` AND `NRestarts ≤ 10` (catches services "active" between rapid restart cycles — D5 crash-loop pattern)
- **rampart.py::_deploy_windows_emulation** — every SSH `subprocess.run` checks rc, raises with stderr. Aggregates error patterns once at end ("19x Authentication failed"). Caller aborts < 90%.
- **post-deploy.py::_check_step_results** — after every parallel batch (register_windows, join_domains, deploy_human, Moodle, setup_fileservers). Counts errors, prints pattern summary, `sys.exit(1)` if > 10% fail. Prints `[step_name] OK — all N succeeded` on success.
- **spinup.py / rampart.py / ghosts.py PHASE register** — return bool. If False, abort with `return 1`. No more "DONE" with VMs invisible to PHASE.
- **spinup.py install fail-loud** — on `install-sups.yaml` rc!=0, `_parse_ansible_recap(log_path)` reports per-host failures, then `return 1` before distribute/register.

### audit.py upgrades
- **NRestarts probe** — `systemctl show -p NRestarts --value`. `FAIL (crash-looping, N restarts)` when active but NRestarts > 10. Catches services oscillating between active and crash.
- **M0 expected-failure exception** — reports `EXPECTED (M0 upstream crashes on Linux)` because `os.startfile()` is the intentional baseline behavior of the unmodified MITRE pyhuman control.
- **Feedback feature probes** (Fdbk, Warn columns) — Fdbk checks for exactly 1 `behavior.json` in `/opt/ruse/deployed_sups/*/behavioral_configurations/` (post-2026-04-16 consolidation). Warn counts `[WARNING]` vs `[INFO]` lines in `systemd.log` separately (see *Ablation gating* below).
- **Neighborhood sidecar orphan exclusion** — VMs ending `-neighborhood-0` excluded from orphan check (live in `neighborhood-inventory.ini`, not `sup_hosts`).

### Teardown improvements
- **Orphan volume cleanup** — `_cleanup_orphaned_volumes(os_client)` in every teardown path. Deletes nameless/200GB/available volumes (was leaking ~200GB per VM).
- **experiments.json closure** — `_close_phase_experiment(config_name)` sets `end_date` so PHASE batch pipelines (`PHASE.py --ruse|--rampart|--ghosts`) don't pick up torn-down deploys. Historical registration preserved; only `end_date` set (yesterday's date — teardown-day Zeek captures partial).

### experiments.json fcntl lock (2026-04-17)

`register_experiment.py` and `teardown.py::_close_phase_experiment` both take
`fcntl.LOCK_EX` on `/mnt/AXES2U1/experiments.json.lock` for full read-modify-write
cycle, then write via tempfile + fsync + `os.replace`. Atomic, serialized,
no torn writes on crash.

Pre-lock incident: 2026-04-17 batch of 7 rampart + 8 ruse + 1 ghosts deploys interleaved
and wiped 14 entries down to 2 (each writer loaded stale view, clobbered others).
PHASE.py errored "Not found in experiments.json".

`register_experiment.py` also re-reads after write to catch NFS blip silent drops
(2026-04-20 incident). Reports missing IPs + recommends retry.

If batch run loses entries again (regression), recover by iterating active deploys:
`register_experiment.py --name <dep> --snippet {run}/ssh_config_snippet.txt --inventory {run}/inventory.ini --run-id {run}`.

### Install-sups transient-flake retries
`install-sups.yaml` wraps `Update apt cache`, `Install prerequisites`, `Clone RUSE repo`
in `retries: 3 delay: 30/15 until: succeeded`. Survives single-VM apt-mirror /
GitHub rate-limit flakes without degrading fail-loud — task still fails after 3 attempts.

## Ablation gating (2026-04-17)

PHASE's feedback engine runs per-feature ablation against the target detection model
and deliberately omits behavior.json sections whose knobs produce |Δscore| < 0.10.
For summer24 and vt-fall22, this gates off `timing` and `behavior` entirely because
those models key on network topology (local_orig, conn_state, id.orig_p), not behavior.

### `_metadata.ablation_gate` in behavior.json
PHASE writes `ablation_gate` subtree with `inactive`, `flat_zero`, `gating_features`,
`per_sup_active`. Non-empty → RUSE treats missing sections as deliberate omissions.

### Runtime: [WARNING] → [INFO] downgrade
`BehavioralConfig.ablation_gate` + `.is_ablation_gated()` populated from `_metadata.ablation_gate`.
All warning emitters check the flag:
- `src/common/emulation_loop.py::_reload_behavioral_config` — D2/D4/G1/W4
- `src/common/timing/phase_timing.py::CalibratedTiming` — D1/D3/G5 (via `ablation_gated=True` constructor kwarg, must thread through fallback path at line ~175)
- `src/brains/mchp/agent.py::_apply_brain_specific_config` — B1/B2/G2

Emitted tag becomes `[INFO] ... DISABLED ... (ablation-gated)` so audit.py can distinguish.

### audit.py Warn column semantics
- Baseline (bc_has_behavior=0): `n/a (baseline)` — runtime short-circuits on `fc.is_empty()`.
- Feedback, 0 warn + N INFO: `OK (N ablation-gated)` — PHASE deliberately omitted sections.
- Feedback, N warn: `FAIL (N unexpected warnings)` — real bug (malformed config or RUSE regression before INFO downgrade fires).

VM probe reports `WARN_COUNT` and `INFO_COUNT` separately from `grep '\[WARNING\]'`
and `grep '\[INFO\].*ablation-gated'` against `systemd.log`.

## Operator observability

```bash
# What aborted the deploy?
grep -E "FAIL|ABORTING|FAILURES" deployments/logs/session-deploy-*.log | tail -30

# What aggregate failure patterns did post-deploy.py see?
grep -E "FAILURES:|nodes:" deployments/*/runs/*/enterprise.log | tail -20

# What did Ansible actually say per-task?
grep -E "FAILED|fatal|UNREACHABLE" deployments/logs/ansible-*.log | tail -30
```

Documentation:
- `docs/silent-failures-audit.md` — 15-item CRITICAL/HIGH/MEDIUM/LOW catalog
- `docs/feedback-consumption-plan.md` — D1-D5, G1-G3 runtime consumption plan
- `docs/feedback-field-audit.md` — per-file gap analysis PHASE emits vs RUSE consumes

---

# RUSE SUPs — Type-Specific

## Topology (ruse-controls — LEAN, gemma-only post 2026-04-08)

```
r-{dep_id}-C0-0          Bare Ubuntu control (no software)
r-{dep_id}-M0-0          Upstream MITRE pyhuman (read-only control)
r-{dep_id}-M1-0          MCHP baseline (no timing, no LLM)
r-{dep_id}-B0-gemma-0    BrowserUse + gemma4:26b on V100
r-{dep_id}-S0-gemma-0    SmolAgents  + gemma4:26b on V100
r-{dep_id}-B0C-gemma-0   BrowserUse + gemma4:e2b on CPU
r-{dep_id}-S0C-gemma-0   SmolAgents  + gemma4:e2b on CPU
(7 VMs — dropped llama variants and RTX tier 2026-04-07/08)
```

Feedback template (5 VMs per `./deploy --ruse --feedback`):
```
r-{dep_id}-M2-0          MCHP + PHASE timing
r-{dep_id}-B2-gemma-0    BrowserUse + gemma + PHASE on V100
r-{dep_id}-S2-gemma-0    SmolAgents  + gemma + PHASE on V100
r-{dep_id}-B2C-gemma-0   BrowserUse + gemma + PHASE on CPU
r-{dep_id}-S2C-gemma-0   SmolAgents  + gemma + PHASE on CPU
```

## Spinup phases (`commands/spinup.py`)

1. Provision VMs (`provision-vms.yaml`)
2. SSH connectivity test (Python `concurrent.futures`, 20 workers)
3. Install SUP agents (`install-sups.yaml`, stage1 → reboot → stage2; C0 skipped)
4. Distribute behavioral configs (`distribute-behavior-configs.yaml`)
5. Neighborhood sidecar (feedback only, if `topology_mimicry` rates non-zero)
6. SSH config install + PHASE register (fail-loud)

## Install flow (install-sups.yaml)

Two-stage with reboot:
1. **Stage 1**: system deps (Chrome, Ollama, Python, etc.) → reboot VM (exit 100)
2. **Stage 2**: `INSTALL_SUP.sh --{behavior} --stage=2` → brain deps + systemd service
3. **C0 skipped**: bare Ubuntu control, only provisioned + SSH-tested
4. **M0 special path**: upstream pyhuman (`m0.service`); crash-loops on Linux by design (`os.startfile()` is Windows-only) — exempted from S4 is-active assertion

**Service naming**: `{behavior_lowercase}.service` with dots → underscores.
- `M1` → `m1.service`
- `B0.gemma` → `b0_gemma.service`
- `S2C.gemma` → `s2c_gemma.service`

(NOT generic `mchp` / `bu` / `smol` — that doc was stale.)

**MCHP maintenance cron** (auto-installed for M-brain VMs):
- `0 3 * * * systemctl restart {svc}.service` — daily restart at 03:00 UTC (Selenium/pyautogui memleak ~4 days)
- `0 4 * * 0 /sbin/reboot` — weekly full VM reboot Sunday 04:00 UTC

**Critical gotcha — `deployed_sups/{behavior}/src/` is a COPY not a symlink.**
Each install copies `/opt/ruse/src/` → `/opt/ruse/deployed_sups/{behavior}/src/`. So
`git pull` in `/opt/ruse` does NOT propagate to running agents. Hot-patch:
1. `git pull` then `cp` changed files into per-deploy `src/`, then `systemctl restart {svc}.service`
2. Or teardown + redeploy

**Critical gotcha — `INSTALL_SUP.sh` and `src/*` pulled from github at install time.**
Local edits on mlserv don't affect new deploys until committed and pushed. Clone URL
in `playbooks/install-sups.yaml::ruse_repo` (`LampSteven17/RUSE.git`).

**Logs aren't in journald** — service redirects stdout/stderr to
`{deploy_dir}/logs/systemd.log` and `systemd_error.log`. Use `tail`, not `journalctl -u`.

## SSH access pattern

Deploy automatically installs SSH config block in `~/.ssh/config` (via `install_ssh_config()`).

```bash
ssh r-controls040826193122-M1-0
ssh r-controls040826193122-B0-gemma-0 "systemctl status b0_gemma"

# Brain output (NOT journalctl)
ssh r-controls040826193122-B0-gemma-0 \
  "sudo tail -f /opt/ruse/deployed_sups/B0.gemma/logs/systemd.log"

# Structured agent log
ssh r-controls040826193122-B0-gemma-0 \
  "tail -f /opt/ruse/deployed_sups/B0.gemma/logs/latest.jsonl | jq ."
```

## LLM models (post 2026-04-08 cutover)

| Alias | Ollama tag | Used for | Why |
|---|---|---|---|
| `gemma` | `gemma4:26b` | V100 32GB | MoE: 25.2B total / 3.8B active. Fits 89% VRAM, ~10 tok/s on real DOM prompts. Best capability/speed on V100. |
| `gemmac` | `gemma4:e2b` | CPU only | Edge-optimized 2.3B effective params. ~7 tok/s for SmolAgents. Times out on BrowserUse on CPU due to large prompts. |
| `llama` | `llama3.1:8b` | (legacy) | Kept in MODELS for back-compat, not in any deploy template. |

Aliases live in 3 places that **must agree** when adding:
- `INSTALL_SUP.sh::MODEL_NAMES` (install-time pull)
- `src/common/config/model_config.py::MODELS` (runtime resolution)
- Runner argparse `choices=[...]` (`run_browseruse.py`, `run_smolagents.py`, `run_mchp.py`)

Empirical reports: `docs/gemma_v100_benchmark.md` (raw data), `docs/gemma_model_selection.md` (writeup with charts).

## Tier-aware num_ctx

`get_num_ctx()` in `model_config.py` detects nvidia-smi at runtime:
- GPU detected → `num_ctx=32768` (V100 32GB has VRAM headroom)
- CPU only → `num_ctx=16384` (fits 28GB system RAM with KV cache)
- Override: `SUP_NUM_CTX` env var

Why: Ollama default `num_ctx` is **4096 on CPU** → silently truncates DOM/tool-use prompts → workflows break.

Wired in:
- **BrowserUse** (`brains/browseruse/agent.py`) — injected into Ollama client `chat()` options dict via `create_logged_chat_ollama` wrapper. Uses `kwargs.get('options') or {}` (NOT `setdefault`) because browser_use sometimes passes `options=None` explicitly (2026-04-08 NoneType crash).
- **SmolAgents** (`brains/smolagents/agent.py` + 3 workflow files) — passed as `num_ctx` in `LiteLLMModel` constructor kwargs.

## BrowserUse Agent tuning (2026-04-08)

`brains/browseruse/agent.py` constructs `Agent` with non-default settings to cap token usage:

```python
Agent(
    task=full_prompt, llm=self._get_llm(), browser_session=...,
    use_vision=False,                  # gemma is text-only — screenshots waste
    use_judge=False,                   # skip extra LLM eval per step
    max_clickable_elements_length=8000,  # cap DOM dump (~2K tokens vs 40K default)
    max_history_items=5,               # bounded conversation memory
    include_attributes=[ "id", "class", "name", "type", "value",
        "placeholder", "aria-label", "role", "href", "title", "alt" ],
    llm_timeout=300,                   # CPU LLM calls can take 2-3 min
)
```

Without these, BU on CPU sent 6-23K-token prompts to gemma4:e2b at 0.5 tok/s and
hit browser_use's hardcoded 75-second LLM timeout every step. With them, V100 BU is
fast (~8 tok/s on 8K prompts); CPU BU makes forward progress.

## Behavioral config distribution

Consolidated to single `behavior.json` per SUP on 2026-04-16 (was 8 separate JSONs).
Distribute playbook (`distribute-behavior-configs.yaml`):
1. Derives baseline config key from versioned key: `B2C.gemma → B0C.gemma`, `M2 → M1`
2. Resolves `{feedback_source}/{behavior_dir}/{baseline_config}/behavior.json` (e.g. `.../B.gemma/B0.gemma/behavior.json`)
3. Validates `python3 -m json.tool` on localhost — corrupt aborts deploy before shipping to VM
4. Copies to `/opt/ruse/deployed_sups/{key}/behavioral_configurations/behavior.json`
5. Only runs for V2+ configs (V0/V1 are baselines with no configs)

### behavior.json schema (PHASE-emitted)

```json
{
  "_metadata": {"source", "sup_config", "dataset", "current_score", "target_score",
                "generated_at", "ablation_gate": {...}},
  "timing": {
    "hourly_distribution": [24 floats],
    "activity_probability_per_hour": [24 floats 0..1],
    "long_idle_probability": 0.05,
    "long_idle_duration_minutes": {"min": 30, "max": 120},
    "burst_percentiles": {
      "connections_per_burst":  {"5","25","50","75","95","max"},
      "idle_gap_minutes":       {"5","25","50","75","95"},
      "burst_duration_minutes": {"5","25","50","75","95"}
    },
    "variance": {
      "cluster_size_sigma": 0.5, "idle_gap_sigma": 0.5,
      "hourly_std_targets": {
        "volume":   {"hourly_std_target": [24 floats]},
        "duration": {"hourly_std_target": [24 floats]}
      }
    }
  },
  "content": {
    "workflow_weights": {"BrowseWeb": 0.3, "GoogleSearch": 0.22, ...},
    "site_categories":  {"lightweight": 0.55, "medium": 0.3, "heavy": 0.15},
    "download_url_pool": ["https://...", ...],
    "whois_domain_pool": ["wikipedia.org", ...]
  },
  "behavior": {
    "page_dwell": {"min_seconds": 2, "max_seconds": 43},
    "navigation_clicks": {"min": 10, "max": 30},
    "keep_alive_probability": 0.8,
    "max_steps": 10
  },
  "diversity": {
    "background_services": {
      "dns_per_hour": [24 ints], "http_head_per_hour": [24 ints],
      "ntp_checks_per_day": 4
    },
    "workflow_rotation": {
      "max_consecutive_same": 2, "min_distinct_per_cluster": 3
    },
    "topology_mimicry": {
      "inbound_smb_per_hour": ..., "inbound_ldap_per_hour": ..., ...
    }
  },
  "prompt_content": "... optional free-form prompt guidance ..."
}
```

Loader (`src/common/behavioral_config.py::load_behavioral_config`) slices these
sections into 8 dataclass fields with no key renaming or re-nesting — every
downstream reader matches the shape PHASE emits verbatim.

### Loader contract (2026-04-16)
- File missing → empty `BehavioralConfig` (baseline path, V0/V1)
- File present but malformed JSON → `JSONDecodeError` propagates → service crash-loops → audit NRestarts probe flags it
- No translation, no re-keying

## PHASE feedback runtime consumption

| behavior.json path | BehavioralConfig field | Consumer |
|---|---|---|
| `timing.hourly_distribution` | `timing_profile` | `CalibratedTimingConfig.hourly_fractions` |
| `timing.burst_percentiles.*` | `timing_profile` | `CalibratedTimingConfig.{burst_duration,idle_gap,connections_per_burst}` |
| `timing.variance.cluster_size_sigma` | `variance_injection` | `get_cluster_size()` lognormal noise |
| `timing.variance.idle_gap_sigma` | `variance_injection` | `get_cluster_delay()` lognormal noise |
| `timing.variance.hourly_std_targets.{volume,duration}.hourly_std_target` | `variance_injection` | D1 per-hour sigma arrays in `_init_variance_targets` |
| `timing.activity_probability_per_hour` | `activity_pattern` | `should_skip_hour()` hourly rolldown |
| `timing.long_idle_probability` + `long_idle_duration_minutes` | `activity_pattern` | `should_take_long_idle()` |
| `content.workflow_weights` | `workflow_weights` | `build_workflow_weights()` for `random.choices()` |
| `content.site_categories` | `site_config` | SmolAgents `BrowseWebWorkflow` filters task pool by category (W3 wired 2026-04-27) |
| `content.download_url_pool` | `download_url_pool` | Smol/BU `DownloadFiles` LLM picker (feedback-only) — falls back to `FALLBACK_URLS` |
| `content.whois_domain_pool` | `whois_domain_pool` | Smol/BU/MCHP `WhoisLookup` workflow (feedback-only) — falls back to `FALLBACK_DOMAINS` |
| `content.download_size_pref` | (informational) | RUSE intentionally ignores |
| `behavior.page_dwell` / `navigation_clicks` | `behavior_modifiers` | MCHP `BrowseWeb.{min,max}_sleep_time`, `max_navigation_clicks`; BU `Agent(register_new_step_callback=...)` per-step uniform delay |
| `behavior.keep_alive_probability` | `behavior_modifiers` | G2: MCHP `BrowseWeb.keep_alive_probability` |
| `behavior.max_steps` | `behavior_modifiers` | BU/Smol per-workflow max_steps |
| `diversity.background_services.*` | `diversity_injection` | `BackgroundServiceGenerator` (D4) |
| `diversity.workflow_rotation.{max_consecutive_same,min_distinct_per_cluster}` | `diversity_injection` | D2 rotation enforcement in `emulation_loop` |
| `diversity.topology_mimicry.inbound_*_per_hour` | `diversity_injection` | Neighborhood sidecar daemon (`common.network.neighborhood_traffic`) |
| `prompt_content` | `prompt_augmentation.prompt_content` | G1: BU + Smol prompt prepend |

**G3 detection_hours was removed** — PHASE no longer emits it. Activity suppression
is driven solely by `activity_probability_per_hour`.

## Workflow set + feedback-only workflows (2026-04-28)

| Brain | Controls | Feedback |
|---|---|---|
| **Smol** | BrowseWeb, WebSearch, BrowseYouTube (3) | + WhoisLookup, DownloadFiles (5) |
| **BU** | BrowseWeb, WebSearch, BrowseYouTube (3) | + WhoisLookup, DownloadFiles (5) |
| **MCHP** | 8 baseline (no download_files) | 9 baseline + WhoisLookup + DownloadFiles |

Gating: `FEEDBACK_ONLY_WORKFLOWS` set in `brains/mchp/agent.py`. Smol+BU loaders
have `is_feedback: bool = False` parameter on `load_workflows()`. All 3 brain loops
implement `_is_feedback_deploy()` checking `Path(self._behavior_config_dir, "behavior.json").exists()`.

### whois_lookup + download_files internals
- **Smol** — dedicated workflow. ONE `LiteLLMModel` picker call → domain/URL from PHASE pool. Deterministic helper (TCP/43 socket or `requests.get`) does the network call. Bypasses CodeAgent's tool-decision loop.
- **BU** — dedicated workflow that BYPASSES `browser_use.Agent` entirely. ONE Ollama HTTP API picker call (loopback `127.0.0.1:11434`, invisible to Zeek). Browser never invoked.
- **MCHP** — `random.choice(pool)` no-LLM picker. Same helpers.

Tool palette of Smol's 3 LLM workflows: `[DuckDuckGoSearchTool(), VisitWebpageTool()]` only.
`WhoisLookupTool` and `DownloadFileTool` classes were **deleted** (2026-04-28) —
entire `src/brains/smolagents/tools/` directory removed.

### Loud-failure semantics
LLM picker exceptions and off-pool selections print to stderr AND log via
`AgentLogger.warning`:
- `[ERROR] {Workflow} LLM picker failed: {ExcType}: {msg}`
- `[WARNING] {Workflow} LLM picked X not in pool — falling back...`

Audit's WARN_COUNT probe surfaces these in the Warn column. Real TCP/43 / HTTP
failures return error strings from helpers — workflow logs `step_error` and
continues without crashing the loop.

## Topology Mimicry — neighborhood sidecar (2026-04-17)

For summer24 and vt-fall22 target models, PHASE ablation showed behavioral knobs
produce |Δscore| < 0.10 but five non-behavioral topology features move score by
0.27-1.00: `local_orig`, `id.orig_p`, `id.resp_p`, `local_resp`, `conn_state`.
Model is detecting sandbox-VM environment, not behavior — sandbox SUPs receive
no inbound LAN traffic (no SMB/LDAP/WSUS/IPMI/mDNS/printer polls) so every Zeek
row has `local_orig=1`, ephemeral `id.orig_p`, `conn_state=SF`. Workstations have
opposite distribution.

### Architecture (components 1+3 shipped)
- **1 neighborhood VM per feedback deploy** (FEEDBACK ONLY). Name `r-{dep_id}-neighborhood-0`, flavor `v1.small` (1 vCPU / 2 GB), `bot-desktop` keypair.
- **Data-driven daemon** `common.network.neighborhood_traffic` reads `/etc/ruse-neighborhood/sups.json` and synthesizes real TCP/UDP probes at each SUP IP. Empty/zero rates → zero probes. Daemon stays alive but idle.
- **10 probe types** in `src/common/network/probes.py`: `inbound_smb_per_hour`, `inbound_ldap_per_hour`, `inbound_wsus_per_hour`, `inbound_ntp_receive_per_hour`, `inbound_printer_per_hour`, `inbound_ipmi_per_hour`, `inbound_winrm_per_hour`, `inbound_mdns_per_hour`, `inbound_ssdp_per_hour`, `inbound_scan_per_hour`. Produce mixed conn_state (SF / S0 / REJ / RSTO / unidir) on Zeek rows from the SUP.
- **PHASE contract (component 3)** — writes `diversity.topology_mimicry.inbound_*_per_hour` per SUP. RUSE's `BehavioralConfig.topology_mimicry()` reads verbatim via `diversity_injection`.

### Deploy flow (`spinup.py` phase 2c, after distribute)
1. `_synthesize_neighborhood_config(behavior_source, inventory_path, run_dir)` reads each SUP's `behavior.json`, collects `topology_mimicry` rates, writes `neighborhood-sups.json` if any non-zero (else returns None → skip sidecar).
2. `_provision_and_install_neighborhood(...)` creates VM via OpenStack CLI, writes `neighborhood-inventory.ini`, runs `install-neighborhood.yaml` (asserts `ruse-neighborhood` service active + NRestarts ≤ 5).

Fail-loud: any failure aborts before PHASE register — feedback deploy without
topology layer would be experimentally worse than no deploy.

### Teardown
VM name `r-{dep_id}-neighborhood-0` → existing `r-` prefix sweep in `teardown.yaml` /
`teardown-all.yaml` deletes it. No special handling.

### Audit
Sidecars excluded from orphan check (live in `neighborhood-inventory.ini`, not `sup_hosts`).
Service status NOT yet audited by main `./audit` — phase B work.

### Observed (2026-04-17 overnight)
7 sidecars running 12+ hours: all active, 0 restarts, 2800-3300 probes each.
Target 360/hr, observed ~235/hr (~65%) — scheduler jitter-sleep accumulation
burns ~35% of each 60s tick. Not broken; rate still produces the topology signal.

### Deferred (phase B/C)
- Component 2 — SUP listening services (sshd/node-exporter/cockpit/http) for `id.orig_p` diversity
- Component 4 — subnet chatter (mDNS/SSDP/NetBIOS multicast) from SUPs for `local_orig=0` rows
- Phase C — PHASE re-runs ablation with topology layer live; target: five features' max|Δ| < 0.10

Full design: `docs/topology-mimicry.md`.

## Key constraints
- **M0 read-only** — upstream MITRE pyhuman control, do not modify
- **C0 no software** — bare Ubuntu control, only provisioned
- **No LLM fallback** — LLM-augmented agents fail loudly if LLM fails (experiment validity)
- **MCHP no LLM** — pure scripted automation
- **Models run locally** via Ollama, installed by INSTALL_SUP.sh
- **Per-deploy `src/` is COPY not symlink** — see hot-patch note above
- **`src/*` and `INSTALL_SUP.sh` pulled from github** — local edits need `git push` first
- **MCHP slow Selenium leak** — mitigated by daily restart cron + weekly reboot

---

# RAMPART Enterprise — Type-Specific

## Topology (enterprise-med.json)

```
Domain: castle.{hash}.{project}.os  (e.g. castle.14a6d.vxn3kr-bot-project.os)

Domain Controllers (Windows Server 2022):
  dc1      - Forest leader (domain_controller_leader)
  dc2, dc3 - Replica DCs (domain_controller)

Windows Endpoints (Windows Server 2022):
  winep1-10  - Domain-joined, personal, standard user

Linux Endpoints (Ubuntu Jammy):
  linep1     - Shared (no user, no emulation)
  linep2     - Shared, standard user
  linep3-8   - Personal, standard user
  linep9     - Personal, admin user
  linep10    - Personal, power user

Total: 23 VMs (3 DC + 10 Win + 10 Linux)
Emulated: 19 endpoints (linep1 excluded — shared, no user)
```

## Deploy flow (`commands/rampart.py`)

```
[1/5] Setup venv in ~/uva-cs-workflow
[2/5] Provision VMs (deploy-nodes.py) → deploy-output.json
      Per-deployment cloud config with unique enterprise_url ({hash}.{project}.os)
[3/5] Configure VMs (post-deploy.py) → post-deploy-output.json
      ├── register_windows()           - Adapter rename + license activation
      ├── deploy_domain_controllers()  - AD forest (dc1) + replicas (dc2, dc3)
      │   -DomainNetBIOSName CASTLE{hash} for multi-deploy isolation
      ├── setup_fileservers()
      ├── join_domains()               - Win + Linux domain join
      ├── deploy_human()               - pyhuman install on all endpoints
      └── setup_moodle_idps/sps/idps_part2()
[---] If --feedback: rampart.py::_generate_feedback_user_roles
      Reads behavior_source/{bare_node}/user-roles.json (Stage 2 target-native;
      no translation), extracts each file's first role, renames to
      e-{hash}-{bare_node}_user, combines with 3 baseline roles (standard/
      power/admin), rewrites each fed enterprise node's "user" field.
      → user-roles-feedback.json (19 tuned + 3 baseline)
      → enterprise-config-feedback.json
[4/5] simulate-logins.py → logins.json (FQDN auth)
[5/5] Deploy emulation services
      ├── Linux: install-rampart-emulation.yaml → systemd (rampart-human.service)
      └── Windows: rampart.py::_deploy_windows_emulation → scheduled task (RampartHuman)
```

Post-deploy: SSH config installed, PHASE registered with `--start-date $(today)`.
Deploy returns; VMs run independently of mlserv.

## Multi-deployment isolation

Each RAMPART deployment gets its own DNS zone and NetBIOS name:
- `run_dir/cloud-config-prefixed.json` — cloud config with per-deployment `enterprise_url={hash}.{project}.os`
- `run_dir/dns_zone.txt` — zone name for scoped teardown
- `openstack_cloud.py:40` respects pre-set enterprise_url
- `-DomainNetBIOSName CASTLE{hash}` in `role_domains.py` — unique on network

Without these, concurrent deploys collided on AD/DNS.

## Autonomous emulation

Emulation runs **on the VMs themselves** — mlserv can shut down.

### Linux endpoints (systemd)
```
Service: rampart-human.service
Binary:  xvfb-run -a /opt/pyhuman/bin/python -u /opt/pyhuman/human.py
Args:    --clustersize 5 --clustersize-sigma 0.5
         --taskinterval 10 --taskinterval-sigma 0.5
         --taskgroupinterval 500
         --seed {seed} --workflows {list}
         --extra passfile /tmp/shib_login.{user}
Config:  /etc/systemd/system/rampart-human.service
Check:   ssh e-XXXXX-linep3 "systemctl status rampart-human"
Logs:    ssh e-XXXXX-linep3 "journalctl -u rampart-human -f"
```

### Windows endpoints (scheduled task)
```
Task:    RampartHuman (AtStartup, SYSTEM, RestartCount=999, RestartInterval=1m)
Script:  C:\tmp\run-emulation.ps1
Binary:  C:\Python\python.exe -u C:\human\human.py
Args:    Same as Linux
Creds:   C:\tmp\shib_login.{username}
Check:   sshpass ssh Administrator@castle.{hash}.{project}.os@{ip} \
           "powershell (Get-ScheduledTask -TaskName RampartHuman).State"
```

### Why not Ansible for Windows?
Ansible's `raw` module strips PowerShell `$` variables (`$action`, `$trigger`,
`$false`). No escape method works (`{{ '$' }}`, `{% raw %}`, cmd echo).
`_deploy_windows_emulation()` in rampart.py uses direct `sshpass` SSH.

10 VMs in parallel via `concurrent.futures`. Each VM: 4 SSH steps (passfile, ps1
script, register task, start task). Each step's `subprocess.run` checks rc and
raises with stderr. Error patterns aggregated via Counter at end (e.g. "19x
Authentication failed" once, not 19 buried warnings). 90% threshold; below → abort.

Windows SSH options: `PubkeyAuthentication=no` (prevents pubkey burning Windows
sshd's `MaxAuthTries` before password attempt).

## SSH access

Linux:
```bash
ssh -i ~/.ssh/id_rsa ubuntu@<linux_ip>     # via installed SSH config block
ssh e-bf351-linep3 "systemctl status rampart-human"
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

## Behavioral configuration

The orchestrator is **user-roles.json** (RAMPART analog to RUSE's `behavior.json`):
- Activity timing: hours/day, logins/hour, start hours per day-of-week
- Workflow selection
- Session behavior: login duration, recursive logins, terminal count
- Node targeting: fraction to personal vs shared vs random machines

### Baseline: 3 role types
`standard user`, `power user`, `admin user` — static in `~/uva-cs-workflow/user-roles/user-roles.json`.

### With --feedback: 19 per-node roles (post Stage 2, 2026-04-09)

`./deploy --rampart --feedback` assembles per-node roles via
`rampart.py::_generate_feedback_user_roles()`. **No translation layer** —
`phase_to_user_roles.py` was deleted in Stage 2. PHASE writes target-native
`user-roles.json` files directly.

PHASE output layout (read as-is):
```
/mnt/AXES2U1/feedback/rampart-controls/{dataset}/
  linep2/user-roles.json    linep3/user-roles.json    ...    linep10/user-roles.json
  winep1/user-roles.json    winep2/user-roles.json    ...    winep10/user-roles.json
  (19 files — dc1/dc2/dc3/linep1 absent; user: null in enterprise-med.json)
```

Each per-node file is self-contained:
```json
{
  "roles": [
    {"name": "linep9_user", ...},   // ← tuned role (first entry)
    {"name": "standard user", ...}, // ← baseline clones (PHASE preserves for reference)
    {"name": "power user", ...},
    {"name": "admin user", ...}
  ],
  "_phase_metadata": { ... provenance ... }
}
```

### Assembly flow (`_generate_feedback_user_roles`)
1. Walks `behavior_source/*/user-roles.json` to discover processed nodes.
2. Extracts first role (the tuned `{bare_node}_user`).
3. Clones the tuned role and renames to `e-{hash}-{bare_node}_user` (e.g. `e-14a6d-linep9_user`). This is where the `e-{hash}-` prefix is applied — PHASE writes bare names, deploy maps to hash-prefixed enterprise config node names so concurrent deployments don't collide on role names.
4. Walks `enterprise-config-prefixed.json` nodes, strips `e-{hash}-` prefix via regex, looks up the matching tuned role, rewrites node's `"user"` field.
5. Nodes with `user: null` (dc1-3, linep1) left unchanged.
6. Combines tuned roles with 3 baseline roles loaded from workflow baseline → fallback for any unfed node.
7. Writes `user-roles-feedback.json` (22 roles = 19 tuned + 3 baseline) + `enterprise-config-feedback.json` to run dir.

**Role naming**: tuned role for `linep9` appears as `e-{hash}-linep9_user`, NOT `linep9_user`. Diverges from PHASE filename so enterprise config can reference unique role name per deployment.

**Baseline role assignment** (from PHASE-side generation, in `_phase_metadata.baseline_role`):
- `winep1..winep10` and `linep2..linep8` → `standard user`
- `linep9` → `admin user`
- `linep10` → `power user`

So `linep9` inherits admin's `fraction_of_logins_to_personal_machine: "0.2"` while
receiving PHASE-supplied `day_start_hour`, `activity_daily_hours`, `logins_per_hour`,
`login_length`, `clustersize`, `taskinterval`, `taskgroupinterval`, `clustersize_sigma`,
`taskinterval_sigma`. Enterprise-only workflows (`browse_iis`, `browse_shibboleth`,
`moodle`, `build_software`) preserved by PHASE during cloning.

## D5 sigma flow

PHASE generates `clustersize_sigma` / `taskinterval_sigma` per-node in each
`{bare_node}/user-roles.json` (from `rampart_generator.py::_clustersize_sigma` +
`_taskinterval_sigma` lognormal calculation).

Wiring:
- `rampart.py::_generate_emulation_inventory` extracts from each user's `login_profile`, passes as `rampart_clustersize_sigma=0.5 rampart_taskinterval_sigma=0.5` per-host vars
- `install-rampart-emulation.yaml` ExecStart inserts `--clustersize-sigma` / `--taskinterval-sigma` into pyhuman command line
- Patched `/opt/pyhuman/human.py` (from local `workflows.zip`) applies `random.lognormvariate(0, sigma)` per cluster + per task

Controls get `0/0` (no jitter); feedback gets `0.5/0.5` → clusters range 2-15 around mean of 5.

**D5 crash-loop incident (Stage 3, 2026-04-14)**: playbook initially passed sigma args
to upstream pyhuman (which didn't recognize them) → 2185 restarts in 12hr. Audit
thought services healthy because "active state + journal activity in 5min" didn't
distinguish workflow runs from crash-loop noise. Fix: rebuilt `workflows.zip` with
sigma support patched in; install-rampart-emulation.yaml now asserts NRestarts ≤ 10;
audit.py reports `FAIL (crash-looping, N restarts)` for > 10 restarts.

## PHASE registration

`_register_phase()` in `rampart.py` calls `register_experiment.py` with:
- `--name rampart-controls` (or `rampart-feedback-...`)
- `--snippet ssh_config_snippet.txt` (all 23 VMs)
- `--run-id MMDDYYHHMMSS`
- `--start-date YYYY-MM-DD` — **critical**: scopes PHASE Zeek log dredging to deploy window. Without this, PHASE processes ALL eno2 Zeek logs → disk full.

`enterprise_ssh_config.py` generates the snippet by navigating
`deploy-output.json → enterprise_built.deployed.nodes[] → addresses[0].addr`.

## Log collection

RAMPART VMs do NOT produce RUSE-format JSONL logs (no `/opt/ruse/deployed_sups/{behavior}/logs/*.jsonl`).

- Linux logs: `journalctl -u rampart-human` (systemd journal, pyhuman stdout)
- Windows logs: scheduled task captures, no persistent log file by default
- Network traffic: Zeek on eno2 (axes), processed by PHASE pipeline with `start_date` range
- RUSE log collector (`collect_sup_logs.py`) finds no JSONL on rampart VMs and skips — harmless but noisy

Health check across VMs:
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

## Testing individual workflows (manual)

`emulate-logins.py` is kept for manual testing (NOT used in deploy):
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

Available: `browse_iis`, `browse_shibboleth`, `browse_web`, `browse_youtube`,
`build_software`, `download_files`, `google_search`, `moodle`, `spawn_shell`.

## Common issues + fixes

| Issue | Cause | Fix |
|-------|-------|-----|
| One VM failure kills entire post-deploy step | joblib.Parallel raises on first exception | `_safe_parallel_call()` wrapper catches per-VM |
| Domain join verification 30+ min | 30 outer × 10 inner retries with backoff | Reduced to 15 × 2 in `role_domains.py` |
| Ansible strips PowerShell `$` | `raw` module treats `$` as Jinja2 | Direct `sshpass` SSH from Python |
| SSH "Too many auth failures" | Wrong key (id_ed25519 vs id_rsa) | Use `~/.ssh/id_rsa` (enterprise-key) |
| PHASE register "No SUP hosts found" | `enterprise_ssh_config.py` couldn't parse deploy-output.json | Navigate `enterprise_built.deployed.nodes` + `addresses[0].addr` |
| Output buffering (deploy looks frozen) | Child Python processes buffer stdout | `PYTHONUNBUFFERED=1` + `bufsize=1` in `_ent_run()` |
| Deprecation warnings in output | neutronclient, cryptography libs | `PYTHONWARNINGS=ignore` + `_is_noise()` filter |
| PHASE dredges all Zeek logs (disk full) | No start_date in experiments.json | `--start-date` flag in register_experiment.py |
| RUSE log collector finds nothing | No JSONL — pyhuman uses stdout | Expected; use journalctl/Zeek |
| NetBIOS collision between deploys | `Install-ADDSForest` can't auto-derive multi-label; even when it can, two deploys collide | `-DomainNetBIOSName CASTLE{hash}` |
| Auth fails on DC | `deploy_users()` used bare domain (`administrator@castle`) but NetBIOS is now `CASTLE{hash}` | Use FQDN (`administrator@castle.{hash}.{project}.os`) |
| 0 endpoints found for emulation | `user_map` keyed by prefixed names but `node_map` by bare names | Strip `ent_prefix` from `home_node` in `_generate_emulation_inventory()` and `_deploy_windows_emulation()` |
| DNS zone collision between deploys | All shared one zone (`vxn3kr-bot-project.os`) | Per-deployment zone (`{hash}.vxn3kr-bot-project.os`), scoped teardown via `dns_zone.txt` |
| 161 Windows endpoints silently undeployed across 7 deploys | `_safe_parallel_call` swallowed every per-VM auth failure as WARNING; `deploy_human` ran as `Administrator@castle` (bare NetBIOS) when actual was `CASTLE{hash}` | Stage 3 (2026-04-14): role_human FQDN, role_domains FQDN, post-deploy `_check_step_results` aborts > 10% fail |
| RAMPART D5 crash loop (2185 restarts/12hr) | Playbook passed sigma args to upstream pyhuman that didn't recognize them | Stage 3: rebuilt workflows.zip with sigma support; install-rampart-emulation NRestarts assertion; audit.py NRestarts probe |
| Linux domain-join verification spurious failures (4/20) | Verification tested AD-auth-via-SSH (sssd+sshd+PAM), not realm join. Slow VMs failed within 75s window | Stage 3: SSH as `ubuntu` (cloud-init creds), run `sudo realm list`. Window 75s → 300s |
| Orphan boot volumes (192 × 200GB) | Teardown deleted VMs but never volumes | 2026-04-14: `_cleanup_orphaned_volumes()` in every teardown |
| `PHASE.py --rampart` dredges torn-down deploys | Teardown left `end_date=None` | 2026-04-15: `_close_phase_experiment()` sets `end_date` |
| dc3 fails `Install-ADDSDomainController` with "A domain controller could not be contacted ... member of a workgroup ... Access is denied" | DC promotion is sequential (`for follower in followers:` in post-deploy.py:373). dc3 starts immediately after dc2's post-promotion reboot — its AD service isn't back up. dc3 can't reach any DC, returns the misleading "workgroup" error. Existing retry loop misreads it and burns 3 attempts on a workgroup-reset cycle that fixes nothing. | 2026-04-30: `role_domains.py::_wait_for_domain_reachable()` runs `nltest /dsgetdc:{domain}` from new follower DC's POV in a 15s loop (timeout 600s) BEFORE the retry loop starts. Same DC-discovery mechanism Install-ADDSDomainController uses internally. Common case returns ~15s; dc3-after-dc2-reboot waits as needed. Timeout → fail loud with clear "leader DC mid-reboot or AD service not started" message. |

## Important constraints
- Do NOT delete `bot-desktop` keypair (RUSE/GHOSTS need it)
- `enterprise-key` (`~/.ssh/id_rsa`) MUST be RSA in PEM format — used for VM provisioning + Windows password decryption
- No package installs on axes
- `~/uva-cs-workflow/` is active copy (ported from nomod); `~/uva-cs-workflow-old/` is pre-fix backup; `~/uva-cs-workflow-nomod/` is reference copy
- `sshpass` must be installed on mlserv (Windows emulation deployment)

---

# GHOSTS NPC — Type-Specific

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

ghosts-controls baseline: 1 API + 5 NPC clients (`v1.14vcpu.28g` flavor),
`cmu-sei/GHOSTS` master branch.

## Spinup phases (`commands/ghosts.py`)

1. Provision VMs (OpenStack Python wrapper, NOT Ansible) — tracks ACTIVE state, IP-extraction audit
2. SSH connectivity test (parallel, 20 workers)
3. Per-NPC timeline routing (feedback only) — between provision and inventory write
4. Install GHOSTS API (`install-ghosts-api.yaml` — Docker + docker compose up)
5. Install GHOSTS clients (`install-ghosts-clients.yaml` — .NET 9 SDK + build + systemd)
6. Finalize (SSH config, deployment_type marker, PHASE register fail-loud)

## GHOSTS config format

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

## Inventory format (two host groups)

```ini
[ghosts_api]
g-{hash}-api-0 ansible_host=10.x.x.x

[ghosts_clients]
g-{hash}-npc-0 ansible_host=10.x.x.y ghosts_api_ip=10.x.x.x ghosts_timeline_file=/abs/path/timelines/g-{hash}-npc-0.json
g-{hash}-npc-1 ansible_host=10.x.x.z ghosts_api_ip=10.x.x.x ghosts_timeline_file=/abs/path/timelines/g-{hash}-npc-1.json
```

`ghosts_api_ip` host var → each client knows API VM's address.
`ghosts_timeline_file` host var (feedback only) → per-NPC tuned timeline.

## Client config files (`/opt/ghosts-client/config/`)

- **application.json** — API connection: `ApiRootUrl: http://{api_ip}:5000/api`, SignalR WebSocket, polling intervals
- **timeline.json** — Behavioral definition: handler types (BrowserFirefox, Bash, Curl), URLs, commands, delays, active hours

## Client registration flow

1. Client starts → connects to API via SignalR WebSocket (`/clientHub`)
2. Sends machine ID, hostname, IP, version via headers
3. API registers in PostgreSQL `Machines` table
4. Client polls for timeline updates, reports activity results
5. Verify: `curl localhost:5000/api/machines` on API VM

## PHASE feedback → per-NPC timeline routing (Stage 2, 2026-04-09)

PHASE writes one tuned `timeline.json` per NPC at
`/mnt/AXES2U1/feedback/ghosts-controls/{dataset}/npc-{N}/timeline.json`. Each
already in native GHOSTS schema — `{"Status": "Run", "TimeLineHandlers": [...],
"_phase_metadata": {...}}` — with per-VM tuning (different DelayAfter, handler mixes,
lognormal sigmas). No translation layer.

Routing flow (`ghosts.py::run_ghosts_spinup`):
1. After provision, before inventory write, call `_build_npc_timeline_mapping(source, client_vms, run_dir)`. Walks `source/npc-*/timeline.json`, matches each to a client VM by extracting trailing `npc-N` from VM name (`g-{hash}-npc-0` → `npc-0`), copies each to `run_dir/timelines/{vm_name}.json` for self-contained run dir, returns `{vm_name: Path}`.
2. `_write_inventory()` accepts mapping, appends per-host `ghosts_timeline_file=/abs/path/{vm_name}.json` to each client line.
3. `install-ghosts-clients.yaml::Deploy PHASE-generated timeline` task uses `{{ ghosts_timeline_file }}` per-host. Baseline deploys with no var → playbook's default-timeline fallback runs instead.

**Fail-loud semantics (G6)**: if `behavior_source` has no `npc-*/timeline.json` files,
deploy exits early. Partial coverage (some VMs missing timelines, or VMs without
`npc-N` naming) raises `RuntimeError` — caller aborts. No silent fallback to default.

**API VM never targeted**: `install-ghosts-clients.yaml` has `hosts: ghosts_clients`;
API VM is in `[ghosts_api]`. Client playbook never runs on it.

## Memleak mitigation — cgroup memory cap (2026-04-27, FEEDBACK-ONLY)

Upstream `cmu-sei/GHOSTS` .NET client leaks memory until kernel OOM-killer takes
out sshd before the leaky process — 23/40 NPCs SSH-unreachable 3h post-deploy on
2026-04-27 audit. **Pure-upstream clients unrunnable past 2-3h without hard-reboot.**

Mitigation: systemd drop-in at `/etc/systemd/system/ghosts-client.service.d/memcap.conf`:

```ini
[Service]
MemoryMax=20G
MemorySwapMax=0
```

When .NET RSS hits cap, kernel kills process **inside its cgroup ONLY**; systemd
respawns via `Restart=always` within `RestartSec=10`. sshd / cron / system services
stay alive — VM remains usable indefinitely even as leak recurs every ~2h.

**Scope: feedback ONLY.** Controls keep pure upstream so they remain experimentally
pristine (leaky-as-designed). Treated as feedback-cycle improvement, not baseline change.

Wiring: `ghosts.py` passes `is_feedback={true,false}` extra_var to
`install-ghosts-clients.yaml`, set from `behavior_source is not None`. Playbook
conditionally creates drop-in dir + memcap.conf via `when: is_feedback | default(false) | bool`.
Drop-in pattern (vs editing base unit) keeps diff reversible — delete to remove cap.

**Audit signal**: feedback NPCs may show `NRestarts > 0` as cgroup OOM cycle fires —
expected and healthy. Pre-cap, NPCs went SSH-fail entirely; post-cap they cycle
gracefully and stay reachable.

## Known build issues (patched in playbooks)

1. **Frontend npm conflict** — GHOSTS Angular frontend has peer dep mismatch. `sed` replaces `RUN npm ci` → `RUN npm ci --legacy-peer-deps` in Dockerfile. Handles Windows line endings (`\r\n`).
2. **Client NLog version** — `Ghosts.Domain` wants NLog ≥ 6.0.6, client pins 6.0.5. Patched with `/p:NoWarn=NU1605` in `dotnet publish`.
3. **Client DLL casing** — Published DLL is PascalCase `Ghosts.Client.Universal.dll`. Systemd ExecStart must match.

## Docker Hub rate-limit auth (2026-04-17)

Unauthenticated Docker Hub pulls capped at 100/6hr per source IP. 7-deploy batch
hit limit on deploy #7 pulling `postgres:16.8`, `grafana/grafana`, `n8nio/n8n`.

`install-ghosts-api.yaml`:
- Reads `~/.docker-hub-token` + `~/.docker-hub-token-user` on mlserv if present, copies to VM `/tmp/.dh-token` + `/tmp/.dh-user`, runs `docker login`, then deletes staged creds. Missing files = unauth pulls (same as before).
- Retries `docker compose up` once after 60s for transient flakes.
- Dedicated `Detect Docker Hub rate-limit` assertion surfaces specific error with remediation ("wait 6h, or add PAT").

Setup (one-time):
```bash
echo 'YOUR_PAT' > ~/.docker-hub-token && chmod 600 ~/.docker-hub-token
echo 'YOUR_USER' > ~/.docker-hub-token-user && chmod 600 ~/.docker-hub-token-user
```

C3 health probe switched from `/api/home` to `/api/machines` on 2026-04-17 —
upstream removed `/api/home`; Kestrel returns 404 even when API healthy.

## .NET memleak (separate from cgroup cap)

If the cap doesn't catch leak fast enough OR you're running a control VM:
```bash
source ~/vxn3kr-bot-rc
openstack server reboot --hard g-<hash>-npc-N
```

Not patching upstream code. If experiments hit this regularly, lower `MemoryMax`
in the drop-in or add daily restart cron mirroring MCHP pattern. See
`memory/project_ghosts_client_memleak.md`.

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
