# Scripted Probe deployments

Probes are an explicit `type: probe` category, not Control/Feedback deployments
or RUSE-only canaries. They reuse the Decoy provisioning and Scripted runtime.
PHASE records use `system: decoy`, `purpose: other`, `target: null`; PHASE's
ordinary Control/Feedback inference excludes them.

From the operator checkout, after publishing this revision:

```sh
./deploy --probes
./list
./teardown --probes
```

Deployment validates all 27 PHASE daily variants before displaying a combined
plan or prompting. It creates seven separate single-CPU-Scripted deployments:
`probe-research`, `probe-video`, `probe-download`, `probe-upload`,
`probe-documents`, `probe-network-share`, and `probe-idle`.
Only network-share gets the existing `v1.small` Samba sidecar. The sidecar is
part of its exact VM cohort, not an eighth measured SUP.

An individual configuration can be selected, for example
`./deploy --probes probe-idle`. Exact teardown uses the usual dated identity:
`./teardown probe-idle-YYYY-MM-DD_HHMMSSZ`. `./teardown --decoys` never selects
probes, even failed probes. `./teardown --probes --failed` selects only failed
probes with exact-prefix cloud VMs. Historical local files are preserved.

`probe_workflow` is an explicit canonical workflow; null selects idle. Idle
installs the same Scripted dependencies and service but constructs no Brain,
plan, scheduler or fake workflow. Normal nonempty-plan validation is unchanged.

Active configurations point at the PHASE-owned directories listed in
`/home/ubuntu/PHASE/plans/probes/README.md`. The installer copies those validated
variants, not generated copies owned by this repository. Resource order is the
explicit approved catalog order. Each local day holds one resource; selection
uses elapsed America/New_York calendar days from the recorded UTC run start,
modulo resource count. A restart does not reset rotation. Existing in-flight
tasks retain their original resource, workspace day and capacity ownership.
Probe artifacts use `workspace/<occurrence-id>/<local-date>/`, preserving the
assigned filename without same-day peer overwrites. Ordinary Decoy workspace
paths are unchanged.

Each plan has 24 hourly bursts, repeating 1/2/4/8/10 scheduled starts:
115 per active VM, 690 across six. First-day past starts and DST gaps retain the
ordinary missed-start rules. Scheduled starts are not guaranteed actual starts
or overlap; gaps between bursts are not guaranteed idle traffic. Daily selection
is logged with date, cycle index, resource and filename; terminal records retain
their resolved resource and actual timing.

No live qualification is implied by local tests. Deployment, real traffic,
midnight rollover under load, and the idle traffic baseline still require an
authorized live run.

## Implementation and local validation (2026-09-15)

Changed files:

- `INSTALL_SUP.sh`
- `decoys/phase_workflow/probes.py` (new)
- `decoys/phase_workflow/registry.py`
- `decoys/phase_workflow/runtime.py`
- `decoys/sup/__main__.py`
- `deployment_engine/__main__.py`
- `deployment_engine/core/config.py`
- `deployment_engine/core/plan.py`
- `deployment_engine/decoy/spinup.py`
- `deployment_engine/list.py`
- `deployment_engine/teardown.py`
- `deployment_engine/playbooks/decoy/install-sups.yaml`
- `deployments/probe-research/config.yaml` (new)
- `deployments/probe-video/config.yaml` (new)
- `deployments/probe-download/config.yaml` (new)
- `deployments/probe-upload/config.yaml` (new)
- `deployments/probe-documents/config.yaml` (new)
- `deployments/probe-network-share/config.yaml` (new)
- `deployments/probe-idle/config.yaml` (new)
- `tests/test_probes.py` (new)
- `tests/test_phase4_control_canary.py`
- `docs/probes.md` (new)

Commands and final results:

```sh
PYTHONPATH=decoys python3 -m unittest tests.test_probes -v
# 16 passed

PYTHONPATH=decoys python3 -m unittest \
  tests.test_probes tests.test_operator_commands \
  tests.test_phase_feedback_deployment tests.test_phase4_control_canary \
  tests.test_phase4_operator_corrections tests.test_phase_workflow_runtime \
  tests.test_phase_share_sidecar tests.test_decoy_runtime_canary \
  tests.test_phase_run_registry tests.test_installer_simplification
# 225 passed

PYTHONPATH=decoys python3 -m unittest discover -s tests
# 334 run: 333 passed, 1 skipped (existing HLS JavaScript test: Node unavailable)

python3 -m compileall -q decoys/phase_workflow decoys/sup deployment_engine tests
bash -n INSTALL_SUP.sh deploy teardown list
ANSIBLE_LOCAL_TEMP=/home/ubuntu/RUSE/deployments/logs/ansible-probe-syntax \
  ansible-playbook -i localhost, \
  deployment_engine/playbooks/decoy/install-sups.yaml --syntax-check
git diff --check
# All passed; syntax-only inventory has no sup_hosts, as expected.
```

All 27 supplied JSON plans parsed and validated with the actual loader. All
seven new YAML configurations and the installer playbook parsed successfully.
The combined plan was rendered with confirmation mocked false. Provisioning,
registration and teardown tests used temporary local state and mocked external
operations. The initial restricted-environment focused run stalled and was
terminated; the final focused/full results above were obtained in the normal
operator environment. No live qualification, deployment, service operation,
commit or push was performed. Unrelated canary evidence was preserved.
