"""
SmolAgents Brain - HuggingFace smolagents-based research agent.

Supports three-prompt configuration for content and mechanics control.
"""
import os
import logging
from datetime import datetime
from typing import Optional

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
    ):
        self.prompts = prompts
        self.model_name = get_model(model)
        self.tools = tools

        # Build the LiteLLM model ID (Ollama format)
        model_id = f"ollama/{self.model_name}"
        self._llm = LiteLLMModel(model_id=model_id)

        # Default tools if none provided
        if self.tools is None:
            self.tools = [DuckDuckGoSearchTool()]

        # Build instructions from content + mechanics prompts
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

        try:
            result = self._agent.run(task)
            log("Task completed successfully!")
            return result
        except Exception as e:
            log(f"Error running agent: {e}")
            import traceback
            traceback.print_exc()
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
