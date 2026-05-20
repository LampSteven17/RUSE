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
FAIL_CONN_HOST = "127.0.0.1"
FAIL_CONN_PORT = 1   # reliably-closed port → REJ

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
    PHASE config. On maybe_run(), fires any service whose schedule
    matches the current UTC minute and that hasn't yet fired this minute.

    Behavior is fully deterministic given the wall clock — same seed,
    same minute → same probes fire. (Probes' own random behavior, if any,
    flows through the seeded module-level random state.)
    """

    def __init__(self, config: Optional[dict] = None, logger=None):
        self.logger = logger
        self.enabled: dict[str, bool] = {}
        # last_fire_min tracks per-service the most recent (utc_hour, minute)
        # we fired in, so re-entering the same minute doesn't re-fire.
        self._last_fire_key: dict[str, Optional[tuple]] = {}
        for name in PROBE_REGISTRY:
            self.enabled[name] = False
            self._last_fire_key[name] = None
        self.update_config(config or {})

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
        """Fire any enabled services whose schedule matches the current minute.

        Returns the number of probes fired this call. Safe to call many
        times per minute — dedup ensures each (service, minute) pair runs
        at most once per pass through that minute.
        """
        if not self._any_enabled():
            return 0
        now = datetime.now(timezone.utc)
        key = (now.hour, now.minute)
        fired = 0
        for name, (fn, schedule) in PROBE_REGISTRY.items():
            if not self.enabled[name]:
                continue
            if now.minute not in schedule:
                continue
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
