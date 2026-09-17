"""MCHP-only Probe selection, contract binding and ordinary GUI environment."""

import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from deployment_engine import __main__ as cli
from deployment_engine import teardown
from deployment_engine.core.plan import build_probe_plan
from deployment_engine.core.vm_naming import make_run_dep_id, make_vm_prefix
from phase_workflow.loader import WorkflowPlanError, load_workflow_plan
from phase_workflow.probes import PROBE_RESOURCES, probe_day_index, validate_probe_plans
from phase_workflow.runtime import run_probe_runtime
from tests.test_probes import DIRS, PHASE, ROOT, RUN, START


class MCHPProbeTests(unittest.TestCase):
    def test_27_variants_bind_only_mchp_identity_and_preserve_source_bytes(self):
        count = 0
        for workflow in PROBE_RESOURCES:
            source = PHASE / DIRS[workflow]
            before = {p.name: p.read_bytes() for p in source.iterdir()}
            scripted = validate_probe_plans(source, workflow)
            mchp = validate_probe_plans(source, workflow, 'mchp-cpu')
            for first, second in zip(scripted, mchp):
                count += 1
                self.assertEqual((second.sup_config, second.brain, second.brain_profile_id), ('mchp-cpu', 'mchp', 'mchp-v1'))
                self.assertEqual(first.windows, second.windows)
                self.assertEqual(first.timezone, second.timezone)
                self.assertEqual(second.max_parallel, 10)
                self.assertEqual(sum(len(w.sequence) for w in second.windows), 115)
                self.assertTrue(all(e.instruction is None for w in second.windows for e in w.sequence))
                self.assertEqual(second.resource_profile, 'feedback-v2')
            self.assertEqual(before, {p.name: p.read_bytes() for p in source.iterdir()})
            self.assertEqual(probe_day_index(START, date(2026, 9, 18), len(mchp)), 3 % len(mchp))
        self.assertEqual(count, 27)

    def test_normal_loader_still_rejects_sup_mismatch_and_probe_source_must_be_valid(self):
        path = PHASE / 'upload/cloudflare_upload.json'
        with self.assertRaises(WorkflowPlanError):
            load_workflow_plan(path, 'mchp-cpu')
        with tempfile.TemporaryDirectory() as td:
            document = json.loads(path.read_text())
            document['brain_profile'] = 'mchp-v1'
            (Path(td) / path.name).write_text(json.dumps(document))
            with self.assertRaises(WorkflowPlanError):
                validate_probe_plans(td, 'FileSyncUpload', 'mchp-cpu')

    def test_separate_fleets_and_cli_dispatch_do_not_select_scripted(self):
        scripted = build_probe_plan(ROOT / 'deployments')
        mchp = build_probe_plan(ROOT / 'deployments', sup_config='mchp-cpu')
        self.assertEqual(len(mchp), 7)
        self.assertEqual(len(scripted), 7)
        self.assertFalse({t['config_name'] for t in scripted} & {t['config_name'] for t in mchp})
        self.assertEqual({t['probe_workflow'] for t in mchp}, {*PROBE_RESOURCES, None})
        self.assertEqual(sum(t['share_required'] for t in mchp), 1)
        for task in mchp:
            self.assertEqual(task['deployments'], [{'behavior': 'mchp-cpu', 'flavor': 'v1.14vcpu.28g', 'count': 1}])
            self.assertEqual((task['purpose'], task['target']), ('other', None))
        with patch('deployment_engine.core.plan.show_plan_and_confirm', return_value=True) as show, patch('deployment_engine.core.plan.execute_plan', return_value=0) as execute:
            self.assertEqual(cli._cmd_deploy(['--probes', '--probe-sup', 'mchp-cpu']), 0)
            self.assertEqual(show.call_args.args[0], mchp)
            self.assertEqual(execute.call_args.args[0], mchp)
        with self.assertRaises(ValueError):
            build_probe_plan(ROOT / 'deployments', 'probe-idle', sup_config='mchp-cpu')
        for args in (['--probe-sup', 'mchp-cpu'], ['--probes', '--probe-sup', 'browseruse-gpu']):
            with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                cli._cmd_deploy(args)

    def test_mchp_idle_has_normal_xvfb_openbox_command_but_no_workflows(self):
        env = {**os.environ, 'RUSE_DEPLOYMENT_TYPE': 'probe', 'RUSE_PROBE_WORKFLOW': 'idle', 'RUSE_PROBE_STARTED_AT': START}
        with tempfile.TemporaryDirectory() as td:
            command = 'source ./INSTALL_SUP.sh; parse_config_key mchp-cpu; copy_source_code "$1"; create_run_script "$1"'
            result = subprocess.run(['bash', '-c', command, 'test', td], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            script = (Path(td) / 'run_agent.sh').read_text()
            self.assertIn('xvfb-run -a', script)
            self.assertIn('openbox', script)
            self.assertIn('python3 -m sup mchp-cpu', script)
            self.assertIn('--probe-workflow=idle', script)
            self.assertTrue((Path(td) / 'decoys/brains/mchp').is_dir())
            self.assertEqual(list((Path(td) / 'behavioral_configurations').iterdir()), [])
        with patch.dict(os.environ, env), patch('phase_workflow.runtime.AgentLogger') as logger, patch('phase_workflow.runtime.build_brain') as brain, patch('phase_workflow.runtime.ProbeExecutor') as executor:
            stop = Mock()
            run_probe_runtime('mchp-cpu', 'idle', None, stop_event=stop)
            stop.wait.assert_called_once_with()
            brain.assert_not_called()
            executor.assert_not_called()
            self.assertEqual(logger.return_value.session_start.call_args.kwargs['config']['brain'], 'mchp')
            logger.return_value.workflow_plan_terminal.assert_not_called()

    def test_active_runtime_dispatches_real_mchp_with_rotating_variants(self):
        with patch.dict(os.environ, {'RUSE_DEPLOYMENT_TYPE': 'probe', 'RUSE_PROBE_STARTED_AT': START}), patch('phase_workflow.runtime.AgentLogger') as logger, patch('phase_workflow.runtime.build_brain') as brain, patch('phase_workflow.runtime.ProbeExecutor') as executor:
            stop = Mock()
            run_probe_runtime('mchp-cpu', 'VideoViewing', str(PHASE / 'video'), stop_event=stop)
            self.assertEqual(brain.call_args.args[0], 'mchp')
            plans, started, registry, _ = executor.call_args.args
            self.assertEqual(started, START)
            self.assertEqual(len(plans), 3)
            self.assertTrue(all(p.brain == 'mchp' for p in plans))
            executor.return_value.run_forever.assert_called_once_with(stop)
            executor.return_value.close.assert_called_once()
            brain.return_value.close.assert_called_once()

    def test_decoy_filtered_teardown_excludes_both_probe_brains_and_sidecars(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            statuses = {}
            for name in ('probe-idle', 'probe-mchp-idle', 'probe-mchp-network-share'):
                config_dir = root / name
                (config_dir / 'runs' / RUN).mkdir(parents=True)
                (config_dir / 'config.yaml').write_bytes((ROOT / 'deployments' / name / 'config.yaml').read_bytes())
                prefix = make_vm_prefix(make_run_dep_id(name, RUN))
                statuses[prefix + ('share-0' if name.endswith('network-share') else 'mchp-cpu-0')] = 'ACTIVE'
            cloud = Mock()
            cloud.server_status_map.return_value = statuses
            with patch.object(teardown, 'OpenStack', return_value=cloud), patch.object(teardown.output, 'confirm') as confirm, redirect_stderr(StringIO()):
                self.assertEqual(teardown.run_teardown_filtered(root, types={'decoy': True}), 0)
            confirm.assert_not_called()
            cloud.server_status_map.assert_called_once()


if __name__ == '__main__':
    unittest.main()
