"""
Background service generator — injects DNS, NTP, HTTP HEAD traffic
between workflow tasks to increase service diversity.

Config comes from diversity_injection.json["background_services"].
When config is absent or empty, this module does nothing.
"""
import socket
import random
import time
from datetime import datetime, timezone


# Common domains for background DNS lookups (CDN, OS updates, services)
BACKGROUND_DOMAINS = [
    "time.google.com", "ntp.ubuntu.com", "pool.ntp.org",
    "connectivity-check.ubuntu.com", "detectportal.firefox.com",
    "ocsp.digicert.com", "crl.microsoft.com",
    "dns.google", "cloudflare-dns.com",
    "api.github.com", "registry.npmjs.org",
]

# Common URLs for HTTP HEAD requests (lightweight, common services).
# Diversified 2026-04-27: original 3 URLs gave http_head_per_hour traffic only
# 3 destinations — Zeek-detectable signature. Real workstations emit
# captive-portal probes + lightweight HTTP/80 fetches across many hosts.
# All HTTP/80 (knob is named http_head_per_hour); the original lone HTTPS
# entry was spurious and removed (HTTPS connectivity probes go through
# DDG/visit_webpage, not the http_head_per_hour rate).
BACKGROUND_URLS = [
    "http://connectivity-check.ubuntu.com",
    "http://detectportal.firefox.com/canonical.html",
    "http://www.msftconnecttest.com/connecttest.txt",
    "http://www.gstatic.com/generate_204",
    "http://clients3.google.com/generate_204",
    "http://nmcheck.gnome.org/check_network_status.txt",
    "http://captive.apple.com",
    "http://neverssl.com",
    "http://example.com",
    "http://httpbin.org/get",
    "http://info.cern.ch",
]


class BackgroundServiceGenerator:
    """Generates background network traffic between workflow tasks."""

    def __init__(self, config: dict = None, logger=None):
        self._config = config or {}
        self._logger = logger
        self._dns_per_hour = self._config.get("dns_per_hour", [0] * 24)
        self._ntp_per_day = self._config.get("ntp_checks_per_day", 0)
        self._http_per_hour = self._config.get("http_head_per_hour", [0] * 24)
        self._enabled = bool(self._config)
        self._dns_count_this_hour = 0
        self._http_count_this_hour = 0
        self._ntp_count_today = 0
        # UTC: dns_per_hour / http_head_per_hour are PHASE-indexed in UTC
        self._last_hour = datetime.now(timezone.utc).hour
        self._last_day = datetime.now(timezone.utc).day

    def _reset_hourly(self):
        """Reset hourly counters if hour changed (UTC)."""
        now = datetime.now(timezone.utc)
        if now.hour != self._last_hour:
            self._dns_count_this_hour = 0
            self._http_count_this_hour = 0
            self._last_hour = now.hour
        if now.day != self._last_day:
            self._ntp_count_today = 0
            self._last_day = now.day

    def maybe_generate(self):
        """
        Probabilistically generate background traffic.
        Call between workflow tasks. Returns number of background actions taken.
        """
        if not self._enabled:
            return 0

        self._reset_hourly()
        hour = datetime.now(timezone.utc).hour
        actions = 0

        # DNS lookups
        dns_target = self._dns_per_hour[hour] if hour < len(self._dns_per_hour) else 0
        if dns_target > 0 and self._dns_count_this_hour < dns_target:
            if random.random() < dns_target / 30.0:
                domain = random.choice(BACKGROUND_DOMAINS)
                self._do_dns_lookup(domain)
                self._dns_count_this_hour += 1
                actions += 1

        # HTTP HEAD
        http_target = self._http_per_hour[hour] if hour < len(self._http_per_hour) else 0
        if http_target > 0 and self._http_count_this_hour < http_target:
            if random.random() < http_target / 30.0:
                url = random.choice(BACKGROUND_URLS)
                self._do_http_head(url)
                self._http_count_this_hour += 1
                actions += 1

        # NTP (very occasional)
        if self._ntp_per_day > 0 and self._ntp_count_today < self._ntp_per_day:
            if random.random() < self._ntp_per_day / (24 * 30.0):
                self._do_ntp_check()
                self._ntp_count_today += 1
                actions += 1

        return actions

    def _do_dns_lookup(self, domain: str):
        """Perform a DNS lookup (creates DNS service connection in Zeek)."""
        try:
            socket.getaddrinfo(domain, None)
            if self._logger:
                self._logger.debug(f"[background] DNS lookup: {domain}")
        except Exception:
            pass

    def _do_http_head(self, url: str):
        """Perform an HTTP HEAD request (creates http/ssl service connections)."""
        try:
            import urllib.request
            req = urllib.request.Request(url, method='HEAD')
            req.add_header('User-Agent', 'Mozilla/5.0')
            urllib.request.urlopen(req, timeout=5)
            if self._logger:
                self._logger.debug(f"[background] HTTP HEAD: {url}")
        except Exception:
            pass

    def _do_ntp_check(self):
        """Perform NTP time check (creates UDP/NTP connection in Zeek)."""
        try:
            ntp_server = random.choice(["pool.ntp.org", "time.google.com", "ntp.ubuntu.com"])
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(3)
            # Minimal NTP request (version 3, mode 3 = client)
            data = b'\x1b' + 47 * b'\0'
            sock.sendto(data, (ntp_server, 123))
            try:
                sock.recv(1024)
            except socket.timeout:
                pass
            sock.close()
            if self._logger:
                self._logger.debug(f"[background] NTP check: {ntp_server}")
        except Exception:
            pass

    def update_config(self, config: dict):
        """Hot-update background service config."""
        self._config = config or {}
        self._dns_per_hour = self._config.get("dns_per_hour", [0] * 24)
        self._ntp_per_day = self._config.get("ntp_checks_per_day", 0)
        self._http_per_hour = self._config.get("http_head_per_hour", [0] * 24)
        self._enabled = bool(self._config)
