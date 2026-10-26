import json
import threading
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from phase_workflow.autonomous import (
    AutonomousHttpsUpload,
    AutonomousShareAccess,
    AutonomousDecisionError,
    AutonomousExecutor,
    OllamaAutonomousPlanner,
    autonomous_task,
    parse_autonomous_decision,
    _thaw,
)
from phase_workflow.registry import WorkflowResult


class AutonomousDecisionTests(unittest.TestCase):
    def parse(self, activity, wait=0):
        return parse_autonomous_decision(json.dumps({
            "wait_seconds": wait,
            "activity": activity,
        }))

    def test_idle_requires_model_selected_positive_wait(self):
        self.assertIsNone(self.parse(None, wait=12.5).activity)
        for wait in (0, -1, float("inf")):
            with self.subTest(wait=wait), self.assertRaises(AutonomousDecisionError):
                self.parse(None, wait=wait)

    def test_all_six_dynamic_resource_shapes_are_accepted(self):
        choices = {
            "WebResearch": {"kind": "direct_url", "url": "https://example.net/new"},
            "VideoViewing": {
                "kind": "hls_video", "url": "https://media.example/live.m3u8",
                "play_seconds": 300,
            },
            "DocumentCreation": {
                "kind": "document", "filename": "chosen.odt", "title": "Chosen",
                "sections": {"Notes": ["Model-selected content"]},
            },
            "FileDownload": {
                "kind": "https_download", "url": "https://files.example/item.bin",
                "expected_bytes": 731,
            },
            "FileSyncUpload": {
                "kind": "https_upload", "url": "https://uploads.example/receive",
            },
            "NetworkShareAccess": {
                "kind": "kerberos_smb_share", "unc": "//files.example/Public",
                "path": "Public/chosen.txt",
            },
        }
        for workflow, resource in choices.items():
            with self.subTest(workflow=workflow):
                decision = self.parse({
                    "workflow": workflow,
                    "resource": resource,
                    "instruction": f"Perform {workflow} using the chosen resource",
                })
                self.assertEqual(decision.activity.workflow, workflow)
                self.assertEqual(decision.activity.resource["kind"], resource["kind"])

    def test_resources_are_not_restricted_to_installed_catalog_values(self):
        decision = self.parse({
            "workflow": "FileDownload",
            "resource": {
                "kind": "https_download",
                "url": "https://unlisted.example/data.bin",
                "expected_bytes": 12345,
            },
            "instruction": "Download this model-selected file",
        })
        self.assertEqual(decision.activity.resource["expected_bytes"], 12345)

    def test_malformed_or_unsafe_decisions_fail_closed(self):
        invalid = [
            "not json",
            json.dumps({"wait_seconds": 1}),
            json.dumps({"wait_seconds": 0, "activity": {"workflow": "Other",
                        "resource": {}, "instruction": "x"}}),
            json.dumps({"wait_seconds": 0, "activity": {
                "workflow": "FileSyncUpload",
                "resource": {"kind": "https_upload", "url": "https://u:p@example/a"},
                "instruction": "upload",
            }}),
            json.dumps({"wait_seconds": 0, "activity": {
                "workflow": "NetworkShareAccess",
                "resource": {"kind": "kerberos_smb_share", "unc": "//host/share",
                             "path": "../secret"},
                "instruction": "access",
            }}),
            json.dumps({"wait_seconds": 0, "activity": {
                "workflow": "VideoViewing",
                "resource": {"kind": "hls_video", "url": "https://x/v", "play_seconds": 2},
                "instruction": "watch",
            }}),
            json.dumps({"wait_seconds": 0, "activity": {
                "workflow": "DocumentCreation",
                "resource": {"kind": "document", "filename": "bad\".odt",
                             "title": "x", "sections": {"x": ["y"]}},
                "instruction": "write",
            }}),
        ]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(AutonomousDecisionError):
                parse_autonomous_decision(raw)

    def test_dynamic_task_uses_installed_identity_not_catalog_resource(self):
        activity = self.parse({
            "workflow": "WebResearch",
            "resource": {"kind": "search_query", "query": "model chosen topic"},
            "instruction": "Research this topic",
        }).activity
        plan = SimpleNamespace(
            sup_config="browseruse-gpu", brain="browseruse", brain_profile_id="bu-v1"
        )
        task = autonomous_task(activity, plan, "autonomous-session-3")
        self.assertEqual(task.resource_profile, "autonomous")
        self.assertEqual(task.resource_id, "autonomous-session-3")
        self.assertEqual(task.resource["query"], "model chosen topic")

    def test_nested_resource_content_is_immutable(self):
        activity = self.parse({
            "workflow": "DocumentCreation",
            "resource": {"kind": "document", "filename": "safe.odt", "title": "T",
                         "sections": {"A": ["line"]}},
            "instruction": "write",
        }).activity
        with self.assertRaises(TypeError):
            activity.resource["sections"]["A"] = ("changed",)
        with self.assertRaises(TypeError):
            activity.resource["sections"]["A"][0] = "changed"


class _Logger:
    def __init__(self):
        self.session_id = "test-session"
        self.events = []

    def info(self, message, details):
        self.events.append(("info", message, details))

    def warning(self, message, details):
        self.events.append(("warning", message, details))

    def llm_request(self, **details):
        self.events.append(("llm_request", details))

    def llm_response(self, **details):
        self.events.append(("llm_response", details))

    def llm_error(self, **details):
        self.events.append(("llm_error", details))


class _Registry:
    def __init__(self, stop, *, fail=False):
        self.stop = stop
        self.fail = fail
        self.calls = []

    def execute(self, task, local_day):
        self.calls.append((task, local_day))
        self.stop.set()
        if self.fail:
            raise RuntimeError("activity failed")
        return WorkflowResult(completed=True, artifact="artifact")


class AutonomousExecutorTests(unittest.TestCase):
    def plan(self):
        return SimpleNamespace(
            sup_config="smolagents-gpu", brain="smolagents",
            brain_profile_id="smol-v1", timezone=ZoneInfo("America/New_York"),
            max_parallel=1,
        )

    def decision(self):
        return json.dumps({
            "wait_seconds": 0,
            "activity": {
                "workflow": "WebResearch",
                "resource": {"kind": "search_query", "query": "new topic"},
                "instruction": "Research the selected topic",
            },
        })

    def test_executes_choice_and_logs_identity_and_actual_times(self):
        stop, logger = threading.Event(), _Logger()
        registry = _Registry(stop)
        now = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)
        AutonomousExecutor(
            self.plan(), lambda _snapshot: self.decision(), registry, logger,
            stop_event=stop, now=lambda: now,
        ).run_forever()
        self.assertEqual(len(registry.calls), 1)
        task, local_day = registry.calls[0]
        self.assertEqual(task.occurrence_id, "autonomous-test-session-1")
        self.assertEqual(local_day, "2026-10-26")
        terminal = logger.events[-1][2]
        self.assertEqual(terminal["status"], "success")
        self.assertIn("chosen_at", terminal)
        self.assertIn("actual_start", terminal)
        self.assertIn("actual_end", terminal)

    def test_activity_failure_is_logged_and_not_raised_as_planning_failure(self):
        stop, logger = threading.Event(), _Logger()
        AutonomousExecutor(
            self.plan(), lambda _snapshot: self.decision(), _Registry(stop, fail=True), logger,
            stop_event=stop,
        ).run_forever()
        self.assertEqual(logger.events[-1][0:2], ("warning", "Autonomous activity failed"))
        self.assertEqual(logger.events[-1][2]["status"], "failed")

    def test_invalid_planner_output_is_fatal_without_execution(self):
        stop, logger = threading.Event(), _Logger()
        registry = _Registry(stop)
        with self.assertRaises(AutonomousDecisionError):
            AutonomousExecutor(
                self.plan(), lambda _snapshot: "invalid", registry, logger, stop_event=stop
            ).run_forever()
        self.assertEqual(registry.calls, [])

    def test_invalid_next_decision_waits_for_running_owned_work(self):
        release, finished, logger = threading.Event(), threading.Event(), _Logger()
        plan = self.plan()
        plan.max_parallel = 2

        class Registry:
            def execute(self, _task, _day):
                release.wait(1)
                finished.set()
                return WorkflowResult(completed=True)

        decisions = iter((self.decision(), "invalid"))

        def planner(_snapshot):
            value = next(decisions)
            if value == "invalid":
                release.set()
            return value

        with self.assertRaises(AutonomousDecisionError):
            AutonomousExecutor(plan, planner, Registry(), logger).run_forever()
        self.assertTrue(finished.is_set())

    def test_allows_overlap_to_limit_and_admits_third_after_capacity(self):
        gate, two_started, stop, logger = (
            threading.Event(), threading.Event(), threading.Event(), _Logger()
        )
        plan = self.plan()
        plan.max_parallel = 2
        lock = threading.Lock()
        state = {"active": 0, "maximum": 0, "started": 0, "planner_calls": 0}

        class Registry:
            def execute(self, _task, _day):
                with lock:
                    state["active"] += 1
                    state["started"] += 1
                    state["maximum"] = max(state["maximum"], state["active"])
                    if state["started"] == 2:
                        two_started.set()
                    third = state["started"] == 3
                if third:
                    stop.set()
                else:
                    gate.wait(1)
                with lock:
                    state["active"] -= 1
                return WorkflowResult(completed=True)

        def planner(_snapshot):
            with lock:
                state["planner_calls"] += 1
            return self.decision()

        executor = AutonomousExecutor(plan, planner, Registry(), logger, stop_event=stop)
        thread = threading.Thread(target=executor.run_forever)
        thread.start()
        self.assertTrue(two_started.wait(1))
        with lock:
            self.assertEqual(state["planner_calls"], 2)
        gate.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(state["maximum"], 2)
        self.assertEqual(state["started"], 3)


class _OllamaClient:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        content = self.content

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"content": content}, "prompt_eval_count": 4,
                        "eval_count": 2}

            def close(self):
                pass

        return Response()


class PlannerTests(unittest.TestCase):
    def test_uses_selected_model_context_and_safe_state_only(self):
        raw = '{"wait_seconds": 3, "activity": null}'
        client, logger = _OllamaClient(raw), _Logger()
        planner = OllamaAutonomousPlanner("chosen:model", logger, requests_api=client)
        self.assertEqual(planner({"available_slots": 2, "running": [],
                                  "last_result": None}), raw)
        url, call = client.calls[0]
        self.assertEqual(url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(call["json"]["model"], "chosen:model")
        self.assertEqual(call["json"]["format"], "json")
        self.assertFalse(call["json"]["stream"])
        self.assertIn("num_ctx", call["json"]["options"])
        self.assertEqual(call["timeout"], 300)
        self.assertEqual(logger.events[-1][1]["output"],
                         "autonomous planner response received")
        self.assertNotIn(raw, str(logger.events))

    def test_real_agent_logger_serializes_thawed_nested_resource(self):
        from common.logging.agent_logger import AgentLogger
        decision = parse_autonomous_decision(json.dumps({
            "wait_seconds": 0,
            "activity": {"workflow": "DocumentCreation", "instruction": "write",
                         "resource": {"kind": "document", "filename": "safe.odt",
                                      "title": "Safe", "sections": {"A": ["line"]}}},
        }))
        with tempfile.TemporaryDirectory() as td:
            logger = AgentLogger("test", log_dir=td, session_id="serialization")
            logger.info("decision", {"resource": _thaw(decision.activity.resource)})
            lines = logger.log_file.read_text().splitlines()
            logger.close()
        self.assertEqual(json.loads(lines[-1])["details"]["resource"]["sections"]["A"],
                         ["line"])


class _Response:
    def __init__(self, error=None):
        self.closed = False
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def close(self):
        self.closed = True


class _Requests:
    def __init__(self):
        self.calls = []
        self.response = _Response()

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class AutonomousAdapterTests(unittest.TestCase):
    def test_dynamic_upload_posts_owned_document_to_selected_https_url(self):
        from phase_workflow.workflows import DailyDocumentStore
        documents, requests = DailyDocumentStore(), _Requests()
        with tempfile.TemporaryDirectory() as td:
            workspace = __import__("pathlib").Path(td) / "2026-10-26"
            workspace.mkdir()
            artifact = workspace / "owned.odt"
            artifact.write_bytes(b"owned")
            documents.register(workspace.name, artifact)
            task = SimpleNamespace(
                resource={"kind": "https_upload", "url": "https://new.example/upload"},
                occurrence_id="autonomous-session-1",
            )
            result = AutonomousHttpsUpload(documents, requests).execute(task, workspace)
        self.assertTrue(result.completed)
        self.assertEqual(requests.calls[0][0], "https://new.example/upload")
        self.assertEqual(requests.calls[0][1]["data"], b"owned")
        self.assertTrue(requests.response.closed)

    def test_dynamic_upload_non2xx_closes_and_releases_owned_document(self):
        from phase_workflow.workflows import DailyDocumentStore
        documents, requests = DailyDocumentStore(), _Requests()
        requests.response = _Response(RuntimeError("503"))
        with tempfile.TemporaryDirectory() as td:
            workspace = __import__("pathlib").Path(td) / "2026-10-26"
            workspace.mkdir()
            artifact = workspace / "owned.odt"
            artifact.write_bytes(b"owned")
            documents.register(workspace.name, artifact)
            task = SimpleNamespace(
                resource={"kind": "https_upload", "url": "https://new.example/upload"},
                occurrence_id="autonomous-session-1",
            )
            with self.assertRaisesRegex(RuntimeError, "503"):
                AutonomousHttpsUpload(documents, requests).execute(task, workspace)
            self.assertEqual(documents.state(workspace.name, artifact),
                             (False, False, set()))
        self.assertTrue(requests.response.closed)

    def test_external_share_forces_anonymous_and_scrubs_credentials(self):
        from phase_workflow.workflows import DailyDocumentStore
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs["env"]))
            raise RuntimeError("anonymous access rejected")

        documents = DailyDocumentStore()
        task = SimpleNamespace(
            resource={"kind": "kerberos_smb_share", "unc": "//other.example/public",
                      "path": "seed/file.txt"},
            occurrence_id="autonomous-session-1", sup_config="browseruse-gpu",
        )
        with tempfile.TemporaryDirectory() as td, patch.dict(
            __import__("os").environ,
            {"KRB5CCNAME": "secret", "PASSWD": "secret", "PASSWD_FD": "9",
             "PASSWD_FILE": "/secret"},
        ):
            workspace = __import__("pathlib").Path(td) / "2026-10-26"
            workspace.mkdir()
            with self.assertRaisesRegex(RuntimeError, "anonymous"):
                AutonomousShareAccess(documents, runner=runner).execute(task, workspace)
        argv, env = calls[0]
        self.assertIn("--use-kerberos=off", argv)
        self.assertIn("--no-pass", argv)
        self.assertNotIn("kinit", argv)
        self.assertIn("--user=%", argv)
        for name in ("KRB5CCNAME", "PASSWD", "PASSWD_FD", "PASSWD_FILE"):
            self.assertNotIn(name, env)

    def test_share_roundtrip_success_cleans_owned_remote_and_fleet_uses_kerberos(self):
        from pathlib import Path
        from phase_workflow.workflows import DailyDocumentStore, SHARE_UNC
        documents, calls, uploaded = DailyDocumentStore(), [], {}

        def runner(argv, **kwargs):
            calls.append(argv)
            if "smbclient" in argv:
                command = argv[argv.index("-c") + 1]
                quoted = __import__("re").findall(r'"([^"]+)"', command)
                if command.startswith("get "):
                    destination = Path(quoted[1])
                    destination.write_bytes(uploaded.get(quoted[0], b"seed"))
                elif "put " in command:
                    uploaded[quoted[-1]] = Path(quoted[-2]).read_bytes()
                elif command.startswith("del "):
                    uploaded.pop(quoted[0], None)
            return SimpleNamespace(stdout="ok")

        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td) / "2026-10-26"
            workspace.mkdir()
            task = SimpleNamespace(
                resource={"kind": "kerberos_smb_share", "unc": SHARE_UNC,
                          "path": "Team/model-choice.txt"},
                occurrence_id="autonomous-session-1", sup_config="browseruse-gpu",
            )
            result = AutonomousShareAccess(documents, runner=runner).execute(task, workspace)
        self.assertTrue(result.completed)
        self.assertTrue(any("kinit" in argv for argv in calls))
        smb = [argv for argv in calls if "smbclient" in argv]
        self.assertTrue(all("--use-kerberos=required" in argv for argv in smb))
        self.assertEqual(uploaded, {})

    def test_share_roundtrip_mismatch_still_deletes_owned_remote(self):
        from pathlib import Path
        from phase_workflow.workflows import DailyDocumentStore
        documents, commands = DailyDocumentStore(), []

        def runner(argv, **kwargs):
            if "smbclient" in argv:
                command = argv[argv.index("-c") + 1]
                commands.append(command)
                quoted = __import__("re").findall(r'"([^"]+)"', command)
                if command.startswith("get "):
                    Path(quoted[1]).write_bytes(b"wrong")
            return SimpleNamespace(stdout="ok")

        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td) / "2026-10-26"
            workspace.mkdir()
            task = SimpleNamespace(
                resource={"kind": "kerberos_smb_share", "unc": "//other.example/public",
                          "path": "seed/file.txt"},
                occurrence_id="autonomous-session-1", sup_config="smolagents-gpu",
            )
            with self.assertRaisesRegex(RuntimeError, "round-trip mismatch"):
                AutonomousShareAccess(documents, runner=runner).execute(task, workspace)
        self.assertTrue(any(command.startswith("del ") for command in commands))


class RuntimeSelectionTests(unittest.TestCase):
    def test_opt_in_selects_autonomous_executor_and_not_daily_executor(self):
        from phase_workflow.runtime import run_workflow_runtime
        from tests.test_phase_workflow_runtime import load_control_plan

        plan = load_control_plan("smolagents-gpu")
        logger, executor = Mock(), Mock()
        logger.session_id = "runtime-test"
        model = "gemma4:26b"
        with (
            patch.dict(__import__("os").environ, {
                "RUSE_NO_SCHEDULE": "1", "RUSE_LLM_COMPARISON": "1",
                "RUSE_WORKFLOW_MODEL": model, "RUSE_WORKFLOW_GPU_TIER": "v100",
                "OLLAMA_MODEL": model,
            }, clear=True),
            patch("phase_workflow.runtime.load_workflow_plan", return_value=plan),
            patch("phase_workflow.runtime.AgentLogger", return_value=logger),
            patch("phase_workflow.runtime.build_brain"),
            patch("phase_workflow.runtime.WorkflowRegistry"),
            patch("phase_workflow.runtime.DailyExecutor") as daily,
            patch("phase_workflow.autonomous.OllamaAutonomousPlanner"),
            patch("phase_workflow.autonomous.AutonomousExecutor", return_value=executor) as auto,
        ):
            run_workflow_runtime("smolagents-gpu", "/tmp/assigned")
        daily.assert_not_called()
        auto.assert_called_once()
        config = logger.session_start.call_args.kwargs["config"]
        self.assertEqual(config["resource_selection"], "model_selected")
        self.assertFalse(config["schedule_applied"])


class DynamicFrameworkExecutionTests(unittest.TestCase):
    def test_model_selected_upload_reaches_both_framework_runners(self):
        from pathlib import Path
        from phase_workflow.brains import browseruse_runner, smolagents_runner
        from tests.test_phase_workflow_runtime import TransferWorkflowTests, load_control_plan

        class Transfer:
            def __init__(self): self.tasks = []
            def execute(self, task, workspace):
                self.tasks.append(task)
                artifact = workspace / "owned"
                artifact.write_bytes(b"owned")
                return WorkflowResult(True, str(artifact))

        for config, runner, api in (
            ("browseruse-gpu", browseruse_runner, TransferWorkflowTests.browser_api(1)),
            ("smolagents-gpu", smolagents_runner, TransferWorkflowTests.smol_api(1)),
        ):
            with self.subTest(config=config), tempfile.TemporaryDirectory() as td:
                plan = load_control_plan(config)
                activity = parse_autonomous_decision(json.dumps({
                    "wait_seconds": 0, "activity": {
                        "workflow": "FileSyncUpload", "instruction": "upload",
                        "resource": {"kind": "https_upload",
                                     "url": "https://chosen.example/new"},
                    },
                })).activity
                task = autonomous_task(activity, plan, "autonomous-session-1")
                transfer = Transfer()
                kwargs = {"syncer": transfer, "framework_api": api}
                if runner is browseruse_runner:
                    kwargs.update(llm_factory=lambda model, logger: model,
                                  step_logger=lambda logger, result: None,
                                  chromium_args=[], workflow_timeout=2)
                async def direct_thread(function):
                    return function()
                with (
                    patch("phase_workflow.brains._require_distribution"),
                    patch("phase_workflow.brains.asyncio.to_thread", direct_thread),
                    patch("requests.sessions.Session.request",
                          side_effect=AssertionError("unexpected network request")) as network,
                ):
                    result = runner(task, Path(td), plan.brain_profile, **kwargs)
                network.assert_not_called()
                self.assertTrue(result.completed)
                self.assertEqual(transfer.tasks[0].resource["url"],
                                 "https://chosen.example/new")


if __name__ == "__main__":
    unittest.main()
