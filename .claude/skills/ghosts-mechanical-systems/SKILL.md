---
name: ghosts-mechanical-systems
description: GHOSTS runtime mechanics — how a deployed NPC actually runs, as a maintainer code-map. GHOSTS = CMU SEI .NET NPC traffic generators (1 API server + N Universal clients) at `~/GHOSTS/src/`, wrapped by RUSE deploy. Traces the client call-graph from `Program.Main` → `CheckId` machine registration → `Updates` → `Orchestrator` (reads per-NPC `config/timeline.json`, one thread per handler via reflection dispatch) → a handler's `RunOnce`/`ExecuteEvents` → one Selenium-Firefox browse. Covers the timeline.json schema as consumed (`Timeline`/`TimelineHandler`/`TimelineEvent`, `HandlerType`, `UtcTimeOn`/`Loop`/`HandlerArgs`/`Initial`/`Id`), UTC `WorkingHours.Is` gating (midnight wrap, per-handler block), the Firefox lifecycle (stays open under `Loop=true`, `browser-id` kill-tag, stickiness), the `_phase_metadata` strip-on-rewrite, and machine/timeline API check-in. Use when modifying GHOSTS runtime behavior or tracing why an NPC generates the traffic it does. Anchors are from the local `~/GHOSTS` checkout; production deploys PIN v9.0.0 (`ghosts-controls/config.yaml`) pulled fresh at install, so exact lines may differ — prefer symbols. Does NOT cover deployment (see /ghosts-deploy) or health auditing (see /ghosts-audit); GHOSTS source is upstream CMU code (read-only here), PHASE feedback is read-only upstream.
type: skill
---

# ghosts-mechanical-systems

The third per-system skill: **operate** (`/ghosts-deploy`) → **verify**
(`/ghosts-audit`) → **understand/modify** (this). A maintainer code-map for how a
GHOSTS NPC *runs*. Sibling of `/decoy-mechanical-systems` and
`/rampart-mechanical-systems`. Anchors verified 2026-06-15 against the local
`~/GHOSTS/src` tree.

## Mental model

A GHOSTS NPC is the **.NET Universal client**. At startup it registers with the
API (gets a machine `Id`), loads its per-NPC `config/timeline.json`, and the
`Orchestrator` launches **one thread per `TimelineHandler`** (reflection-dispatched
by `HandlerType` name). Each handler is **UTC-gated by `WorkingHours.Is`** — out
of window it blocks (sleeps) until its `UtcTimeOn`. In window it runs its
`TimeLineEvents` (browse/random/click/…) via Selenium-Firefox; under `Loop=true`
the same Firefox window stays open and the event list repeats. Activity is posted
back to the API. RUSE's job is **deploy-time**: route each PHASE-tuned
`npc-{N}/timeline.json` to a client VM and pin the engine version. There is no
RUSE Python in the runtime path — it's all CMU .NET.

## Code locations + version caveat

| What | Where | Editable? |
|---|---|---|
| GHOSTS engine (Universal client, API, Domain) | `~/GHOSTS/src/` | upstream CMU — read-only here |
| RUSE deploy wiring | `RUSE/deployment_engine/ghosts/` + `playbooks/ghosts/` | yes (RUSE) |
| PHASE feedback (`npc-{N}/timeline.json`) | `/mnt/AXES2U1/feedback/ghosts-controls/{preset}_v{version}/{dataset}/` | read-only upstream |

**Version:** RUSE deploys **pin v9.0.0** (`deployments/ghosts-controls/config.yaml:33`
`ghosts_branch: v9.0.0`, memory `project_ghosts_version_pin`), pulled fresh at
install. The local `~/GHOSTS` checkout is a later master-track commit
(`v8.3.1+`), so line numbers here approximate the deployed tag — **prefer
symbols**. master regressed Firefox via missing libgtk; system Firefox is
installed separately from the source pin.

## 1. Client startup call-graph — `Ghosts.Client.Universal/`

`Program.Main` (`Program.cs`) → `Program.Run`:
1. `CheckId.Id` (`Comms/CheckId.cs`) — machine identity. Reads
   `instance/id.json`; if absent, `GET /api/clientid` (server `FindOrCreate`s a
   machine row) and caches the returned Guid to disk. Registration is what makes
   the NPC show up in `/api/machines` (the audit's distinct-name check).
2. `Updates.Run` (`Comms/Updates.cs`) — background polls `/api/clientupdates`;
   an `UpdateType.Timeline` rewrites `config/timeline.json`, and
   `PostClientResults`/`PostCurrentTimeline` report activity to
   `/api/clienttimeline`.
3. `Orchestrator.Run` (`TimelineManager/Orchestrator.cs:38`):
   - `TimelineBuilder.GetTimeline()` (`:42`) loads `config/timeline.json`.
   - `FileSystemWatcher`s on the timeline (`:62`) and `config/stop.txt` (`:75`)
     → live reload / `Environment.Exit`.
   - For each `TimelineHandler`: `ThreadLaunch` (`:157`/`:164`) → `ThreadLaunchEx`
     (`:170`) → `RunHandler` (`:173`), which **reflection-dispatches**:
     `Type.GetType("Ghosts.Client.Universal.Handlers.{HandlerType}")` (`:176`) →
     `Activator.CreateInstance(...)` → `instance.Run()`. Adding a handler type
     needs no Orchestrator change.

## 2. timeline.json schema as consumed — `Ghosts.Domain/Messages/Timeline.cs`

- `Timeline` (`:16`): `Id: Guid` (`:26`), `Status`, `TimeLineHandlers[]`.
- `TimelineHandler` (`:47`): `HandlerType` enum (`:101`, e.g. `BrowserFirefox`),
  `Initial` (`:62`, the first URL), `UtcTimeOn`/`UtcTimeOff` (`:66`, the gate),
  `HandlerArgs: Dictionary<string,object>` (`:77`), `Loop: bool` (`:79`).
- `TimelineEvent` (`:139`): `Command` (browse/random/click/crawl/…), `CommandArgs`,
  `DelayBefore`/`DelayAfter`, `TrackableId` (`:150`).

`TimelineBuilder` (`Ghosts.Domain/Code/TimelineBuilder.cs`): `GetTimeline` (`:63`/
`:68`) reads + deserializes; `GetTimelineFromString` (`:81`) assigns a `Guid` if
missing and **writes the file back** via `SetLocalTimeline` (`:93`/`:124`).

**`_phase_metadata` strip:** PHASE stamps `_phase_metadata` on each source
timeline, but it's not a field on the C# `Timeline` class, so Newtonsoft drops it
on deserialize — and the startup `SetLocalTimeline` rewrite persists the
stripped object. So the on-VM `config/timeline.json` has **no** `_phase_metadata`.
That's why `/ghosts-audit` checks the FATAL `_phase_metadata.mode` gate against
the **run_dir copy** of the timeline, not the on-VM file.

## 3. UTC WorkingHours gating — `Ghosts.Domain/Code/WorkingHours.cs`

`WorkingHours.Is(TimelineHandler handler)` (`:14`): computes today's UTC on/off
from `UtcTimeOn`/`UtcTimeOff`; if both zero, ungated. Handles **midnight wrap**
(off < on → off is tomorrow) and optional `UtcTimeBlocks` (multiple windows).
Out of window → it **blocks the handler thread** (`Thread.Sleep`) until the next
on-time. Called at handler start (`BaseHandler.cs:44`) **and** per timeline event
(`BrowserBase.cs`), so a handler re-checks the gate between events. This is the
GHOSTS analogue of DECOY's window gate / RAMPART's hour-gate — all UTC (CLAUDE.md
2026-05-06).

## 4. Handler loop + Firefox lifecycle

- `BaseHandler` (`Handlers/BaseHandler.cs:18`): abstract `RunOnce` (`:73`); `Run`
  gates via `WorkingHours.Is` (`:44`) then, if `Handler.Loop` (`:51`), calls
  `RunOnce()` in a `while (!cancelled)` loop, else once.
- `BrowserFirefox` (`Handlers/BrowserFirefox.cs:18`, a primary-ctor class):
  `RunOnce` (`:94`) → `GetDriver` (`:184`) builds the `FirefoxDriver` from
  `HandlerArgs` (headless/incognito/ua-string/etc.) and tags each instance with a
  **`browser-id` GUID** (`:213-222`) baked into the command line; `Navigate` to
  `Handler.Initial` (`:116`); then `ExecuteEvents`. On exit, `finally` quits the
  driver and `KillBrowser()` (`:167`) `ps|grep {browser-id}|kill`s orphans
  (Selenium's `Quit()` is unreliable on Linux).
- **`Loop=true` keeps Firefox open:** the event loop lives inside `RunOnce`, so the
  same driver/window is reused across event-list repeats — no per-cycle restart
  until cancellation, error, or an `actions-before-restart` threshold.
- `ExecuteEvents` (`Handlers/BrowserBase.cs:57`) — per event: re-gate, `DelayBefore`,
  then command dispatch: `browse` (`:211` → `MakeRequest` GET `:215`), `random`
  (`:197` → `DoRandomCommand`), `randomalt` (`:203`), `click`, `crawl`, …, then
  `DelayAfter` (jittered). **Stickiness** (`HandlerArgs["stickiness"]` parsed at
  `:359`) = % chance after a URL to follow internal links for
  `stickiness-depth-{min,max}` clicks — the in-window fan-out lever (memory
  `reference_ghosts_runtime_internals`).

## 5. API server — `Ghosts.Api/`

1 API VM (Docker stack) backs N clients. Endpoints the client hits:
`GET /api/clientid` (registration / `Id`), `GET /api/clientupdates` (timeline
push), `POST /api/clienttimeline` (activity report). `GET /api/machines` lists
registered machines by distinct name — the audit's registration check. The API
also serves the frontend.

## 6. RUSE wiring (deploy-time only)

`deployment_engine/ghosts/spinup.py`: `run_ghosts_spinup` (`:24`) routes per-NPC
timelines via `_build_npc_timeline_mapping` (`:122`) — **fail-loud** if a feedback
source has no `npc-*/timeline.json` (`:129`, no silent shared-timeline fallback) —
copies them into the run_dir and bakes per-host `ghosts_timeline_file` into the
inventory (`_write_inventory:411`); NPC VMs named `g-{hash}-npc-{i}` (`:276`).
Feedback NPCs get an `m1.medium` flavor + **3G cgroup memcap drop-in** (the
.NET memleak is fixed on v9.0.0; the footprint is now Firefox itself ~3GB —
memory `project_ghosts_client_memleak`); controls run pure upstream, no drop-in.
GHOSTS has the idempotent same-deploy refresh keyed on its topology signature
(CLAUDE.md).

## 7. Where do I change X?

| Goal | Where |
|---|---|
| Change an NPC's traffic mix / timing | the PHASE `npc-{N}/timeline.json` (read-only) — handlers, `UtcTimeOn`, delays, stickiness; to change *routing*, `spinup.py::_build_npc_timeline_mapping` |
| Add/modify a handler behavior | `~/GHOSTS/src/.../Handlers/` (upstream — read-only here; changes belong upstream/in the pinned tag) |
| Change working-hours gating | `WorkingHours.Is` (upstream) |
| Change the pinned engine version | `deployments/ghosts-controls/config.yaml::ghosts_branch` (+ keep memory/skills in sync) |
| Change memcap / flavor | `spinup.py` + the ghosts client install playbook drop-in |
| Debug a stripped `_phase_metadata` | check the **run_dir** timeline copy, not the on-VM file (§2) |

Keep this skill + `/ghosts-deploy` + `/ghosts-audit` in sync when the engine
changes or the version pin moves (memory `feedback_keep_skills_memory_in_sync`).
