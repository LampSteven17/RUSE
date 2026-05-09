---
name: ghosts-audit
description: GHOSTS audit — `./audit --ghosts`. STUB only at the moment. Routes to `deployment_engine/ghosts/audit.py::run_ghosts_audit` which prints a TODO list and returns rc=1. GHOSTS check semantics differ from DECOY (Docker stack on api-0, .NET ghosts-client systemd state with NRestarts as memcap-OOM signal, /api/machines registration count, RSS trend) so the DECOY probe doesn't transfer. Skill exists so the dispatch shape is symmetric with /decoy-audit and /rampart-audit; see /ghosts-deploy for what an implementation would need to probe.
---

# ghosts-audit

`./audit --ghosts` — currently a placeholder. Returns rc=1 with a TODO
list of what a real implementation would check.

| | |
|---|---|
| Entry point | `./audit --ghosts` at RUSE root |
| Code | `deployment_engine/ghosts/audit.py::run_ghosts_audit` |
| Status | **stub only** — exits 1 with `GHOSTS audit not yet implemented` |

## Why no implementation yet

DECOY's audit probes (Ollama / GPU / brain process / behavior.json) don't
apply to GHOSTS. GHOSTS-specific concerns:

- API VM Docker stack health (ghosts-api / postgres / frontend / n8n /
  grafana containers all up + healthy)
- NPC `ghosts-client.service` systemd state
- NRestarts as a memcap-OOM signal (cgroup memcap drop-in for feedback
  deploys means OOM-kill-and-respawn cycle is HEALTHY for these VMs;
  threshold tuning differs from DECOY's "10 restarts = crash-loop")
- .NET RSS trend (memcap working but trending toward repeated OOM is a
  signal vs. stable RSS within cap)
- Machines registered via SignalR: `curl localhost:5000/api/machines`
  on api-0 should return one entry per NPC client
- per-NPC `_phase_metadata.mode ∈ {feedback, controls}` — same FATAL
  gate concept as DECOY's window-mode contract, but on the deployed
  `timeline.json` files at `/opt/ghosts-client/config/timeline.json`

## What the implementation would check (TODO)

Per-VM probe (id_ed25519 / bot-desktop):

1. SSH reachable
2. **API VM (`g-{hash}-api-0`)**:
   - Docker stack status: `docker compose ps` for ghosts-api +
     postgres + frontend + n8n + grafana, all `Up (healthy)`
   - API healthcheck: `curl -sf localhost:5000/api/machines | jq length`
     should match the deploy's `client_count`
   - Check Docker Hub rate-limit pulls in past hour (the
     `Detect Docker Hub rate-limit` failure pattern from
     `install-ghosts-api.yaml` should not be re-occurring)
3. **NPC client VMs (`g-{hash}-npc-N`)**:
   - `systemctl is-active ghosts-client`
   - `systemctl show ghosts-client -p NRestarts` — high values are
     EXPECTED on feedback (cgroup memcap → OOM → respawn cycle every
     ~2h). Threshold for FAIL: ?
   - .NET RSS trend: `systemctl show ghosts-client -p MemoryCurrent` —
     compare against drop-in `MemoryMax=20G` cap
   - Timeline parity: SHA256 of
     `/opt/ghosts-client/config/timeline.json` should match the
     `run_dir/timelines/g-{hash}-npc-N.json` that PHASE wrote
   - Per-NPC mode contract: parse the timeline.json's `_phase_metadata.mode`,
     FATAL on anything other than `{feedback, controls}` — same shape
     as DECOY's mode FATAL gate
4. **Cross-NPC**: api-0's `/api/machines` count == number of healthy NPC clients

Cross-deployment:

- Orphan / missing diff: `g-{hash}-` prefix on OpenStack vs.
  inventory's `[ghosts_clients]` group
- PHASE registration in `experiments.json`
- Memcap drop-in present on every feedback NPC, absent on every
  controls NPC (per the per-mode rule in `/ghosts-deploy`)
- Docker volumes not orphaned — `docker volume ls` on api-0 shouldn't
  have unattached volumes from prior runs

## NRestarts threshold note

Pre-memcap, NPCs went SSH-fail entirely after ~2h on feedback because
the .NET memleak OOM-killed sshd. Post-memcap (drop-in writes
`MemoryMax=20G + MemorySwapMax=0`), the kernel kills the leaky process
inside its cgroup and `Restart=always` respawns within 10s. So:

- Controls: `NRestarts == 0` is healthy, `> 0` is suspicious
- Feedback: `NRestarts in [0, ~50]` is healthy (one cycle every ~2h
  over a 24h deploy = ~12 cycles); much higher means cap is too low

This bimodal threshold means the audit must dispatch on
`is_feedback = "ghosts-feedback-" in deployment_name`.

## Until then

To eyeball GHOSTS health, see the manual recipes in `/ghosts-deploy`:

```bash
ssh g-14a6d-api-0 "curl -s localhost:5000/api/machines | jq length"
ssh g-14a6d-npc-0 "systemctl status ghosts-client"
ssh g-14a6d-npc-0 "journalctl -u ghosts-client --no-pager -n 100"
```

## Related

- Deploy lifecycle: `/ghosts-deploy`
- DECOY audit (real implementation): `/decoy-audit`
- RAMPART audit (also stub): `/rampart-audit`
