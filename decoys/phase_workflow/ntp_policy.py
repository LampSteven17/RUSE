"""Optional local-minute NTP policy; no workflow, daemon, or time client."""
from contextlib import contextmanager
from datetime import datetime, timezone
import os
import signal
import subprocess


def command(*args):
    # Same bounded native-command allowance as the existing service probes.
    result = subprocess.run(args, capture_output=True, text=True, timeout=120,
                            env={**os.environ, "LC_ALL": "C"})
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed ({result.returncode}): {result.stderr[-1024:]}")
    return result.stdout


class NtpPolicy:
    def __init__(self, intervals, timezone, logger, *, run=command,
                 now=lambda: datetime.now(timezone.utc)):
        self.intervals = intervals
        self.timezone = timezone
        self.logger = logger
        self.run = run
        self.now = now
        self.last_requested = None
        self.disabled_by_ruse = False

    def state(self):
        # timedated controls the installed native NTP service; do not install or
        # guess a competing client. NTP=yes is NOT proof of synchronization.
        raw = self.run("timedatectl", "show", "--property=CanNTP", "--property=NTP")
        state = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        if state.get("CanNTP") != "yes" or state.get("NTP") not in {"yes", "no"}:
            raise RuntimeError("No usable native NTP service reported by timedated")
        return state["NTP"] == "yes"

    def update(self, now):
        local = now.astimezone(self.timezone)
        minute = local.hour * 60 + local.minute
        enabled = not any(start <= minute < end for start, end in self.intervals)
        if enabled != self.last_requested:
            self._transition(enabled, now, "membership")
            self.last_requested = enabled

    def _transition(self, enabled, now, reason):
        details = {
            "service": "ntp", "operation": "enable" if enabled else "disable",
            "reason": reason, "timezone": str(self.timezone),
            "off_intervals_local": self.intervals,
            "requested_utc": now.isoformat(),
            "requested_local": now.astimezone(self.timezone).isoformat(),
            "synchronization_verified": False,
        }
        self.logger.info("NTP policy requested", details)
        try:
            before = self.state()
            if before != enabled:
                # Retain restoration ownership even if a stop partly succeeds
                # before the command/query raises. Never restore an already-off
                # service merely because this policy observed it.
                if not enabled:
                    self.disabled_by_ruse = True
                self.run("sudo", "-n", "timedatectl", "set-ntp", "true" if enabled else "false")
            after = self.state()
            if after != enabled:
                raise RuntimeError("Native NTP state did not reach requested state")
            if enabled:
                self.disabled_by_ruse = False
            self.logger.info("NTP policy result", {
                **details, "actual_utc": self.now().isoformat(),
                "outcome": "changed" if before != after else "already_in_state",
                "ntp_enabled": after,
            })
        except Exception as exc:
            self.logger.info("NTP policy failure", {
                **details, "actual_utc": self.now().isoformat(),
                "error_type": type(exc).__name__, "error": str(exc)[:1024],
            })
            raise

    def close(self):
        if self.disabled_by_ruse:
            self._transition(True, self.now(), "shutdown_restore")


@contextmanager
def ntp_lifecycle(policy):
    if policy is None:
        yield
        return
    # Only enabled-policy runtimes need graceful unwinding here. The logger's
    # normal SIGTERM re-raise would otherwise bypass the restoration finally.
    previous = {}

    def stop(signum, frame):
        raise KeyboardInterrupt

    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, stop)
        yield
    finally:
        try:
            policy.close()
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
