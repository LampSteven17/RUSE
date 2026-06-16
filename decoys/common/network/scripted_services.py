"""Scripted services — deterministic protocol-diversity traffic generator.

PHASE-toggled probes that fire at fixed minute-of-hour marks. Each probe
produces a distinct Zeek signature (different ports, conn_state outcomes,
or protocols) to inflate protocol-mix and conn-outcome features the
target BiLSTM may key on.

Distinct from background_services.py (which is rate-based DNS/HTTP HEAD/
NTP volume) — this module is about *protocol diversity*, not volume.

PHASE schema (Phase 3):
  diversity.background_services.{name}_enabled: bool   per service toggle

Per-service schedule is hardcoded minute marks (UTC) — same SUP, same
seed, same wall-clock minute → same probe firings. Endpoints are
hardcoded too; PHASE only flips on/off.

Service registry:
  smb         TCP → 8.8.8.8:445       closed port, conn_state=REJ
  ldap        TCP → 8.8.8.8:389       closed port, conn_state=REJ
  imap        IMAPS → imap.gmail.com  real TLS handshake + server greeting
  doh         HTTPS → cloudflare-dns  DNS-over-HTTPS POST, real query
  mdns        UDP → 224.0.0.251:5353  LAN multicast PTR query
  websocket   (not yet implemented — v1 stub)
  failed_conn TCP → 127.0.0.1:1        closed port, conn_state=REJ

Integration: emulation_loop's tick calls `maybe_run()` between workflows.
Schedules are deduped per-minute so multiple calls within one minute
don't re-fire the same probe.
"""
from __future__ import annotations

import logging
import random
import socket
import ssl
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import requests


# ─── Endpoints (hardcoded; PHASE only toggles on/off) ────────────────────

SMB_PROBE_HOST = "8.8.8.8"
SMB_PROBE_PORT = 445
LDAP_PROBE_HOST = "8.8.8.8"
LDAP_PROBE_PORT = 389
IMAP_PROBE_HOST = "imap.gmail.com"
IMAP_PROBE_PORT = 993
DOH_URL = "https://cloudflare-dns.com/dns-query"
MDNS_GROUP = ("224.0.0.251", 5353)
# Phase 3 failed_conn target: 1.1.1.1:1 — Cloudflare DNS, port 1 almost
# certainly closed. SYN → RST (or DROP). Previously pointed at 127.0.0.1
# which Zeek can't see (loopback); fix shipped 2026-05-20.
FAIL_CONN_HOST = "1.1.1.1"
FAIL_CONN_PORT = 1

TCP_TIMEOUT_SECS = 5.0
HTTP_TIMEOUT_SECS = 10.0


# ─── Probe implementations ──────────────────────────────────────────────

def _tcp_probe(host: str, port: int) -> tuple[bool, str, int]:
    """TCP connect, immediate close. Returns (connected, conn_state_hint, ms).

    conn_state_hint values mirror Zeek's interpretation: "SF" if we shut
    down cleanly after connect, "REJ" if the server refused (port closed),
    "S0" if no response (timeout).
    """
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=TCP_TIMEOUT_SECS) as s:
            s.shutdown(socket.SHUT_RDWR)
        return True, "SF", int((time.monotonic() - start) * 1000)
    except ConnectionRefusedError:
        return False, "REJ", int((time.monotonic() - start) * 1000)
    except socket.timeout:
        return False, "S0", int((time.monotonic() - start) * 1000)
    except OSError as e:
        # ENETUNREACH, EHOSTUNREACH, etc. — Zeek typically sees these as
        # blocked-by-host or never-arrives. Use S0 as a catch-all.
        return False, "S0", int((time.monotonic() - start) * 1000)


def probe_smb():
    return _tcp_probe(SMB_PROBE_HOST, SMB_PROBE_PORT)


def probe_ldap():
    return _tcp_probe(LDAP_PROBE_HOST, LDAP_PROBE_PORT)


def probe_imap():
    """IMAPS — TCP connect + TLS handshake + read server greeting + close.

    Produces a real TLS flow with imap.gmail.com SNI; Zeek records it as
    ssl.log with service=imaps. No login attempted.
    """
    start = time.monotonic()
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection(
            (IMAP_PROBE_HOST, IMAP_PROBE_PORT), timeout=TCP_TIMEOUT_SECS
        ) as raw:
            with ctx.wrap_socket(raw, server_hostname=IMAP_PROBE_HOST) as tls:
                tls.settimeout(TCP_TIMEOUT_SECS)
                tls.recv(1024)  # IMAP greeting (e.g. "* OK Gimap ready ...")
        return True, "SF", int((time.monotonic() - start) * 1000)
    except (OSError, ssl.SSLError):
        return False, "S0", int((time.monotonic() - start) * 1000)


# Pre-built DoH query: example.com A record, RFC 8484 wire format.
# Header: id=0x0000, qr=0, opcode=0, rd=1; qdcount=1.
# Question: example.com  type A (1)  class IN (1).
_DOH_QUERY = (
    b"\x00\x00\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    b"\x07example\x03com\x00\x00\x01\x00\x01"
)


def probe_doh():
    """DNS-over-HTTPS query → Cloudflare for example.com A record."""
    start = time.monotonic()
    try:
        r = requests.post(
            DOH_URL,
            data=_DOH_QUERY,
            headers={"Content-Type": "application/dns-message"},
            timeout=HTTP_TIMEOUT_SECS,
        )
        return r.status_code == 200, "SF", int((time.monotonic() - start) * 1000)
    except requests.RequestException:
        return False, "S0", int((time.monotonic() - start) * 1000)


# Pre-built mDNS query: PTR for _services._dns-sd._udp.local
# (the canonical mDNS service-discovery question).
_MDNS_QUERY = (
    b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    b"\x09_services\x07_dns-sd\x04_udp\x05local\x00\x00\x0c\x00\x01"
)


def probe_mdns():
    """Send mDNS service-discovery query on the local LAN multicast group."""
    start = time.monotonic()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            s.sendto(_MDNS_QUERY, MDNS_GROUP)
        return True, "SF", int((time.monotonic() - start) * 1000)
    except OSError:
        return False, "S0", int((time.monotonic() - start) * 1000)


def probe_websocket():
    """v1 stub — websocket support requires websocket-client package not
    available on stock VMs. Logs a [WARNING] when toggle is on but not
    implemented; falls back to a no-op so scheduler can still proceed."""
    print(
        "[WARNING] scripted_services.websocket_enabled=true but RUSE has "
        "no websocket implementation yet — skipping. Add websocket-client "
        "to INSTALL_SUP.sh and implement probe_websocket() for v2."
    )
    return False, "S0", 0


def probe_failed_conn():
    """Reliably-failing TCP connect → produces conn_state=REJ in Zeek."""
    return _tcp_probe(FAIL_CONN_HOST, FAIL_CONN_PORT)


# ─── Registry ───────────────────────────────────────────────────────────

# Each service: (probe_fn, [minute_marks]). Schedules spread across the
# hour so multiple services don't bunch on the same minute.
PROBE_REGISTRY: dict[str, tuple[Callable, list[int]]] = {
    "smb":         (probe_smb,         [10, 25, 40, 55]),
    "ldap":        (probe_ldap,        [5, 20, 35, 50]),
    "imap":        (probe_imap,        [15, 45]),
    "doh":         (probe_doh,         [12, 27, 42, 57]),
    "mdns":        (probe_mdns,        [7, 22, 37, 52]),
    "websocket":   (probe_websocket,   [30]),
    "failed_conn": (probe_failed_conn, [17, 47]),
}


# ─── Scheduler ──────────────────────────────────────────────────────────

class ScriptedServiceScheduler:
    """Cron-style scheduler for scripted protocol probes.

    Reads diversity.background_services.{name}_enabled booleans from
    PHASE config. On maybe_run(), fires any enabled service whose most
    recent scheduled minute this hour is at or before the current minute
    and hasn't yet fired this hour (catch-up semantics — robust to a loop
    that sleeps past the exact scheduled minute).

    Each (service, hour-slot) fires at most once. (Probes' own random
    behavior, if any, flows through the seeded module-level random state.)
    """

    def __init__(self, config: Optional[dict] = None, logger=None):
        self.logger = logger
        self.enabled: dict[str, bool] = {}
        # _last_fire_key tracks per-service the most recent (utc_hour, slot)
        # we fired, so re-ticking the same hour-slot doesn't re-fire.
        self._last_fire_key: dict[str, Optional[tuple]] = {}
        for name in PROBE_REGISTRY:
            self.enabled[name] = False
            self._last_fire_key[name] = None
        # Closed-loop ShapeController (Phase 1, 2026-06-16). When attached AND it
        # carries a conn_state_mix.failed_conn target, failed_conn switches from
        # its fixed cron slot to a target-driven per-minute rate.
        self._controller = None
        self._fc_minute_stamp: Optional[tuple] = None
        self._fc_fired_this_minute = 0
        self.update_config(config or {})

    def set_controller(self, controller) -> None:
        """Inject (or clear) the ShapeController. Idempotent across reloads."""
        self._controller = controller

    def update_config(self, config: dict) -> None:
        """Re-read enable booleans on PHASE hot-reload."""
        for name in PROBE_REGISTRY:
            new_val = bool(config.get(f"{name}_enabled", False))
            if new_val != self.enabled[name]:
                if self.logger:
                    self.logger.info(
                        f"[scripted-svc] {name}={'enabled' if new_val else 'disabled'}"
                    )
            self.enabled[name] = new_val

    def _any_enabled(self) -> bool:
        return any(self.enabled.values())

    def maybe_run(self) -> int:
        """Fire any enabled service whose latest scheduled slot this hour is due.

        Returns the number of probes fired this call. Safe to call many
        times per minute — dedup on (service, hour-slot) ensures each slot
        runs at most once per hour even across repeated/late ticks.
        """
        now = datetime.now(timezone.utc)
        fired = 0
        # Rate-driven failed_conn (conn_state_mix actuator, Phase 1) runs
        # independent of the cron *_enabled toggles — conn_state_mix may ship a
        # failed_conn target with no scripted service toggled on.
        rate_active = self._failed_conn_rate_active()
        if rate_active:
            fired += self._maybe_fire_failed_conn_rate(now)
        if not self._any_enabled():
            return fired
        for name, (fn, schedule) in PROBE_REGISTRY.items():
            if not self.enabled[name]:
                continue
            # When the controller owns failed_conn, skip its fixed cron slot so
            # the cron and rate paths don't double-fire.
            if name == "failed_conn" and rate_active:
                continue
            # Catch-up semantics: fire the most recent scheduled minute at
            # or before the current minute this hour. A sleepy loop (long
            # inter-task / inter-cluster sleeps) rarely ticks exactly on a
            # scheduled minute, so exact-minute matching missed the 2-min/hr
            # window entirely. Firing the latest due-but-unfired slot keeps
            # the per-hour cadence without bursting all missed slots at once.
            due = [m for m in schedule if m <= now.minute]
            if not due:
                continue
            key = (now.hour, max(due))
            if self._last_fire_key[name] == key:
                continue
            try:
                ok, conn_state, ms = fn()
            except Exception as e:
                # Probe internals raised — log loudly but don't crash the loop.
                print(f"[WARNING] scripted_services.{name} raised "
                      f"{type(e).__name__}: {str(e)[:80]}")
                if self.logger:
                    self.logger.warning(
                        f"scripted-svc {name} exception: {type(e).__name__}: {e}"
                    )
                ok, conn_state, ms = False, "S0", 0
            self._last_fire_key[name] = key
            fired += 1
            msg = (f"[scripted-svc] {name} ok={ok} state={conn_state} "
                   f"latency_ms={ms}")
            print(msg)
            if self.logger:
                self.logger.info(msg)
        return fired

    def _failed_conn_rate_active(self) -> bool:
        """True when a ShapeController with a non-zero failed_conn target is
        attached (so failed_conn is rate-driven, not cron-driven)."""
        ctrl = self._controller
        if ctrl is None:
            return False
        try:
            return ctrl.has_failed_conn_target()
        except Exception:
            return False

    def _maybe_fire_failed_conn_rate(self, now: datetime) -> int:
        """Fire probe_failed_conn() toward the controller's per-minute target
        rate. Probabilistic spread across the many maybe_run() calls per minute,
        bounded by a per-minute budget so total firings track the rate regardless
        of call cadence. Returns 1 if it fired, else 0."""
        ctrl = self._controller
        try:
            rate = float(ctrl.failed_conn_rate_per_min())
        except Exception:
            rate = 0.0
        if rate <= 0:
            return 0
        key = (now.hour, now.minute)
        if key != self._fc_minute_stamp:
            self._fc_minute_stamp = key
            self._fc_fired_this_minute = 0
        if self._fc_fired_this_minute >= rate:
            return 0
        if random.random() >= rate / 30.0:
            return 0
        try:
            ok, conn_state, ms = probe_failed_conn()
        except Exception as e:
            print(f"[WARNING] scripted_services.failed_conn(rate) raised "
                  f"{type(e).__name__}: {str(e)[:80]}")
            if self.logger:
                self.logger.warning(
                    f"scripted-svc failed_conn(rate) exception: "
                    f"{type(e).__name__}: {e}")
            ok, conn_state, ms = False, "S0", 0
        self._fc_fired_this_minute += 1
        msg = (f"[scripted-svc] failed_conn ok={ok} state={conn_state} "
               f"latency_ms={ms} src=rate")
        print(msg)
        if self.logger:
            self.logger.info(msg)
        return 1
