"""OutboundConnSampler — OS-level outbound TCP connection sampling from /proc.

Why this exists: the SUP jsonl logs application semantics (workflows/steps/LLM
calls), and one "step" buys wildly different amounts of wire traffic per brain
(a BrowserUse `navigate` = a full page load with dozens of sub-resource conns;
an MCHP step = one local micro-action). Workflow/step frequency therefore is
NOT a usable proxy for emitted traffic. This sampler gives each SUP a real,
self-reported per-minute connection volume so the jsonl stops misleading.

Signals (read from /proc, pure stdlib, no subprocess, no root):
  - active_opens: delta of `Tcp: ActiveOpens` from /proc/net/snmp since the last
    sample = outbound TCP connections opened in the window. This counts the
    short-lived sub-resource connections a socket snapshot would miss. Caveat:
    ActiveOpens is global and includes loopback (Ollama) opens; httpx keep-alive
    keeps that minor, but treat it as "volume incl. small loopback noise".
  - distinct_remote_hosts: distinct non-loopback remote peer IPs currently/recently
    in /proc/net/tcp{,6} (loopback-excluded; a clean external floor, undercounts
    very short-lived peers seen only between samples).
  - window_s: actual elapsed seconds since the previous sample.

Fully defensive: any read/parse failure yields None for that field and never
raises — telemetry must never crash-loop the experiment.
"""
import time

_SNMP = "/proc/net/snmp"
_TCP4 = "/proc/net/tcp"
_TCP6 = "/proc/net/tcp6"
_V6_ALLZERO = "0" * 32
_V6_LOOPBACK = "00000000000000000000000001000000"  # ::1


class OutboundConnSampler:
    """Stateful per-minute outbound-connection sampler. Construct once; call
    sample() on each minute roll."""

    def __init__(self):
        self._last_active_opens = self._read_active_opens()
        self._last_ts = time.time()

    @staticmethod
    def _read_active_opens():
        """Return cumulative Tcp:ActiveOpens, or None on any failure."""
        try:
            with open(_SNMP) as f:
                lines = f.readlines()
            for i in range(len(lines) - 1):
                if lines[i].startswith("Tcp:") and lines[i + 1].startswith("Tcp:"):
                    keys = lines[i].split()
                    vals = lines[i + 1].split()
                    idx = keys.index("ActiveOpens")
                    return int(vals[idx])
        except Exception:
            return None
        return None

    @staticmethod
    def _distinct_remote_hosts():
        """Count distinct non-loopback remote peer IPs in /proc/net/tcp{,6}.
        Returns an int, or None if neither file could be read."""
        hosts = set()
        read_any = False
        for path in (_TCP4, _TCP6):
            try:
                with open(path) as f:
                    next(f, None)  # header
                    for line in f:
                        parts = line.split()
                        if len(parts) < 4:
                            continue
                        rem = parts[2]
                        st = parts[3]
                        if st == "0A":  # LISTEN — no remote peer
                            continue
                        ip_hex = rem.split(":")[0]
                        if path is _TCP4:
                            if len(ip_hex) != 8:
                                continue
                            # /proc stores the addr little-endian; octet 1 is the
                            # last hex byte. 127/8 → first octet 127.
                            try:
                                if int(ip_hex[6:8], 16) == 127:
                                    continue  # loopback
                                if int(ip_hex, 16) == 0:
                                    continue  # 0.0.0.0
                            except ValueError:
                                continue
                        else:
                            if ip_hex in (_V6_ALLZERO, _V6_LOOPBACK):
                                continue
                        hosts.add(ip_hex)
                read_any = True
            except Exception:
                continue
        return len(hosts) if read_any else None

    def sample(self):
        """Return {active_opens, distinct_hosts, window_s} for the window since
        the previous sample. Fields are None when unavailable. Never raises."""
        now = time.time()
        window_s = now - self._last_ts
        cur = self._read_active_opens()
        opens = None
        if (cur is not None and self._last_active_opens is not None
                and cur >= self._last_active_opens):
            opens = cur - self._last_active_opens
        # Counter reset (reboot) or first read: skip delta, just re-baseline.
        if cur is not None:
            self._last_active_opens = cur
        self._last_ts = now
        return {
            "active_opens": opens,
            "distinct_hosts": self._distinct_remote_hosts(),
            "window_s": round(window_s, 1),
        }
