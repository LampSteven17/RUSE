"""Closed-loop connection-shape controller (Phase 1, 2026-06-16).

PHASE's exp model scores a SUP-day near-binary (pass ~0.999 / dead ~0.00). The
deciding factor is per-connection BYTE/PACKET/DURATION shape + the conn_state
mix — NOT activity volume (a confirmed-dead lever). The runtime already emits a
passing shape on some days and a dead one on others; the failure mode is
day-to-day VARIANCE. This controller specifies the target distribution and
drives the runtime to hit it CONSISTENTLY.

It is the actuator + feedback loop for two PHASE-emitted behavior.json blocks:

  diversity.connection_shape   per-connection target percentile distributions
                               {enabled, orig_bytes/{p25,p50,p75,p90,max},
                                resp_bytes, orig_pkts, resp_pkts, duration}
  diversity.conn_state_mix     {SF, failed_conn, OTH, RSTR} target fractions
                               (FOLD 2026-06-16: REJ+S0 collapsed into
                                failed_conn; SF is the uncontrolled baseline,
                                emitted for reference only)

Phase 1 scope — what this controller actuates TODAY:
  • orig_bytes / duration  → per-connection sampling from the distribution
    (not the p50 scalar) on the persistent-session channel, with a bounded
    multiplicative bias closed-loop-corrected from the EMIT-SIDE ledger.
  • failed_conn fraction   → a per-minute failed-conn probe rate handed to
    scripted_services, computed as failed_conn_frac × the per-minute aggregate
    outbound conn count (OutboundConnSampler — a valid use of /proc: a COUNT,
    not a per-connection distribution).

Deliberately NOT actuated yet (await later backlog builds, see RUSE spec):
  • orig_pkts            → packetization build (#3)
  • resp_bytes/resp_pkts → RUSE-controlled response endpoints (#2)
  • the shape-floor channel (#5) will register as a second emit-side reporter.

Measurement source (RUSE spec §B.1 correction): conn_sampler CANNOT read a
per-connection byte/pkt/duration distribution or conn_state from /proc — those
come from this controller's EMIT-SIDE ledger (channels report each connection
they close). conn_sampler is used only for the aggregate conn COUNT that scales
the failed_conn rate.

Correctness:
  • `max` is a HARD per-connection ceiling — every sample (post-bias) is clamped
    to it (RUSE spec invariant; the dataset MAX is unphysical as a target).
  • Private RNG — sample_*() is called from the persistent-session daemon's OWN
    thread; drawing from global random.* there would corrupt the seed-derived
    AgentLogger.session_id (same rule as persistent_session.py).
  • Additive / absent-safe — a missing block leaves the feature OFF; an
    `enabled:true` block with a malformed distribution WARNS loud (audit-caught)
    and falls back to the daemon's scalar, never crashing the fleet (fail-loud is
    reserved for the load-time mode/missing-file contract, not producer typos).
"""
from __future__ import annotations

import random
import threading
from datetime import datetime, timezone
from typing import Optional

try:
    # Aggregate per-minute outbound conn COUNT — scales the failed_conn rate.
    # Guarded so a telemetry import failure never breaks the SUP.
    from common.network.conn_sampler import OutboundConnSampler
except Exception:  # pragma: no cover - defensive
    OutboundConnSampler = None


# Percentile knots: cumulative-probability → dict key. Below p25 we hold p25
# (no p0/min is emitted); at/above 1.0 we return max.
_KNOTS = ((0.25, "p25"), (0.50, "p50"), (0.75, "p75"), (0.90, "p90"), (1.0, "max"))
_REQUIRED_KEYS = ("p25", "p50", "p75", "p90", "max")

# Bias bounds + damping. The bias is a multiplicative correction applied to every
# sample so the EMITTED median converges on the target even when a channel's
# mechanics (e.g. the persistent session's 4 KB/request cap, or early server
# closes) systematically under/over-shoot. Bounded so a saturating channel can't
# wind it to infinity; damped so it eases toward target rather than oscillating.
_BIAS_MIN = 0.5
_BIAS_MAX = 4.0
_BIAS_DAMP = 0.3  # fraction of the log-ratio applied per minute

_LOG_PREFIX = "[shape]"


def _valid_dist(d) -> bool:
    """True if d is a usable percentile dict: all keys present, numeric,
    monotonic non-decreasing, max >= p90 (PHASE clamps on emit, so this should
    always hold — we assert it cheaply rather than trust it)."""
    if not isinstance(d, dict):
        return False
    try:
        vals = [float(d[k]) for k in _REQUIRED_KEYS]
    except (KeyError, TypeError, ValueError):
        return False
    if any(v < 0 for v in vals):
        return False
    return all(vals[i] <= vals[i + 1] for i in range(len(vals) - 1))


def _interp(dist: dict, u: float) -> float:
    """Piecewise-linear inverse-CDF sample of a percentile dict at quantile u."""
    if u <= _KNOTS[0][0]:
        return float(dist[_KNOTS[0][1]])
    prev_p, prev_k = _KNOTS[0]
    for p, k in _KNOTS[1:]:
        if u <= p:
            lo, hi = float(dist[prev_k]), float(dist[k])
            frac = (u - prev_p) / (p - prev_p) if p > prev_p else 0.0
            return lo + frac * (hi - lo)
        prev_p, prev_k = p, k
    return float(dist["max"])


class ShapeController:
    """Per-connection shape sampler + per-minute closed-loop corrector."""

    def __init__(self, shape_cfg: Optional[dict], csm_cfg: Optional[dict],
                 logger=None, seed: int = 0):
        self._logger = logger
        # Private RNG (off-thread caller — see module docstring).
        self._rng = random.Random((seed or 0) ^ 0x53484150)  # "SHAP"
        self._lock = threading.Lock()

        # Targets (set by update_config).
        self._bytes_dist: Optional[dict] = None
        self._dur_dist: Optional[dict] = None
        self._failed_conn_frac: float = 0.0

        # Closed-loop state.
        self._bytes_bias = 1.0
        self._dur_bias = 1.0
        # Emit-side ledger for the current minute (channels append on close).
        self._obs_bytes: list = []
        self._obs_dur: list = []
        # Per-minute failed-conn probe rate handed to scripted_services.
        self._failed_conn_rate = 0.0

        self._minute_stamp = self._utc_minute()
        try:
            self._conn_sampler = OutboundConnSampler() if OutboundConnSampler else None
        except Exception:
            self._conn_sampler = None

        self.update_config(shape_cfg, csm_cfg)

    # ── Config ────────────────────────────────────────────────────────

    def update_config(self, shape_cfg: Optional[dict],
                      csm_cfg: Optional[dict]) -> None:
        """Hot-reload. Validates each distribution; an enabled-but-malformed
        block warns loud and disables that feature (daemon falls back to scalar)
        rather than crashing."""
        with self._lock:
            self._bytes_dist = None
            self._dur_dist = None
            if shape_cfg and shape_cfg.get("enabled"):
                ob = shape_cfg.get("orig_bytes")
                du = shape_cfg.get("duration")
                if ob is not None:
                    if _valid_dist(ob):
                        self._bytes_dist = {k: float(ob[k]) for k in _REQUIRED_KEYS}
                    else:
                        self._warn("connection_shape.enabled but orig_bytes "
                                   "distribution is malformed — falling back to "
                                   "scalar orig_bytes_per_session")
                if du is not None:
                    if _valid_dist(du):
                        self._dur_dist = {k: float(du[k]) for k in _REQUIRED_KEYS}
                    else:
                        self._warn("connection_shape.enabled but duration "
                                   "distribution is malformed — falling back to "
                                   "scalar session_duration_seconds")

            # conn_state_mix.failed_conn — clamp to [0,1]; ignore SF/OTH/RSTR for
            # now (SF uncontrolled baseline; REJ/RSTR await the responder build).
            frac = 0.0
            if isinstance(csm_cfg, dict):
                try:
                    frac = float(csm_cfg.get("failed_conn", 0.0) or 0.0)
                except (TypeError, ValueError):
                    frac = 0.0
            self._failed_conn_frac = min(max(frac, 0.0), 1.0)

    # ── Per-connection sampling (called from channel threads) ──────────

    def sample_orig_bytes(self) -> Optional[int]:
        """Per-connection orig_bytes target, or None if not shaping bytes (caller
        falls back to its scalar). Clamped to the distribution max (hard ceiling)
        after bias."""
        with self._lock:
            if self._bytes_dist is None:
                return None
            val = _interp(self._bytes_dist, self._rng.random()) * self._bytes_bias
            return int(min(max(val, 0.0), self._bytes_dist["max"]))

    def sample_duration(self) -> Optional[float]:
        """Per-connection duration target (seconds), or None if not shaping
        duration. Clamped to max after bias; the daemon owns the lognormal spread
        and the block-end cap around this center."""
        with self._lock:
            if self._dur_dist is None:
                return None
            val = _interp(self._dur_dist, self._rng.random()) * self._dur_bias
            return float(min(max(val, 0.0), self._dur_dist["max"]))

    # ── Emit-side ledger (called from channel threads on conn close) ───

    def observe_connection(self, channel: str, orig_bytes: Optional[int],
                           duration_s: Optional[float], conn_state: str) -> None:
        """A shaped channel reports one closed connection. Append to the current
        minute's ledger; never raises."""
        try:
            with self._lock:
                if orig_bytes is not None and orig_bytes >= 0:
                    self._obs_bytes.append(float(orig_bytes))
                if duration_s is not None and duration_s >= 0:
                    self._obs_dur.append(float(duration_s))
        except Exception:
            pass

    # ── Read by scripted_services ──────────────────────────────────────

    def failed_conn_rate_per_min(self) -> float:
        """Desired failed-conn probes this minute (failed_conn_frac × aggregate
        outbound conn count). 0 when conn_state_mix shipped no failed_conn."""
        with self._lock:
            return self._failed_conn_rate

    def has_failed_conn_target(self) -> bool:
        """True when conn_state_mix shipped a non-zero failed_conn fraction — the
        signal for scripted_services to hand failed_conn to the rate actuator
        instead of its fixed cron slot."""
        with self._lock:
            return self._failed_conn_frac > 0

    # ── Per-minute closed-loop tick ────────────────────────────────────

    def maybe_tick(self) -> None:
        """Minute-roll guard. Idempotent across callers (daemon thread + main
        loop both call). On a UTC-minute roll: correct the bias from the just-
        elapsed minute's emit-side ledger, recompute the failed_conn rate from
        the aggregate conn count, then reset the ledger."""
        now = self._utc_minute()
        with self._lock:
            if now == self._minute_stamp:
                return
            self._minute_stamp = now

            self._bytes_bias = self._corrected_bias(
                self._bytes_bias, self._obs_bytes, self._bytes_dist)
            self._dur_bias = self._corrected_bias(
                self._dur_bias, self._obs_dur, self._dur_dist)

            active_opens = None
            if self._conn_sampler is not None:
                try:
                    active_opens = self._conn_sampler.sample().get("active_opens")
                except Exception:
                    active_opens = None
            if self._failed_conn_frac > 0 and active_opens:
                self._failed_conn_rate = self._failed_conn_frac * float(active_opens)
            else:
                self._failed_conn_rate = 0.0

            self._log_locked(active_opens)
            self._obs_bytes = []
            self._obs_dur = []

    def _corrected_bias(self, bias: float, obs: list,
                        dist: Optional[dict]) -> float:
        """Damped, bounded multiplicative correction toward the target p50 using
        the median of the just-emitted (post-bias) values. No observations or no
        target → hold the bias."""
        if dist is None or not obs:
            return bias
        measured = self._median(obs)
        target = dist["p50"]
        if measured <= 0 or target <= 0:
            return bias
        ratio = target / measured
        # Damped step in log-space: ratio ** _BIAS_DAMP eases toward target.
        new_bias = bias * (ratio ** _BIAS_DAMP)
        return min(max(new_bias, _BIAS_MIN), _BIAS_MAX)

    @staticmethod
    def _median(xs: list) -> float:
        s = sorted(xs)
        n = len(s)
        if n == 0:
            return 0.0
        mid = n // 2
        return s[mid] if n % 2 else 0.5 * (s[mid - 1] + s[mid])

    # ── Helpers ────────────────────────────────────────────────────────

    def _log_locked(self, active_opens) -> None:
        b_med = self._median(self._obs_bytes) if self._obs_bytes else None
        d_med = self._median(self._obs_dur) if self._obs_dur else None
        b_tgt = self._bytes_dist["p50"] if self._bytes_dist else None
        d_tgt = self._dur_dist["p50"] if self._dur_dist else None
        msg = (f"{_LOG_PREFIX} "
               f"bytes_med={self._fmt(b_med)}/{self._fmt(b_tgt)} "
               f"bias={self._bytes_bias:.2f} "
               f"dur_med={self._fmt(d_med)}/{self._fmt(d_tgt)} "
               f"bias={self._dur_bias:.2f} "
               f"failed_conn_rate={self._failed_conn_rate:.2f} "
               f"active_opens={active_opens if active_opens is not None else '-'} "
               f"n_obs={len(self._obs_bytes)}")
        print(msg, flush=True)
        if self._logger is not None:
            try:
                self._logger.info(msg)
            except Exception:
                pass

    def _warn(self, msg: str) -> None:
        line = f"[WARNING] {_LOG_PREFIX} {msg}"
        print(line, flush=True)
        if self._logger is not None:
            try:
                self._logger.warning(line)
            except Exception:
                pass

    @staticmethod
    def _fmt(v) -> str:
        return "-" if v is None else f"{v:.0f}"

    @staticmethod
    def _utc_minute():
        now = datetime.now(timezone.utc)
        return (now.hour, now.minute)
