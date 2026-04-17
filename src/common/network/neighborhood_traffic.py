"""Neighborhood-traffic sidecar daemon.

Runs on a per-deploy sidecar VM (prefix `n-`) that co-lives with RUSE SUPs.
Generates ambient inbound TCP/UDP probes toward each SUP so Zeek captures
workstation-like topology features (local_orig=0, diverse id.orig_p,
mixed conn_state) — neutralizing the sandbox-fingerprint that PHASE's
target models key on for summer24 and vt-fall22.

Design principles (strict):
  * STRICTLY DATA-DRIVEN. If /etc/ruse-neighborhood/sups.json has zero
    non-zero rates, the daemon emits zero probes. No default traffic.
    This makes CONTROL deploys safe: they can legally carry the same
    daemon code without generating any network-layer feedback signal.
  * Only feedback deploys populate sups.json with PHASE-emitted rates.
  * No hardcoded probing. Every probe is gated on a per-SUP, per-probe
    rate sourced from behavior.json -> diversity.topology_mimicry.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Permit running both from the deploy tree (`python -m common.network.neighborhood_traffic`)
# and as a standalone script on the sidecar VM.
try:
    from common.network.probes import PROBE_REGISTRY
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from common.network.probes import PROBE_REGISTRY


CONFIG_PATH_DEFAULT = Path("/etc/ruse-neighborhood/sups.json")
LOG_PATH_DEFAULT = Path("/var/log/ruse-neighborhood.jsonl")


# ─── Config model ──────────────────────────────────────────────────────────

@dataclass
class SUPTarget:
    name: str
    ip: str
    # Probe key (e.g. "inbound_smb_per_hour") -> rate (int, probes / hour)
    rates: dict[str, int]


def load_config(path: Path) -> list[SUPTarget]:
    """Load sups.json. Returns [] if file is missing — the daemon then
    sits idle, which is the expected state on control deploys that never
    shipped a populated config."""
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    targets = []
    for s in data.get("sups", []):
        if not s.get("ip"):
            continue
        rates = {k: int(v) for k, v in (s.get("rates") or {}).items()
                 if isinstance(v, (int, float)) and v > 0 and k in PROBE_REGISTRY}
        targets.append(SUPTarget(name=s.get("name", "?"), ip=s["ip"], rates=rates))
    return targets


def any_active_rates(targets: list[SUPTarget]) -> bool:
    return any(t.rates for t in targets)


# ─── Scheduler ─────────────────────────────────────────────────────────────

class ProbeScheduler:
    """Schedules per-SUP per-probe work.

    Strategy: every tick (60s), for each (SUP, probe_key, rate_per_hour)
    triplet, fire floor(rate/60) probes deterministically + one extra
    probe with probability (rate%60)/60. Random jitter inside the tick so
    Zeek doesn't see perfectly on-the-minute rows.
    """

    TICK_SECONDS = 60

    def __init__(self, targets: list[SUPTarget], logger: logging.Logger):
        self.targets = targets
        self.logger = logger
        self._stop = False
        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT, self._handle_stop)

    def _handle_stop(self, *_) -> None:
        self._stop = True

    def _execute_probe(self, target: SUPTarget, probe_key: str,
                       probe_fn: Callable[[str], tuple]) -> None:
        try:
            ok, state, elapsed_ms = probe_fn(target.ip)
        except Exception as e:
            self.logger.warning(json.dumps({
                "event": "probe_error",
                "sup": target.name, "ip": target.ip,
                "probe": probe_key, "error": repr(e)[:120],
            }))
            return
        self.logger.info(json.dumps({
            "event": "probe",
            "sup": target.name, "ip": target.ip,
            "probe": probe_key, "ok": ok,
            "conn_state_hint": state, "elapsed_ms": elapsed_ms,
        }))

    def _probes_this_tick(self, rate_per_hour: int) -> int:
        """Given N probes/hour, how many this 60s tick? Floor + stochastic
        remainder to preserve the hourly rate in expectation."""
        base = rate_per_hour // 60
        remainder = rate_per_hour % 60
        extra = 1 if random.random() < (remainder / 60.0) else 0
        return base + extra

    def tick(self) -> int:
        """Fire one tick's worth of probes. Returns count emitted."""
        count = 0
        for target in self.targets:
            for probe_key, rate in target.rates.items():
                probe_fn = PROBE_REGISTRY.get(probe_key)
                if probe_fn is None:
                    continue
                n = self._probes_this_tick(rate)
                for _ in range(n):
                    # Jitter inside the tick so probes don't bunch on 0s
                    time.sleep(random.uniform(0, self.TICK_SECONDS / max(1, n + 1)))
                    self._execute_probe(target, probe_key, probe_fn)
                    count += 1
                    if self._stop:
                        return count
        return count

    def run(self) -> None:
        """Main loop. Emits a heartbeat log line each tick so audit can
        distinguish a healthy-but-idle daemon from a crashed one."""
        self.logger.info(json.dumps({
            "event": "daemon_start",
            "sup_count": len(self.targets),
            "active_sup_count": sum(1 for t in self.targets if t.rates),
            "total_probes_per_hour": sum(
                sum(t.rates.values()) for t in self.targets
            ),
        }))
        while not self._stop:
            start = time.monotonic()
            emitted = self.tick()
            self.logger.info(json.dumps({
                "event": "tick", "emitted": emitted,
                "active_sups": sum(1 for t in self.targets if t.rates),
            }))
            # Sleep the remainder of the 60s window (tick may have eaten
            # some of it with jitter + probe latency).
            elapsed = time.monotonic() - start
            remaining = max(0, self.TICK_SECONDS - elapsed)
            # Split remaining sleep into short naps so SIGTERM is responsive
            end = time.monotonic() + remaining
            while not self._stop and time.monotonic() < end:
                time.sleep(min(1.0, end - time.monotonic()))
        self.logger.info(json.dumps({"event": "daemon_stop"}))


# ─── Entry point ───────────────────────────────────────────────────────────

def _build_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ruse-neighborhood")
    logger.setLevel(logging.INFO)
    # File handler (JSONL) — audit reads this
    fh = logging.FileHandler(log_path)
    fh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(fh)
    # Stderr handler — systemd journal gets it too
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(logging.Formatter("[%(asctime)s] %(message)s"))
    logger.addHandler(sh)
    return logger


def main() -> int:
    parser = argparse.ArgumentParser(description="RUSE neighborhood-traffic daemon")
    parser.add_argument("--config", default=str(CONFIG_PATH_DEFAULT),
                        help=f"Path to sups.json (default: {CONFIG_PATH_DEFAULT})")
    parser.add_argument("--log", default=str(LOG_PATH_DEFAULT),
                        help=f"Path to JSONL log (default: {LOG_PATH_DEFAULT})")
    parser.add_argument("--once", action="store_true",
                        help="Run one tick and exit (for testing)")
    args = parser.parse_args()

    logger = _build_logger(Path(args.log))

    config_path = Path(args.config)
    targets = load_config(config_path)

    if not targets:
        # Control-deploy mode OR config not yet delivered. Daemon stays
        # alive but emits no probes — keeps the audit-ble heartbeat going.
        logger.info(json.dumps({
            "event": "daemon_idle",
            "reason": f"no SUPs in {config_path} (control deploy or config missing)",
        }))
    elif not any_active_rates(targets):
        # Config present but every rate is zero. Same idle mode, different
        # reason — useful for distinguishing "no config" from "explicit zero".
        logger.info(json.dumps({
            "event": "daemon_idle",
            "reason": "all SUP rates are zero (PHASE has not enabled topology_mimicry)",
            "sup_count": len(targets),
        }))

    scheduler = ProbeScheduler(targets, logger)
    if args.once:
        n = scheduler.tick()
        logger.info(json.dumps({"event": "once_complete", "emitted": n}))
        return 0

    scheduler.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
