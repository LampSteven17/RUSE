"""Per-protocol probe functions for the neighborhood-traffic sidecar.

Each probe generates a real TCP or UDP transaction so Zeek captures it with
an accurate conn_state / service classification. Probes are designed for
stateless, short-lived invocation from the scheduler in neighborhood_traffic.py.

Return convention: `(ok: bool, conn_state_hint: str, elapsed_ms: int)`.
The hint is best-guess; Zeek infers the actual conn_state from wire events.
"""
from __future__ import annotations

import random
import socket
import struct
import subprocess
import time
from typing import Tuple


Result = Tuple[bool, str, int]  # (ok, conn_state_hint, elapsed_ms)


def _tcp_connect(ip: str, port: int, timeout: float = 1.0, full_handshake: bool = False) -> Result:
    """Open a TCP socket to ip:port with a hard timeout.

    full_handshake=False: `connect_ex` + immediate close. Produces S0 if the
    port is firewalled/dropped, REJ if RST comes back, SF if the target
    accepted and the FIN close completed cleanly.
    full_handshake=True:  `connect` + small write + graceful close. More
    likely to produce SF on open ports.
    """
    start = time.monotonic()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        rc = s.connect_ex((ip, port))
        elapsed = int((time.monotonic() - start) * 1000)
        if rc == 0:
            if full_handshake:
                try:
                    s.send(b"\x00")
                except OSError:
                    pass
            return True, "SF" if full_handshake else "RSTO", elapsed
        if rc in (111, 113):  # ECONNREFUSED, EHOSTUNREACH
            return False, "REJ", elapsed
        return False, "S0", elapsed
    except socket.timeout:
        return False, "S0", int((time.monotonic() - start) * 1000)
    except OSError:
        return False, "OTH", int((time.monotonic() - start) * 1000)
    finally:
        try:
            s.close()
        except OSError:
            pass


def _udp_send(ip: str, port: int, payload: bytes, timeout: float = 0.5) -> Result:
    """Send one UDP datagram. Zeek captures as a unidirectional flow."""
    start = time.monotonic()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(timeout)
        s.sendto(payload, (ip, port))
        return True, "unidir", int((time.monotonic() - start) * 1000)
    except OSError:
        return False, "OTH", int((time.monotonic() - start) * 1000)
    finally:
        try:
            s.close()
        except OSError:
            pass


# ─── Inbound probes targeting SUPs ────────────────────────────────────────

def probe_smb(ip: str) -> Result:
    """SMB poll (TCP 445). Common on workstation LANs (file-share polling,
    share-enumeration, LDAP-associated SMB probes)."""
    return _tcp_connect(ip, 445, timeout=1.5, full_handshake=True)


def probe_ldap(ip: str) -> Result:
    """LDAP bind attempt (TCP 389 or 88 alternating for variety).
    Typically rejected by non-DC hosts — produces REJ / S0."""
    port = random.choice([389, 88])
    return _tcp_connect(ip, port, timeout=1.0)


def probe_wsus(ip: str) -> Result:
    """WSUS check-in (TCP 8530). Windows-Update-Service port. Rejected
    on Linux hosts; still generates a row in Zeek."""
    return _tcp_connect(ip, 8530, timeout=1.0)


def probe_printer(ip: str) -> Result:
    """Printer status poll (TCP 9100 RAW or 631 IPP, alternating)."""
    port = random.choice([9100, 631])
    return _tcp_connect(ip, port, timeout=1.0)


def probe_ipmi(ip: str) -> Result:
    """IPMI / management poll (UDP 623 or TCP 5989).
    UDP 623 is the canonical IPMI-over-LAN; some iDRAC / iLO use TCP."""
    if random.random() < 0.7:
        # IPMI Get Channel Auth Capabilities request (minimal)
        payload = bytes.fromhex("0600ff07000000000000000020182038")
        return _udp_send(ip, 623, payload)
    return _tcp_connect(ip, 5989, timeout=1.0)


def probe_winrm(ip: str) -> Result:
    """WinRM / cockpit probe (TCP 5985 or 9090)."""
    port = random.choice([5985, 9090])
    return _tcp_connect(ip, port, timeout=1.0)


def probe_ntp_receive(ip: str) -> Result:
    """Send an NTP client packet TO the SUP. Workstation NTP daemons
    sometimes broadcast / multicast; we simulate a neighbor NTP server
    pushing a packet toward the SUP."""
    # NTP mode 3 (client) request — 48 bytes
    payload = b'\x1b' + b'\x00' * 47
    return _udp_send(ip, 123, payload)


def probe_mdns(ip: str) -> Result:
    """Multicast DNS query targeted at the SUP's unicast address. Real
    mDNS is on 224.0.0.251 but unicast mDNS queries are legal (RFC 6762
    §5.5) and produce a visible UDP row for the SUP."""
    # mDNS query: transaction-id=0, flags=0, qd=1, query for _workstation._tcp.local
    payload = (
        b"\x00\x00"     # transaction id
        b"\x00\x00"     # flags (standard query)
        b"\x00\x01"     # 1 question
        b"\x00\x00\x00\x00\x00\x00"  # 0 answer/auth/additional
        b"\x0c_workstation\x04_tcp\x05local\x00"
        b"\x00\x0c"     # PTR
        b"\x00\x01"     # IN
    )
    return _udp_send(ip, 5353, payload)


def probe_ssdp(ip: str) -> Result:
    """SSDP M-SEARCH sent unicast to the SUP. Real SSDP is on 239.255.255.250
    but devices also respond to unicast M-SEARCH — and the UDP flow shows up."""
    payload = (
        b"M-SEARCH * HTTP/1.1\r\n"
        b"HOST: 239.255.255.250:1900\r\n"
        b"MAN: \"ssdp:discover\"\r\n"
        b"MX: 2\r\n"
        b"ST: ssdp:all\r\n\r\n"
    )
    return _udp_send(ip, 1900, payload)


_SCAN_PORTS = [21, 23, 53, 135, 139, 443, 1433, 3306, 3389, 5432, 5900, 8080]


def probe_scan(ip: str) -> Result:
    """Random-port TCP scan. Generates the rare-but-present probing noise
    that real workstations see from security tools, PXE scans, and
    misconfigured hosts. Mix of S0/REJ/RSTO."""
    return _tcp_connect(ip, random.choice(_SCAN_PORTS), timeout=0.8)


# ─── Probe registry ────────────────────────────────────────────────────────
# Maps behavior.json key → probe function. Rates are per hour.

PROBE_REGISTRY = {
    "inbound_smb_per_hour":            probe_smb,
    "inbound_ldap_per_hour":           probe_ldap,
    "inbound_wsus_per_hour":           probe_wsus,
    "inbound_printer_per_hour":        probe_printer,
    "inbound_ipmi_per_hour":           probe_ipmi,
    "inbound_winrm_per_hour":          probe_winrm,
    "inbound_ntp_receive_per_hour":    probe_ntp_receive,
    "inbound_mdns_per_hour":           probe_mdns,
    "inbound_ssdp_per_hour":           probe_ssdp,
    "inbound_scan_per_hour":           probe_scan,
}
