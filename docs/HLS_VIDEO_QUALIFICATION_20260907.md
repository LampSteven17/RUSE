# HLS VideoViewing qualification — 2026-09-07

## Scope and identity

The intended matrix was one assigned 300-second occurrence per candidate and
Brain, run serially through the real loader, executor, Brain and bounded action.
Qualification was stopped by the operator before the final SmolAgents checks
completed. No recurring scheduler was enabled. No scored Control/Feedback VM
was changed.

- CPU: `decoy-mchp-canary/2026-09-07_162426Z`, VM
  `d-mchpcanary2026-09-07_162426Z-mchp-cpu-0`, `10.246.115.36`.
- GPU: `decoy-gpu-canary/2026-09-07_183346Z`, VM
  `d-gpucanary2026-09-07_183346Z-browseruse-gpu-0`, `10.246.118.217`.
- Published checkpoint: `9d5b6ceb13d7ac53425a8e42d5ce62ad14d3de38`.
  Checks used its corrected runtime plus this HLS patch applied to the isolated
  installations. The older installed revision marker was not rewritten; these
  observations are patch qualification, not an immutable fresh-deployment soak.

Complete local evidence is retained under
`deployments/decoy-gpu-canary/hls-matrix-20260907/`: exact plans, observation
JSONL, actual start/end and terminal records, detailed logs, player screenshots
where captured, and post-run process inventories. Each separate output path has
one occurrence `w0-s0`; the output path, SUP and resource disambiguate identity.

## Demonstrated installer defect

Initial MCHP checks for all three sources failed before playback with
`mediaError: manifestIncompatibleCodecsError`. A traffic-free blank-page probe
reported H.264 unsupported and MSE H.264 false. The CPU installation had no
`libavcodec60`. Installing Noble's system decoder, without recommended packages,
changed those probes to `probably` and `true`. No GPU drivers, browser changes,
timeout changes, or playback fallback were introduced. Initial failures were
retained; only the three affected MCHP observations were repeated.

## Demonstrated BrowserUse result-adapter defect

The first BrowserUse Mux action genuinely ran once and passed its final media
inspection, but the framework logged Judge FAIL while RUSE emitted completed.
That terminal is not accepted as a valid workflow success. BrowserUse 0.12.7's
`is_successful()` returns the action's `success`; the separate `is_validated()`
returns the judge's verdict. RUSE now requires both, alongside `is_done()` and
the existing actual-action validation. No judge rejection is overridden, and
missing/malformed validation is failure. The original evidence is preserved.

## Canary-only setup corrections

The first SmolAgents Mux observation initialized across its selected start minute
and was correctly marked `startup_past_due`, without executing. The one-shot
observation harness now allows two minutes of preparation; production scheduling
is unchanged. Its unexecuted case was rescheduled in a separate evidence path.

SmolAgents Apple initially invoked the real tool but the shared GPU canary
lacked `ffmpeg`. The existing SmolAgents installer already requires that package;
it was installed on this isolated canary without a product packaging change.
The model subsequently claimed playback had occurred, but RUSE correctly failed
the workflow. This failed environment check is retained separately.

## Acceptance boundary

Browser success means exactly one assigned playback started, the assigned wait
elapsed, and no explicit media/fatal-player failure was detected at the final
inspection. It does **not** prove uninterrupted playback throughout the interval.
SmolAgents must genuinely invoke its tool; FFmpeg must exit successfully under
the existing `-re -t 300` command and 360-second bound. Its final progress time
is a packet timestamp, not packet end time: the Mux check completed normally in
302.081 seconds with a final timestamp of 299.983333. An initially added strict
`>= 300` progress check incorrectly rejected it and was removed; no arbitrary
rounding tolerance was introduced. This retains the original FFmpeg acceptance
boundary, not a new guarantee against a provider's early clean EOF.
LLM prose or missing/repeated
actions remain failures, even when the endpoint is accessible.

All non-video resources, approved instructions, schedule schema, scheduler,
exactly-once validation and worker-owned cleanup remain unchanged. The PHASE
contract delta is documented in [the handoff](PHASE_HLS_VIDEO_HANDOFF.md).

## Results at the operator stop

| Source | Scripted | MCHP | BrowserUse | SmolAgents |
| --- | --- | --- | --- | --- |
| Mux Big Buck Bunny | Completed | Completed after decoder fix | Completed after judge fix | Final verification interrupted |
| Apple BipBop | Completed | Completed after decoder fix | Failed: duplicate action requested | Final adapter not rechecked |
| Unified Tears of Steel | Completed | Completed after decoder fix | Completed | Not executed |

Browser final media times (seconds) were respectively:

- Scripted: Mux 300.442738; Apple 300.340676; Unified 299.979516.
- MCHP: Mux 300.158358; Apple 300.139641; Unified 300.124901.
- BrowserUse: corrected Mux 300.257006; Apple 300.319943; Unified 299.960121.

All these browser actions showed initial media advancement, one actual playback,
and no explicit final media/player error. BrowserUse Apple requested two actions;
only the first ran, and the missing second result correctly prevented completion.
The initial BrowserUse Mux false-success record is invalid qualification evidence;
its corrected repeat required and received an affirmative judge verdict.

SmolAgents consumed Mux and Apple through FFmpeg with exit code 0, empty stderr,
and final packet timestamp 299.983333, but the temporary timestamp comparator
rejected them. That comparator is absent from the committed implementation.
The final Mux rerun was interrupted on operator request; Apple was not rerun
after its removal, and Unified never executed through SmolAgents. These are
not claimed as completed workflows or completed four-Brain qualification.

Both local test controllers and the active isolated Python/FFmpeg processes
were stopped. Canary schedulers were never enabled. The canary VMs and evidence
remain; no production fleet was modified. The FFmpeg dependency installation
restarted `fwupd` on the isolated GPU canary, not a canonical SUP service.

## Validation commands

- `PYTHONPATH=decoys python3 -m unittest tests.test_hls_video tests.test_video_start_verification tests.test_phase_workflow_runtime tests.test_browseruse_worker_lifetime tests.test_smol_logging_policy tests.test_mchp_editor_readiness -q`
  — 136 run, 135 passed, one Node-dependent skip; 5.801 seconds, before the final
  FFmpeg timestamp-comparator removal.
- `PYTHONPATH=decoys python3 -m unittest tests.test_hls_video tests.test_phase_workflow_runtime -q`
  — final FFmpeg correction: 101 run, 100 passed, one Node-dependent skip;
  5.066 seconds.
- `PYTHONPATH=decoys python3 -m unittest discover -s tests -q`
  — final implementation: 317 run, 316 passed, one Node-dependent skip;
  28.068 seconds.
- The skipped JavaScript regression was executed separately with the existing
  Playwright-bundled Node on the GPU canary and passed. It exercises native/HLS
  selection, exact assignment, disabled request retries, fatal-error latching,
  no automatic play and owned player destruction.
- `python3 -m compileall -q decoys deployment_engine tests` — passed.
- `bash -n INSTALL_SUP.sh deploy teardown list` — passed.
- JSON parsing of every workflow contract and Draft 2020-12 schema validation
  — passed; YAML parsing of every Decoy playbook — passed.
- `ansible-playbook -i deployments/hosts.ini deployment_engine/playbooks/decoy/install-sups.yaml --syntax-check`
  — passed (no matching test inventory hosts; no tasks executed).
- `git diff --check` — passed.
