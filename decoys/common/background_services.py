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

    # Safety cap on how aggressively deficit-burst can overshoot the
    # PHASE-emitted target. Going over is acceptable per spec ("median
    # conn/min during ON ≥ 1000"), but a runaway overshoot would skew
    # Zeek volume far past human peak. 1.5x of target is the ceiling.
    _DEFICIT_BURST_OVERSHOOT_CAP = 1.5
    # Per-minute conn-count log emitted to systemd.log so the audit
    # `Vol` column can scrape median conn/min during ON-windows from
    # a self-reported counter without needing Zeek crosscheck.
    _COUNTER_LOG_PREFIX = "[bg-counter]"

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
        # Window-mode deficit-burst state (PHASE 2026-05-08).
        # Inside a window, top up bg-service rate to
        # `_volume_target` conn/min. emulation_loop pushes state at each
        # cluster boundary via set_window_state(); when False, behaves
        # exactly as the legacy hour-rate generator.
        self._in_window = False
        self._volume_target = None
        # Per-minute counter — every call to _do_dns/http/ntp increments.
        # Reset + logged when the UTC minute rolls. Logged value feeds
        # the audit Vol column. Cheap-and-dirty: counts only bg-service
        # conns, not brain workflow conns; tendency is to undercount, so
        # going-over the target is preferred over false alarms.
        self._minute_conn_count = 0
        # Stamp at minute granularity to detect rollover.
        now = datetime.now(timezone.utc)
        self._last_minute_stamp = (now.hour, now.minute)

    def _reset_hourly(self):
        """Reset hourly counters if hour changed (UTC). Also rolls the
        per-minute counter when the UTC minute changes — emits a single
        line to stdout (systemd.log) summarizing the just-elapsed minute
        so the audit Vol column can grep median conn/min."""
        now = datetime.now(timezone.utc)
        cur_minute = (now.hour, now.minute)
        if cur_minute != self._last_minute_stamp:
            # Roll: emit count for the just-elapsed minute so audit can
            # scrape median conn/min during ON-windows.
            in_win = "1" if self._in_window else "0"
            tgt = (f"{self._volume_target:.0f}"
                   if self._volume_target else "-")
            print(f"{self._COUNTER_LOG_PREFIX} "
                  f"minute={self._last_minute_stamp[0]:02d}:"
                  f"{self._last_minute_stamp[1]:02d} "
                  f"conns={self._minute_conn_count} "
                  f"in_window={in_win} target={tgt}",
                  flush=True)
            self._minute_conn_count = 0
            self._last_minute_stamp = cur_minute
        if now.hour != self._last_hour:
            self._dns_count_this_hour = 0
            self._http_count_this_hour = 0
            self._last_hour = now.hour
        if now.day != self._last_day:
            self._ntp_count_today = 0
            self._last_day = now.day

    def set_window_state(self, in_window: bool,
                         volume_target: float = None):
        """Push window-mode state from emulation_loop at cluster
        boundaries. When in_window is True and a positive volume_target
        is given, maybe_generate() will deficit-burst extra probes to
        approach `volume_target` bg-conns/minute (cap at 1.5x).

        Outside windows or with no target, behaves as the legacy
        hour-rate generator (no burst)."""
        self._in_window = bool(in_window)
        self._volume_target = (float(volume_target)
                               if volume_target and volume_target > 0
                               else None)

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

        # Deficit-burst (PHASE 2026-05-08). Inside a window with a target
        # set, top up to ~target conn/min, capped at target * 1.5. Issues
        # a small batch (max 8 per call) so per-task latency stays bounded
        # even if maybe_generate runs once between long workflows. Caller
        # is expected to invoke us multiple times per minute via inter-
        # task-delay loops; over a minute we converge on target.
        if self._in_window and self._volume_target:
            cap = self._volume_target * self._DEFICIT_BURST_OVERSHOOT_CAP
            deficit = self._volume_target - self._minute_conn_count
            if deficit > 0 and self._minute_conn_count < cap:
                # How many probes to emit this call. Keep small — the
                # generator is called many times per minute.
                burst_n = min(int(deficit / 4) + 1, 8)
                for _ in range(burst_n):
                    if self._minute_conn_count >= cap:
                        break
                    # Random split: 70% DNS (cheap, sub-100ms), 30% HTTP
                    # HEAD. Skip NTP in burst (it's daily-budgeted).
                    if random.random() < 0.7:
                        self._do_dns_lookup(random.choice(BACKGROUND_DOMAINS))
                    else:
                        self._do_http_head(random.choice(BACKGROUND_URLS))
                    actions += 1

        return actions

    def _do_dns_lookup(self, domain: str):
        """Perform a DNS lookup (creates DNS service connection in Zeek)."""
        try:
            socket.getaddrinfo(domain, None)
            self._minute_conn_count += 1
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
            self._minute_conn_count += 1
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
            self._minute_conn_count += 1
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
