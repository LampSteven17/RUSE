"""
SmolAgents Brain - HuggingFace smolagents-based research agent.

Supports three-prompt configuration for content and mechanics control.
"""
import os
import time
from datetime import datetime
from typing import Optional

from smolagents import CodeAgent, LiteLLMModel, DuckDuckGoSearchTool

from common.logging.agent_logger import AgentLogger


def log(msg: str):
    """Print with timestamp to console."""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] {msg}")

from common.config.model_config import get_model
from brains.smolagents.prompts import SMOLPrompts, DEFAULT_PROMPTS


class SmolAgent:
    """
    SmolAgents agent with three-prompt support.

    Configurations:
    - S1.llama: DEFAULT_PROMPTS + llama3.1:8b
    - S2.gemma: DEFAULT_PROMPTS + gemma3:4b
    - S3.deepseek: DEFAULT_PROMPTS + deepseek-r1:8b
    - S?.model+: PHASE_PROMPTS + any model (POST-PHASE)
    """

    def __init__(
        self,
        prompts: SMOLPrompts = DEFAULT_PROMPTS,
        model: str = None,
        tools: list = None,
        logger: Optional[AgentLogger] = None,
    ):
        self.prompts = prompts
        self.model_name = get_model(model)
        self.tools = tools
        self.logger = logger

        # Build the LiteLLM model ID (Ollama format)
        model_id = f"ollama/{self.model_name}"
        self._llm = LiteLLMModel(model_id=model_id)

        # Default tools if none provided
        if self.tools is None:
            self.tools = [DuckDuckGoSearchTool()]

        # Build system prompt from content + mechanics
        system_prompt = prompts.build_system_prompt()

        # Create the underlying CodeAgent
        self._agent = CodeAgent(
            tools=self.tools,
            model=self._llm,
            system_prompt=system_prompt,
        )

    def _log(self, msg: str, level: str = "info", **details):
        """Log to both console and AgentLogger if available."""
        log(msg)  # Console output
        if self.logger:
            if level == "info":
                self.logger.info(msg, details if details else None)
            elif level == "warning":
                self.logger.warning(msg, details if details else None)
            elif level == "error":
                self.logger.error(msg, **details)

    def run(self, task: str) -> str:
        """
        Run a task with configured prompts.

        Args:
            task: The specific task to perform (e.g., "What is the weather in Paris?")

        Returns:
            Result from the agent
        """
        self._log(f"Starting SmolAgents agent with model: {self.model_name}")
        self._log(f"Task: {task}")

        if self.logger:
            self.logger.workflow_start("agent_run", params={
                "task": task[:500] if len(task) > 500 else task,
                "model": self.model_name,
            })

        start_time = time.time()
        try:
            if self.logger:
                self.logger.llm_request(action="agent_run", input_data={"task": task}, model=self.model_name)

            result = self._agent.run(task)
            duration_ms = int((time.time() - start_time) * 1000)

            self._log("Task completed successfully!")

            if self.logger:
                self.logger.llm_response(
                    output=str(result) if result else "",
                    duration_ms=duration_ms,
                    model=self.model_name,
                )
                self.logger.workflow_end("agent_run", success=True, duration_ms=duration_ms, result=result)

            return result
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            error_msg = str(e)

            self._log(f"Error running agent: {e}", level="error", exception=e, fatal=False)

            import traceback
            traceback.print_exc()

            if self.logger:
                self.logger.llm_error(error=error_msg, action="agent_run", fatal=False)
                self.logger.workflow_end("agent_run", success=False, duration_ms=duration_ms, error=error_msg)

            return None


def run(task: str, model: str = None, prompts: SMOLPrompts = DEFAULT_PROMPTS,
        tools: list = None, logger: Optional[AgentLogger] = None) -> Optional[str]:
    """Convenience function to run SmolAgents agent."""
    agent = SmolAgent(
        prompts=prompts,
        model=model,
        tools=tools,
        logger=logger,
    )
    return agent.run(task)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='SmolAgents Agent')
    parser.add_argument('task', nargs='?', default="What is the current weather in Paris?")
    parser.add_argument('--model', type=str, default=None, help='Model key: llama, gemma, deepseek')
    parser.add_argument('--phase', action='store_true', help='Use PHASE-improved prompts')
    args = parser.parse_args()

    from brains.smolagents.prompts import PHASE_PROMPTS

    prompts = PHASE_PROMPTS if args.phase else DEFAULT_PROMPTS

    run(
        task=args.task,
        model=args.model,
        prompts=prompts,
    )
