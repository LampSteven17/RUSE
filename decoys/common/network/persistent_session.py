"""Persistent-session daemon — long-lived TCP-TLS sessions for ssl/duration/orig_bytes.

PHASE measured that SUPs produce almost no long-lived TLS: humans hold ~73% of
active minutes ssl-dominant with long durations, completed TCP handshakes, and real
upload bytes; SUPs fire short foreground bursts then go silent except DNS/NTP. This
daemon closes that hole by holding TCP-TLS sessions open during the workday and
opening NEW sessions spread across active minutes — each open is one completed `ssl`
conn row crediting its start minute.

Distinct from background_services.py (rate-based DNS/HTTP/NTP volume, fires INLINE
from the main loop) and scripted_services.py (cron-style protocol probes, also
inline): this runs in its OWN thread so it can open sessions and send keepalives
during the loop's inter-window sleeps. It is NOT a content.workflow_weights workflow
— it never occupies the sequential workflow slot.

PHASE schema (diversity.persistent_sessions):
  enabled                     bool
  session_opens_per_hour      [24 ints, UTC]  new ssl opens/hour, spread across the
                                              hour; the NON-ZERO hours double as the
                                              active-hours envelope (day/night gate)
  keepalive_interval_seconds  int   PHASE's UPPER bound (stay under Zeek's ~300s TCP
                                    inactivity timeout); RUSE clamps the actual send
                                    cadence below common server keep-alive timeouts
  session_duration_seconds    int   target typical session length; RUSE owns the
                                    lognormal spread
  orig_bytes_per_session      int   target upload bytes/session, dribbled across
                                    keepalives via request-header padding
  endpoint_pool               [str] live external https sites

Correctness invariants (see CLAUDE.md / decoy-deploy skill):
  - start-minute binning  → OPENS spread across minutes, not held concurrency
  - DNS tie-break         → resolve each host ONCE, connect by cached IP (SNI kept),
                            zero steady-state dns (else dns starts >= ssl and dns
                            wins the per-minute MODE tie, "dns" < "ssl")
  - circular envelope     → student bands wrap midnight (e.g. 21->04)
  - lifetime cap          → min(sampled_duration, time-to-active-block-end): close
                            gracefully (FIN -> Zeek conn_state=SF) at the workday
                            boundary, never bleed into the overnight zeros
  - TCP-TLS only          → no QUIC/wss (forfeits the SF + handshake-history win)
"""
from __future__ import annotations

import base64
import random
import socket
import ssl
import threading
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse


# Actual send cadence is clamped below the configured keepalive_interval_seconds:
# ordinary websites close idle keep-alive sockets in 5-60s, so sending more often
# than PHASE's (Zeek-timeout-driven) upper bound keeps the single long flow alive
# without violating the contract.
_SEND_INTERVAL_CEILING_S = 10.0
_SEND_INTERVAL_FLOOR_S = 2.0
# Defensive: bound fds well under the 1024 default if PHASE misconfigures opens.
_DEFAULT_MAX_CONCURRENT = 50
# Per-request total size cap — stay under the common 8KB nginx/Apache header limit
# so padding never trips a 431.
_MAX_REQUEST_BYTES = 4096
# Bounded response drain so a large body can't stall the manager thread.
_DRAIN_CAP_BYTES = 32 * 1024
_SOCK_TIMEOUT_S = 5.0
# A session that has sent this many requests recycles (some servers cap requests
# per keep-alive connection); treated as a benign server-side close.
_MAX_REQUESTS_PER_SESSION = 500

_TLS_PORT = 443
_LOG_PREFIX = "[psess]"


class _Session:
    """One held TLS socket and its lifecycle bookkeeping (monotonic clock)."""

    __slots__ = ("sock", "host", "ip", "open_mono", "end_mono",
                 "next_keepalive_mono", "bytes_cum", "bytes_target",
                 "n_requests")

    def __init__(self, sock, host, ip, open_mono, end_mono,
                 first_keepalive_mono, bytes_target):
        self.sock = sock
        self.host = host
        self.ip = ip
        self.open_mono = open_mono
        self.end_mono = end_mono
        self.next_keepalive_mono = first_keepalive_mono
        self.bytes_cum = 0
        self.bytes_target = bytes_target
        self.n_requests = 0


class PersistentSessionDaemon:
    """Background thread holding long-lived TCP-TLS sessions during the workday."""

    def __init__(self, config: Optional[dict] = None, logger=None, seed: int = 0):
        self._logger = logger
        # Private RNG — the daemon runs OFF the main thread, and run() seeds the
        # GLOBAL random (AgentLogger.session_id derives from it; audit validates
        # Random(seed).getrandbits(32)). Drawing from global random.* here would
        # non-deterministically corrupt that derivation. background/scripted
        # services get away with global random only because they're inline on the
        # main thread.
        self._rng = random.Random((seed or 0) ^ 0x50534553)  # "PSES"

        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Resolve-once IP cache (the zero-dns invariant). host -> ip.
        self._ip_cache: dict = {}

        # Config-derived state (filled by update_config).
        self._enabled = False
        self._opens_per_hour = [0] * 24
        self._send_interval = _SEND_INTERVAL_CEILING_S
        self._duration_target = 120.0
        self._bytes_target = 2000
        self._endpoints: list = []          # list of (host, url)
        self._max_concurrent = _DEFAULT_MAX_CONCURRENT

        # Per-minute open accounting for the D4 net-out.
        self._opens_this_minute = 0
        self._minute_stamp = self._utc_minute_key()

        # Per-hour minute schedule: counts[m] opens to fire in minute m of the
        # current hour; rerolled on hour change.
        self._hour_stamp = -1
        self._minute_counts = [0] * 60
        # Pending open times (monotonic) scheduled within the current minute.
        self._pending_opens: list = []
        self._pending_minute = -1

        self._sessions: list = []

        self.update_config(config or {}, seed=seed)

    # ── Public API ────────────────────────────────────────────────────

    def update_config(self, config: dict, seed: int = 0) -> None:
        """Hot-reload. Diffs endpoint_pool and re-resolves ONLY new hosts so the
        IP cache (and the zero-dns invariant) survives every cluster-boundary
        reload."""
        config = config or {}
        self._enabled = bool(config.get("enabled"))

        oph = config.get("session_opens_per_hour") or [0] * 24
        # Coerce to a 24-int array defensively.
        self._opens_per_hour = [int(oph[i]) if i < len(oph) else 0
                                for i in range(24)]

        ka = config.get("keepalive_interval_seconds")
        try:
            ka = float(ka)
        except (TypeError, ValueError):
            ka = _SEND_INTERVAL_CEILING_S
        self._send_interval = max(_SEND_INTERVAL_FLOOR_S,
                                  min(ka, _SEND_INTERVAL_CEILING_S))

        try:
            self._duration_target = max(float(config.get("session_duration_seconds", 120)), 5.0)
        except (TypeError, ValueError):
            self._duration_target = 120.0
        try:
            self._bytes_target = max(int(config.get("orig_bytes_per_session", 2000)), 0)
        except (TypeError, ValueError):
            self._bytes_target = 2000

        self._max_concurrent = int(config.get("max_concurrent", _DEFAULT_MAX_CONCURRENT))

        # Endpoint pool — parse host once; keep the cache for unchanged hosts.
        pool = config.get("endpoint_pool") or []
        endpoints = []
        seen_hosts = set()
        for url in pool:
            try:
                host = urlparse(url if "://" in url else "https://" + url).hostname
            except Exception:
                host = None
            if host:
                endpoints.append((host, url))
                seen_hosts.add(host)
        self._endpoints = endpoints
        # Drop cache entries for hosts no longer in the pool; new hosts resolve
        # lazily on first use (NOT here, so a reload doesn't burst dns).
        for host in list(self._ip_cache.keys()):
            if host not in seen_hosts:
                self._ip_cache.pop(host, None)

        if self._enabled and not self._endpoints:
            # Genuine misconfig — warn loud (audit catches [WARNING]).
            self._warn("enabled=true but endpoint_pool is empty/unparseable — "
                       "no sessions will open")

    def start(self) -> None:
        """Spawn the manager thread (idempotent)."""
        if not self._enabled:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name="persistent-session", daemon=True)
        self._thread.start()
        self._info(f"daemon started send_interval={self._send_interval:.0f}s "
                   f"endpoints={len(self._endpoints)} "
                   f"max_concurrent={self._max_concurrent}")

    def stop(self) -> None:
        """Signal stop, join, FIN-close every live socket (-> Zeek SF)."""
        self._stop_evt.set()
        t = self._thread
        if t is not None:
            t.join(timeout=5.0)
        with self._lock:
            for s in self._sessions:
                self._close_session(s, reason="shutdown")
            self._sessions = []

    def opens_in_current_minute(self) -> int:
        """Opens fired in the current UTC minute — read by the main loop and
        passed to D4's deficit-burst so daemon opens net out of the volume
        budget. Rolls lazily so a stale prior-minute count is never returned."""
        with self._lock:
            self._roll_minute_locked()
            return self._opens_this_minute

    # ── Manager thread ────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self._tick()
            except Exception as e:  # never let the thread die
                self._info(f"tick error {type(e).__name__}: {str(e)[:80]}")
            self._stop_evt.wait(1.0)

    def _tick(self) -> None:
        now_mono = time.monotonic()
        now_utc = datetime.now(timezone.utc)
        with self._lock:
            self._roll_minute_locked(now_utc)
            self._reap_and_keepalive_locked(now_mono)
            self._maybe_open_locked(now_mono, now_utc)

    def _roll_minute_locked(self, now_utc: Optional[datetime] = None) -> None:
        now_utc = now_utc or datetime.now(timezone.utc)
        key = (now_utc.hour, now_utc.minute)
        if key != self._minute_stamp:
            self._minute_stamp = key
            self._opens_this_minute = 0
            # Reroll the per-hour minute distribution on hour change.
            if now_utc.hour != self._hour_stamp:
                self._hour_stamp = now_utc.hour
                self._reroll_hour_locked(now_utc.hour)
            # Schedule this minute's opens at random sub-minute offsets so they
            # stagger on the wire (start-minute binning is indifferent to the
            # offset, but a simultaneous burst is less human).
            n = self._minute_counts[now_utc.minute]
            base = time.monotonic()
            self._pending_opens = sorted(
                base + self._rng.uniform(0.0, 58.0) for _ in range(n))
            self._pending_minute = now_utc.minute

    def _reroll_hour_locked(self, hour: int) -> None:
        total = self._opens_per_hour[hour] if 0 <= hour < 24 else 0
        counts = [0] * 60
        for _ in range(max(0, total)):
            counts[self._rng.randrange(60)] += 1
        self._minute_counts = counts

    def _reap_and_keepalive_locked(self, now_mono: float) -> None:
        survivors = []
        for s in self._sessions:
            if now_mono >= s.end_mono:
                self._close_session(s, reason="duration")
                continue
            if now_mono >= s.next_keepalive_mono:
                if not self._keepalive(s):
                    self._close_session(s, reason="server")
                    continue
                s.next_keepalive_mono = now_mono + self._send_interval
                if s.n_requests >= _MAX_REQUESTS_PER_SESSION:
                    self._close_session(s, reason="server")
                    continue
            survivors.append(s)
        self._sessions = survivors

    def _maybe_open_locked(self, now_mono: float, now_utc: datetime) -> None:
        if not self._endpoints:
            return
        # Not in the active-hours band (this hour has zero opens) → no new opens.
        if self._opens_per_hour[now_utc.hour] <= 0:
            return
        while self._pending_opens and now_mono >= self._pending_opens[0]:
            self._pending_opens.pop(0)
            if len(self._sessions) >= self._max_concurrent:
                self._info(f"skip open reason=maxconc concurrent={len(self._sessions)}")
                continue
            self._open_session(now_mono, now_utc)

    # ── Session lifecycle ─────────────────────────────────────────────

    def _open_session(self, now_mono: float, now_utc: datetime) -> None:
        host, _url = self._rng.choice(self._endpoints)
        ip = self._resolve(host)
        if ip is None:
            self._info(f"open failed host={host} reason=resolve")
            return
        try:
            raw = socket.create_connection((ip, _TLS_PORT), timeout=_SOCK_TIMEOUT_S)
            ctx = ssl.create_default_context()
            # SNI must carry the real host even though we dialed the cached IP.
            sock = ctx.wrap_socket(raw, server_hostname=host)
            sock.settimeout(_SOCK_TIMEOUT_S)
        except (OSError, ssl.SSLError) as e:
            # A stale cached IP (CDN rotation) is the likely culprit — re-resolve
            # this one host once and let the next scheduled open retry.
            self._ip_cache.pop(host, None)
            self._info(f"open failed host={host} ip={ip} "
                       f"reason=connect:{type(e).__name__}")
            return

        # Lifetime = min(sampled duration, time-to-active-block-end). The cap makes
        # the rare long-tail session close gracefully at the workday boundary.
        sampled = self._sample_duration()
        block_end = self._active_block_end_seconds(now_utc)
        lifetime = max(self._send_interval, min(sampled, block_end))
        end_mono = now_mono + lifetime

        s = _Session(sock, host, ip, now_mono, end_mono,
                     now_mono + self._send_interval, self._bytes_target)
        # First request establishes the keep-alive; counts toward orig_bytes.
        if not self._keepalive(s):
            try:
                sock.close()
            except Exception:
                pass
            self._info(f"open failed host={host} reason=first_request")
            return
        self._sessions.append(s)
        self._opens_this_minute += 1
        self._info(f"open endpoint={host} ip={ip} lifetime={lifetime:.0f}s "
                   f"concurrent={len(self._sessions)}")

    def _keepalive(self, s: _Session) -> bool:
        """Send one padded GET (Range-bounded body) and drain the response.
        Returns False on any socket error / desync → caller reaps as server-close."""
        # Front-load the orig_bytes target: ordinary sites often close idle
        # keep-alives within seconds, so a session may only get 1-2 requests.
        # orig_bytes is summed per-conn (start-minute binning sees the conn
        # total), so padding each request toward the FULL remaining target —
        # capped by the per-request size in _build_request — hits the target in
        # the first request(s) rather than relying on a long-lived session.
        remaining = max(0, s.bytes_target - s.bytes_cum)
        req = self._build_request(s.host, remaining)
        try:
            s.sock.sendall(req)
            s.bytes_cum += len(req)
            s.n_requests += 1
            self._drain_response(s.sock)
            return True
        except (OSError, ssl.SSLError):
            return False

    def _build_request(self, host: str, pad_target: int) -> bytes:
        """GET / with a cache-buster and a single padding header sized toward the
        per-keepalive byte budget, total capped under _MAX_REQUEST_BYTES."""
        token = base64.urlsafe_b64encode(
            self._rng.getrandbits(48).to_bytes(6, "big")).decode().rstrip("=")
        base = (f"GET /?_={token} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"User-Agent: Mozilla/5.0\r\n"
                f"Accept: */*\r\n"
                f"Range: bytes=0-0\r\n"
                f"Connection: keep-alive\r\n")
        tail = "\r\n"
        fixed = len(base) + len("X-Ruse-Pad: \r\n") + len(tail)
        pad_len = max(0, min(pad_target - fixed, _MAX_REQUEST_BYTES - fixed))
        if pad_len > 0:
            pad = base64.urlsafe_b64encode(
                self._rng.getrandbits(pad_len * 6).to_bytes(
                    (pad_len * 6 + 7) // 8, "big")).decode()[:pad_len]
            return (base + f"X-Ruse-Pad: {pad}\r\n" + tail).encode("ascii", "ignore")
        return (base + tail).encode("ascii", "ignore")

    def _drain_response(self, sock) -> None:
        """Bounded read of one HTTP response so the keep-alive stream stays
        framed. Range: bytes=0-0 keeps the body to ~1 byte on compliant servers;
        the cap bounds non-compliant ones."""
        buf = b""
        try:
            while b"\r\n\r\n" not in buf and len(buf) < _DRAIN_CAP_BYTES:
                chunk = sock.recv(2048)
                if not chunk:
                    return
                buf += chunk
            # Best-effort body drain by Content-Length, capped.
            head, _, body = buf.partition(b"\r\n\r\n")
            clen = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    try:
                        clen = int(line.split(b":", 1)[1].strip())
                    except ValueError:
                        clen = 0
                    break
            got = len(body)
            while got < clen and got < _DRAIN_CAP_BYTES:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                got += len(chunk)
        except (OSError, ssl.SSLError):
            return

    def _close_session(self, s: _Session, reason: str) -> None:
        try:
            s.sock.shutdown(socket.SHUT_RDWR)  # FIN -> Zeek conn_state=SF
        except Exception:
            pass
        try:
            s.sock.close()
        except Exception:
            pass
        self._info(f"close endpoint={s.host} reason={reason} "
                   f"bytes_cum={s.bytes_cum} requests={s.n_requests}")

    # ── Helpers ───────────────────────────────────────────────────────

    def _resolve(self, host: str) -> Optional[str]:
        """Resolve a host ONCE, cache the IP. Steady-state emits zero dns."""
        ip = self._ip_cache.get(host)
        if ip is not None:
            return ip
        try:
            # IPv4 only — the SUP VMs have no IPv6 route, so a AAAA pick would
            # fail every connect (and burn the open).
            infos = socket.getaddrinfo(host, _TLS_PORT,
                                       family=socket.AF_INET,
                                       type=socket.SOCK_STREAM)
        except OSError:
            return None
        ips = [info[4][0] for info in infos if info[4] and info[4][0]]
        if not ips:
            return None
        ip = self._rng.choice(ips)
        self._ip_cache[host] = ip
        return ip

    def _sample_duration(self) -> float:
        """Lognormal centered on the duration target (median == target),
        modest sigma so most sessions are near-target with a light tail."""
        import math
        mu = math.log(self._duration_target)
        return self._rng.lognormvariate(mu, 0.6)

    def _active_block_end_seconds(self, now_utc: datetime) -> float:
        """Seconds from now until the active-hours boundary, reading
        session_opens_per_hour CIRCULARLY. Walk forward from the current hour to
        the first zero hour; the boundary is the top of that hour. A 21->04 band
        opened at 03:00 caps at 04:00, not the 00:00 array boundary."""
        h = now_utc.hour
        for k in range(1, 25):
            if self._opens_per_hour[(h + k) % 24] <= 0:
                secs_into_hour = now_utc.minute * 60 + now_utc.second
                return float(k * 3600 - secs_into_hour)
        # All 24 hours non-zero (PHASE clamps bands to <=16h so this shouldn't
        # happen) — fall back to a generous bound.
        return 16 * 3600.0

    def _utc_minute_key(self):
        now = datetime.now(timezone.utc)
        return (now.hour, now.minute)

    def _info(self, msg: str) -> None:
        line = f"{_LOG_PREFIX} {msg}"
        print(line, flush=True)
        if self._logger is not None:
            try:
                self._logger.info(line)
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
