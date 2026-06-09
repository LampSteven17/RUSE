"""Service-mix background generators — PHASE service_mix_targets (v1, 2026-06).

PHASE measured that on several target models (cptc9-24 worst: 55% reachable)
a large share of the HUMAN active-minute service mix consists of Zeek
service types no DECOY workflow can produce — udp, splunk, smb, ntp, http2,
quic. Workflows cover only http/ssl/dns/whois/ssh. PHASE now emits

  diversity.background_services.service_mix_targets:
    targets:
      {service}: {fraction: float, suggested_events_per_hour: [24 ints]}

on feedback-mode docs where the gap exists; the field is OMITTED when the
human mix is fully workflow-reachable (absence = clean no-op).

Contract honored here:
  - suggested_events_per_hour is UTC-indexed and already anchored to the
    human baseline volume (capped at 120/hr) — never rescaled by RUSE.
  - NOT gated on timing.active_minute_windows. The diurnal shape lives in
    the vector itself (near-zero off hours), so this runs on its own
    daemon thread across all 24 hours, independent of the emulation loop.
  - Jittered, never metronomic: hourly count ±20%, event times uniform-
    random within the hour (PHASE's models key on per-minute regularity).
  - Unknown service key → one [WARNING], then skip. Forward-compat: PHASE
    may name services this runtime has no generator for yet.
  - Precedence: a service named in targets WINS over the legacy knob for
    the same service. The suppression itself lives in the legacy
    consumers (BackgroundServiceGenerator zeroes ntp_checks_per_day /
    dns_per_hour / http_head_per_hour; ScriptedServiceScheduler forces
    {name}_enabled off) via covered_services() below.
  - Destinations: smb/splunk/ntp/udp go to stable fake-infra IPs derived
    from _metadata.dataset — same /24 and hosts for every SUP in a deploy,
    so the topology reads as shared infrastructure, not random scatter.
    http2/quic are internet-bound (real handshakes).

Each fired event is logged to the per-VM jsonl as event_type
"background_service" (details: service/host/port/proto/ok/conn_state/
latency_ms/count) so PHASE can attribute this traffic separately from
workflow traffic. systemd.log gets one plan line and one summary line per
hour — per-event printing at up to 120/hr/service would flood it.
"""
from __future__ import annotations

import hashlib
import random
import socket
import ssl
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple


# ─── Destination derivation ──────────────────────────────────────────────

# Real internet hosts that negotiate the respective protocol. Used for the
# internet-bound services only.
H2_HOSTS = [
    "www.google.com", "www.cloudflare.com", "www.wikipedia.org",
    "github.com", "cdn.jsdelivr.net", "www.youtube.com",
]
QUIC_HOSTS = ["www.google.com", "www.cloudflare.com", "www.youtube.com"]

TCP_TIMEOUT_SECS = 5.0
QUIC_TIMEOUT_SECS = 10.0

# Defensive ceiling on per-service hourly events. PHASE caps its vector at
# 120; this only guards against a malformed emission.
MAX_EVENTS_PER_HOUR = 150


def _local_ip() -> Optional[str]:
    """Best-effort local primary IP (no traffic sent — UDP connect only)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def derive_fake_infra(dataset: Optional[str]) -> Dict[str, list]:
    """Derive the deploy-stable fake-infra endpoints from the dataset name.

    Keyed on _metadata.dataset (shared by every SUP in a deploy) — NOT the
    per-SUP seed — so all SUPs in a deployment talk to the same fake
    splunk indexer / file servers / NTP host, like real infrastructure.

    Subnet is a 10.x/24 derived from md5(dataset), avoiding the VM's own
    /16 so the SYNs/datagrams route via the gateway and hit the wire
    (an unroutable same-subnet pick would die at ARP, invisible to Zeek).
    """
    token = dataset or "default"
    digest = hashlib.md5(token.encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    second = 16 + digest[8] % 224          # 10.16-239.x.0/24
    third = digest[9]
    local = _local_ip()
    if local and local.startswith(f"10.{second}."):
        second = 16 + (second - 16 + 1) % 224
    base = f"10.{second}.{third}"

    def hosts(n: int) -> List[str]:
        return [f"{base}.{rng.randint(5, 250)}" for _ in range(n)]

    return {
        # (host, port) pools per service
        "smb": [(h, 445) for h in hosts(3)],
        "splunk": [(h, p) for h in hosts(2) for p in (9997, 9997, 8089)],
        "ntp": [(h, 123) for h in hosts(2)],
        # Generic UDP: a few hosts × a few stable high ports Zeek leaves
        # service-less.
        "udp": [(h, rng.randint(10000, 59999)) for h in hosts(4)],
    }


# ─── Wire helpers ────────────────────────────────────────────────────────

def _tcp_send(host: str, port: int, payload: bytes = b"") -> Tuple[bool, str, int]:
    """TCP connect (+ optional payload on success) → (ok, conn_state_hint, ms).

    Fake-infra targets won't answer: expect S0 (dropped at the gateway) or
    REJ. If something does accept, send the protocol bytes so Zeek's DPD
    can classify the session."""
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=TCP_TIMEOUT_SECS) as s:
            if payload:
                s.sendall(payload)
                s.settimeout(2.0)
                try:
                    s.recv(1024)
                except (socket.timeout, OSError):
                    pass
        return True, "SF", int((time.monotonic() - start) * 1000)
    except ConnectionRefusedError:
        return False, "REJ", int((time.monotonic() - start) * 1000)
    except (socket.timeout, OSError):
        return False, "S0", int((time.monotonic() - start) * 1000)


def _udp_send(host: str, port: int, payload: bytes) -> Tuple[bool, str, int]:
    """One UDP datagram — Zeek logs a unidirectional flow either way."""
    start = time.monotonic()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.5)
            s.sendto(payload, (host, port))
            return True, "unidir", int((time.monotonic() - start) * 1000)
        finally:
            s.close()
    except OSError:
        return False, "OTH", int((time.monotonic() - start) * 1000)


# SMB1 Negotiate Protocol request (NetBIOS session wrapper + single
# "NT LM 0.12" dialect). Only sent if a target ever accepts the connect;
# enough originator bytes for Zeek's DPD to classify smb.
_SMB_NEGOTIATE = bytes.fromhex(
    "00000036"                          # NetBIOS session message, len 0x36
    "ff534d42"                          # \xffSMB
    "72"                                # SMB_COM_NEGOTIATE
    "00000000" "18" "0128"              # status / flags / flags2
    "0000" "000000000000000000000000"   # pid-high, signature, reserved
    "0000" "fffe" "0000" "0000"         # tid, pid, uid, mid
    "00" "1300"                         # wct=0, bcc=0x13
    "024e54204c4d20302e313200"          # "\x02NT LM 0.12\x00"
)

# NTP v3 client request (mode 3), 48 bytes — same shape chronyd emits.
_NTP_REQUEST = b"\x1b" + b"\x00" * 47

# HTTP/2 client connection preface + empty SETTINGS frame, sent after a
# successful ALPN-h2 TLS handshake.
_H2_PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n" + b"\x00\x00\x00\x04\x00\x00\x00\x00\x00"


class GeneratorUnavailable(Exception):
    """Raised by a generator whose runtime dependency is missing.

    The scheduler warns once and disables that service for this process
    (same forward-compat path as an unknown service key)."""


# ─── Generators ──────────────────────────────────────────────────────────
# Each takes (scheduler) and returns (ok, conn_state, ms, host, port, proto).

def _gen_smb(sched) -> Tuple[bool, str, int, str, int, str]:
    host, port = sched.pick_infra("smb")
    ok, state, ms = _tcp_send(host, port, _SMB_NEGOTIATE)
    return ok, state, ms, host, port, "tcp"


def _gen_splunk(sched) -> Tuple[bool, str, int, str, int, str]:
    # 9997-weighted (forwarder→indexer dominates real splunk traffic);
    # 8089 mgmt REST appears occasionally. Bare connect — a splunk
    # forwarder opens TCP and waits for the server banner.
    host, port = sched.pick_infra("splunk")
    ok, state, ms = _tcp_send(host, port)
    return ok, state, ms, host, port, "tcp"


def _gen_ntp(sched) -> Tuple[bool, str, int, str, int, str]:
    host, port = sched.pick_infra("ntp")
    ok, state, ms = _udp_send(host, port, _NTP_REQUEST)
    return ok, state, ms, host, port, "udp"


def _gen_udp(sched) -> Tuple[bool, str, int, str, int, str]:
    # Generic service-less UDP flow: random small binary payload to a
    # stable (host, high-port) pair. Varied size so flows aren't uniform.
    host, port = sched.pick_infra("udp")
    size = sched.rng.randint(32, 512)
    payload = bytes(sched.rng.getrandbits(8) for _ in range(size))
    ok, state, ms = _udp_send(host, port, payload)
    return ok, state, ms, host, port, "udp"


def _gen_http2(sched) -> Tuple[bool, str, int, str, int, str]:
    """Real TLS handshake offering ALPN h2 to an internet host, then the
    h2 client preface + SETTINGS. The ClientHello ALPN offer is cleartext,
    so the sensor sees the h2 negotiation regardless of TLS version."""
    host = sched.rng.choice(H2_HOSTS)
    start = time.monotonic()
    try:
        ctx = ssl.create_default_context()
        ctx.set_alpn_protocols(["h2"])
        with socket.create_connection((host, 443), timeout=TCP_TIMEOUT_SECS) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                if tls.selected_alpn_protocol() == "h2":
                    tls.sendall(_H2_PREFACE)
                    tls.settimeout(2.0)
                    try:
                        tls.recv(2048)  # server SETTINGS
                    except (socket.timeout, OSError):
                        pass
        return True, "SF", int((time.monotonic() - start) * 1000), host, 443, "tcp"
    except (OSError, ssl.SSLError):
        return False, "S0", int((time.monotonic() - start) * 1000), host, 443, "tcp"


def _gen_quic(sched) -> Tuple[bool, str, int, str, int, str]:
    """Real QUIC+TLS1.3 handshake via aioquic (added to INSTALL_SUP.sh
    alongside this module). A hand-crafted Initial isn't viable — header
    protection + initial-secret AEAD — so missing aioquic raises
    GeneratorUnavailable and the service is warn-once skipped."""
    try:
        import asyncio
        from aioquic.asyncio import connect as quic_connect
        from aioquic.quic.configuration import QuicConfiguration
    except ImportError:
        raise GeneratorUnavailable(
            "quic target present but aioquic is not installed "
            "(INSTALL_SUP.sh adds it; older VMs need a hot-patch)")
    host = sched.rng.choice(QUIC_HOSTS)
    start = time.monotonic()

    async def _handshake():
        cfg = QuicConfiguration(is_client=True, alpn_protocols=["h3"])
        # Traffic realism, not data integrity — no payload is exchanged.
        cfg.verify_mode = ssl.CERT_NONE
        async with quic_connect(host, 443, configuration=cfg) as client:
            await client.ping()

    try:
        import asyncio
        asyncio.run(asyncio.wait_for(_handshake(), timeout=QUIC_TIMEOUT_SECS))
        return True, "SF", int((time.monotonic() - start) * 1000), host, 443, "udp"
    except Exception:
        return False, "S0", int((time.monotonic() - start) * 1000), host, 443, "udp"


GENERATORS = {
    "udp": _gen_udp,
    "splunk": _gen_splunk,
    "smb": _gen_smb,
    "ntp": _gen_ntp,
    "http2": _gen_http2,
    "quic": _gen_quic,
}

# Legacy BackgroundServiceGenerator knobs that the new field supersedes
# per-service (requirement 4). Consumed by background_services.py.
LEGACY_KNOB_BY_SERVICE = {
    "ntp": "ntp_checks_per_day",
    "dns": "dns_per_hour",
    "http": "http_head_per_hour",
}


def covered_services(bg_config: Optional[dict]) -> set:
    """Service keys named in service_mix_targets.targets, or empty set.

    Shared precedence helper: BackgroundServiceGenerator and
    ScriptedServiceScheduler both suppress their legacy knob/toggle for
    any service in this set."""
    smt = (bg_config or {}).get("service_mix_targets") or {}
    targets = smt.get("targets") or {}
    return set(targets.keys())


# ─── Scheduler ───────────────────────────────────────────────────────────

class ServiceMixScheduler:
    """Own-thread scheduler for service_mix_targets generators.

    update_config() is called from the emulation loop's reload tick (every
    cluster boundary); the thread notices the config generation bump and
    rebuilds its in-hour plan. The thread is a daemon — process exit (and
    therefore systemd stop/restart) kills it; stop() exists for clean
    in-process shutdown."""

    _WAKE_CAP_S = 30.0  # max sleep slice → stop/config responsiveness

    def __init__(self, bg_config: Optional[dict] = None, logger=None,
                 dataset: Optional[str] = None, seed: Optional[int] = None):
        self._logger = logger
        self._dataset = dataset
        # Dedicated RNG — never the global instance. The emulation loop's
        # determinism rests on global random; a thread racing it would
        # break seed-replay. Jitter offsets the PHASE seed so the mix
        # stream is decorrelated from workflow selection.
        self.rng = random.Random(((seed if seed is not None else 0) ^ 0x5E12C3A1))
        self._infra = derive_fake_infra(dataset)
        self._lock = threading.Lock()
        self._targets: Dict[str, List[int]] = {}
        self._config_gen = 0
        self._warned: set = set()
        self._unavailable: set = set()
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.update_config(bg_config or {})

    # ── config ───────────────────────────────────────────────────────

    def update_config(self, bg_config: dict) -> None:
        """Swap targets from the latest background_services dict."""
        smt = (bg_config or {}).get("service_mix_targets") or {}
        raw = smt.get("targets") or {}
        clean: Dict[str, List[int]] = {}
        for name, spec in raw.items():
            vec = (spec or {}).get("suggested_events_per_hour")
            if not isinstance(vec, list) or len(vec) != 24:
                self._warn_once(
                    f"malformed-{name}",
                    f"[service-mix] {name}: suggested_events_per_hour is not "
                    f"a 24-element list — skipping this service")
                continue
            clean[name] = [min(max(0, int(v)), MAX_EVENTS_PER_HOUR) for v in vec]
        with self._lock:
            changed = clean != self._targets
            self._targets = clean
            if changed:
                self._config_gen += 1
        if changed:
            names = ", ".join(
                f"{n}={sum(v)}/day" for n, v in sorted(clean.items())) or "none"
            print(f"[service-mix] targets updated: {names}", flush=True)
            if self._logger:
                self._logger.info(f"[service-mix] targets updated: {names}",
                                  details={"services": sorted(clean)})
        if clean and self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name="service-mix", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()

    def pick_infra(self, service: str) -> Tuple[str, int]:
        return self.rng.choice(self._infra[service])

    # ── internals ────────────────────────────────────────────────────

    def _warn_once(self, key: str, msg: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        print(f"[WARNING] {msg}", flush=True)
        if self._logger:
            self._logger.warning(msg)

    def _snapshot(self) -> Tuple[Dict[str, List[int]], int]:
        with self._lock:
            return dict(self._targets), self._config_gen

    def _stale(self, gen: int) -> bool:
        with self._lock:
            return gen != self._config_gen

    def _build_plan(self, targets: Dict[str, List[int]],
                    now: datetime) -> List[Tuple[float, str]]:
        """Jittered (epoch_ts, service) events for the remainder of this
        UTC hour. Count = vector[hour] ±20%, scaled by the remaining
        fraction of the hour; times uniform-random over the remainder, so
        inter-event gaps are irregular by construction."""
        remaining = 3600 - (now.minute * 60 + now.second)
        if remaining <= 0:
            return []
        frac = remaining / 3600.0
        now_epoch = time.time()
        plan: List[Tuple[float, str]] = []
        for name, vec in targets.items():
            if name not in GENERATORS:
                self._warn_once(
                    f"unknown-{name}",
                    f"[service-mix] no generator for service '{name}' — "
                    f"skipping (PHASE schema may be ahead of this runtime)")
                continue
            if name in self._unavailable:
                continue
            n = vec[now.hour] * self.rng.uniform(0.8, 1.2) * frac
            n = int(round(n))
            for _ in range(n):
                plan.append((now_epoch + self.rng.uniform(0, remaining), name))
        plan.sort()
        return plan

    def _fire(self, name: str) -> bool:
        try:
            ok, state, ms, host, port, proto = GENERATORS[name](self)
        except GeneratorUnavailable as e:
            self._unavailable.add(name)
            self._warn_once(f"unavailable-{name}", f"[service-mix] {e}")
            return False
        except Exception as e:
            # A generator bug must never take down the thread loop, let
            # alone the agent (requirement 3).
            self._warn_once(
                f"crashed-{name}",
                f"[service-mix] {name} generator raised "
                f"{type(e).__name__}: {str(e)[:80]} — further failures "
                f"of this kind are silent")
            return False
        if self._logger:
            try:
                self._logger.background_service(
                    service=name, host=host, port=port, proto=proto,
                    ok=ok, conn_state=state, latency_ms=ms)
            except Exception:
                pass
        return True

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self._run_one_hour()
            except Exception as e:
                print(f"[WARNING] [service-mix] scheduler error: "
                      f"{type(e).__name__}: {str(e)[:120]} — retrying in 60s",
                      flush=True)
                self._stop_evt.wait(60)

    def _run_one_hour(self) -> None:
        targets, gen = self._snapshot()
        if not targets:
            self._stop_evt.wait(60)
            return
        now = datetime.now(timezone.utc)
        hour = now.hour
        plan = self._build_plan(targets, now)
        planned = Counter(name for _, name in plan)
        if planned:
            print(f"[service-mix] hour={hour:02d} plan: "
                  + " ".join(f"{n}={c}" for n, c in sorted(planned.items())),
                  flush=True)
        fired: Counter = Counter()
        for ts, name in plan:
            while True:
                if self._stop_evt.is_set() or self._stale(gen):
                    break
                delay = ts - time.time()
                if delay <= 0:
                    break
                self._stop_evt.wait(min(delay, self._WAKE_CAP_S))
            if self._stop_evt.is_set() or self._stale(gen):
                break
            if datetime.now(timezone.utc).hour != hour:
                break
            if self._fire(name):
                fired[name] += 1
        if planned:
            print(f"[service-mix] hour={hour:02d} fired: "
                  + (" ".join(f"{n}={fired[n]}/{planned[n]}"
                              for n in sorted(planned)) or "none"),
                  flush=True)
        # Hot-reload mid-hour rebuilds the plan for the remainder; a
        # finished plan idles to the hour boundary.
        while (not self._stop_evt.is_set() and not self._stale(gen)
               and datetime.now(timezone.utc).hour == hour):
            self._stop_evt.wait(self._WAKE_CAP_S)
