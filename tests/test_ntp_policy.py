"""Policy tests never call host services or change the clock."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
import signal
import subprocess

from phase_workflow.loader import parse_workflow_plan, WorkflowPlanError
from phase_workflow.executor import DailyExecutor
from phase_workflow.ntp_policy import NtpPolicy, ntp_lifecycle, command
from phase_workflow.runtime import run_workflow_runtime
from tests.test_phase_workflow_runtime import control_document, feedback_document


def stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


class NativeNtp:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.calls = []
        self.timeouts = []
        self.fail = False
        self.readbacks_before_apply = 0
        self.transition_pending = False
        self.pending_enabled = enabled
        self.query_fail_after = None
        self.queries = 0

    def __call__(self, *args, timeout):
        self.calls.append(args)
        self.timeouts.append(timeout)
        if args[0] == "timedatectl":
            self.queries += 1
            if self.query_fail_after == self.queries:
                raise RuntimeError("native state query failed")
            reported = self.enabled
            if self.transition_pending and self.readbacks_before_apply:
                self.readbacks_before_apply -= 1
                if not self.readbacks_before_apply:
                    self.enabled = self.pending_enabled
                    self.transition_pending = False
            return f'CanNTP=yes\nNTP={"yes" if reported else "no"}\n'
        if self.fail:
            raise RuntimeError("native service transition failed")
        self.pending_enabled = args[-1] == "true"
        self.transition_pending = True
        if not self.readbacks_before_apply:
            self.enabled = self.pending_enabled
            self.transition_pending = False
        return ""

    @property
    def transitions(self):
        return [args[-1] for args in self.calls if args[0] == "sudo"]


class FakeTime:
    def __init__(self):
        self.value = 0
        self.sleeps = []

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


class NtpPolicyTests(TestCase):
    def make(self, intervals=((75, 105),), enabled=True, clock=None):
        native, logger = NativeNtp(enabled), Mock()
        clock = clock or FakeTime()
        policy = NtpPolicy(intervals, ZoneInfo("America/New_York"), logger,
                           run=native, now=lambda: stamp("2026-11-01T07:00:00"),
                           monotonic=clock.monotonic, sleep=clock.sleep)
        return policy, native, logger

    def test_all_brains_and_profiles_accept_optional_policy_and_preserve_windows(self):
        for sup in ("scripted-cpu", "mchp-cpu", "browseruse-gpu", "smolagents-gpu"):
            for factory in (control_document, feedback_document):
                document = factory(sup)
                old = parse_workflow_plan(document, sup)
                self.assertIsNone(old.ntp_off_intervals)
                for enabled in (False, True):
                    document["background_services"] = {"enabled": enabled, "ntp": {"off_intervals_local": [[60, 120]]}}
                    new = parse_workflow_plan(document, sup)
                    self.assertEqual(old.windows, new.windows)
                    self.assertEqual(new.ntp_off_intervals, ((60, 120),) if enabled else None)

    def test_interval_validation_including_disabled(self):
        for intervals in ([[1, 1]], [[2, 1]], [[-1, 5]], [[0, 1441]], [[1, 2], [3, 4]], [[True, 2]], [[1]], "bad"):
            for enabled in (True, False):
                document = control_document()
                document["background_services"] = {"enabled": enabled, "ntp": {"off_intervals_local": intervals}}
                with self.subTest(intervals=intervals, enabled=enabled), self.assertRaises(WorkflowPlanError):
                    parse_workflow_plan(document, "scripted-cpu")
        for intervals in ([], [[0, 1440]]):
            document["background_services"]["enabled"] = True
            document["background_services"]["ntp"]["off_intervals_local"] = intervals
            self.assertEqual(parse_workflow_plan(document, "scripted-cpu").ntp_off_intervals,
                             tuple(map(tuple, intervals)))

    def test_startup_inside_outside_restart_and_shutdown_ownership(self):
        policy, native, _ = self.make()
        policy.update(stamp("2026-11-01T05:20:00"))
        self.assertFalse(native.enabled)
        policy.close()
        self.assertEqual(native.transitions, ["false", "true"])
        # An already-stopped service was not disabled by this runtime.
        policy, native, _ = self.make(enabled=False)
        policy.update(stamp("2026-11-01T05:20:00"))
        policy.close()
        self.assertEqual(native.transitions, [])
        # Restart outside the interval re-enables even if previously off.
        restarted = NtpPolicy(((75, 105),), policy.timezone, Mock(), run=native)
        restarted.update(stamp("2026-11-01T07:00:00"))
        self.assertEqual(native.transitions, ["true"])

    def test_empty_interval_enables_ntp_without_claiming_synchronization(self):
        policy, native, logger = self.make(intervals=(), enabled=False)
        policy.update(stamp("2026-11-01T05:20:00"))
        policy.close()
        self.assertEqual(native.transitions, ["true"])
        self.assertFalse(logger.info.call_args.args[1]["synchronization_verified"])

    def test_dst_fall_back_repeats_membership_transitions(self):
        policy, native, _ = self.make()
        for value in ("05:10", "05:15", "05:44", "05:45", "06:00", "06:15", "06:45"):
            policy.update(stamp(f"2026-11-01T{value}:00"))
        self.assertEqual(native.transitions, ["false", "true", "false", "true"])

    def test_dst_forward_reenables_after_skipped_end_and_skips_nonexistent_interval(self):
        policy, native, _ = self.make(((115, 150),))
        for value in ("06:54", "06:55", "07:00"):
            policy.update(stamp(f"2026-03-08T{value}:00"))
        self.assertEqual(native.transitions, ["false", "true"])
        policy, native, _ = self.make(((120, 150),))
        for value in ("06:59", "07:00"):
            policy.update(stamp(f"2026-03-08T{value}:00"))
        self.assertEqual(native.transitions, [])

    def test_midnight_end_1440_and_next_day(self):
        policy, native, _ = self.make(((1380, 1440),))
        for value in ("2026-09-21T03:00:00", "2026-09-21T04:00:00", "2026-09-22T03:00:00"):
            policy.update(stamp(value))
        self.assertEqual(native.transitions, ["false", "true", "false"])

    def test_failure_is_logged_and_not_treated_as_transition_success(self):
        policy, native, logger = self.make(enabled=False)
        native.fail = True
        with self.assertRaisesRegex(RuntimeError, "transition failed"):
            policy.update(stamp("2026-11-01T07:00:00"))
        self.assertIsNone(policy.last_requested)
        self.assertEqual(logger.info.call_args.args[0], "NTP policy failure")
        self.assertFalse(native.enabled)

    def test_transition_polls_old_state_once_for_enable_and_disable(self):
        for initially_enabled, desired in ((False, True), (True, False)):
            with self.subTest(initially_enabled=initially_enabled, desired=desired):
                clock = FakeTime()
                policy, native, _ = self.make(enabled=initially_enabled, clock=clock)
                native.readbacks_before_apply = 1
                policy._transition(desired, stamp("2026-11-01T07:00:00"), "test")
                self.assertEqual(native.transitions, ["true" if desired else "false"])
                self.assertEqual(clock.sleeps, [1])
                self.assertEqual(native.timeouts, [120, 120, 120, 119])

    def test_never_converging_readback_uses_one_deadline_and_retains_observation(self):
        clock = FakeTime()
        policy, native, logger = self.make(enabled=False, clock=clock)
        native.readbacks_before_apply = 999
        with self.assertRaisesRegex(RuntimeError, "before deadline"):
            policy._transition(True, stamp("2026-11-01T07:00:00"), "test")
        self.assertEqual(native.transitions, ["true"])
        self.assertEqual(clock.value, 120)
        self.assertEqual(native.timeouts[:3], [120, 120, 120])
        self.assertEqual(native.timeouts[-1], 1)
        failure = logger.info.call_args.args[1]
        self.assertEqual(failure["observed_ntp_state"], {"CanNTP": "yes", "NTP": "no"})

    def test_command_duration_consumes_the_same_transition_budget(self):
        clock = FakeTime()
        native = NativeNtp(enabled=False)

        def delayed_native(*args, timeout):
            result = native(*args, timeout=timeout)
            clock.value += 30
            return result

        policy = NtpPolicy((), ZoneInfo("America/New_York"), Mock(), run=delayed_native,
                           monotonic=clock.monotonic, sleep=clock.sleep)
        policy._transition(True, stamp("2026-11-01T07:00:00"), "test")
        self.assertEqual(native.timeouts, [120, 90, 60])

    def test_initial_query_failure_does_not_report_a_previous_transition_state(self):
        policy, native, logger = self.make(enabled=False)
        policy._transition(True, stamp("2026-11-01T07:00:00"), "first")
        native.query_fail_after = native.queries + 1
        with self.assertRaisesRegex(RuntimeError, "state query failed"):
            policy._transition(False, stamp("2026-11-01T07:00:00"), "second")
        self.assertIsNone(logger.info.call_args.args[1]["observed_ntp_state"])

    def test_query_failure_reports_last_observed_state(self):
        policy, native, logger = self.make(enabled=False)
        native.query_fail_after = 2
        with self.assertRaisesRegex(RuntimeError, "state query failed"):
            policy._transition(True, stamp("2026-11-01T07:00:00"), "test")
        self.assertEqual(logger.info.call_args.args[1]["observed_ntp_state"],
                         {"CanNTP": "yes", "NTP": "no"})

    def test_native_commands_are_bounded_and_do_not_install_clients(self):
        with patch("phase_workflow.ntp_policy.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="ok")) as run:
            self.assertEqual(command("timedatectl", "show"), "ok")
        self.assertEqual(run.call_args.kwargs["timeout"], 120)
        with patch("phase_workflow.ntp_policy.subprocess.run", side_effect=subprocess.TimeoutExpired("timedatectl", 120)):
            with self.assertRaises(subprocess.TimeoutExpired):
                command("timedatectl", "show")

    def test_missing_client_or_unconfirmed_state_fails_truthfully(self):
        policy, _, logger = self.make()
        policy.run = Mock(return_value="CanNTP=no\nNTP=no\n")
        with self.assertRaisesRegex(RuntimeError, "No usable native NTP"):
            policy.update(stamp("2026-11-01T05:20:00"))
        self.assertFalse(policy.disabled_by_ruse)
        policy.run.assert_called_once()
        # A successful native command return is insufficient if state stays off.
        policy.run = Mock(return_value="CanNTP=yes\nNTP=no\n")
        with self.assertRaisesRegex(RuntimeError, "did not reach"):
            policy.update(stamp("2026-11-01T07:00:00"))
        self.assertEqual(logger.info.call_args.args[0], "NTP policy failure")

    def test_failed_stop_restores_on_unwind_and_restore_failure_is_visible(self):
        policy, native, logger = self.make()
        native.fail = True
        with self.assertRaises(RuntimeError):
            policy.update(stamp("2026-11-01T05:20:00"))
        self.assertTrue(policy.disabled_by_ruse)
        # Model a stop which changed state before returning its error.
        native.enabled = False
        with self.assertRaises(RuntimeError):
            policy.close()
        self.assertEqual(logger.info.call_args.args[1]["reason"], "shutdown_restore")
        self.assertTrue(policy.disabled_by_ruse)
        native.fail = False
        policy.close()
        self.assertTrue(native.enabled)

    def test_runtime_cleans_workflows_then_restores_ntp_before_session_end(self):
        document = control_document()
        document["background_services"] = {"enabled": True, "ntp": {"off_intervals_local": [[60, 120]]}}
        plan = parse_workflow_plan(document, "scripted-cpu")
        events = []
        controller = Mock(close=Mock(side_effect=lambda: events.append("ntp_restore")))
        logger = Mock(session_end=Mock(side_effect=lambda: events.append("session_end")))
        executor = Mock(run_forever=Mock(side_effect=RuntimeError("worker loop failed")),
                        close=Mock(side_effect=lambda: events.append("executor_cleanup")))
        registry = Mock(close=Mock(side_effect=lambda: events.append("registry_cleanup")))
        with patch("phase_workflow.runtime.load_workflow_plan", return_value=plan), \
             patch("phase_workflow.runtime.AgentLogger", return_value=logger), \
             patch("phase_workflow.runtime.build_brain"), \
             patch("phase_workflow.runtime.WorkflowRegistry", return_value=registry), \
             patch("phase_workflow.runtime.DailyExecutor", return_value=executor), \
             patch("phase_workflow.runtime.NtpPolicy", return_value=controller), \
             self.assertRaisesRegex(RuntimeError, "worker loop failed"):
            run_workflow_runtime("scripted-cpu", "/unused")
        self.assertEqual(events, ["executor_cleanup", "registry_cleanup", "ntp_restore", "session_end"])
        logger.session_fail.assert_called_once()

    def test_enabled_lifecycle_unwinds_sigterm_and_restores_handlers(self):
        policy, native, _ = self.make()
        old = signal.getsignal(signal.SIGTERM)
        with self.assertRaises(KeyboardInterrupt):
            with ntp_lifecycle(policy):
                policy.update(stamp("2026-11-01T05:20:00"))
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        self.assertTrue(native.enabled)
        self.assertIs(signal.getsignal(signal.SIGTERM), old)
        with patch("phase_workflow.ntp_policy.signal.signal") as change:
            with ntp_lifecycle(None):
                pass
            change.assert_not_called()

    def test_executor_observes_minute_boundaries_only_when_enabled(self):
        now = stamp("2026-11-01T05:14:58")
        clock = Mock(now=Mock(return_value=now))
        plan = SimpleNamespace(timezone=ZoneInfo("America/New_York"), max_parallel=1, windows=())
        policy = Mock()
        executor = DailyExecutor(plan, Mock(), Mock(), clock=clock, starter=Mock(), background_policy=policy)
        policy.update.assert_called_once_with(now)
        self.assertEqual(executor._seconds_until_next_clock_event(), 2)
        clock.now.return_value = stamp("2026-11-01T05:15:00")
        executor.tick()
        policy.update.assert_called_with(clock.now.return_value)
        policy.update.reset_mock()
        executor.close()
        policy.update.assert_not_called()
        unchanged = DailyExecutor(plan, Mock(), Mock(), clock=clock, starter=Mock())
        self.assertGreater(unchanged._seconds_until_next_clock_event(), 60)

    def test_missing_and_disabled_runtime_never_constructs_controller(self):
        for block in (None, {"enabled": False, "ntp": {"off_intervals_local": [[60, 120]]}}):
            document = control_document()
            if block is not None:
                document["background_services"] = block
            plan = parse_workflow_plan(document, "scripted-cpu")
            with patch("phase_workflow.runtime.load_workflow_plan", return_value=plan), \
                 patch("phase_workflow.runtime.AgentLogger"), patch("phase_workflow.runtime.build_brain"), \
                 patch("phase_workflow.runtime.WorkflowRegistry"), patch("phase_workflow.runtime.DailyExecutor") as executor, \
                 patch("phase_workflow.runtime.NtpPolicy") as controller:
                run_workflow_runtime("scripted-cpu", "/unused")
                controller.assert_not_called()
                self.assertIsNone(executor.call_args.kwargs["background_policy"])
