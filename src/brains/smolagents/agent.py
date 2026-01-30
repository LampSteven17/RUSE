"""
SmolAgents Brain - HuggingFace smolagents-based research agent.

Supports prompt configuration for content/behavior control.
"""
import os
import logging
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger

from common.logging.llm_callbacks import setup_litellm_callbacks

# Configure logging to show smolagents library output (like browser_use does)
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)-8s [%(name)s] %(message)s'
)

from smolagents import CodeAgent, LiteLLMModel, DuckDuckGoSearchTool


def log(msg: str):
    """Print with timestamp."""
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
        logger: Optional["AgentLogger"] = None,
    ):
        self.prompts = prompts
        self.model_name = get_model(model)
        self.tools = tools
        self.logger = logger

        # Set up LLM logging callbacks if logger is provided
        if self.logger:
            setup_litellm_callbacks(self.logger)

        # Build the LiteLLM model ID (Ollama format)
        # Use 5 minute timeout for CPU models
        model_id = f"ollama/{self.model_name}"
        self._llm = LiteLLMModel(model_id=model_id, timeout=300)

        # Default tools if none provided
        if self.tools is None:
            self.tools = [DuckDuckGoSearchTool()]

        # Build instructions from content prompts
        instructions = prompts.build_system_prompt()

        # Create the underlying CodeAgent
        # Note: smolagents v1.x uses 'instructions' parameter instead of 'system_prompt'
        self._agent = CodeAgent(
            tools=self.tools,
            model=self._llm,
            instructions=instructions,
        )

    def run(self, task: str) -> str:
        """
        Run a task with configured prompts.

        Args:
            task: The specific task to perform (e.g., "What is the weather in Paris?")

        Returns:
            Result from the agent
        """
        log(f"Starting SmolAgents agent with model: {self.model_name}")
        log(f"Task: {task}")

        # Use the task itself as workflow name for better queryability
        workflow_name = task[:100] if len(task) > 100 else task

        if self.logger:
            self.logger.workflow_start(workflow_name, params={
                "agent_type": "smolagents",
                "model": self.model_name
            })

        try:
            result = self._agent.run(task)
            log("Task completed successfully!")

            if self.logger:
                self.logger.workflow_end(workflow_name, success=True,
                                        result=str(result)[:500] if result else None)
            return result
        except Exception as e:
            log(f"Error running agent: {e}")
            import traceback
            traceback.print_exc()

            if self.logger:
                self.logger.workflow_end(workflow_name, success=False, error=str(e))
                self.logger.error(f"SmolAgents task failed", exception=e)
            return None


def run(task: str, model: str = None, prompts: SMOLPrompts = DEFAULT_PROMPTS,
        tools: list = None) -> Optional[str]:
    """Convenience function to run SmolAgents agent."""
    agent = SmolAgent(
        prompts=prompts,
        model=model,
        tools=tools,
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
