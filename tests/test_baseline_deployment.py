"""Global Baseline selection/registration; cloud execution is always mocked."""
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest import TestCase
from unittest.mock import patch

from deployment_engine import __main__ as cli
from deployment_engine import list as list_command
from deployment_engine.core import feedback, plan, phase_run_registry as registry
from deployment_engine.core.config import DeploymentConfig
from deployment_engine.core.deploy_steps import register_phase_run
from deployment_engine.core.vm_naming import make_run_dep_id, make_vm_prefix
from deployment_engine.decoy.spinup import _validate_behavior_source
from tests.test_phase_workflow_runtime import CONTRACT_ROOT, six_workflow_document


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
        instructions = json.loads(
            (CONTRACT_ROOT / "capabilities-v1.json").read_text()
        )["instructions"]["controls-v2"]
        for sup, name in feedback.DECOY_PLAN_FILENAMES.items():
            document = six_workflow_document(sup)
            document["resource_profile"] = "controls-v2"
            for window in document["schedule"]:
                for entry in window["sequence"]:
                    if sup in {"browseruse-gpu", "smolagents-gpu"}:
                        entry["instruction"] = instructions[entry["workflow"]]
                    else:
                        entry.pop("instruction", None)
            (path / name).write_text(json.dumps(document))
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

    def test_llm_comparison_is_ordered_unique_single_v100_tasks(self):
        selected = self.generation(direction="human")
        smol_path = selected / "smolagents-v1.json"
        smol_document = json.loads(smol_path.read_text())
        for window in smol_document["schedule"]:
            window["sequence"] = [
                entry for entry in window["sequence"]
                if entry["workflow"] != "NetworkShareAccess"
            ]
        smol_path.write_text(json.dumps(smol_document))
        tasks = plan.build_baseline_plan(
            "colfix_v12.5.0", "human", llm_comparison=True
        )
        self.assertEqual(len(tasks), 36)
        self.assertEqual(len({task["config_name"] for task in tasks}), 36)
        gpu_expected = [
            (model, behavior)
            for model in plan.LLM_COMPARISON_MODELS
            for behavior in ("browseruse-gpu", "smolagents-gpu")
        ]
        cpu_expected = [
            (model, behavior)
            for model in plan.LLM_COMPARISON_CPU_MODELS
            for behavior in ("browseruse-gpu", "smolagents-gpu")
        ]
        self.assertEqual(
            [(task["requested_model"], task["deployments"][0]["behavior"])
             for task in tasks],
            gpu_expected + cpu_expected,
        )
        for index, task in enumerate(tasks):
            self.assertEqual(task["behavior_source"], selected)
            self.assertEqual(task["baseline_direction"], "human")
            hardware = "gpu" if index < 20 else "cpu"
            self.assertEqual(task["gpu_tier"], "v100" if hardware == "gpu" else "cpu")
            flavor = "v100-1gpu.14vcpu.28g" if hardware == "gpu" else "v1.14vcpu.28g"
            self.assertEqual(task["comparison_hardware"], hardware)
            self.assertEqual(task["deployments"][0]["flavor"], flavor)
            self.assertEqual(task["deployments"][0]["count"], 1)
            if hardware == "cpu":
                self.assertTrue(task["config_name"].endswith("-cpu"))
        with patch.object(plan.output, "info") as info, \
             patch.object(plan.output, "confirm", return_value=False):
            self.assertFalse(plan.show_plan_and_confirm(tasks, "decoy"))
        rendered = "\n".join(str(call.args[0]) for call in info.call_args_list if call.args)
        self.assertIn(
            "Comparison total: 36 SUPs (20 GPU, 16 CPU) + 18 selected-plan share sidecars = 54 VMs",
            rendered,
        )

    def test_llm_comparison_cli_scope_and_hardware_are_strict(self):
        self.generation(direction="human")
        ctrlfix_source = self.generation(
            preset="ctrlfix_v12.5.1", direction="human"
        )
        invalid = (
            ["--llm-comparison"],
            ["--baseline", "--llm-comparison", "--human"],
            ["--decoys", "--baseline", "--llm-comparison"],
            ["--decoys", "--baseline", "--llm-comparison", "--human", "--gpu", "rtx"],
            ["--decoys", "--baseline", "--llm-comparison", "--human", "--controls"],
        )
        for args in invalid:
            with self.subTest(args=args), patch.object(plan, "build_baseline_plan") as build, self.assertRaises(SystemExit):
                cli._cmd_deploy(args)
            build.assert_not_called()
        with patch.object(plan, "show_plan_and_confirm", return_value=False) as show:
            self.assertEqual(cli._cmd_deploy([
                "--decoys", "--baseline", "--human", "--llm-comparison"
            ]), 0)
        self.assertEqual(len(show.call_args.args[0]), 36)
        with patch.object(plan, "show_plan_and_confirm", return_value=False) as show:
            self.assertEqual(cli._cmd_deploy([
                "--decoys", "--baseline", "--ctrlfix", "--human",
                "--llm-comparison",
            ]), 0)
        self.assertEqual(len(show.call_args.args[0]), 36)
        self.assertTrue(all(
            task["behavior_source"] == ctrlfix_source
            for task in show.call_args.args[0]
        ))
        with patch.object(plan, "show_plan_and_confirm", return_value=False) as show:
            self.assertEqual(cli._cmd_deploy([
                "--decoys", "--baseline", "--human", "--llm-comparison", "--gpu", "v100"
            ]), 0)
        self.assertEqual(len(show.call_args.args[0]), 36)

    def test_saved_llm_comparison_config_deploys_exactly_one_immutable_task(self):
        source = self.generation(
            preset="ctrlfix_v12.5.1", direction="human",
            timestamp="2026-10-23_2156Z",
        )
        task = next(
            task for task in plan.build_baseline_plan(
                "ctrlfix_v12.5.1", "human", llm_comparison=True,
                no_schedule=True,
            )
            if task["requested_model"] == "ministral-3:3b-instruct-2512-q4_K_M"
            and task["deployments"][0]["behavior"] == "smolagents-gpu"
        )
        feedback.generate_baseline_config(task, self.deploy)
        config_path = self.deploy / task["config_name"] / "config.yaml"
        config_before = config_path.read_bytes()
        source_before = {path.name: path.read_bytes() for path in source.iterdir()}

        with patch.object(cli, "DEPLOY_DIR", self.deploy), \
             patch.object(feedback, "find_decoy_baseline_generation") as find, \
             patch.object(feedback, "generate_baseline_config") as generate, \
             patch.object(plan, "show_plan_and_confirm", return_value=True) as show, \
             patch.object(plan, "execute_plan", return_value=0) as execute:
            self.assertEqual(cli._cmd_deploy([
                "--decoys", task["config_name"],
            ]), 0)

        find.assert_not_called()
        generate.assert_not_called()
        saved = show.call_args.args[0]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["behavior_source"], source)
        self.assertEqual(saved[0]["requested_model"], task["requested_model"])
        self.assertEqual(saved[0]["deployments"], task["deployments"])
        self.assertTrue(saved[0]["no_schedule"])
        self.assertTrue(saved[0]["share_required"])
        execute.assert_called_once_with(
            saved, "decoy", task["config_name"], self.deploy, gpu_tier="v100"
        )
        with patch.object(feedback, "generate_baseline_config") as generate, \
             patch("deployment_engine.decoy.spinup.run_decoy_spinup", return_value=0) as spinup:
            self.assertEqual(
                plan.execute_plan(
                    saved, "decoy", task["config_name"], self.deploy,
                    gpu_tier="v100",
                ),
                0,
            )
        generate.assert_not_called()
        spinup.assert_called_once_with(
            task["config_name"], self.deploy, str(source), None
        )
        self.assertEqual(config_path.read_bytes(), config_before)
        self.assertEqual(
            {path.name: path.read_bytes() for path in source.iterdir()}, source_before
        )

    def test_saved_llm_comparison_invalid_source_fails_before_dispatch(self):
        source = self.generation(direction="human")
        task = plan.build_baseline_plan(
            "colfix_v12.5.0", "human", llm_comparison=True
        )[0]
        feedback.generate_baseline_config(task, self.deploy)
        (source / "browseruse-v1.json").unlink()
        with patch.object(cli, "DEPLOY_DIR", self.deploy), \
             patch.object(plan, "show_plan_and_confirm") as show, \
             patch.object(plan, "execute_plan") as execute:
            self.assertEqual(cli._cmd_deploy([
                "--decoys", task["config_name"],
            ]), 1)
        show.assert_not_called()
        execute.assert_not_called()

    def test_saved_llm_comparison_rejects_overrides(self):
        self.generation(direction="human")
        task = plan.build_baseline_plan(
            "colfix_v12.5.0", "human", llm_comparison=True
        )[0]
        feedback.generate_baseline_config(task, self.deploy)
        conflicts = (
            ["--controls"], ["--feedback"], ["--baseline"], ["--human"],
            ["--llm-comparison"], ["--no-schedule"], ["--ctrlfix"],
            ["--preset", "colfix_v12.5.0"], ["--source", "/unused"],
            ["--target", "axes-summer24"], ["--gpu", "v100"],
        )
        for conflict in conflicts:
            with self.subTest(conflict=conflict), \
                 patch.object(cli, "DEPLOY_DIR", self.deploy), \
                 patch.object(plan, "show_plan_and_confirm") as show, \
                 patch.object(plan, "execute_plan") as execute, \
                 self.assertRaises(SystemExit):
                cli._cmd_deploy([
                    "--decoys", task["config_name"], *conflict,
                ])
            show.assert_not_called()
            execute.assert_not_called()

    def test_no_schedule_requires_explicit_human_llm_comparison(self):
        invalid = (
            ["--no-schedule"],
            ["--baseline", "--no-schedule"],
            ["--decoys", "--baseline", "--human", "--no-schedule"],
            ["--decoys", "--baseline", "--human", "--llm-comparison", "--no-schedule", "--gpu", "rtx"],
        )
        for args in invalid:
            with self.subTest(args=args), patch.object(plan, "build_baseline_plan") as build, self.assertRaises(SystemExit):
                cli._cmd_deploy(args)
            build.assert_not_called()

    def test_no_schedule_plan_is_autonomous_unique_and_keeps_source_immutable(self):
        source = self.generation(direction="human")
        before = {path.name: path.read_bytes() for path in source.iterdir()}
        scheduled = plan.build_baseline_plan(
            "colfix_v12.5.0", "human", llm_comparison=True
        )
        autonomous = plan.build_baseline_plan(
            "colfix_v12.5.0", "human", llm_comparison=True, no_schedule=True
        )
        self.assertEqual(len(autonomous), 36)
        self.assertEqual(
            [(t["requested_model"], t["comparison_hardware"], t["deployments"])
             for t in autonomous],
            [(t["requested_model"], t["comparison_hardware"], t["deployments"])
             for t in scheduled],
        )
        self.assertEqual(len({task["config_name"] for task in autonomous}), 36)
        self.assertTrue(all(task["config_name"].endswith("-autonomous") for task in autonomous))
        self.assertTrue({task["config_name"] for task in autonomous}.isdisjoint(
            {task["config_name"] for task in scheduled}
        ))
        self.assertTrue(all(task["no_schedule"] for task in autonomous))
        self.assertEqual(sum(task["share_required"] for task in autonomous), 36)
        self.assertEqual(
            {path.name: path.read_bytes() for path in source.iterdir()}, before
        )
        with patch.object(plan.output, "info") as info, \
             patch.object(plan.output, "confirm", return_value=False):
            self.assertFalse(plan.show_plan_and_confirm(autonomous, "decoy"))
        rendered = "\n".join(str(call.args[0]) for call in info.call_args_list if call.args)
        self.assertIn("36 selected-plan share sidecars = 72 VMs", rendered)
        self.assertIn("autonomous; no PHASE timing; all six activities", rendered)

    def test_no_schedule_config_and_install_environment_are_opt_in(self):
        self.generation(direction="human")
        scheduled, autonomous = (
            plan.build_baseline_plan(
                "colfix_v12.5.0", "human", llm_comparison=True,
                no_schedule=value,
            )[0]
            for value in (False, True)
        )
        feedback.generate_baseline_config(scheduled, self.deploy)
        feedback.generate_baseline_config(autonomous, self.deploy)
        scheduled_path = self.deploy / scheduled["config_name"] / "config.yaml"
        autonomous_path = self.deploy / autonomous["config_name"] / "config.yaml"
        scheduled_document = __import__("yaml").safe_load(scheduled_path.read_text())
        autonomous_document = __import__("yaml").safe_load(autonomous_path.read_text())
        self.assertNotIn("no_schedule", scheduled_document)
        self.assertIs(autonomous_document["no_schedule"], True)
        self.assertFalse(DeploymentConfig.load(scheduled_path).no_schedule)
        self.assertTrue(DeploymentConfig.load(autonomous_path).no_schedule)

        playbook = (Path(__file__).parents[1] / "deployment_engine/playbooks/decoy/install-sups.yaml").read_text()
        installer = (Path(__file__).parents[1] / "INSTALL_SUP.sh").read_text()
        self.assertEqual(playbook.count('RUSE_NO_SCHEDULE="{{ \'1\' if no_schedule | bool else \'\' }}"'), 2)
        self.assertIn('export RUSE_NO_SCHEDULE="${RUSE_NO_SCHEDULE:-}"', installer)

        autonomous_document["no_schedule"] = False
        autonomous_path.write_text(__import__("yaml").safe_dump(autonomous_document))
        self.assertFalse(DeploymentConfig.load(autonomous_path).no_schedule)
        autonomous_document["no_schedule"] = "true"
        autonomous_path.write_text(__import__("yaml").safe_dump(autonomous_document))
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            DeploymentConfig.load(autonomous_path)

    def test_installed_run_script_captures_no_schedule_opt_in_and_empty_default(self):
        source = self.generation(direction="human") / "browseruse-v1.json"
        installer = Path(__file__).parents[1] / "INSTALL_SUP.sh"
        command = (
            f'source "{installer}"; '
            'parse_config_key browseruse-gpu; '
            f'RUSE_WORKFLOW_BEHAVIOR_PATH="{source}"; '
            'export RUSE_WORKFLOW_BEHAVIOR_PATH; '
            'copy_source_code "$1"; create_run_script "$1"'
        )
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                destination = self.root / ("installed-autonomous" if enabled else "installed-scheduled")
                env = dict(os.environ)
                env.pop("RUSE_NO_SCHEDULE", None)
                if enabled:
                    env["RUSE_NO_SCHEDULE"] = "1"
                result = subprocess.run(
                    ["bash", "-c", command, "installer-test", str(destination)],
                    cwd=Path(__file__).parents[1], env=env, text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                run_script = (destination / "run_agent.sh").read_text()
                expected = 'export RUSE_NO_SCHEDULE="1"' if enabled else 'export RUSE_NO_SCHEDULE=""'
                self.assertIn(expected, run_script)

    def test_comparison_config_round_trip_and_invalid_model_fail(self):
        self.generation(direction="human")
        task = plan.build_baseline_plan(
            "colfix_v12.5.0", "human", llm_comparison=True
        )[0]
        feedback.generate_baseline_config(task, self.deploy)
        path = self.deploy / task["config_name"] / "config.yaml"
        loaded = DeploymentConfig.load(path)
        self.assertTrue(loaded.llm_comparison)
        self.assertEqual(loaded.requested_model, "gemma4:26b")
        self.assertEqual(loaded.flavor_capacity, {"v100-1gpu.14vcpu.28g": 1})
        self.assertEqual(_validate_behavior_source(str(task["behavior_source"]), loaded), [])
        document = __import__("yaml").safe_load(path.read_text())
        document["requested_model"] = "unapproved:latest"
        path.write_text(__import__("yaml").safe_dump(document))
        with self.assertRaisesRegex(ValueError, "approved model"):
            DeploymentConfig.load(path)

        cpu_task = plan.build_baseline_plan(
            "colfix_v12.5.0", "human", llm_comparison=True
        )[20]
        feedback.generate_baseline_config(cpu_task, self.deploy)
        cpu_config = DeploymentConfig.load(
            self.deploy / cpu_task["config_name"] / "config.yaml"
        )
        self.assertEqual(cpu_config.comparison_hardware, "cpu")
        self.assertEqual(cpu_config.gpu_tier, "cpu")
        self.assertEqual(cpu_config.flavor_capacity, {"v1.14vcpu.28g": 1})

    def test_comparison_model_reaches_installer_pull_and_service_environment(self):
        playbook = (Path(__file__).parents[1] / "deployment_engine/playbooks/decoy/install-sups.yaml").read_text()
        installer = (Path(__file__).parents[1] / "INSTALL_SUP.sh").read_text()
        ollama_installer = (
            Path(__file__).parents[1] / "decoys/install_scripts/install_ollama.sh"
        ).read_text()
        self.assertEqual(playbook.count('RUSE_WORKFLOW_MODEL="{{ workflow_model }}"'), 2)
        self.assertEqual(playbook.count('RUSE_LLM_COMPARISON="{{ \'1\' if llm_comparison | bool else \'\' }}"'), 2)
        self.assertIn('MODEL_NAMES["comparison"]="$requested_model"', installer)
        self.assertIn('export OLLAMA_MODELS="$model_name"', installer)
        self.assertIn('export OLLAMA_MODEL="$model_name"', installer)
        self.assertIn('export RUSE_WORKFLOW_MODEL="${RUSE_WORKFLOW_MODEL:-}"', installer)
        self.assertIn('grep -Fqx -- "$model"', ollama_installer)
        self.assertIn('"${RUSE_LLM_COMPARISON:-}" == "1"', installer)
        self.assertIn('"${RUSE_COMPARISON_HARDWARE:-}" == "cpu"', installer)

    def test_install_playbook_boolean_flags_render_ansible_extra_vars(self):
        with patch.dict(
            os.environ,
            {"ANSIBLE_LOCAL_TEMP": str(self.root / "ansible-tmp")},
        ):
            from ansible.plugins.filter.core import FilterModule
        from jinja2 import Environment
        import yaml

        playbook_path = (
            Path(__file__).parents[1]
            / "deployment_engine/playbooks/decoy/install-sups.yaml"
        )
        document = yaml.safe_load(playbook_path.read_text())
        stage_shells = [
            task["shell"]
            for task in document[0]["tasks"]
            if task["name"].startswith(("Stage 1:", "Stage 2:"))
        ]
        self.assertEqual(len(stage_shells), 2)

        environment = Environment()
        environment.filters["bool"] = FilterModule().filters()["bool"]
        common = {
            "ruse_path": "/opt/ruse",
            "workflow_gpu_tier": "v100",
            "workflow_model": "",
            "comparison_hardware": "",
            "sup_behavior": "browseruse-gpu",
            "staged_workflow_plan": "/tmp/plan.json",
            "canonical_workflow_configs": ["browseruse-gpu"],
            "probe_install": False,
            "probe_workflow": "",
            "probe_started_at": "",
            "probe_service": "",
            "staged_probe_plans": "/tmp/probe-plans",
        }
        cases = (
            ("ordinary-native", False, False, "", ""),
            ("ordinary-PythonFalse", "False", "False", "", ""),
            ("ordinary-lowercase-false", "false", "false", "", ""),
            ("scheduled-comparison", "true", "false", "1", ""),
            ("autonomous-native", True, True, "1", "1"),
            ("autonomous-string", "True", "true", "1", "1"),
        )
        for name, comparison, no_schedule, expected_comparison, expected_schedule in cases:
            with self.subTest(name=name):
                values = {
                    **common,
                    "llm_comparison": comparison,
                    "no_schedule": no_schedule,
                }
                for shell in stage_shells:
                    rendered = environment.from_string(shell).render(values)
                    self.assertIn(
                        f'RUSE_LLM_COMPARISON="{expected_comparison}"', rendered
                    )
                    self.assertIn(
                        f'RUSE_NO_SCHEDULE="{expected_schedule}"', rendered
                    )

    def test_comparison_lifecycle_preflight_prefixes_and_selected_sidecar(self):
        missing_show = patch.object(plan, "show_plan_and_confirm")
        missing_execute = patch.object(plan, "execute_plan")
        with missing_show as show, missing_execute as execute:
            self.assertEqual(cli._cmd_deploy([
                "--decoys", "--baseline", "--human", "--llm-comparison"
            ]), 1)
        show.assert_not_called()
        execute.assert_not_called()

        old = self.generation(direction="human", timestamp="2026-09-20_1200Z")
        invalid = self.generation(direction="human", timestamp="2026-09-21_1200Z")
        (invalid / "browseruse-v1.json").unlink()
        with patch.object(plan, "show_plan_and_confirm") as show, \
             patch.object(plan, "execute_plan") as execute:
            self.assertEqual(cli._cmd_deploy([
                "--decoys", "--baseline", "--human", "--llm-comparison"
            ]), 1)
        show.assert_not_called()
        execute.assert_not_called()
        self.assertTrue(old.is_dir())

        (invalid / "browseruse-v1.json").write_text(
            (old / "browseruse-v1.json").read_text()
        )
        before = {path.name: path.read_bytes() for path in invalid.iterdir()}
        original = feedback.find_decoy_baseline_generation
        with patch.object(
            feedback, "find_decoy_baseline_generation", wraps=original
        ) as find:
            tasks = plan.build_baseline_plan(
                "colfix_v12.5.0", "human", llm_comparison=True
            )
        find.assert_called_once_with("colfix_v12.5.0", "human")
        self.assertTrue(all(task["behavior_source"] == invalid for task in tasks))
        self.assertEqual(
            {path.name: path.read_bytes() for path in invalid.iterdir()}, before
        )

        first, second = tasks[:2]
        first_config = DeploymentConfig(
            deployment_name=first["config_name"], purpose="control", target=None,
            deployments=first["deployments"], baseline_direction="human",
            model_preset="colfix_v12.5.0", llm_comparison=True,
            requested_model=first["requested_model"], gpu_tier="v100",
        )
        second_config = DeploymentConfig(
            deployment_name=second["config_name"], purpose="control", target=None,
            deployments=second["deployments"], baseline_direction="human",
            model_preset="colfix_v12.5.0", llm_comparison=True,
            requested_model=second["requested_model"], gpu_tier="v100",
        )
        run_id = "2026-10-09_120000Z"
        first_prefix = make_vm_prefix(make_run_dep_id(first["config_name"], run_id))
        second_prefix = make_vm_prefix(make_run_dep_id(second["config_name"], run_id))
        self.assertFalse(first_prefix.startswith(second_prefix))
        self.assertFalse(second_prefix.startswith(first_prefix))
        statuses = {
            first_prefix + "browseruse-gpu-0": "ACTIVE",
            second_prefix + "smolagents-gpu-0": "ACTIVE",
        }
        self.assertTrue(list_command.has_exact_run_vm(
            first["config_name"], run_id, first_config, statuses
        ))
        self.assertTrue(list_command.has_exact_run_vm(
            second["config_name"], run_id, second_config, statuses
        ))
        self.assertFalse(list_command.has_exact_run_vm(
            first["config_name"], run_id, first_config,
            {second_prefix + "smolagents-gpu-0": "ACTIVE"},
        ))

        browser_path = invalid / "browseruse-v1.json"
        browser_document = json.loads(browser_path.read_text())
        for window in browser_document["schedule"]:
            window["sequence"] = [
                entry for entry in window["sequence"]
                if entry["workflow"] != "NetworkShareAccess"
            ]
        browser_path.write_text(json.dumps(browser_document))
        self.assertFalse(feedback.decoy_generation_uses_network_share(
            invalid, purpose="control", expected_configs={"browseruse-gpu"}
        ))
        self.assertTrue(feedback.decoy_generation_uses_network_share(
            invalid, purpose="control", expected_configs={"smolagents-gpu"}
        ))

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
