"""WHOIS lookup helper — shared by all three brains' whois_lookup workflows.

Raw socket TCP/43 to whois.iana.org. Returns the IANA referral block
(registrar / creation date / refer-to TLD whois server) — sufficient for
producing a categorical port-43 Zeek row. NOT the full registration data;
chase the response's `refer:` line if downstream needs that.

Used by:
  - brains/smolagents/workflows/whois_lookup.py (LLM-picked-domain)
  - brains/browseruse/workflows/whois_lookup.py (LLM-picked-domain)
  - brains/mchp/app/workflows/whois_lookup.py (random-pick, no LLM)
"""
from __future__ import annotations

import socket


WHOIS_HOST = "whois.iana.org"
WHOIS_PORT = 43
TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 8192


# Curated fallback list — used when no PHASE-supplied content.whois_domain_pool
# is present and the LLM picker fails / is bypassed (MCHP). Diverse mix of
# common workstation-relevant domains.
FALLBACK_DOMAINS = [
    "wikipedia.org", "github.com", "python.org", "ietf.org",
    "mozilla.org", "cloudflare.com", "google.com", "stackoverflow.com",
    "kernel.org", "ubuntu.com", "debian.org", "fedoraproject.org",
    "redhat.com", "apache.org", "nginx.org", "postgresql.org",
    "mysql.com", "mongodb.com", "docker.com", "nodejs.org",
    "rust-lang.org", "golang.org", "openssl.org", "torproject.org",
    "archlinux.org",
]


def whois_lookup(domain: str, timeout: float = TIMEOUT_SECONDS,
                 max_bytes: int = MAX_RESPONSE_BYTES) -> str:
    """Perform a WHOIS lookup against whois.iana.org over TCP/43.

    Returns the raw response text (truncated to max_bytes), or an
    "error: ..." string on socket failure. Always returns a string —
    never raises — so callers can log a single result line.
    """
    domain = (domain or "").strip()
    if not domain:
        return "whois_lookup error: empty domain"
    try:
        with socket.create_connection((WHOIS_HOST, WHOIS_PORT), timeout=timeout) as s:
            s.sendall(f"{domain}\r\n".encode("ascii", errors="replace"))
            chunks = []
            received = 0
            while received < max_bytes:
                data = s.recv(4096)
                if not data:
                    break
                chunks.append(data)
                received += len(data)
        return b"".join(chunks).decode("utf-8", errors="replace")[:2000]
    except OSError as e:
        return f"whois_lookup error: {type(e).__name__}: {e}"
