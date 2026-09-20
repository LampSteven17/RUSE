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
No post-freeze stability fixes have been made at this point.

Keep each later approved stability correction in a separate focused commit on
`main`. Update this section with its scope and validation, without moving the
recovery tag or changing the frozen experiment pin. Review the exact sequence:

```bash
git log --reverse --oneline recovery/phase-experiments-2026-09-20..main
git diff recovery/phase-experiments-2026-09-20..main -- decoys INSTALL_SUP.sh deployment_engine
```
