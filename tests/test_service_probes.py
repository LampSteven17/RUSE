"""Native operations are mocked here; no network/service changes from tests."""
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import subprocess
import shutil
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tempfile
import unittest

import yaml

from deployment_engine import __main__ as cli
from deployment_engine.core.config import DeploymentConfig
from deployment_engine.core.plan import build_probe_plan
from phase_workflow.service_probes import (
    CYCLES, SERVICES, ServiceObserver, ServiceProbeExecutor, command,
    observation_plan, network_delta, run_service_probe,
)
from phase_workflow.registry import WorkflowResult

ROOT = Path(__file__).resolve().parents[1]


class NativeSystem:
    def __init__(self):
        self.active = {u: ('active' if u == 'systemd-timesyncd.service' or u.endswith('.timer') else 'inactive')
                       for pair in SERVICES.values() for u in pair if u}
        self.calls = []
        self.packet_count = 5
        self.stamp = 'Fri 2026-09-18 12:00:00 UTC'
        self.failure = None

    def __call__(self, *argv):
        self.calls.append(argv)
        rc, text = 0, ''
        if argv[:2] == ('systemctl', 'show'):
            text = f'LoadState=loaded\nActiveState={self.active[argv[2]]}\nUnitFileState=enabled\nMainPID=123\nResult=success\nExecMainStatus=2\n'
        elif argv[:2] in [('systemctl', 'start'), ('systemctl', 'stop')]:
            if self.failure is not None and argv[1] == 'start':
                rc = self.failure
            else:
                self.active[argv[2]] = 'active' if argv[1] == 'start' and argv[2] == 'systemd-timesyncd.service' else 'inactive'
        elif argv[:2] == ('timedatectl', 'show-timesync'):
            if self.active['systemd-timesyncd.service'] != 'active':
                raise AssertionError('off observation must not D-Bus activate timesyncd')
            text = f'NTPMessage={{ DestinationTimestamp={self.stamp}, Ignored=no, PacketCount={self.packet_count}, Jitter=1ms }}'
        elif argv[:2] == ('timedatectl', 'timesync-status'):
            text = 'Offset: +2.5ms\n'
        return dict(argv=list(argv), rc=rc, stdout=text, stderr='failure' if rc else '')


class ServiceProbeTests(unittest.TestCase):
    def observer(self, name):
        native, logger = NativeSystem(), Mock()
        obj = ServiceObserver(name, logger, run=native, counters=lambda: {'ens3': {'rx_bytes': 0, 'tx_bytes': 0}})
        return obj, native, logger

    def test_selection_only_three_cpu_no_workflow_or_sidecar(self):
        tasks = build_probe_plan(ROOT / 'deployments', background_services=True)
        self.assertEqual({t['label'] for t in tasks}, {'probe-ntp', 'probe-firmware', 'probe-motd'})
        for task in tasks:
            self.assertFalse(task['share_required'])
            self.assertIsNone(task['probe_workflow'])
            self.assertIsNone(task['behavior_source'])
            self.assertEqual((task['purpose'], task['target']), ('other', None))
            self.assertEqual(task['deployments'], [{'behavior': 'scripted-cpu', 'flavor': 'v1.14vcpu.28g', 'count': 1}])

    def test_old_default_and_mchp_seven_unchanged(self):
        for sup in ('scripted-cpu', 'mchp-cpu'):
            tasks = build_probe_plan(ROOT / 'deployments', sup_config=sup)
            self.assertEqual(len(tasks), 7)
            self.assertFalse(any(t['probe_service'] for t in tasks))
            self.assertEqual(sum(t['share_required'] for t in tasks), 1)

    def test_cli_exact_command_only_background_combined_plan(self):
        with patch('deployment_engine.core.plan.show_plan_and_confirm', return_value=False) as show, patch('deployment_engine.core.plan.execute_plan') as execute:
            self.assertEqual(cli._cmd_deploy(['--probes', '--background-services']), 0)
        self.assertEqual(len(show.call_args.args[0]), 3)
        execute.assert_not_called()

    def test_cli_rejects_wrong_scope_and_brain(self):
        for args in (['--background-services'], ['--probes', '--background-services', '--probe-sup', 'mchp-cpu'], ['--probes', '--background-services', '--decoys']):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                cli._cmd_deploy(args)

    def test_explicit_background_requires_selector(self):
        with self.assertRaises(ValueError):
            build_probe_plan(ROOT / 'deployments', 'probe-ntp')

    def test_invalid_service_or_foreground_rejected(self):
        raw = yaml.safe_load((ROOT / 'deployments/probe-ntp/config.yaml').read_text())
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'config.yaml'
            for mutation in ({'probe_service': 'other'}, {'probe_workflow': 'WebResearch'}, {'type': 'decoy'}, {'behavior_source': '/tmp/plans'}):
                path.write_text(yaml.safe_dump({**raw, **mutation}))
                with self.assertRaises(ValueError):
                    DeploymentConfig.load(path)

    def test_hourly_cycle_one_hour_off_four_repeats_utc(self):
        for service in SERVICES:
            plan = observation_plan(service)
            self.assertEqual(str(plan.timezone), 'UTC')
            self.assertEqual(plan.max_parallel, 1)
            self.assertEqual(len(plan.windows), 24)
            operations = [w.sequence[0].resource_id for w in plan.windows]
            self.assertEqual(operations, list(CYCLES[service]) * 4)
            self.assertEqual([i for i, o in enumerate(operations) if o == 'disable'], [3, 9, 15, 21])
            self.assertEqual([i for i, o in enumerate(operations) if o == 'enable'], [4, 10, 16, 22])

    def test_native_motd_retrieve_and_separate_cache_read(self):
        obj, native, logger = self.observer('motd')
        self.assertTrue(obj.observe('retrieve', identity='day/w1-s0'))
        self.assertIn(('systemctl', 'start', 'motd-news.service'), native.calls)
        self.assertIn('unverified', logger.info.call_args.args[1]['outcome'])
        native.calls.clear()
        obj.observe('cache_read', identity='day/w2-s0')
        self.assertIn(('/etc/update-motd.d/50-motd-news',), native.calls)
        self.assertNotIn(('systemctl', 'start', 'motd-news.service'), native.calls)
        event = logger.info.call_args.args[1]
        self.assertEqual(event['outcome'], 'cache_only_no_retrieval_requested')
        self.assertEqual(event['network_delta']['ens3'], {'rx_bytes': 0, 'tx_bytes': 0})
        self.assertIn('not process attribution', event['network_scope'])

    def test_firmware_cache_exit_two_is_not_failure_no_force(self):
        obj, native, logger = self.observer('firmware')
        self.assertTrue(obj.observe('refresh', identity='test'))
        self.assertIn(('systemctl', 'start', 'fwupd-refresh.service'), native.calls)
        self.assertFalse(any('--force' in call for call in native.calls))
        self.assertEqual(logger.info.call_args.args[1]['outcome'], 'cache_fresh_no_refresh')

    def test_ntp_no_new_exchange_and_real_sample_delta(self):
        obj, native, logger = self.observer('ntp')
        obj.previous_ntp = obj.snapshot()['ntp']
        obj.observe('observe', identity='hour0')
        self.assertFalse(logger.info.call_args.args[1]['new_ntp_exchange'])
        self.assertEqual(logger.info.call_args.args[1]['ntp_packet_delta'], 0)
        native.packet_count += 1
        native.stamp = 'Fri 2026-09-18 12:34:08 UTC'
        obj.observe('observe', identity='hour1')
        event = logger.info.call_args.args[1]
        self.assertTrue(event['new_ntp_exchange'])
        self.assertEqual(event['ntp_packet_delta'], 1)
        self.assertEqual(event['after']['ntp']['offset'], '+2.5ms')
        self.assertNotIn(('systemctl', 'start', 'systemd-timesyncd.service'), native.calls)

    def test_ntp_disabled_observation_no_activation_or_fresh_offset_claim(self):
        obj, native, logger = self.observer('ntp')
        obj.observe('disable', identity='off')
        native.calls.clear()
        obj.observe('observe', identity='off-observation')
        self.assertFalse(any(call[0] == 'timedatectl' for call in native.calls))
        self.assertIsNone(logger.info.call_args.args[1]['after']['ntp'])
        self.assertIn('unavailable', logger.info.call_args.args[1]['offset_while_off'])
        obj.observe('enable', identity='on')
        self.assertEqual(native.active['systemd-timesyncd.service'], 'active')

    def test_ntp_zero_packet_initial_message_is_not_sync(self):
        obj, native, logger = self.observer('ntp')
        obj.previous_ntp = obj.snapshot()['ntp']
        native.packet_count = 0
        native.stamp = 'Thu 1970-01-01 00:00:00 UTC'
        obj.observe('observe', identity='empty-message')
        self.assertFalse(logger.info.call_args.args[1]['new_ntp_exchange'])

    def test_failures_and_timeout_retained_with_owned_stop(self):
        obj, native, logger = self.observer('motd')
        native.failure = 1
        self.assertFalse(obj.observe('retrieve', identity='bad'))
        self.assertEqual(logger.info.call_args.args[1]['outcome'], 'operation_failed')
        def timeout(*args):
            result = native(*args)
            if args[:2] == ('systemctl', 'start'):
                result['rc'] = None
            return result
        obj.run = timeout
        self.assertFalse(obj.observe('retrieve', identity='timeout'))
        self.assertIn(('systemctl', 'stop', 'motd-news.service'), native.calls)

    def test_executor_reuses_ownership_and_logs_no_workflow_terminal(self):
        obj, native, logger = self.observer('motd')
        clock = SimpleNamespace(now=lambda: datetime(2026, 9, 18, 1, tzinfo=timezone.utc))
        handle = Future()
        sink, starter = Mock(), Mock(return_value=handle)
        executor = ServiceProbeExecutor(observation_plan('motd'), obj, sink, clock=clock, starter=starter)
        executor.tick()
        self.assertEqual(executor.active_count, 1)
        handle.set_result(WorkflowResult(completed=True))
        executor.tick()
        event = sink.call_args.args[0]
        self.assertEqual(event['operation'], 'retrieve')
        self.assertEqual(event['status'], 'completed')
        self.assertNotIn('workflow', event)
        self.assertEqual(executor.active_count, 0)

    def test_installer_packages_and_dispatch_without_plans(self):
        source = (ROOT / 'INSTALL_SUP.sh').read_text()
        self.assertIn('--probe-service=$RUSE_PROBE_SERVICE', source)
        self.assertIn('"$RUSE_PROBE_WORKFLOW" == idle', source)
        play = yaml.safe_load((ROOT / 'deployment_engine/playbooks/decoy/install-sups.yaml').read_text())[0]
        install = next(t for t in play['tasks'] if t['name'].startswith('Stage 2:'))
        self.assertIn('RUSE_PROBE_SERVICE=', install['shell'])
        self.assertEqual(play['vars']['probe_service'], '')
        verify = next(t for t in play['tasks'] if t['name'] == 'Verify native background probe services before runtime startup')
        self.assertEqual(verify['when'], "probe_install and probe_service != ''")
        self.assertEqual(verify['loop'], [x[0] for x in SERVICES.values()])
        names = [t['name'] for t in play['tasks']]
        self.assertLess(names.index(verify['name']), names.index('Start canonical workflow service after Stage 2'))
        config = DeploymentConfig.load(ROOT / 'deployments/probe-ntp/config.yaml')
        from deployment_engine.decoy.spinup import _validate_behavior_source
        self.assertEqual(_validate_behavior_source(None, config), [])

    def test_runtime_rejects_outside_probe_category(self):
        with patch.dict('os.environ', {'RUSE_DEPLOYMENT_TYPE': 'decoy'}):
            with self.assertRaises(RuntimeError):
                run_service_probe('ntp')

    def test_counter_reset_is_not_negative_traffic(self):
        self.assertIsNone(network_delta({'eth0': {'rx_bytes': 10}}, {'eth0': {'rx_bytes': 2}})['eth0']['rx_bytes'])

    def test_only_tested_timer_paused_and_restored(self):
        obj, native, logger = self.observer('firmware')
        obj.prepare()
        self.assertIn(('systemctl', 'stop', 'fwupd-refresh.timer'), native.calls)
        self.assertNotIn(('systemctl', 'stop', 'motd-news.timer'), native.calls)
        self.assertNotIn(('systemctl', 'stop', 'systemd-timesyncd.service'), native.calls)
        self.assertEqual(logger.info.call_args_list[0].args[0], 'Background service initial startup')
        obj.close()
        self.assertIn(('systemctl', 'start', 'fwupd-refresh.timer'), native.calls)

    def test_missing_native_service_fails_without_operations(self):
        obj, native, logger = self.observer('ntp')
        def unavailable(*argv):
            result = native(*argv)
            if argv[:3] == ('systemctl', 'show', 'motd-news.service'):
                result['stdout'] = 'LoadState=not-found\n'
            return result
        obj.run = unavailable
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            obj.prepare()
        self.assertFalse(any(c[:2] in [('systemctl', 'stop'), ('systemctl', 'start')] for c in native.calls))

    def test_background_spinup_wiring_and_probe_category(self):
        # The real spinup's probe branch passes the service selector; lifecycle
        # exclusion is config-driven, never a deployment-name heuristic.
        source = (ROOT / 'deployment_engine/decoy/spinup.py').read_text()
        self.assertIn('"probe_service": config.probe_service or ""', source)
        for service in SERVICES:
            config = DeploymentConfig.load(ROOT / f'deployments/probe-{service}/config.yaml')
            self.assertTrue(config.is_probe())
            self.assertEqual(config.probe_service, service)

    def test_installed_tree_has_background_runner_no_behavior_plan(self):
        for service in SERVICES:
            with self.subTest(service=service), tempfile.TemporaryDirectory() as td:
                env = {**os.environ, 'RUSE_DEPLOYMENT_TYPE': 'probe', 'RUSE_PROBE_WORKFLOW': 'idle',
                       'RUSE_PROBE_STARTED_AT': '2026-09-18T12:00:00+00:00', 'RUSE_PROBE_SERVICE': service}
                result = subprocess.run(['bash', '-c',
                    'source ./INSTALL_SUP.sh; parse_config_key scripted-cpu; copy_source_code "$1"; create_run_script "$1"',
                    'test-service-probe', td], cwd=ROOT, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                script = (Path(td) / 'run_agent.sh').read_text()
                self.assertIn(f'--probe-service={service}', script)
                self.assertTrue((Path(td) / 'decoys/phase_workflow/service_probes.py').exists())
                self.assertEqual(list((Path(td) / 'behavioral_configurations').iterdir()), [])

    def test_decoy_teardown_excludes_background_probes(self):
        from deployment_engine import teardown
        from deployment_engine.core.vm_naming import make_run_dep_id, make_vm_prefix
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            statuses = {}
            for service in SERVICES:
                name = f'probe-{service}'
                directory = root / name
                directory.mkdir()
                shutil.copy(ROOT / 'deployments' / name / 'config.yaml', directory)
                run = directory / 'runs/2026-09-18_120000Z'
                run.mkdir(parents=True)
                statuses[make_vm_prefix(make_run_dep_id(name, run.name)) + 'scripted-cpu-0'] = 'ACTIVE'
            cloud = Mock()
            cloud.server_status_map.return_value = statuses
            with patch.object(teardown, 'OpenStack', return_value=cloud), patch.object(teardown.output, 'confirm') as confirm:
                teardown.run_teardown_filtered(root, types={'decoy': True})
                confirm.assert_not_called()


if __name__ == '__main__':
    unittest.main()
