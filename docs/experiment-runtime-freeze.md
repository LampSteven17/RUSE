# PHASE Experiment Runtime Freeze

## Published recovery point

- Deployment/development branch: `main` (no separate branch or worktree).
- Annotated recovery tag: `recovery/phase-experiments-2026-09-20`.
- Frozen commit: `71bb7a4fb90a6c3e67bf5811a3823e74c6b50ef0`.

Preserve this tag: do not move, replace, or delete it. The reviewed state was
already committed and published before the tag was created. Unrelated local
canary evidence is not part of the recovery point.

## Explicit experiment pin

For an operator-authorized deployment, prefix the existing command with:

```bash
RUSE_GIT_REF=71bb7a4fb90a6c3e67bf5811a3823e74c6b50ef0 ./deploy <approved arguments>
```

Use the full SHA, not `main`, `HEAD`, or the tag name as `RUSE_GIT_REF`.
The existing revision resolver passes this exact SHA to VM installation;
Ansible checks out and verifies it. Without the environment variable, the
resolver selects the current checkout's HEAD. The tag alone does not change
that default. This note does not change shell settings or authorize a deployment.

Existing fleets retain their installed revisions and records unchanged; this
freeze does not assert that they already run the frozen SHA. Do not restart,
redeploy, or alter them to apply this pin. Deployment of a newer runtime requires
explicit operator approval.

## Changes after the recovery point

The first subsequent commit documents this freeze only; it changes no runtime.

The 2026-09-24 stability corrections are limited to the parenthesized-link
correction (10 link/truth tests) and bounded native NTP verification (20 NTP
tests). The pre-stability checkpoint remains preserved as
`recovery/pre-stability-audit-2026-09-24` at
`2bfa7d2a66460fdfbd1f4efb0a239224cd6946d9`.

On 2026-09-24, isolated CPU canary
`decoy-mchp-canary/2026-09-24_140731Z` ran one native NTP adapter verification
cycle. Its SUP remained continuous (PID `11198`, `NRestarts=0`); the native
timesync journal recorded accepted synchronization at
`14:36:06.631962Z`, within 0.95 seconds of enable. The harness's immediate
active-state assertion failed during normal service activation, but its
preserved journal and final health evidence confirm the completed transition.
This is native-adapter plus SUP-continuity evidence, not scheduler integration
or fleet qualification.

Keep each later approved stability correction in a separate focused commit on
`main`. Update this section with its scope and validation, without moving the
recovery tag or changing the frozen experiment pin. Review the exact sequence:

```bash
git log --reverse --oneline recovery/phase-experiments-2026-09-20..main
git diff recovery/phase-experiments-2026-09-20..main -- decoys INSTALL_SUP.sh deployment_engine
```
