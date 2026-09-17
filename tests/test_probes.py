"""Traffic-free Probe category, packaging, rotation and lifecycle regressions."""

import ast
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import redirect_stderr
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

from deployment_engine import __main__ as cli
from deployment_engine import list as listing
from deployment_engine import teardown
from deployment_engine.core.config import DeploymentConfig
from deployment_engine.core.plan import build_probe_plan
from deployment_engine.core.phase_run_registry import create_deployment
from deployment_engine.core.run_status import FAILED, write_run_status
from deployment_engine.core.vm_naming import make_run_dep_id, make_vm_prefix
from deployment_engine.decoy import spinup
from phase_workflow.loader import WorkflowPlanError
from phase_workflow.probes import PROBE_RESOURCES, probe_day_index, validate_probe_plans
from phase_workflow.registry import WorkflowRegistry, WorkflowResult
from phase_workflow.runtime import ProbeExecutor, run_probe_runtime
from phase_workflow.workflows import OpenDocumentWriter, validate_open_document


ROOT = Path(__file__).resolve().parents[1]
PHASE = Path('/home/ubuntu/PHASE/plans/probes')
DIRS = dict(zip(PROBE_RESOURCES, ('research', 'video', 'download', 'upload', 'documents', 'share')))
RUN = '2026-09-15_120000Z'
START = '2026-09-15T12:00:00+00:00'


class ProbeTests(unittest.TestCase):
    def test_all_27_authoritative_variants_real_loader_and_counts(self):
        count = 0
        for workflow, resources in PROBE_RESOURCES.items():
            plans = validate_probe_plans(PHASE / DIRS[workflow], workflow)
            self.assertEqual(len(plans), len(resources))
            for resource, plan in zip(resources, plans):
                count += 1
                entries = [entry for window in plan.windows for entry in window.sequence]
                self.assertEqual(len(entries), 115)
                self.assertEqual({entry.resource_id for entry in entries}, {resource})
                self.assertEqual({entry.workflow for entry in entries}, {workflow})
                self.assertTrue(all(entry.instruction is None for entry in entries))
                self.assertEqual([len(w.sequence) for w in plan.windows], [(1, 2, 4, 8, 10)[h % 5] for h in range(24)])
        self.assertEqual(count, 27)

    def test_seven_cpu_tasks_exactly_one_conditional_share_no_provisioning(self):
        tasks = build_probe_plan(ROOT / 'deployments')
        self.assertEqual(len(tasks), 7)
        self.assertEqual(sum(t['share_required'] for t in tasks), 1)
        self.assertEqual(next(t['probe_workflow'] for t in tasks if t['share_required']), 'NetworkShareAccess')
        for task in tasks:
            self.assertEqual(task['purpose'], 'other')
            self.assertIsNone(task['target'])
            self.assertEqual(task['deployments'], [{'behavior': 'scripted-cpu', 'flavor': 'v1.14vcpu.28g', 'count': 1}])
        self.assertEqual(6 * 115, 690)

    def test_spinup_reuses_provisioning_installs_variants_then_registers_probe(self):
        for name in ('probe-idle', 'probe-research', 'probe-network-share',
                     'probe-mchp-idle', 'probe-mchp-research', 'probe-mchp-network-share'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                sup = 'mchp-cpu' if name.startswith('probe-mchp-') else 'scripted-cpu'
                is_idle = name.endswith('-idle')
                is_share = name.endswith('-network-share')
                root = Path(td)
                config_dir = root / name
                shutil.copytree(ROOT / 'deployments' / name, config_dir)
                (root / 'hosts.ini').write_text('[openstack_controller]\n')
                calls = []
                def playbook(play, inventory, *, extra_vars, **kwargs):
                    calls.append((play, extra_vars))
                    run = Path(extra_vars['run_dir'])
                    if play == 'shared/provision-vms.yaml':
                        (run / 'inventory.ini').write_text('[sup_hosts]\n' + extra_vars['vm_prefix'] + f'{sup}-0 ansible_host=192.0.2.1 sup_behavior={sup}\n')
                    return SimpleNamespace(rc=0, log_path=run / 'test.log')
                sidecar = {'name': make_vm_prefix(make_run_dep_id(name, RUN)) + 'share-0',
                           'ip': '192.0.2.2', 'flavor': 'v1.small', 'sup_config': None}
                with patch.object(spinup, '_active_current_runs', return_value=[]), patch.object(spinup, 'resolve_ruse_revision', return_value='a' * 40), patch.object(spinup, 'utc_deployment_start', return_value=datetime.fromisoformat(START)), patch.object(spinup, 'AnsibleRunner') as runner, patch.object(spinup, '_provision_share_sidecar', return_value=sidecar) as share, patch.object(spinup, 'ssh_connectivity_test', return_value=1), patch.object(spinup, 'register_phase_run', return_value=True) as register, patch.object(spinup, 'OpenStack') as cloud, patch.object(spinup, 'install_ssh_config') as ssh:
                    runner.return_value.run_playbook.side_effect = playbook
                    self.assertEqual(spinup.run_decoy_spinup(name, root), 0)
                    cloud.assert_not_called()
                    ssh.assert_not_called()
                self.assertEqual(share.call_count, int(is_share))
                plays = [p for p, _ in calls]
                self.assertNotIn('decoy/distribute-behavior-configs.yaml', plays)
                self.assertEqual('decoy/prepare-share.yaml' in plays, is_share)
                if is_share:
                    self.assertLess(plays.index('decoy/prepare-share.yaml'), plays.index('decoy/install-sups.yaml'))
                install = next(v for p, v in calls if p == 'decoy/install-sups.yaml')
                self.assertTrue(install['probe_install'])
                self.assertEqual(install['probe_started_at'], START)
                self.assertEqual(install['probe_workflow'], 'idle' if is_idle else ('NetworkShareAccess' if is_share else 'WebResearch'))
                config, system, started, vms = register.call_args.args
                self.assertTrue(config.is_probe())
                self.assertEqual((system, config.purpose, config.target), ('decoy', 'other', None))
                self.assertEqual([v['sup_config'] for v in vms], [sup, None] if is_share else [sup])
                if not is_idle:
                    self.assertEqual(install['behavior_source'], str(config_dir / 'runs' / RUN / 'plans'))

    def test_playbook_stages_probe_variants_before_normal_service_start(self):
        play = yaml.safe_load((ROOT / 'deployment_engine/playbooks/decoy/install-sups.yaml').read_text())[0]
        tasks = play['tasks']
        names = [task['name'] for task in tasks]
        stage = next(t for t in tasks if t['name'] == 'Stage approved probe daily variants')
        self.assertEqual(stage['when'], "probe_install and probe_workflow != 'idle'")
        self.assertEqual(stage['copy']['src'], '{{ behavior_source }}/')
        self.assertFalse(play['vars']['probe_install'])
        self.assertLess(names.index(stage['name']), names.index('Stage 2: ollama + python + services'))
        install = next(t for t in tasks if t['name'] == 'Stage 2: ollama + python + services')
        self.assertIn('RUSE_NO_SERVICE_START=1', install['shell'])
        self.assertIn('RUSE_PROBE_STARTED_AT=', install['shell'])
        service = next(i for i, t in enumerate(tasks) if t.get('systemd', {}).get('state') == 'started' and 'canonical' in t['name'].lower())
        self.assertLess(names.index(install['name']), service)

    def test_invalid_variant_aborts_before_display_or_execution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_dir = root / 'not-a-name-filter'
            config_dir.mkdir()
            source = root / 'variants'
            shutil.copytree(PHASE / 'research', source)
            raw = yaml.safe_load((ROOT / 'deployments/probe-research/config.yaml').read_text())
            raw['behavior_source'] = str(source)
            (config_dir / 'config.yaml').write_text(yaml.safe_dump(raw))
            path = source / 'wikipedia_python.json'
            document = json.loads(path.read_text())
            document['schedule'][0]['sequence'][0]['resource_id'] = 'wikipedia_compiler'
            path.write_text(json.dumps(document))
            with patch.object(cli, 'DEPLOY_DIR', root), patch('deployment_engine.core.plan.show_plan_and_confirm') as display, patch('deployment_engine.core.plan.execute_plan') as execute:
                self.assertEqual(cli._cmd_deploy(['--probes', config_dir.name]), 1)
            display.assert_not_called()
            execute.assert_not_called()

    def test_probe_config_guards_category_topology_and_explicit_idle(self):
        original = yaml.safe_load((ROOT / 'deployments/probe-idle/config.yaml').read_text())
        mutations = [dict(type='decoy'), dict(purpose='control'), dict(target='axes-fall24'),
                     dict(deployments=[]), dict(gpu_tier='rtx'), dict(behavior_source='/unexpected')]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'config.yaml'
            for mutation in mutations:
                path.write_text(yaml.safe_dump({**original, **mutation}))
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    DeploymentConfig.load(path)
            del original['probe_workflow']
            path.write_text(yaml.safe_dump(original))
            with self.assertRaises(ValueError):
                DeploymentConfig.load(path)

    def test_missing_extra_and_malformed_daily_variants_fail(self):
        for mutation in ('missing', 'extra', 'malformed'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as td:
                root = Path(td) / 'video'
                shutil.copytree(PHASE / 'video', root)
                first = next(root.iterdir())
                if mutation == 'missing':
                    first.unlink()
                elif mutation == 'extra':
                    (root / 'unexpected.json').write_text('{}')
                else:
                    first.write_text('{')
                with self.assertRaises(WorkflowPlanError):
                    validate_probe_plans(root, 'VideoViewing')

    def test_idle_never_loads_plan_builds_brain_or_schedules_work(self):
        with patch.dict(os.environ, {'RUSE_DEPLOYMENT_TYPE': 'probe', 'RUSE_PROBE_STARTED_AT': START}), patch('phase_workflow.runtime.AgentLogger') as logger, patch('phase_workflow.runtime.build_brain') as brain, patch('phase_workflow.runtime.ProbeExecutor') as executor:
            stop = Mock()
            run_probe_runtime('scripted-cpu', 'idle', None, stop_event=stop)
            stop.wait.assert_called_once_with()
            brain.assert_not_called()
            executor.assert_not_called()
            logger.return_value.workflow_plan_terminal.assert_not_called()
            logger.return_value.session_end.assert_called_once()
        with patch.dict(os.environ, {'RUSE_DEPLOYMENT_TYPE': 'decoy'}), self.assertRaises(RuntimeError):
            run_probe_runtime('scripted-cpu', 'idle', None)

    def test_calendar_rotation_wrap_restart_and_dst(self):
        self.assertEqual(probe_day_index(START, date(2026, 9, 15), 5), 0)
        self.assertEqual(probe_day_index(START, date(2026, 9, 21), 5), 1)
        self.assertEqual(probe_day_index(START, date(2026, 9, 21), 5), 1)  # process restart
        self.assertEqual(probe_day_index('2026-03-08T04:30:00Z', date(2026, 3, 8), 5), 1)
        self.assertEqual(probe_day_index('2026-11-01T04:30:00Z', date(2026, 11, 2), 5), 1)
        self.assertEqual(probe_day_index('2026-09-16T01:00:00Z', date(2026, 9, 15), 5), 0)

    def test_dst_gap_remains_missed_and_fall_fold_does_not_repeat_burst(self):
        plans = validate_probe_plans(PHASE / 'research', 'WebResearch')
        for start, ticks, expected_starts, expected_misses in (
            ('2026-03-08T05:00:00Z', ['2026-03-08T05:00:00Z'], 1, 4),
            ('2026-11-01T05:00:00Z', ['2026-11-01T05:00:00Z', '2026-11-01T06:00:00Z'], 2, 0),
        ):
            with self.subTest(start=start):
                clock = SimpleNamespace(value=datetime.fromisoformat(start.replace('Z', '+00:00')))
                clock.now = lambda: clock.value
                logger, starter = Mock(), Mock(side_effect=lambda *_: Future())
                registry = WorkflowRegistry(plans[0], Mock(), Path('/unused'))
                executor = ProbeExecutor(plans, start, registry, logger, clock=clock, starter=starter)
                for tick in ticks:
                    clock.value = datetime.fromisoformat(tick.replace('Z', '+00:00'))
                    executor.tick()
                self.assertEqual(starter.call_count, expected_starts)
                gaps = [c.args[0] for c in logger.workflow_plan_terminal.call_args_list if c.args[0].get('reason') == 'dst_nonexistent_time']
                self.assertEqual(len(gaps), expected_misses)
                self.assertTrue(all(e['resource_id'] == 'wikipedia_compiler' for e in gaps))

    def test_concurrent_same_resource_documents_keep_assigned_filename_in_owned_paths(self):
        plan = validate_probe_plans(PHASE / 'documents', 'DocumentCreation')[0]
        entry = plan.windows[4].sequence[0]
        class Writer:
            def execute(self, task, workspace):
                return OpenDocumentWriter().create(task, workspace)
        with tempfile.TemporaryDirectory() as td:
            registry = WorkflowRegistry(plan, Writer(), Path(td), isolate_occurrences=True)
            tasks = [registry.resolve(entry, occurrence_id=f'w4-s{i}') for i in range(10)]
            with ThreadPoolExecutor(max_workers=10) as workers:
                results = list(workers.map(lambda task: registry.execute(task, '2026-09-15'), tasks))
            artifacts = [Path(result.artifact) for result in results]
            self.assertEqual(len(set(artifacts)), 10)
            for task, result, artifact in zip(tasks, results, artifacts):
                self.assertTrue(result.completed)
                self.assertEqual(artifact.name, task.resource['filename'])
                self.assertEqual(artifact.parent.name, '2026-09-15')
                validate_open_document(task, artifact.parent, artifact)
            prior = artifacts[0].read_bytes()
            next_day = registry.execute(tasks[0], '2026-09-16')
            self.assertNotEqual(next_day.artifact, str(artifacts[0]))
            self.assertEqual(artifacts[0].read_bytes(), prior)

    def test_midnight_retains_inflight_resource_capacity_and_failure_identity(self):
        plans = validate_probe_plans(PHASE / 'research', 'WebResearch')
        clock = SimpleNamespace(value=datetime(2026, 9, 16, 3, 0, tzinfo=timezone.utc))
        clock.now = lambda: clock.value
        log = Mock()
        started = []
        handles = []
        def start(task, day):
            started.append((task, day))
            handle = Future()
            handles.append(handle)
            return handle
        registry = WorkflowRegistry(plans[0], Mock(), Path('/unused'))
        executor = ProbeExecutor(plans, START, registry, log, clock=clock, starter=start)
        executor.tick()  # 23:00 EDT: eight compiler tasks
        self.assertEqual(executor.active_count, 8)
        clock.value = datetime(2026, 9, 16, 4, 0, tzinfo=timezone.utc)
        executor.tick()  # midnight: one geometry task; old eight retain capacity
        self.assertEqual(executor.active_count, 9)
        self.assertEqual(started[-1][0].resource_id, 'wikipedia_geometry')
        self.assertEqual(started[-1][1], '2026-09-16')
        for handle in handles[:8]:
            handle.set_result(WorkflowResult(completed=False))
        executor.tick()
        failed = [c.args[0] for c in log.workflow_plan_terminal.call_args_list if c.args[0]['status'] == 'failed']
        self.assertEqual(len(failed), 8)
        self.assertEqual({e['resource_id'] for e in failed}, {'wikipedia_compiler'})
        missed = [c.args[0] for c in log.workflow_plan_terminal.call_args_list if c.args[0]['status'] == 'missed']
        self.assertEqual(len(missed), 107)
        self.assertTrue(all(e['resource_id'] == 'wikipedia_compiler' for e in missed))
        self.assertEqual(log.info.call_args.args[1], {'local_date': '2026-09-16', 'cycle_index': 1, 'resource_id': 'wikipedia_geometry', 'filename': 'wikipedia_geometry.json'})

    def test_installed_probe_and_idle_tree_reuse_scripted_service_command(self):
        for workflow, directory in (('WebResearch', PHASE / 'research'), ('idle', None)):
            with self.subTest(workflow=workflow), tempfile.TemporaryDirectory() as td:
                destination = Path(td)
                env = {**os.environ, 'RUSE_DEPLOYMENT_TYPE': 'probe', 'RUSE_PROBE_WORKFLOW': workflow,
                       'RUSE_PROBE_STARTED_AT': START, 'RUSE_PROBE_PLANS_DIR': str(directory or '')}
                command = 'source ./INSTALL_SUP.sh; parse_config_key scripted-cpu; copy_source_code "$1"; create_run_script "$1"'
                result = subprocess.run(['bash', '-c', command, 'test-probe', td], cwd=ROOT, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                script = (destination / 'run_agent.sh').read_text()
                self.assertIn('python3 -m sup scripted-cpu', script)
                self.assertIn('--probe-workflow=' + workflow, script)
                self.assertIn('export RUSE_DEPLOYMENT_TYPE="probe"', script)
                self.assertIn(START, script)
                self.assertFalse((destination / 'behavioral_configurations/behavior.json').exists())
                plans = validate_probe_plans(None if directory is None else destination / 'behavioral_configurations', None if directory is None else workflow)
                self.assertEqual(len(plans), 0 if directory is None else 5)
                # Workspaces are outside the immutable variants directory: restart validation stays valid.
                (destination / 'workspace').mkdir()
                if directory:
                    validate_probe_plans(destination / 'behavioral_configurations', workflow)

    def test_mixed_lifecycle_uses_explicit_type_not_name_including_failed_sidecar(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            statuses = {}
            records = [('decoy-controls', 'probe', 'other'), ('looks-like-probe', 'decoy', 'control'),
                       ('feedback', 'decoy', 'feedback'), ('canary', 'decoy', 'other')]
            for name, kind, purpose in records:
                config_dir = root / name
                run = config_dir / 'runs' / RUN
                run.mkdir(parents=True)
                raw = {'deployment_name': name, 'type': kind, 'purpose': purpose,
                       'target': 'axes-fall24' if purpose == 'feedback' else None,
                       'deployments': [{'behavior': 'scripted-cpu', 'flavor': 'v1.14vcpu.28g', 'count': 1}]}
                if kind == 'probe':
                    raw['probe_workflow'] = None
                (config_dir / 'config.yaml').write_text(yaml.safe_dump(raw))
                write_run_status(run, FAILED, 'test')
                prefix = make_vm_prefix(make_run_dep_id(name, RUN))
                statuses[prefix + ('share-0' if kind == 'probe' else 'scripted-cpu-0')] = 'ERROR'
            for category, included, excluded in (
                ('decoy', ['looks-like-probe', 'feedback'], ['decoy-controls', 'canary']),
                ('probe', ['decoy-controls'], ['looks-like-probe', 'feedback', 'canary']),
            ):
                cloud = Mock()
                cloud.server_status_map.return_value = statuses
                out = StringIO()
                with patch.object(teardown, 'OpenStack', return_value=cloud), patch.object(teardown.output, 'confirm', return_value=False), redirect_stderr(out):
                    teardown.run_teardown_filtered(root, types={category: True}, failed_only=True)
                for name in included:
                    self.assertIn(f'{name}/{RUN}', out.getvalue())
                for name in excluded:
                    self.assertNotIn(f'{name}/{RUN}', out.getvalue())
                cloud.server_status_map.assert_called_once()
            out = StringIO()
            cloud.server_status_map.return_value = statuses
            with patch.object(listing, 'OpenStack', return_value=cloud), patch.object(listing, 'deployment_path', return_value=root / 'missing'), redirect_stderr(out):
                listing.run_list(root)
            self.assertIn('PROBE SUPs', out.getvalue())
            self.assertIn('unregistered', out.getvalue())
            with patch('deployment_engine.decoy.teardown.run_decoy_teardown', return_value=0) as exact:
                self.assertEqual(teardown.run_teardown(f'decoy-controls-{RUN}', root), 0)
                self.assertEqual(exact.call_args.args[1:3], ('decoy-controls', RUN))

    def test_probe_cli_exclusivity_and_dispatch(self):
        with patch('deployment_engine.teardown.run_teardown_filtered', return_value=0) as filtered:
            self.assertEqual(cli._cmd_teardown(['--probes', '--failed']), 0)
            self.assertEqual(filtered.call_args.kwargs['types'], {'probe': True})
        for args in (['--probes', '--decoys'], ['--probes', '--controls'], ['--probes', '--all']):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                cli._cmd_teardown(args)
        with patch('deployment_engine.core.plan.show_plan_and_confirm', return_value=False) as show, patch('deployment_engine.core.plan.execute_plan') as execute:
            self.assertEqual(cli._cmd_deploy(['--probes']), 0)
            self.assertEqual(len(show.call_args.args[0]), 7)
            execute.assert_not_called()

    def test_shared_registry_other_is_excluded_by_actual_phase_selector(self):
        with tempfile.TemporaryDirectory() as td:
            _, path = create_deployment(experiment_id='probe-idle', system='decoy', purpose='other', target=None,
                started_at=datetime.fromisoformat(START), capture_interface='eno2',
                vms=[{'name': 'exact-vm', 'ip': '192.0.2.1', 'sup_config': 'scripted-cpu'}], experiments_root=Path(td))
            record = json.loads(path.read_text())
            self.assertEqual(record['purpose'], 'other')
            # Compile only PHASE's pure selector, not its executable CLI/imports.
            tree = ast.parse(Path('/home/ubuntu/PHASE/infer').read_text())
            node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_newest_runs')
            namespace = {}
            exec(compile(ast.Module(body=[node], type_ignores=[]), '<PHASE selector>', 'exec'), namespace)
            self.assertEqual(namespace['_newest_runs']([record]), [])


if __name__ == '__main__':
    unittest.main()
