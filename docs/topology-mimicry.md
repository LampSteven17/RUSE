# Topology Mimicry — Making Sandbox SUPs Look Like Workstations

**Status.** Phase A (components 1 + 3) — implementing. Phase B (components 2 + 4) — deferred.

**Date.** 2026-04-17

## Problem

PHASE's ablation engine reports that on the `summer24` and `vt-fall22-*` target
models, behavioral knobs (volume, duration, service, proto, bytes) produce
|Δscore| < 0.10. Five non-behavioral features produce massive deltas:

| Feature       | max\|Δ\| | Direction    | Perturbation     |
|---------------|---------|--------------|------------------|
| `local_orig`  | 1.00    | flip to 0    | zero-ablation    |
| `id.orig_p`   | 1.00    | push up      | +0.5             |
| `id.resp_p`   | 0.75    | push up      | +0.5             |
| `local_resp`  | 0.62    | pull down    | -0.25            |
| `conn_state`  | 0.27    | push up      | +0.5             |

These are topology / TCP-stack features that SUPs cannot steer from their
`behavior.json`. PHASE is not detecting behavior on these models — it is
detecting the host's network environment. Sandboxed SUP VMs look different
from workstations, and the model learned that.

### Mechanistic hypothesis

Workstations sit inside noisy LANs: SMB polling, LDAP queries, WSUS check-ins,
IT-management pings, NTP broadcasts, mDNS/Bonjour, printer status polls, IPMI
/ iDRAC, WinRM, MDM agents, the occasional misrouted scan. This inbound
activity produces Zeek rows with:

- `local_orig=0` (initiator elsewhere on the LAN, not the workstation)
- ephemeral-range `id.resp_p` (responder = workstation's listening port)
- diverse `conn_state` distribution (S0, REJ, RSTO, OTH mixed with SF)

SUP sandbox VMs receive none of that. They sit behind NAT, no peer machine is
configured to probe them, and every Zeek row captures a SUP-originated client
connection: `local_orig=1`, `id.orig_p=ephemeral`, `id.resp_p ∈ {80,443,53}`,
`conn_state=SF` dominant.

## Solution — 4-component topology mimicry layer

Goal: per-hour feature distributions for `local_orig`, `id.orig_p`, `id.resp_p`,
`local_resp`, `conn_state` match workstation traffic, not sandbox-VM traffic.

### Component 1 — Inbound-traffic generator (Phase A)

**Where it lives.** Per-subnet sidecar VM co-provisioned with each RUSE deploy
(one `neighborhood` VM per deploy, not per SUP). This VM runs a Python daemon
that generates real TCP/UDP probes at each SUP IP on the subnet.

**Why sidecar, not per-SUP daemon.** Zeek attributes the `orig` side of a
connection to the initiator's IP. If the SUP probes itself (loopback), the
conn row has both `orig` and `resp` as the SUP IP, which the model will see
as synthetic. Real inbound traffic needs a real other host.

**Why not mlserv as the source.** mlserv is the OpenStack controller. Every
"inbound" probe originating from its IP would show up in Zeek logs with a
single peer that has a very distinctive traffic profile. Model would learn
"mlserv IP = probing sandbox." Sidecar per deploy spreads the source across
many neighborhood VMs matching the subnet.

**Deploy prefix.** `n-` (neighborhood) — parallels `r-` (RUSE SUPs),
`e-` (RAMPART), `g-` (GHOSTS).

**Probes generated.** Real TCP / UDP transactions; Zeek sees them natively:

| Probe type        | Proto | Port      | Cadence       | Produces          |
|-------------------|-------|-----------|---------------|-------------------|
| SMB poll          | TCP   | 445       | 2-4 / hr      | SF or RSTO        |
| LDAP bind         | TCP   | 389 / 88  | 1-3 / hr      | S0 or REJ         |
| WSUS check-in     | TCP   | 8530      | 0-1 / hr      | REJ / RSTO        |
| NTP broadcast     | UDP   | 123       | 10-60 / hr    | unidir SF         |
| Printer poll      | TCP   | 9100, 631 | 1-2 / hr      | S0 / RSTO         |
| IPMI / mgmt       | UDP   | 623       | 1-2 / hr      | unidir / S0       |
| WinRM / cockpit   | TCP   | 5985,9090 | 0-1 / hr      | RSTO              |
| mDNS / Bonjour    | UDP   | 5353 mc   | 10-30 / hr    | unidir            |
| SSDP              | UDP   | 1900 mc   | 5-15 / hr     | unidir            |
| Random scan       | TCP   | varies    | 3-10 / hr     | S0 / REJ dominant |

All rates are per SUP VM. The neighborhood VM consumes each SUP's
`behavior.json` → `diversity.topology_mimicry` and targets each SUP's IP
accordingly.

### Component 2 — Ephemeral-port diversity on SUPs (Phase B, deferred)

Install + enable a handful of listening services on each SUP VM so Zeek
captures source ports outside the kernel-default 32768-60999 ephemeral range
when those services phone out or respond to probes. Candidates:

- `sshd` on 22 (already present on every SUP)
- `node-exporter` on 9100
- `cockpit` on 9090
- `python3 -m http.server 8080`
- `grafana-agent` on 12345

Many will be dead ends behind NAT — their value is creating the port
distribution, not being functional services. Component 1's probes land on
these ports → bidirectional conn rows with service-port `id.orig_p` values.

### Component 3 — behavior.json schema extension (Phase A)

PHASE generator extends `diversity` with `topology_mimicry`:

```json
{
  "diversity": {
    "background_services": { ... },   // already exists
    "workflow_rotation": { ... },     // already exists
    "topology_mimicry": {             // NEW
      "inbound_smb_per_hour":          2,
      "inbound_ldap_per_hour":         1,
      "inbound_wsus_per_hour":         1,
      "inbound_ntp_receive_per_hour":  30,
      "inbound_printer_per_hour":      1,
      "inbound_ipmi_per_hour":         2,
      "inbound_winrm_per_hour":        1,
      "inbound_mdns_per_hour":         20,
      "inbound_ssdp_per_hour":         10,
      "inbound_scan_per_hour":         5
    }
  }
}
```

PHASE populates per-SUP rates by sampling workstation training-data
distributions. RUSE loads these via `BehavioralConfig.diversity_injection`
and the neighborhood VM consumes them via a synthesized master config
(see component 4 orchestration below).

### Component 4 — Subnet-local chatter (Phase B, deferred)

Adds lightweight chatter-agent on SUP VMs producing broadcast / multicast on
the subnet every few minutes:

- UDP multicast 224.0.0.251:5353 (mDNS)
- UDP multicast 239.255.255.250:1900 (SSDP)
- Subnet broadcast 255.255.255.255:137 (NetBIOS)

These create `local_orig=0` rows when observed by neighborhood VMs or peer
SUPs. Cheaper than component 1 but lower yield (multicast traffic is only
interesting if another host captures it — and Zeek's bzar monitor is
typically off-subnet).

## Deployment architecture (Phase A)

```
┌─────────────────────────────────────────────────────────────────┐
│ RUSE deploy (config: ruse-feedback-stdctrls-sum24-all)          │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │  r-…-M2-0    │  │r-…-B2-gemma-0│  │ … 3 more SUPs│          │
│  └───────┬──────┘  └───────┬──────┘  └───────┬──────┘          │
│          │ inbound         │ inbound         │ inbound         │
│          ▼                 ▼                 ▼                 │
│  ┌────────────────────────────────────────────────────┐        │
│  │  n-{deploy_hash}-neighborhood-0                    │        │
│  │  ├─ neighborhood_traffic.py (daemon)               │        │
│  │  │  reads /etc/ruse-neighborhood/sups.json         │        │
│  │  │  per-SUP inbound_*_per_hour → probe scheduler   │        │
│  │  │  sends real TCP/UDP at each SUP IP              │        │
│  │  └─ systemd: ruse-neighborhood.service             │        │
│  └────────────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────────┘
```

- **1 neighborhood VM per RUSE deploy** (`count: 1`, flavor `v1.2vcpu.4g`)
- **Name pattern** `n-{deploy_hash}-neighborhood-0`
- **OpenStack keypair** `bot-desktop` (same as RUSE SUPs)
- **Packages** `samba-common-bin ldap-utils ntpdate avahi-utils`
- **Config file on VM** `/etc/ruse-neighborhood/sups.json`:
  ```json
  {
    "sups": [
      {
        "name": "r-abc123-M2-0",
        "ip":   "10.246.117.104",
        "rates": { "inbound_smb_per_hour": 2, "inbound_ldap_per_hour": 1, ... }
      },
      ...
    ]
  }
  ```
- **Deploy flow** (additions to `spinup.py`):
  1. After SUP provisioning, parse each SUP's resolved `behavior.json` for
     `diversity.topology_mimicry`.
  2. Aggregate into a `sups.json` synthesized config.
  3. Provision 1 extra VM with prefix `n-{deploy_hash}-neighborhood-0` on
     the same flavor pool.
  4. Run `install-neighborhood.yaml` — installs packages, copies
     `sups.json`, deploys `neighborhood_traffic.py`, starts systemd service.
  5. Audit: new probe for `n-*` VMs checks `ruse-neighborhood.service`
     active + recent probe-log activity.

## Validation (Phase C, follow-on)

PHASE re-runs knob ablation against a topology-enriched deploy:

1. Target: each of the five topology features' max|Δ| drops below the 0.10
   ablation threshold. Once the model can't separate on topology, behavior
   features become the actual deciders.
2. Secondary: deployed SUP score on summer24 moves from ~0.0 toward 0.3-0.5
   (no behavior tuning; just neutralizing topology fingerprint).
3. Once topology is neutralized, PHASE's behavior feedback has real teeth.

A/B validation: one SUP before + after the topology layer, measuring
score delta on the summer24 model. Documented in `docs/topology-ablation.md`
(future).

## Scope boundaries

This is **not**:

- A replacement for PHASE's behavior feedback. It complements.
- A per-model solution. The same neighborhood layer runs for every deploy;
  PHASE drives which probe rates match which model's training data.
- A way to evade detection. It's an anti-artifact: stopping the model from
  cheating via environment fingerprints it wasn't supposed to learn.

## Code layout

```
src/
  common/
    network/
      neighborhood_traffic.py    # component 1 daemon (runs on sidecar VM)
      probes.py                  # per-protocol probe functions

deployments/
  playbooks/
    install-neighborhood.yaml    # Phase A installer
  cli/
    commands/
      spinup.py                  # Phase A: sidecar provisioning + config synth
```

## Open questions (not blocking Phase A)

- Should neighborhood VMs be shared across multiple RUSE deploys on the
  same OpenStack tenant? Cost says yes, experimental isolation says no.
  Default: per-deploy for now, revisit if capacity becomes an issue.
- How do we produce `local_resp=0` rows? (SUP initiates to external IP.)
  The existing workflow_weights path already produces these.
- Firewall / security-group handling: inbound TCP from neighborhood VM
  will hit SUP default-deny rules unless we add a subnet-local allow.
  Decision: add allow rule during install for neighborhood VM's IP only.
