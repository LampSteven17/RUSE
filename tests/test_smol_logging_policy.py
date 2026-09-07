"""Logging observes SmolAgents errors without changing Agent execution."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from common.logging.llm_callbacks import make_smol_step_callback
from phase_workflow.brains import smolagents_runner
from tests import test_phase_workflow_runtime as runtime_fixtures


class AgentParsingError(Exception):
    pass


class SmolLoggingPolicyTests(unittest.TestCase):
    def test_interleaved_agents_are_never_interrupted_by_logging(self):
        logger_a, logger_b = Mock(), Mock()
        agent_a, agent_b = Mock(), Mock()
        callback_a = make_smol_step_callback(logger_a)
        callback_b = make_smol_step_callback(logger_b)
        step = SimpleNamespace(error=AgentParsingError("invalid code"), code_action=None)
        for _ in range(3):
            callback_a(step, agent=agent_a)
        callback_b(step, agent=agent_b)
        # Continue beyond the former threshold for each Agent independently.
        for _ in range(10):
            callback_a(step, agent=agent_a)
            callback_b(step, agent=agent_b)
        agent_a.interrupt.assert_not_called()
        agent_b.interrupt.assert_not_called()
        self.assertEqual(logger_a.step_error.call_count, 13)
        self.assertEqual(logger_b.step_error.call_count, 11)
        logger_a.step_success.assert_not_called()
        logger_b.step_success.assert_not_called()
        logger_a.warning.assert_not_called()
        logger_b.warning.assert_not_called()

    def test_parse_error_is_logged_once_with_bounded_message(self):
        logger = Mock()
        callback = make_smol_step_callback(logger)
        callback(SimpleNamespace(error=AgentParsingError("x" * 500), code_action=None))
        logger.step_error.assert_called_once_with("code_parse", message="x" * 200)
        logger.step_success.assert_not_called()

    def run_scenario(self, scenario):
        fixtures = runtime_fixtures.LLMVideoRunnerTests
        plan, task = fixtures.video_task("smolagents-gpu")
        api, state = fixtures.smol_api(False)
        state["interrupt"] = Mock()
        agent_class = api[0]

        def run(agent, raw_task):
            state["task"] = raw_task
            agent.interrupt = state["interrupt"]
            values = state["agent"]
            callback = values["step_callbacks"][0]
            tool = values["tools"][0]
            if scenario in {"malformed", "then_valid"}:
                count = values["max_steps"] if scenario == "malformed" else 4
                for _ in range(count):
                    callback(SimpleNamespace(
                        error=AgentParsingError("invalid code"), code_action=None,
                    ), agent=agent)
            if scenario == "forbidden":
                # An attempted override must be rejected by the real tool interface.
                with self.assertRaises(TypeError):
                    tool(video_id="unassigned-video")
            if scenario in {"valid", "repeated", "then_valid"}:
                tool()
            if scenario == "repeated":
                with self.assertRaisesRegex(RuntimeError, "only once"):
                    tool()
            return "The video played successfully."

        agent_class.run = run
        player = Mock(return_value=True)
        logger = Mock()
        with patch("phase_workflow.brains._require_distribution"), patch(
            "common.logging.llm_callbacks.setup_litellm_callbacks"
        ):
            result = smolagents_runner(
                task, Path("/unused"), plan.brain_profile, logger,
                video_player=player, framework_api=api,
            )
        self.assertEqual(state["agent"]["max_steps"], plan.brain_profile["max_steps"])
        state["interrupt"].assert_not_called()
        return result, player, logger

    def test_malformed_missing_and_forbidden_execution_cannot_complete(self):
        for scenario in ("malformed", "missing", "forbidden"):
            with self.subTest(scenario=scenario):
                result, player, _ = self.run_scenario(scenario)
                self.assertFalse(result.completed)
                player.assert_not_called()

    def test_repeated_action_fails_without_repeating_playback(self):
        result, player, _ = self.run_scenario("repeated")
        self.assertFalse(result.completed)
        player.assert_called_once()

    def test_single_tool_invocation_completes_including_after_prior_parse_errors(self):
        for scenario in ("valid", "then_valid"):
            with self.subTest(scenario=scenario):
                result, player, logger = self.run_scenario(scenario)
                self.assertTrue(result.completed)
                player.assert_called_once()
                self.assertEqual(logger.step_error.call_count, 4 if scenario == "then_valid" else 0)


if __name__ == "__main__":
    unittest.main()
