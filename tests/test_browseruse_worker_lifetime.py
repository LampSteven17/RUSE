"""Traffic-free regressions for cancelled-action ownership. The real
runner, shared loop, registry, scheduler, and terminal writer are exercised; the
framework and blocking media action are injected. No runtime timeout is changed.
"""

import asyncio
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common.logging.agent_logger import AgentLogger
from phase_workflow import brains
from phase_workflow.executor import DailyExecutor
from phase_workflow.registry import WorkflowRegistry, WorkflowResult
from tests import test_phase_workflow_runtime as fixtures


class BrowserUseWorkerLifetimeTests(unittest.TestCase):
    def exercise(self, cancellation=None, workflow="VideoViewing", outcome=True,
                 repeat_cancellation=False, judge_success=True,
                 swallow_after_cleanup=False):
        document = fixtures.six_workflow_document("browseruse-gpu")
        entry = dict(next(
            entry for entry in document["schedule"][0]["sequence"]
            if entry["workflow"] == workflow
        ))
        entry["offset_minutes"] = 0
        document["schedule"][0]["sequence"] = [
            entry, dict(entry, offset_minutes=1),
        ]
        plan = fixtures.load_document(document)
        clock = fixtures.FakeClock(datetime(2026, 9, 7, 9, tzinfo=plan.timezone))
        trace = []
        lock = threading.Lock()
        started = [threading.Event(), threading.Event()]
        release = [threading.Event(), threading.Event()]
        cleaned = [threading.Event(), threading.Event()]
        running = set()
        state = {}

        def record(message):
            with lock:
                trace.append(message)

        def operation(task, workspace=None):
            index = int(task.occurrence_id.rsplit("s", 1)[1])
            with lock:
                running.add(index)
                trace.append(f"action[{index}] started; live_actions={len(running)}")
            started[index].set()
            try:
                if not release[index].wait(10):
                    raise RuntimeError("test harness did not release the blocked action")
                if not outcome:
                    raise RuntimeError("injected action failure")
                if workflow == "VideoViewing":
                    return True
                if workflow == "FileDownload":
                    artifact = workspace / "assigned-download"
                    artifact.write_bytes(b"x" * task.resource["expected_bytes"])
                    return artifact
                if workflow == "DocumentCreation":
                    return brains.OpenDocumentWriter().create(task, workspace)
                return WorkflowResult(completed=True)
            finally:
                with lock:
                    running.remove(index)
                    trace.append(f"action[{index}] resources closed by its worker")
                cleaned[index].set()
                if index == 0:
                    state["loop"].call_soon_threadsafe(state["worker_cleaned"].set)

        _, Session, Tools, ActionResult = fixtures.LLMVideoRunnerTests.browser_api(False)[0]

        class History:
            def is_done(self):
                return True

            def is_successful(self):
                # Deliberately claim success after swallowed cancellation too.
                # The owned action must still force a truthful failure.
                return judge_success

        class Agent:
            def __init__(self, **values):
                self.tools = values.get("tools")
                self.index = "unrelated"
                if self.tools is not None:
                    self.index = state.setdefault("agent_count", 0)
                    state["agent_count"] += 1

            async def run(self, max_steps):
                if self.tools is None:
                    record("unrelated workflow progressed on the same event loop")
                    return History()
                action = asyncio.create_task(self.tools.act())
                if self.index == 0:
                    state["loop"] = asyncio.get_running_loop()
                    state["action"] = action
                    state["enclosing"] = asyncio.current_task()
                    state["worker_cleaned"] = asyncio.Event()
                try:
                    await action
                except asyncio.CancelledError:
                    record(f"{cancellation} cancellation observed; action[0] still running={0 in running}")
                    if cancellation == "enclosing":
                        raise
                    if swallow_after_cleanup:
                        await state["worker_cleaned"].wait()
                        record("framework claims success after cancelled worker succeeds")
                return History()

            async def close(self):
                record(f"Agent[{self.index}].close; action[{self.index}] still running={self.index in running}")

        original_runner = brains.browseruse_runner

        def injected_runner(*args, **kwargs):
            # Keep build_brain's canonical process-owned loop and all deadlines.
            execute_on_shared_loop = kwargs["async_executor"]

            def traced_executor(coroutine):
                async def trace_runner():
                    state.setdefault("runner", asyncio.current_task())
                    return await coroutine
                return execute_on_shared_loop(trace_runner())

            kwargs.update(
                video_player=operation,
                document_writer=SimpleNamespace(create=operation),
                downloader=operation,
                syncer=SimpleNamespace(execute=operation),
                share=SimpleNamespace(execute=operation),
                framework_api=(Agent, Session, Tools, ActionResult),
                llm_factory=lambda *_args: object(),
                step_logger=lambda *_args: None,
                chromium_args=[],
                async_executor=traced_executor,
            )
            return original_runner(*args, **kwargs)

        with tempfile.TemporaryDirectory(dir=fixtures.REPOSITORY_ROOT) as temporary:
            logger = AgentLogger("browseruse-gpu", log_dir=temporary, session_id="ownership-test")
            terminals = []

            def terminal_sink(details):
                logger.workflow_plan_terminal(details)
                terminals.append(details)
                record(
                    f"workflow_plan_terminal[{details['sequence_index']}] "
                    f"status={details['status']} reason={details.get('reason')}; "
                    f"action[0] still running={0 in running}"
                )

            with patch.object(brains, "_require_distribution"), patch.object(
                brains, "browseruse_runner", side_effect=injected_runner
            ):
                brain = brains.build_brain(plan.brain, plan.brain_profile)
                registry = WorkflowRegistry(plan, brain, Path(temporary) / "workspace")
                executor = DailyExecutor(plan, registry, terminal_sink, clock=clock)
                try:
                    executor.tick()
                    self.assertTrue(started[0].wait(5))
                    record(f"before cancellation/completion: scheduler_active={executor.active_count}")
                    first = executor.occurrences[0].handle
                    finished = threading.Event()
                    first.add_done_callback(lambda _future: finished.set())
                    async def checkpoint(cancel=None):
                        if cancel is not None:
                            state[cancel].cancel()
                        for _ in range(12):
                            await asyncio.sleep(0)

                    def advance_loop(cancel=None):
                        asyncio.run_coroutine_threadsafe(
                            checkpoint(cancel), state["loop"]
                        ).result(timeout=5)

                    advance_loop(cancellation)
                    if repeat_cancellation:
                        for _ in range(3):
                            advance_loop("runner")
                        record("runner cancelled three more times; ownership retained")
                    self.assertFalse(finished.is_set())
                    self.assertFalse(cleaned[0].is_set())
                    clock.value += timedelta(minutes=1)
                    executor.tick()
                    self.assertEqual(terminals, [])
                    self.assertEqual(running, {0})
                    self.assertFalse(started[1].is_set())
                    self.assertEqual(executor.active_count, 1)
                    record("while blocked: terminals=0, scheduler_active=1, action[1] not started")

                    # Another invocation using this same Brain/loop can finish
                    # while the cancelled worker is still blocked.
                    research_document = fixtures.six_workflow_document("browseruse-gpu")
                    research_plan = fixtures.load_document(research_document)
                    research_registry = WorkflowRegistry(research_plan, brain, Path(temporary))
                    research = research_registry.resolve(research_plan.windows[0].sequence[0])
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        other = pool.submit(brain.execute, research, Path(temporary))
                        self.assertEqual(other.result(timeout=5).completed, judge_success)
                    self.assertFalse(cleaned[0].is_set())

                    release[0].set()
                    self.assertTrue(cleaned[0].wait(5))
                    self.assertTrue(finished.wait(5))
                    record("workflow future done AFTER action[0] cleanup")
                    if cancellation is not None:
                        with self.assertLogs("phase_workflow.executor", level="ERROR") as errors:
                            executor.tick()
                        self.assertIn("CancelledError", "\n".join(errors.output))
                    else:
                        executor.tick()
                    self.assertTrue(started[1].wait(5))
                    expected = "completed" if cancellation is None and outcome and judge_success else "failed"
                    self.assertEqual(terminals[0]["status"], expected)
                    if expected == "failed":
                        self.assertEqual(terminals[0]["reason"], "workflow_failed")
                    self.assertEqual(running, {1})
                    self.assertEqual(executor.active_count, 1)
                    record(
                        f"slot reused: scheduler_active={executor.active_count}, "
                        f"live_actions={len(running)}, max_parallel={plan.max_parallel}"
                    )
                    events = [json.loads(line) for line in logger.log_file.read_text().splitlines()]
                    self.assertEqual(events[0]["event_type"], "workflow_plan_terminal")
                    self.assertEqual(events[0]["details"]["status"], expected)
                finally:
                    for index in range(2):
                        release[index].set()
                        if started[index].is_set():
                            self.assertTrue(cleaned[index].wait(5))
                    executor.close()
                    registry.close()
                    logger.close()

        print(f"\nCORRECTED: {workflow}, cancellation={cancellation}, success={outcome}, "
              f"judge={judge_success}, repeated={repeat_cancellation}")
        for index, message in enumerate(trace, 1):
            print(f"{index:02d}. {message}")

    def test_cancelled_action_retains_slot_until_worker_cleanup(self):
        self.exercise("action")

    def test_cancelled_enclosing_run_retains_slot_until_worker_cleanup(self):
        self.exercise("enclosing")

    def test_repeated_runner_cancellation_cannot_abandon_worker(self):
        self.exercise("action", repeat_cancellation=True)

    def test_swallowed_cancellation_and_late_success_still_fail(self):
        self.exercise("action", swallow_after_cleanup=True)

    def test_all_bounded_action_paths_retain_cancelled_workers(self):
        for workflow in ("DocumentCreation", "FileDownload", "FileSyncUpload", "NetworkShareAccess"):
            with self.subTest(workflow=workflow):
                self.exercise("action", workflow=workflow)

    def test_ordinary_success_waits_for_cleanup(self):
        self.exercise()

    def test_ordinary_failure_waits_for_cleanup(self):
        self.exercise(outcome=False)

    def test_judge_failure_remains_failure(self):
        self.exercise(judge_success=False)


if __name__ == "__main__":
    unittest.main()
