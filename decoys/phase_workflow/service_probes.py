"""Native background-service observations, scheduled by the existing executor.

No Brain, PHASE behavior plan, synthesized request, or foreground workflow.
The small in-memory timetable is not a new accepted workflow schema.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import threading
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from common.logging.agent_logger import AgentLogger
from .executor import DailyExecutor
from .loader import PlanEntry, PlanWindow
from .registry import WorkflowResult


SERVICES = {
    "ntp": ("systemd-timesyncd.service", None),
    "firmware": ("fwupd-refresh.service", "fwupd-refresh.timer"),
    "motd": ("motd-news.service", "motd-news.timer"),
}
CYCLES = {
    "ntp": ("observe", "observe", "observe", "disable", "enable", "observe"),
    "firmware": ("refresh", "refresh", "observe", "disable", "enable", "refresh"),
    "motd": ("cache_read", "retrieve", "cache_read", "disable", "enable", "retrieve"),
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def command(*argv):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=120,
                           env={**os.environ, "LC_ALL": "C", "SYSTEMD_PAGER": "cat"})
        return {"argv": list(argv), "rc": p.returncode,
                "stdout": p.stdout[-8192:], "stderr": p.stderr[-8192:]}
    except subprocess.TimeoutExpired:
        return {"argv": list(argv), "rc": None, "stdout": "", "stderr": "command timed out after 120 seconds"}


def properties(text):
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def network_counters():
    result = {}
    for line in Path('/proc/net/dev').read_text().splitlines()[2:]:
        name, values = line.split(':', 1)
        fields = values.split()
        result[name.strip()] = {"rx_bytes": int(fields[0]), "tx_bytes": int(fields[8])}
    return result


def network_delta(before, after):
    return {name: {key: after[name][key] - values[key]
                   if after[name][key] >= values[key] else None for key in values}
            for name, values in before.items() if name in after}


class ServiceObserver:
    def __init__(self, service, logger, *, run=command, counters=network_counters):
        self.service = service
        self.unit, self.timer = SERVICES[service]
        self.logger, self.run, self.counters = logger, run, counters
        self.previous_ntp = None
        self.previous_snapshot = None
        self.original = None

    def state(self, unit):
        result = self.run('systemctl', 'show', unit, '-p', 'LoadState', '-p', 'ActiveState',
                          '-p', 'UnitFileState', '-p', 'MainPID', '-p', 'Result', '-p', 'ExecMainStatus')
        return {**properties(result['stdout']), "query": result}

    def snapshot(self):
        states = {key: self.state(unit) for key, (unit, _) in SERVICES.items()}
        data = {"services": states, "network": self.counters(), "ntp": None}
        # Never D-Bus-activate timesyncd while observing its intentional off phase.
        if states['ntp'].get('ActiveState') == 'active':
            raw = self.run('timedatectl', 'show-timesync', '--all')
            status = self.run('timedatectl', 'timesync-status')
            text = raw['stdout']
            packet = re.search(r'PacketCount=(\d+)', text)
            stamp = re.search(r'DestinationTimestamp=([^,}]+)', text)
            offset = re.search(r'^\s*Offset:\s*(.+)$', status['stdout'], re.M)
            data['ntp'] = {"packet_count": int(packet[1]) if packet else None,
                           "destination_timestamp": stamp[1].strip() if stamp else None,
                           "offset": offset[1].strip() if offset else None,
                           "sample_accepted": raw['rc'] == 0 and packet is not None
                               and int(packet[1]) > 0 and 'Ignored=no' in text,
                           "pid": states['ntp'].get('MainPID'), "raw": raw, "status": status,
                           "offset_scope": "last received sample; not an independent current clock measurement"}
        data['timer'] = self.state(self.timer) if self.timer else None
        cache = Path('/var/cache/motd-news')
        if self.service == 'motd':
            data['cache'] = ({"size": cache.stat().st_size, "mtime_ns": cache.stat().st_mtime_ns}
                             if cache.exists() else None)
        return data

    def prepare(self):
        initial = self.snapshot()
        for name, state in initial['services'].items():
            if state.get('LoadState') != 'loaded':
                raise RuntimeError(f"required native service unavailable: {name}")
        self.original = initial
        self.previous_ntp = initial['ntp']
        self.previous_snapshot = (utc_now(), initial['network'])
        settings = self.run('systemctl', 'cat', *(unit for pair in SERVICES.values() for unit in pair if unit))
        self.logger.info('Background service initial startup', {
            "service": self.service, "utc_start": utc_now(), "snapshot": initial,
            "effective_settings": settings,
            "native_boot_history": self.run('journalctl', '-b', '-u', self.unit, '--no-pager', '-n', '40'),
            "settings": self.run('systemd-analyze', 'cat-config', 'systemd/timesyncd.conf')
                if self.service == 'ntp' else self.run('cat', '/etc/default/motd-news')
                if self.service == 'motd' else self.run('fwupdmgr', 'get-remotes', '--json'),
            "idle_difference": "only tested native timer paused; hourly probe triggers native service" if self.timer
                else "timesyncd unchanged except explicit hourly off/on conditions",
            "initial_boot_observation": "retrospective journal; capture may predate probe startup",
            "native_packages": self.run('dpkg-query', '-W', 'systemd-timesyncd', 'fwupd', 'motd-news-config'),
        })
        if self.timer:
            result = self.run('systemctl', 'stop', self.timer)
            if result['rc'] != 0:
                raise RuntimeError(f"cannot pause tested timer: {result}")
        # Reconcile the explicit one-hour off phase on restart, not missed actions.
        if datetime.now(timezone.utc).hour % 6 == 3:
            self.observe('disable', identity='startup_condition')
        elif self.service == 'ntp':
            self.observe('enable', identity='startup_condition')

    def resolve(self, entry, *, occurrence_id):
        return SimpleNamespace(operation=entry.resource_id, occurrence_id=occurrence_id)

    def execute(self, task, local_day):
        return WorkflowResult(completed=self.observe(task.operation, identity=f'{local_day}/{task.occurrence_id}'))

    def observe(self, operation, *, identity):
        start = utc_now()
        before = self.snapshot()
        results = []
        if operation == 'disable':
            results.append(self.run('systemctl', 'stop', self.unit))
        elif operation in {'enable', 'refresh', 'retrieve'}:
            results.append(self.run('systemctl', 'start', self.unit))
            if results[-1]['rc'] is None:
                # A CLI timeout must not silently leave an unbounded oneshot behind.
                results.append(self.run('systemctl', 'stop', self.unit))
        elif operation == 'cache_read':
            results.append(self.run('/etc/update-motd.d/50-motd-news'))
        elif operation != 'observe':
            raise ValueError(f'unknown observation operation: {operation}')
        after = self.snapshot()
        failed = any(result['rc'] != 0 for result in results)
        if operation == 'disable' and after['services'][self.service].get('ActiveState') not in {'inactive', 'failed'}:
            failed = True
        ntp = after['ntp']
        old = self.previous_ntp
        exchange = None
        delta = None
        if ntp is not None and old is not None:
            exchange = bool(ntp['sample_accepted'] and ntp['destination_timestamp']
                            and ntp['destination_timestamp'] != old['destination_timestamp'])
            if ntp['pid'] == old['pid'] and ntp['packet_count'] is not None and old['packet_count'] is not None:
                delta = ntp['packet_count'] - old['packet_count']
        if ntp is not None:
            self.previous_ntp = ntp
        state = after['services'][self.service]
        if self.service == 'ntp' and operation == 'enable' and state.get('ActiveState') != 'active':
            failed = True
        if operation in {'refresh', 'retrieve', 'enable'} and state.get('Result') not in {'success', None}:
            failed = True
        outcome = 'operation_failed' if failed else 'observed'
        if not failed and self.service == 'firmware' and operation in {'refresh', 'enable'}:
            outcome = 'cache_fresh_no_refresh' if state.get('ExecMainStatus') == '2' else 'native_refresh_completed'
        elif not failed and operation == 'cache_read':
            outcome = 'cache_only_no_retrieval_requested'
        elif not failed and self.service == 'motd' and operation in {'retrieve', 'enable'}:
            outcome = 'native_retrieval_finished_content_success_unverified'
        elif not failed and self.service == 'ntp':
            outcome = 'new_sync_sample' if exchange else 'no_new_sync_verified'
        previous = self.previous_snapshot
        end = utc_now()
        self.previous_snapshot = (end, after['network'])
        self.logger.info('Background service observation', {
            "service": self.service, "operation": operation, "occurrence_id": identity,
            "utc_start": start, "utc_end": end, "outcome": outcome,
            "commands": results, "before": before, "after": after,
            "new_ntp_exchange": exchange, "ntp_packet_delta": delta,
            "offset_while_off": "unavailable; retain last sample and compare next accepted sample" if ntp is None else None,
            "network_delta": network_delta(before['network'], after['network']),
            "between_observations": {"since_utc": previous[0],
                "network_delta": network_delta(previous[1], after['network'])} if previous else None,
            "network_scope": "whole VM during operation; not process attribution; zero is retained",
            "journal": self.run('journalctl', '-u', self.unit, '--since', start, '--no-pager', '-n', '40'),
        })
        return not failed

    def close(self):
        if self.original is None:
            return
        # Restore only the tested control changed by this observer.
        if self.timer:
            unit, active = self.timer, self.original['timer'].get('ActiveState') == 'active'
        else:
            unit, active = self.unit, self.original['services']['ntp'].get('ActiveState') == 'active'
        result = self.run('systemctl', 'start' if active else 'stop', unit)
        self.logger.info('Background service cleanup', {"service": self.service, "utc": utc_now(), "result": result})


def observation_plan(service):
    # Reuse FIFO, day rollover, missed-start and worker ownership machinery.
    # These entries never enter the workflow loader or Brain registry.
    windows = tuple(PlanWindow(h * 60, (h + 1) * 60,
                    (PlanEntry(0, service, CYCLES[service][h % 6], {}, None),)) for h in range(24))
    return SimpleNamespace(timezone=ZoneInfo('UTC'), max_parallel=1, windows=windows)


class ServiceProbeExecutor(DailyExecutor):
    def _terminal(self, occurrence, *, status, reason, actual_end, artifact=None):
        occurrence.state = status
        self._sink({"service": occurrence.entry.workflow, "operation": occurrence.entry.resource_id,
                    "occurrence_id": f'{occurrence.local_day}/w{occurrence.window_index}-s{occurrence.sequence_index}',
                    "scheduled_utc": self._iso(occurrence.scheduled_utc),
                    "utc_start": self._iso(occurrence.actual_start), "utc_end": self._iso(actual_end),
                    "status": status, "reason": 'operation_failed' if reason == 'workflow_failed' else reason})


def run_service_probe(service, *, stop_event=None):
    if os.environ.get('RUSE_DEPLOYMENT_TYPE') != 'probe' or service not in SERVICES:
        raise RuntimeError('background observations require explicit Probe installation')
    logger = AgentLogger(agent_type='scripted-cpu')
    logger.session_start(config={"deployment_type": "probe", "probe_service": service,
                                 "foreground_workflows": False, "timezone": "UTC"})
    observer = ServiceObserver(service, logger)
    executor = None
    try:
        observer.prepare()
        executor = ServiceProbeExecutor(observation_plan(service), observer,
            lambda event: logger.info('Background service terminal', event))
        executor.run_forever(stop_event or threading.Event())
    finally:
        try:
            if executor is not None:
                executor.close()
        finally:
            observer.close()
            logger.session_end()
