# Background-service probes

Deploy **only** the three new single-CPU service probes:

```sh
./deploy --probes --background-services
```

This selects `probe-ntp`, `probe-firmware`, and `probe-motd`, each with one
`scripted-cpu` environment on `v1.14vcpu.28g`. No Brain or foreground workflow
runs. No GPU, sidecar, behavior plan, PHASE generation, or synthetic network
request is added. Existing `./deploy --probes`, `--probe-sup mchp-cpu`, and idle
references are unchanged. A single new configuration can be selected with,
for example, `./deploy --probes --background-services probe-ntp`.

They retain `type: probe`, `purpose: other`, `target: null`; ordinary inference
and `./teardown --decoys` exclude them. `./teardown --probes` includes them and
the existing Probe fleets; use an exact dated identity for selective removal.

## Hourly observations

Reuse the existing daily executor, serial worker ownership, missed-start rules,
and AgentLogger. The private in-memory observation timetable is not a PHASE
workflow schema or a seventh workflow. Events use existing INFO records, never
fabricated `workflow_plan_terminal` records. Each hour has one observation.
The following UTC six-hour cycle repeats four times each day:

| Hour modulo 6 | NTP | Firmware | MOTD |
|---|---|---|---|
| 0 | Observe | Native refresh | Cache read |
| 1 | Observe | Native refresh | Native retrieval |
| 2 | Observe | Observe | Cache read |
| 3 | Stop tested service | Stop tested service | Stop tested service |
| 4 | Start tested service | Start native refresh | Start native retrieval |
| 5 | Observe | Native refresh | Native retrieval |

The explicit inactive interval lasts one hour. There is no cache deletion,
firmware force option, endpoint substitution, package upgrade, suspend or reboot.
The tested firmware/MOTD timer is paused while the probe owns hourly triggering;
the native service definition is unchanged. The other services and their timers
remain at the idle environment's defaults. Cleanup restores the tested timer
or NTP service's original active state. Startup records effective unit settings,
versions, state and available boot journal separately. Earlier boot activity is
retrospective, not claimed to have been captured by the new runtime logger.

NTP uses existing `systemd-timesyncd`. Observation reads accepted sample
timestamps, packet counts, PID and offsets; it does not issue an hourly NTP
request, change polling parameters, or treat a synchronized flag as new traffic.
Counter deltas are not compared across daemon restarts. While NTP is stopped,
the observer does not D-Bus-activate it and marks current offset unavailable.
Last pre-stop and next accepted post-enable sample offsets are retained with
their timestamps; these do not measure continuous drift during the off interval.
The collector clock is never changed.

Firmware uses `systemctl start fwupd-refresh.service`, whose native command is
`fwupdmgr refresh`. Native exit status 2 means nothing to refresh and is retained,
not retried. Fresh metadata is requested only when native cache policy allows.
MOTD uses `systemctl start motd-news.service`, including its approved native
`--force`; cache-only observations invoke `50-motd-news` without that argument.
The native MOTD helper may return zero even after a download error. Therefore
the event says retrieval finished, **content success unverified**; zero exit or
an updated cache is not presented as verified successful HTTPS retrieval.

Commands are bounded at 120 seconds. A timed-out service start requests a bounded
stop of that exact unit and records the timeout/cleanup result. Missing native
units fail installation before SUP startup; no alternate implementation or
automatic remote enablement is introduced.

## Evidence and interpretation

Each observation records service, operation, exact date/window identity, UTC
start/end, command results, service state, settings at startup, cache metadata,
available journal errors, NTP sample details, and interface byte deltas both
during the command and since the preceding observation. Counter resets are
unknown, not negative traffic. Zero-traffic outcomes are retained.

Interface counters are **whole-VM evidence**, not process ownership. Timing
overlap, cache timestamps and aggregate traffic do not establish attribution.
Existing Zeek capture—including DNS—is unchanged. No packet filter, network
policy, DNS/DHCP/SSH change or clock collector is introduced.

## Bounded qualification, 2026-09-18

Native commands were exercised on the existing isolated CPU canary, without
deploying probes or running foreground workflows. This was operation
qualification with a five-second inactive check, not a live one-hour cycle or
variability experiment. Existing workflow/idle behavior is also regression-tested.

- MOTD native retrieval and re-enable returned normally, with whole-VM byte
  deltas observed. Both cache-only reads had zero observed interface-byte delta.
  Content/network success remains explicitly unverified by the native helper.
- Three firmware starts returned native cache-fresh status 2 and zero observed
  interface-byte delta. Cache expiration/fresh metadata transfer was **not**
  observed and was not forced.
- NTP no-exchange observations were retained. Stop prevented D-Bus observation
  from starting the daemon. Re-enable followed by a 40-second observation
  produced a new accepted synchronization timestamp. Last pre-stop offset was
  +31.228 ms; post-enable sample offset was +15.135 ms.
- All tested settings were restored afterward. No existing Probe, Control or
  Feedback VM was changed. Raw qualification records remain under the retired
  canary's `runs/2026-09-18_190427Z/evidence/service-qualification.jsonl`.

The deployed hourly experiment must still establish variability, native fresh
firmware retrieval, long off/on behavior and Zeek-level network outcomes. Local
tests and short native-operation checks do not establish those results.

## Validation and canary cleanup

```sh
PYTHONPATH=decoys python3 -m unittest tests.test_service_probes tests.test_probes tests.test_mchp_probes tests.test_operator_commands tests.test_phase_run_registry tests.test_installer_simplification -q
# 88 passed
PYTHONPATH=decoys python3 -m unittest discover -s tests
# 362 run: 361 passed, 1 existing skip (Node unavailable for HLS JavaScript test)
python3 -m compileall -q decoys/phase_workflow decoys/sup deployment_engine tests
bash -n INSTALL_SUP.sh deploy teardown list
ANSIBLE_LOCAL_TEMP=/home/ubuntu/RUSE/deployments/logs/ansible-service-probe-syntax ansible-playbook -i localhost, deployment_engine/playbooks/decoy/install-sups.yaml --syntax-check
git diff --check
# All passed; all Probe YAML configs and installer YAML parsed successfully.
```

The retired `decoy-mchp-canary/2026-09-18_190427Z` VM
`2805c9c2-efd6-425f-a5f1-91544261e1e2` and captured boot volume
`8407129a-c59e-4137-961d-a4c0974bf861` were deleted and independently verified
absent. The existing teardown command then attempted PHASE closure for this
unregistered canary; after rechecking exact resource absence, the existing
RUSE-only finalizer marked it cleaned and removed its exact SSH block. Historical
run files and qualification evidence remain. No teardown implementation was
changed as part of this patch. No new Probe VM was deployed.
