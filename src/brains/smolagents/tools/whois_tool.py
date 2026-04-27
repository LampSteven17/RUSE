"""WhoisLookupTool — TCP/43 WHOIS lookup for SmolAgents.

Adds a categorical port dimension (43/tcp) outside the default
{53/udp, 80/tcp, 123/udp, 443/tcp} mix that DDG + background services
produce. Real workstation traffic occasionally hits whois servers
(IT staff, registrars, security tools); fall24-style Zeek captures
have non-zero rows on port 43.

Raw socket implementation — no `whois` system package dependency,
so INSTALL_SUP.sh is unchanged.

NOTE: queries IANA's referral server only (whois.iana.org). The response
is the IANA referral block — registrar, creation date, and refer-to
TLD whois server — NOT the full registration record. This is sufficient
for the goal of producing a TCP/43 SF flow in conn.log. If downstream
ever needs full registrar details, chase the `refer:` line in the
response. Don't expect rich whois output from this tool.
"""
import socket

from smolagents import Tool


class WhoisLookupTool(Tool):
    """Look up WHOIS registration info for a domain over TCP/43."""

    name = "whois_lookup"
    description = (
        "Look up WHOIS registration info (registrar, creation date, name "
        "servers) for a domain. Useful when researching the provenance of "
        "a website or domain name."
    )
    inputs = {
        "domain": {
            "type": "string",
            "description": "Domain name to look up, e.g. 'example.com'",
        }
    }
    output_type = "string"

    # IANA's whois server delegates to the right TLD whois server in its
    # response body. Single-hop is enough for a categorical port-43 row in
    # Zeek; we don't need to chase the referral.
    WHOIS_HOST = "whois.iana.org"
    WHOIS_PORT = 43
    TIMEOUT_SECONDS = 5
    MAX_RESPONSE_BYTES = 8192

    def forward(self, domain: str) -> str:
        domain = (domain or "").strip()
        if not domain:
            return "whois_lookup error: empty domain"
        try:
            with socket.create_connection(
                (self.WHOIS_HOST, self.WHOIS_PORT),
                timeout=self.TIMEOUT_SECONDS,
            ) as s:
                s.sendall(f"{domain}\r\n".encode("ascii", errors="replace"))
                chunks = []
                received = 0
                while received < self.MAX_RESPONSE_BYTES:
                    data = s.recv(4096)
                    if not data:
                        break
                    chunks.append(data)
                    received += len(data)
            text = b"".join(chunks).decode("utf-8", errors="replace")
            # Truncate so the LLM context stays manageable
            return text[:2000]
        except OSError as e:
            return f"whois_lookup error: {type(e).__name__}: {e}"
