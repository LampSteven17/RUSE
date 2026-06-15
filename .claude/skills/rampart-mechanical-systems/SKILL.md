---
name: rampart-mechanical-systems
description: RAMPART runtime mechanics — how a deployed enterprise endpoint's synthetic-user emulation actually runs, as a maintainer code-map. RAMPART = an AD forest of Windows/Linux VMs each running MITRE pyhuman (`/opt/pyhuman/human.py`, from `~/uva-cs-workflow/Downloads/workflows.zip`). Traces the call-graph from RUSE wiring (`deployment_engine/rampart/spinup.py::_generate_emulation_inventory` → inventory vars → `playbooks/rampart/install-rampart-emulation.yaml` ExecStart / Windows scheduled task) into pyhuman itself (`human.py` main → import_workflows → emulation_loop → handle_workflow → one workflow `.action()`). Covers the live CLI knobs (clustersize/taskinterval/taskgroupinterval + sigmas + UTC hour-gate day-start/activity flags), the 13 pyhuman workflows, the UTC hour-gating in `_select_active_hours_for_day`, the inert login-graph fields (`logins.json` is dead; `_phase_block_mode.window` has no `--block-window`), the volume model, and Windows concerns (chrome reaper, scheduled-task restart, passfile). Use when modifying RAMPART runtime behavior or tracing why an endpoint generates the traffic it does. Does NOT cover deployment orchestration (see /rampart-deploy) or health auditing (see /rampart-audit); never commit in `~/uva-cs-workflow/` and never edit `~/PHASE/`.
type: skill
---

# rampart-mechanical-systems

The third per-system skill: **operate** (`/rampart-deploy`) → **verify**
(`/rampart-audit`) → **understand/modify** (this). A maintainer code-map for how
a RAMPART endpoint's synthetic-user emulation *runs*. Sibling of
`/decoy-mechanical-systems` and `/ghosts-mechanical-systems`. Anchors verified
2026-06-15; prefer the named symbol over the line number when they drift.

## Mental model

Every non-DC RAMPART endpoint runs **MITRE pyhuman** as a forever-loop service.
pyhuman's `emulation_loop` is UTC-hour-gated: outside the node's activity window
it sleeps; inside, it fires a *cluster* of `clustersize` tasks, each a uniformly
random pick from the node's loaded workflows, separated by `taskinterval`, with
`taskgroupinterval` between clusters. RUSE's job is purely **deploy-time
wiring**: it folds each user's PHASE-tuned `login_profile` into pyhuman CLI flags
and installs the service (systemd on Linux, scheduled task on Windows). There is
no hot-reload and no per-action LLM — pyhuman is scripted Selenium/pyautogui.
`logins.json` (from `simulate-logins.py`) is **not executed** by pyhuman; the
login-graph fields that feed it are runtime-inert.

## Code locations (two repos + read-only rules)

| What | Where | Editable? |
|---|---|---|
| RUSE deploy wiring | `RUSE/deployment_engine/rampart/` + `playbooks/rampart/` | yes (RUSE) |
| pyhuman driver scripts | `~/uva-cs-workflow/*.py` (role_human, simulate-logins, role_domains, …) | edits OK, **NEVER git commit** (memory `feedback_no_uva_workflow_commits`) |
| pyhuman engine | `~/uva-cs-workflow/Downloads/workflows.zip` → `human.py` + `app/workflows/*.py` | the bundle deployed to `/opt/pyhuman`; read with `python3 -c "import zipfile;..."` (no `unzip` binary on this host) |
| PHASE feedback (`user-roles.json`) | `/mnt/AXES2U1/feedback/rampart-controls/...` | **read-only** (upstream) |

## 1. Deploy-time wiring → the running service

`deployment_engine/rampart/spinup.py::_generate_emulation_inventory` (`:486`):
reads each user's `login_profile` from `user-roles.json` and emits per-host
inventory vars (`:608-616` `rampart_clustersize`, `rampart_day_start_hour_min`,
`rampart_activity_daily_min_hours`, …). The live fields it extracts (`:536-542`):
`clustersize`, `clustersize_sigma`, `taskinterval`, `taskinterval_sigma`,
`taskgroupinterval`, `day_start_hour_min`, `day_start_hour_max`,
`activity_daily_min_hours[7]`, `activity_daily_max_hours[7]`.

- **Linux** → `playbooks/rampart/install-rampart-emulation.yaml:47` writes a
  `rampart-human.service` whose **ExecStart** is the authoritative knob list:
  `xvfb-run -a /opt/pyhuman/bin/python -u /opt/pyhuman/human.py --clustersize …
  --clustersize-sigma … --taskinterval … --taskinterval-sigma …
  --taskgroupinterval … --seed … --workflows … --day-start-hour-min …
  --day-start-hour-max … --activity-daily-min-hours '<csv>'
  --activity-daily-max-hours '<csv>' --extra passfile /tmp/shib_login.<user>`.
  `Restart=always`, `RestartSec=30` → forever-loop.
- **Windows** → `spinup.py::_deploy_windows_emulation` (`:657`) SSHes per-VM
  (no Ansible, to dodge PowerShell `$` escaping), writes `C:\tmp\run-emulation.ps1`
  with the same flags (`:798-803`), and registers the `RampartHuman` scheduled
  task (AtStartup, `RestartCount=999`, 1-min interval).

**There is no `--block-window`.** The ExecStart and `human.py` argparse prove it
— so `login_profile._phase_block_mode.window` is inert (CLAUDE.md's hour-of-day
note mentions it aspirationally; the deployed `workflows.zip` does not implement
it).

## 2. pyhuman runtime — `workflows.zip::human.py`

Call-graph `main → run → emulation_loop → handle_workflow → workflow.action`:

- `main` (`:293`) — argparse. Live flags at `:303-321` (clustersize `:303`,
  taskinterval `:304`, taskgroupinterval `:305`, **stopafter `:306` default 0 =
  infinite**, extra `:307`, seed `:308`, workflows `:309`, clustersize-sigma
  `:310`, taskinterval-sigma `:312`, day-start-hour-min/max `:315`/`:317`,
  activity-daily-min/max-hours `:319`/`:321`). `--seed` → `random.seed`.
- `import_workflows(selected)` (`:196`) — loads `app/workflows/*.py`, filtered to
  `--workflows` names; each module exposes `load()`. RUSE installs the bundle to
  `/opt/pyhuman` and sed-rewrites the hardcoded domain in every workflow file to
  the deploy's AD domain (`role_human.py:228-231`).
- `_select_active_hours_for_day(day_start_hour_min, day_start_hour_max,
  activity_daily_*_hours, day_of_week)` (`:65`) — **UTC** hour set for today:
  random `start_hour ∈ [min,max]`, random duration ∈ the per-DoW activity range,
  `end = min(start+dur, 24)` (no midnight wrap). Recomputed at the UTC-date
  rollover (`:136`/`:154`).
- `emulation_loop(...)` (`:99`) — the engine. Each tick: if hour-gating is on and
  `now.hour not in active_hours` (`:162`) sleep 60s and retry; else fire a cluster
  of `clustersize` tasks (lognormal-jittered by `*_sigma`, the D5 knob), each
  `handle_workflow(random.choice(workflows))` after a `random.randrange(taskinterval)`
  sleep, then `random.randrange(taskgroupinterval)` between clusters.
- `handle_workflow(workflow, extra)` (`:23`) — `workflow.action(extra)`; logs
  start/success/error; one `.action()` call = one unit of traffic (it may do
  several internal clicks/fills). Workflows subclass pyhuman's `BaseWorkflow`
  (`app/utility/base_workflow.py`).

## 3. The 13 pyhuman workflows (`workflows.zip::app/workflows/`)

`browse_iis`, `browse_shibboleth`, `browse_web`, `browse_youtube`,
`google_search`, `moodle`, `download_files`, `build_software`, `execute_command`,
`spawn_shell`, `open_office_calc`, `open_office_writer`, `ms_paint`. Membership
per node = the `--workflows` list (space-separated) from the inventory. Selection
inside a cluster is **uniform random** (no weighting — unlike DECOY's
weighted/scheduled selection). Per-workflow action counts (links clicked, pages
browsed) are **hardcoded in each module**, not PHASE-tunable (memory
`project_rampart_runtime_levers`).

## 4. Volume model + what's inert

Traffic volume ≈ **clustersize × (1/taskinterval-ish) × cluster cadence**, bounded
by the hour-gate window and workflow membership. Knobs that move volume:
`clustersize`, `taskinterval`, `taskgroupinterval`, the activity-hours window, and
the workflow set. Sigmas add lognormal jitter only (D5).

**Runtime-inert (written by PHASE / present in `login_profile` but pyhuman never
reads — they feed only the dead `logins.json`):** `logins_per_hour`
(`activity_min/max_logins_per_hour`), `login_length` (`min/max_login_length`),
`terminals_open`, `recursive_logins_*`, `fraction_of_logins_*`, and
`_phase_block_mode.window`. `simulate-logins.py::simulate_terminal_day` (`:150`)
generates `logins.json` mirroring the same UTC hour math, but **pyhuman ignores
it** — it's historical metadata, not an execution plan. Memory
`project_rampart_runtime_levers` is the canonical list.

## 5. Role assignment + install (`~/uva-cs-workflow`)

`role_human.py`: `human_plugin_version = "Downloads/workflows.zip"` (`:7`);
`install_human_linux` (`:211`) unzips to `/opt/pyhuman`, sed-rewrites the domain
(`:228-231`), builds the venv + installs requirements; `install_human_windows`
(`:25`) the Windows equivalent; `deploy_human` (`:250`) the dispatcher.
`role_domains.py` assigns roles/login-profiles per node; `role_register.py`,
`role_impact.py`, `role_iis.py`, `role_moodle.py`, `role_fs.py` are the other
node roles.

## 6. Windows runtime concerns

- **Chrome reaper** — feedback deploys only (controls stay pristine):
  `spinup.py:842-868` registers the hourly `RampartChromeReaper` task that kills
  `chrome`/`chromedriver` older than 2h. pyhuman browse workflows leak orphan
  Chrome on exception paths until the VM wedges; Linux is clean (memory
  `project_rampart_chrome_reaper`). Pre-existing deploys need retrofit.
- **Scheduled-task restart** — `RestartCount=999` / 1-min interval ≈ forever.
- **Passfile auth** — creds written to `C:\tmp\shib_login.<user>` /
  `/tmp/shib_login.<user>`, read via `--extra passfile <path>` for Shibboleth
  workflows.

## 7. experiments.json + UTC contract

RAMPART entries need `start_date` + `baseline_user_roles` (neither auto-populated
— memory `feedback_rampart_experiments_fields`). The hour-gate reads UTC
(`datetime` UTC), per CLAUDE.md's 2026-05-06 contract; VM TZ
(`America/New_York`) is for log readability only.

## 8. Where do I change X?

| Goal | Where |
|---|---|
| Change a volume/timing knob | the field in PHASE `user-roles.json` (read-only) → it flows through `spinup.py::_generate_emulation_inventory` to the ExecStart flag; to change the *wiring*, edit `_generate_emulation_inventory` + the playbook ExecStart |
| Add/modify a workflow | `workflows.zip::app/workflows/<x>.py` (rebuild the zip; edits OK, no commit) + add its name to the node's `--workflows` |
| Change hour-gating logic | `human.py::_select_active_hours_for_day` / `emulation_loop` (in the zip) |
| Make a `login_profile` field live | it must become a `human.py` argparse flag **and** be passed by both ExecStart (Linux playbook) + `_deploy_windows_emulation` — wiring alone does nothing |
| Windows orphan cleanup | `spinup.py::_deploy_windows_emulation` reaper block |

RAMPART has **no idempotent same-deploy refresh** (DECOY/GHOSTS do) — use
explicit `./teardown` to refresh (CLAUDE.md). Keep this skill + `/rampart-deploy`
+ `/rampart-audit` in sync when the engine changes (memory
`feedback_keep_skills_memory_in_sync`).
