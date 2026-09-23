# Baseline and optional NTP policy handoff

Implementation is local and uncommitted. No live qualification or deployment
has been performed. Preserve the published experiment recovery tag and frozen
SHA in `experiment-runtime-freeze.md`; that older runtime does not support this
new policy. Publishing or deploying a newer revision requires approval.

## Global Baselines

After publication and deployment approval, the CLI is:

```bash
./deploy --decoys --baseline
./deploy --decoys --baseline --human
./deploy --decoys --baseline --ctrlfix --not-human
./deploy --decoys --baseline --ctrlfix --human
```

Default direction is `not-human`; default preset is `colfix_v12.5.0`.
`--human` and `--not-human` conflict. Existing `--preset` and `--ctrlfix` conflict.
Baselines cannot combine with Controls, Feedback, probes, canaries, targets,
or explicit sources. The existing `--gpu v100|rtx` selection is retained.

Discovery reads only `/data/axes-mirror/baselines/<preset>/<direction>/`.
The lexically newest calendar-valid direct timestamp child is selected and
must validate as exactly `scripted-v1.json`, `mchp-v1.json`,
`browseruse-v1.json`, and `smolagents-v1.json`, using `controls-v2` and each
canonical SUP identity. An invalid newest generation aborts; no historical,
version, or direction fallback occurs. All four plans validate before display
or confirmation. Configuration is written only after confirmation.

One global four-SUP fleet is created per selection, with the existing conditional
NetworkShareAccess sidecar and immutable-revision installation path. Examples:

- `decoy-baseline-colfix-v12-5-0-human`
- `decoy-baseline-colfix-v12-5-0-not-human`
- `decoy-baseline-ctrlfix-v12-5-1-human`
- `decoy-baseline-ctrlfix-v12-5-1-not-human`

Existing `decoy-controls` identities, directories and commands are unchanged.

## Registration agreement for PHASE

The existing timestamped `deployment.json` contains the usual timestamps,
capture interface and VM roster, plus these Baseline facts:

```json
{
  "system": "decoy",
  "purpose": "control",
  "target": null,
  "baseline_direction": "human",
  "model_preset": "colfix_v12.5.0"
}
```

`baseline_direction` allows `human` or `not-human`; `model_preset` allows
`colfix_v12.5.0` or `ctrlfix_v12.5.1`. These optional fields occur together and
only on Decoy control records with null target. Existing Controls and Feedback
records have neither field. The saved RUSE configuration carries both fields.
RUSE's extended schema is `contracts/phase-run-v1/phase-run-v1.schema.json`.

Before deployment, PHASE must adopt this schema extension and separate inference
and graphs by explicit Baseline direction and preset. Its newest-Control-only
selection is not sufficient and has not been changed here. Do not infer direction
from names or substitute Baselines for historical Controls. RUSE's existing
control-purpose list/teardown filtering also includes these Baseline fleets.

## Optional NTP policy

The workflow schema is copied exactly from PHASE's local checkout. Absent policy
or `enabled: false` performs no time-service queries, changes, extra minute wakes,
or signal-handler replacement. Schema validation still rejects malformed blocks.
An enabled policy admits an empty off-interval list or one integer interval with
`0 <= start < end <= 1440`.

Enabled runtimes enforce `[start, end)` using current plan-local minutes on
startup and each minute through the existing executor. Outside the interval,
NTP is re-enabled, including after restart. DST gaps use the time actually reached;
fall-back may repeat stop/start transitions. No once-per-day ledger is used.
Workflows, resource assignments, limits and missed-occurrence rules are unchanged.

Native `timedatectl` controls the installed Ubuntu NTP service; no client is
installed or guessed, and DNS, MOTD, firmware and packet capture are unchanged.
Commands are noninteractive and bounded to the existing service-probe allowance
of 120 seconds. Service failures are logged and propagate through the runtime's
existing failure path; the existing service restart policy is unchanged.

Existing detailed/JSONL info logs record requested intervals, timezone, request
and observed UTC timestamps, enabled state and failures. Re-enable success is
not proof of clock synchronization. Clean shutdown restores NTP only if this
process disabled it; enabled-policy SIGTERM/SIGINT unwind cleanup before exiting.
SIGKILL, VM loss and failed native-service restoration cannot guarantee cleanup.
On next startup an enabled policy re-enforces current membership.

Tests use fake clocks and mocked service operations, including DST. Native
timedated permissions/client integration, restart/clock-step effects and observed
traffic still require a separately authorized isolated qualification. Neither
traffic improvements nor DST traffic estimates are qualified by these tests.

## Local validation

```bash
PYTHONPATH=decoys python3 -m unittest tests.test_ntp_policy tests.test_baseline_deployment tests.test_phase4_control_canary tests.test_phase_feedback_deployment tests.test_phase_run_registry tests.test_operator_commands -q
PYTHONPATH=decoys python3 -m unittest discover -s tests -v
python3 -m compileall -q deployment_engine decoys/phase_workflow tests
bash -n deploy INSTALL_SUP.sh
git diff --check
```

Final results: focused **101 passed**; full **393 run, 392 passed, one existing
skip** (Node unavailable for the HLS JavaScript test). Both modified JSON schemas
pass Draft 2020-12 schema checks; the workflow schema is byte-identical to PHASE's
local copy. Existing Controls config and installer playbook parse as YAML.
Compileall, shell syntax and diff checks passed. No live service was exercised.

Changed files:

- `contracts/phase-run-v1/phase-run-v1.schema.json`
- `contracts/phase-workflow-plan-v1/phase-workflow-plan-v1.schema.json`
- `decoys/phase_workflow/{loader,executor,runtime,ntp_policy}.py`
- `deployment_engine/__main__.py`
- `deployment_engine/core/{config,deploy_steps,feedback,phase_run_registry,plan}.py`
- `tests/{test_baseline_deployment,test_ntp_policy,test_phase_run_registry}.py`
- `docs/baseline-ntp-handoff.md`
