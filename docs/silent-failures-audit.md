# Silent Failure Audit — RUSE Deployment System

**Date:** 2026-04-14
**Discovered:** 161 RAMPART Windows endpoints never deployed pyhuman across 7 "successful" deploys. Root cause: `_safe_parallel_call` in `post-deploy.py:22-31` swallows every exception per-VM, logs a WARNING, and continues. Deploys complete with "success" despite 100% Windows failure rate.

**Principle:** Deploy reporting "DONE" MUST mean every VM actually works. Silent warnings that hide full-category failures are unacceptable.

## CRITICAL (5) — Deploy Reports Success But VMs Not Functional

### C1. Windows emulation deploy swallows all SSH errors
**File:** `deployments/cli/commands/rampart.py:678-744`
**What fails:** 4 subprocess.run SSH calls (write passfile, write run-emulation.ps1, register RampartHuman task, start task). All use `capture_output=True` with no returncode check, wrapped in broad `except Exception: return False`.
**Hidden failures:** Auth failed, connection refused, PowerShell policy block, task registration denied, password wrong.
**Deploy output:** Prints `OK {vm} (Windows)` even when nothing happened.
**Fix:** Check `result.returncode` after each call, raise `RuntimeError(f"step {N} failed: {stderr}")`.

### C2. Windows emulation batch has no failure threshold
**File:** `deployments/cli/commands/rampart.py:738-745`
**What fails:** `ok, total = _deploy_windows_emulation(...)` returns `(5, 161)` silently. Caller logs "5/161 succeeded" but doesn't abort.
**Fix:** Abort deploy if `ok / total < 0.9` (configurable). Currently the threshold is effectively 0%.

### C3. GHOSTS API health check ignores failures
**File:** `deployments/playbooks/install-ghosts-api.yaml:90-104`
**What fails:** `uri:` task has `ignore_errors: yes`. If API never starts (docker pull fail, port conflict, DB migration fail), task passes.
**Deploy output:** "Report API status" prints "NOT RESPONDING (may still be starting)" as INFO and continues.
**Fix:** Remove `ignore_errors: yes`. Add explicit `fail:` task when `api_health.status not in [200, 302]`.

### C4. GHOSTS client systemctl check ignores result
**File:** `deployments/playbooks/install-ghosts-clients.yaml:292-296`
**What fails:** `systemctl is-active ghosts-client` runs with `ignore_errors: yes` and no assertion on `client_status.stdout`. If service is `inactive` or `failed`, task still passes.
**Fix:** Add assertion task that fails if `stdout != "active"`.

### C5. RAMPART Linux emulation systemctl check ignores result
**File:** `deployments/playbooks/install-rampart-emulation.yaml:63-67`
**Same pattern as C4.** Service could be in 2000+ restart loop (exactly what happened today with D5 arg mismatch) and check still passes.
**Fix:** Assert `stdout == "active"` after check.

## HIGH (4) — Features Silently Disabled

### H1. `post-deploy.py::_safe_parallel_call` swallows every failure per-node
**File:** `uva-cs-workflow/post-deploy.py:22-31`
**Pattern:** Wraps `deploy_human`, `deploy_domain_controllers`, `join_domains`, etc. Every exception per-VM returns `{"error": ...}` and the parallel batch continues.
**Consequence:** The `deploy_human failed for e-bcefa-winep3: Authentication failed` line × 161 didn't abort deploy. Each was a per-VM warning with no aggregated check at end of step.
**Fix:** After parallel batch, count `{"error": ...}` entries. If > 10% OR any error type appears for > 50% of nodes of same role, abort with a summary: `RuntimeError("deploy_human failed on 19/19 Windows endpoints: Authentication failed (likely bare-domain auth bug)")`.

### H2. distribute-behavior-configs silently skips missing sources
**File:** `deployments/playbooks/distribute-behavior-configs.yaml:72-79`
**What fails:** `stat` checks if source dir exists, then `when: source_dir.stat.exists` gates the copy block. If source dir is missing (wrong PHASE path, typo, regen didn't happen), the playbook logs nothing and continues.
**Consequence:** Feedback deploy silently degrades to baseline with no warning.
**Fix:** Add `fail:` task when `not source_dir.stat.exists` with message pointing at expected path.

### H3. provision-vms.yaml has `failed_when: false` on ACTIVE wait
**File:** `deployments/playbooks/provision-vms.yaml:109`
**What fails:** If VMs stuck in BUILD or ERROR state after 60 retries × 5s = 5 min, `failed_when: false` lets task pass. Downstream "successful VMs" detection filters out failures but never aborts even if 0 VMs provisioned.
**Fix:** Already partially handled via `failed_vms`/`successful_vms` detection. Should additionally fail if `successful_vms | length < vm_list | length * 0.9`.

### H4. RAMPART `/opt/pyhuman/human.py` from workflows.zip doesn't fail on unknown args
**Discovered today.** Upstream RAMPART pyhuman crashes with `error: unrecognized arguments: --clustersize-sigma 0` → systemd restart loop → 2185 restarts per endpoint over 12 hours. **Audit didn't catch this** because "journal activity in last 5 min" was true (crash messages count as activity).
**Fix:** Audit should also check `systemctl show -p NRestarts` and fail if > 10.

## MEDIUM (3) — Diagnostic Data Lost

### M1. PHASE registration silently fails
**File:** `deployments/cli/commands/rampart.py:434-444`
**Pattern:** `except Exception: pass  # Non-critical`.
**Consequence:** Deploy not tracked in `experiments.json`, PHASE analysis can't correlate logs.
**Fix:** Log warning with the error, still don't abort.

### M2. SSH config generation silently fails
**File:** `deployments/cli/commands/rampart.py:373-381`
**Consequence:** No `~/.ssh/config` block installed for this deploy. Operator can't `ssh name` — must look up IP manually.
**Fix:** Log warning with error, don't abort.

### M3. `list_cmd.py` silently drops broken configs
**File:** `deployments/cli/commands/list_cmd.py:41-44`
**Consequence:** Broken config.yaml makes deployment invisible to `./list`. Operator confused why deploy doesn't exist.
**Fix:** Log warning naming the file and the parse error.

## LOW (3) — Intentional Resilience

### L1. Orphan volume cleanup `ignore_errors`
**File:** `deployments/playbooks/teardown-all.yaml:140-145`
Volumes may be deleted out-of-band. Retry-safe. **Keep as-is.**

### L2. `audit.py` silently drops broken configs
**File:** `deployments/cli/commands/audit.py:308-311`
Same as M3 but in audit. Should warn but not crash. **Add warning, keep resilience.**

### L3. `dns_zone.txt` written without verifying zone exists
**File:** `deployments/cli/commands/rampart.py:70-72`
Harmless — teardown gracefully handles missing zones. **Keep as-is.**

## Rollout Plan

### Phase 1 (TODAY) — Stop the bleeding
- [x] Fix `role_human.py` FQDN auth (**DONE**)
- [x] Fix `role_domains.py:348` FQDN credential in join-domain PowerShell (**DONE**)
- [ ] **C1**: Add returncode checks to all 4 SSH calls in `_deploy_windows_emulation`
- [ ] **C2**: Add failure threshold abort (>10% failures = raise)
- [ ] **H1**: Add aggregate failure detection to `_safe_parallel_call` callers — count error dicts, abort step if > 10% fail

### Phase 2 (This week) — Playbook assertions
- [ ] **C3**: Remove `ignore_errors` from GHOSTS API health, add `fail:` task
- [ ] **C4, C5**: Add `assert` tasks after `systemctl is-active` checks
- [ ] **H2**: Add `fail:` when behavior config source dir missing
- [ ] **H3**: Add `fail:` when `<90%` VMs reach ACTIVE

### Phase 3 (Next week) — Audit completeness
- [ ] **H4**: Audit checks `NRestarts > 10` and flags as failure
- [ ] **M1, M2**: Convert silent `pass` to explicit warning logging
- [ ] **M3, L2**: Convert config skip to warning
- [ ] Post-deploy verification step: SSH to every VM, verify expected service active + NRestarts < 5 + log activity. Fail deploy if any VM doesn't pass.

### Phase 4 (Long-term) — Architectural
- [ ] Define a "deployment contract": for each VM type, list required post-deploy invariants (service X active, file Y exists, log Z fresh). Deploy fails if contract not met on any VM.
- [ ] Migrate `_safe_parallel_call` to a pattern that collects per-VM results, then evaluates against role-aware failure thresholds (e.g., "0% of endpoints can fail" vs. "50% of background workers can fail").
- [ ] Add deploy-time smoke test: after all install steps, run a 60s verification pass that:
  - Reads deployment contract
  - SSHs to every VM
  - Confirms invariants
  - Aborts with a list of non-compliant VMs

## Principles Going Forward

1. **No broad `except Exception`** without logging the actual error.
2. **No `ignore_errors: yes`** on anything that verifies state or infrastructure readiness.
3. **No aggregate metric** (e.g., "5 VMs succeeded") without a pass/fail threshold.
4. **Deploys emit a success contract**. Deploy completes → every VM in expected state. If not, deploy aborts and rolls back (or at minimum blocks registration as complete).
5. **Audit verifies the contract on a live system**. Current audit only checks process-level health — add infrastructure-level contract checks.
