"""Global Baseline selection/registration; cloud execution is always mocked."""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from deployment_engine import __main__ as cli
from deployment_engine.core import feedback, plan, phase_run_registry as registry
from deployment_engine.core.config import DeploymentConfig
from deployment_engine.core.deploy_steps import register_phase_run
from deployment_engine.decoy.spinup import _validate_behavior_source
from tests.test_phase_workflow_runtime import expanded_hls_control_document


class BaselineTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "baselines"
        self.deploy = self.root / "deployments"
        p = patch.object(feedback, "DECOY_BASELINE_BASE", self.base)
        p.start()
        self.addCleanup(p.stop)

    def generation(self, preset="colfix_v12.5.0", direction="not-human", timestamp="2026-09-21_1200Z"):
        path = self.base / preset / direction / timestamp
        path.mkdir(parents=True)
        for sup, name in feedback.DECOY_PLAN_FILENAMES.items():
            (path / name).write_text(json.dumps(expanded_hls_control_document(sup)))
        return path

    def test_cli_default_directions_and_versions_are_isolated(self):
        identities = []
        for preset, version_flags in (("colfix_v12.5.0", []), ("ctrlfix_v12.5.1", ["--ctrlfix"])):
            for direction, direction_flags in (("not-human", []), ("human", ["--human"])):
                selected = self.generation(preset, direction)
                with patch.object(plan, "show_plan_and_confirm", return_value=False) as show, \
                     patch.object(plan, "execute_plan") as execute:
                    self.assertEqual(cli._cmd_deploy(["--decoys", "--baseline", *version_flags, *direction_flags]), 0)
                execute.assert_not_called()
                tasks = show.call_args.args[0]
                self.assertEqual(len(tasks), 1)
                task = tasks[0]
                identities.append(task["config_name"])
                self.assertEqual(task["behavior_source"], selected)
                self.assertEqual(task["baseline_direction"], direction)
                self.assertEqual(task["model_preset"], preset)
                self.assertEqual((task["purpose"], task["target"]), ("control", None))
                self.assertEqual(task["deployments"], list(feedback.DECOY_FEEDBACK_DEPLOYMENTS))
        self.assertEqual(len(set(identities)), 4)
        with patch.object(plan, "show_plan_and_confirm", return_value=False) as show:
            cli._cmd_deploy(["--baseline", "--not-human", "--preset", "colfix_v12.5.0"])
            self.assertEqual(show.call_args.args[0][0]["config_name"], identities[0])

    def test_newest_calendar_valid_and_invalid_newest_fails(self):
        self.generation(timestamp="2026-09-20_1200Z")
        selected = self.generation()
        self.generation(timestamp="2026-02-30_2359Z")
        self.assertEqual(feedback.find_decoy_baseline_generation("colfix_v12.5.0", "not-human"), selected)
        (selected / "mchp-v1.json").unlink()
        with patch.object(plan, "show_plan_and_confirm") as show, patch.object(plan, "execute_plan") as execute:
            self.assertEqual(cli._cmd_deploy(["--baseline"]), 1)
            show.assert_not_called()
            execute.assert_not_called()

    def test_missing_version_direction_empty_and_flat_never_fall_back(self):
        self.generation()
        self.generation(timestamp="2026-09-20_1200Z")
        for preset, direction in (("ctrlfix_v12.5.1", "not-human"), ("colfix_v12.5.0", "human")):
            with self.assertRaisesRegex(feedback.FeedbackSourceError, str(self.base / preset / direction)):
                feedback.find_decoy_baseline_generation(preset, direction)
            (self.base / preset / direction).mkdir(parents=True)
            with self.assertRaisesRegex(feedback.FeedbackSourceError, "no YYYY"):
                feedback.find_decoy_baseline_generation(preset, direction)

    def test_conflicting_scope_and_direction_flags_rejected_before_plan(self):
        for args in (["--human"], ["--not-human"], ["--baseline", "--human", "--not-human"],
                     ["--baseline", "--ctrlfix", "--preset", "colfix_v12.5.0"],
                     *[["--baseline", flag] for flag in ("--controls", "--feedback", "--probes", "--canary", "--rampart", "--ghosts")],
                     ["--baseline", "--target", "axes-summer24"], ["--baseline", "--source", "/unused"]):
            with self.subTest(args=args), patch.object(plan, "build_baseline_plan") as build, self.assertRaises(SystemExit):
                cli._cmd_deploy(args)
            build.assert_not_called()

    def test_all_four_loader_validations_precede_display(self):
        path = self.generation()
        original = feedback.validate_decoy_control_generation
        observed = []

        def validate(source):
            loaded = original(source)
            observed.extend(loaded)
            return loaded

        def show(tasks, *args, **kwargs):
            self.assertEqual(observed, list(feedback.DECOY_PLAN_FILENAMES))
            return False

        with patch.object(feedback, "validate_decoy_control_generation", side_effect=validate), \
             patch.object(plan, "show_plan_and_confirm", side_effect=show), patch.object(plan, "execute_plan") as execute:
            self.assertEqual(cli._cmd_deploy(["--baseline"]), 0)
            execute.assert_not_called()
        self.assertTrue(feedback.decoy_generation_uses_network_share(path, purpose="control"))

    def test_saved_config_installation_and_registry_metadata(self):
        source = self.generation(direction="human")
        tasks = plan.build_baseline_plan("colfix_v12.5.0", "human")
        with patch("deployment_engine.decoy.spinup.run_decoy_spinup", return_value=0) as spinup:
            self.assertEqual(plan.execute_plan(tasks, "decoy", None, self.deploy), 0)
        name = tasks[0]["config_name"]
        self.assertEqual(spinup.call_args.args, (name, self.deploy, str(source), None))
        config = DeploymentConfig.load(self.deploy / name / "config.yaml")
        self.assertEqual(_validate_behavior_source(str(source), config), [])
        started = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
        vms = [{"name": "vm1", "ip": "10.0.0.1", "sup_config": "scripted-cpu"}]
        with patch.dict("os.environ", {"PHASE_AXES_ROOT": str(self.root)}):
            self.assertTrue(register_phase_run(config, "decoy", started, vms))
            run_id = registry.run_id_from_started_at(started)
            path = registry.deployment_path(name, run_id)
            record = json.loads(path.read_text())
            self.assertEqual((record["system"], record["purpose"], record["target"]), ("decoy", "control", None))
            self.assertEqual(record["baseline_direction"], "human")
            self.assertEqual(record["model_preset"], "colfix_v12.5.0")
            registry.close_deployment(name, run_id, ended_at=started + timedelta(hours=1))
            self.assertEqual(json.loads(path.read_text())["baseline_direction"], "human")

    def test_metadata_requires_both_fields_and_only_decoy_control(self):
        args = dict(experiment_id="test", system="decoy", purpose="control", target=None,
                    started_at=datetime(2026, 9, 21, tzinfo=timezone.utc), capture_interface="eno2",
                    vms=[{"name": "vm", "ip": "10.0.0.1", "sup_config": "scripted-cpu"}],
                    experiments_root=self.root / "experiments")
        for metadata in ({"baseline_direction": "human"}, {"model_preset": "colfix_v12.5.0"},
                         {"baseline_direction": "other", "model_preset": "colfix_v12.5.0"},
                         {"baseline_direction": "human", "model_preset": "ctrlfix_v12.5.1", "purpose": "feedback", "target": "axes-summer24"}):
            with self.subTest(metadata=metadata), self.assertRaises(registry.PhaseRunRegistryError):
                registry.create_deployment(**{**args, **metadata})
        _, path = registry.create_deployment(**args)
        record = json.loads(path.read_text())
        self.assertNotIn("baseline_direction", record)
        self.assertNotIn("model_preset", record)

    def test_registry_schema_changes_only_approved_optional_metadata(self):
        local = json.loads(registry.CONTRACT_PATH.read_text())
        phase = json.loads(Path("/home/ubuntu/PHASE/contracts/phase-run-v1/phase-run-v1.schema.json").read_text())
        for schema in (local, phase):
            for key in ("baseline_direction", "model_preset"):
                schema["properties"].pop(key, None)
            schema.pop("dependentRequired", None)
            schema.pop("allOf", None)
        self.assertEqual(local, phase)
