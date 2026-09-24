"""Optional local-minute NTP policy; no workflow, daemon, or time client."""
from contextlib import contextmanager
from datetime import datetime, timezone
import os
import signal
import subprocess
import time


def command(*args, timeout=120):
    # Same bounded native-command allowance as the existing service probes.
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, "LC_ALL": "C"})
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed ({result.returncode}): {result.stderr[-1024:]}")
    return result.stdout


class NtpPolicy:
    def __init__(self, intervals, timezone, logger, *, run=command,
                 now=lambda: datetime.now(timezone.utc), monotonic=time.monotonic,
                 sleep=time.sleep):
        self.intervals = intervals
        self.timezone = timezone
        self.logger = logger
        self.run = run
        self.now = now
        self.monotonic = monotonic
        self.sleep = sleep
        self.last_requested = None
        self.disabled_by_ruse = False
        self.last_observed_state = None

    def _run_before(self, deadline, *args):
        remaining = deadline - self.monotonic()
        if remaining <= 0:
            raise RuntimeError("Native NTP transition did not reach requested state before deadline")
        return self.run(*args, timeout=remaining)

    def state(self, deadline):
        # timedated controls the installed native NTP service; do not install or
        # guess a competing client. NTP=yes is NOT proof of synchronization.
        raw = self._run_before(deadline, "timedatectl", "show", "--property=CanNTP", "--property=NTP")
        state = {}
        for line in raw.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in {"CanNTP", "NTP"}:
                state[key] = value[:128]
        self.last_observed_state = state
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
        self.last_observed_state = None
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
            deadline = self.monotonic() + 120
            before = self.state(deadline)
            if before != enabled:
                # Retain restoration ownership even if a stop partly succeeds
                # before the command/query raises. Never restore an already-off
                # service merely because this policy observed it.
                if not enabled:
                    self.disabled_by_ruse = True
                self._run_before(deadline, "sudo", "-n", "timedatectl", "set-ntp", "true" if enabled else "false")
            after = self.state(deadline)
            while after != enabled:
                # timedated may return before its systemd job applies the request.
                remaining = deadline - self.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Native NTP transition did not reach requested state before deadline")
                self.sleep(min(1, remaining))
                after = self.state(deadline)
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
                "observed_ntp_state": self.last_observed_state,
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
