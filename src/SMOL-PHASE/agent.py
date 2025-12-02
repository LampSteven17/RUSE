#!/usr/bin/env python3
"""
SMOL-PHASE Agent

SMOL agent with PHASE timing improvements and comprehensive logging.
Uses smolagents with LiteLLM/Ollama for local LLM inference.

Features:
- PHASE timing with time-of-day activity awareness
- Comprehensive JSON-Lines logging for experiment analysis
- Task clustering with realistic delays
- Graceful error handling and session management
"""

import os
import sys
import time
import random
from datetime import datetime
from pathlib import Path

# Add common modules to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from smolagents import CodeAgent, LiteLLMModel, DuckDuckGoSearchTool
from common.logging.agent_logger import AgentLogger
from common.timing.phase_timing import PhaseTiming, PhaseTimingConfig


# Configuration
MODEL_ID = os.getenv("LITELLM_MODEL", "ollama/llama3.1:8b")

# Task definitions - can be expanded
TASKS = [
    # Technical searches
    "Search for 'how to remove specific text from a file' and summarize the methods",
    "Look up 'Python programming tutorials' and list beginner resources",
    "Search for 'how to use vscode' and list keyboard shortcuts",
    "Find information about 'git best practices' and summarize",

    # Practical queries
    "Search for 'what is my ip address' and explain IP types",
    "Look up 'weather forecast' and explain how forecasting works",
    "Search 'best productivity tools' and list recommendations",

    # News and current events
    "Check for breaking news headlines today",
    "Look up technology news and summarize trends",
    "Search for science news and interesting discoveries",

    # Educational content
    "Search for free online courses and list platforms",
    "Look up interesting Wikipedia articles about history",
    "Find NASA space exploration updates",

    # Tech topics
    "Search for AI news and developments",
    "Look up cybersecurity best practices",
    "Find cloud computing trends and providers",
]


class SmolPhaseAgent:
    """SMOL agent with PHASE timing and comprehensive logging."""

    def __init__(self, log_dir: str = None):
        """Initialize the SMOL-PHASE agent."""
        self.logger = AgentLogger(
            agent_type="SMOL-PHASE",
            log_dir=log_dir
        )
        self.timing = PhaseTiming()

        # Initialize LLM model
        self.logger.info(f"Initializing LLM model: {MODEL_ID}")
        try:
            self.model = LiteLLMModel(model_id=MODEL_ID)
            self.agent = CodeAgent(
                tools=[DuckDuckGoSearchTool()],
                model=self.model,
            )
            self.logger.info("LLM model initialized successfully")
        except Exception as e:
            self.logger.error(f"Failed to initialize LLM: {e}", fatal=True)

        self._tasks_completed = 0

    def _select_task(self) -> str:
        """Select a task to execute."""
        task = random.choice(TASKS)
        self.logger.decision(
            choice="task_selection",
            options=TASKS[:5],  # Log sample of options
            selected=task,
            method="random"
        )
        return task

    def _execute_task(self, task: str) -> bool:
        """Execute a single task."""
        self.logger.workflow_start(task[:50])  # Truncate for log

        start_time = time.time()
        success = False

        try:
            self.logger.llm_request(
                action="execute_task",
                input_data={"task": task},
                model=MODEL_ID
            )

            result = self.agent.run(task)

            duration_ms = int((time.time() - start_time) * 1000)
            self.logger.llm_response(
                output=str(result)[:500] if result else "No output",
                duration_ms=duration_ms,
                model=MODEL_ID
            )

            success = True
            self._tasks_completed += 1

        except KeyboardInterrupt:
            raise
        except Exception as e:
            self.logger.error(f"Task failed: {e}", fatal=False, exception=e)

        finally:
            duration_ms = int((time.time() - start_time) * 1000)
            self.logger.workflow_end(
                task[:50],
                success=success,
                duration_ms=duration_ms
            )

        return success

    def _run_cluster(self) -> int:
        """Run a cluster of tasks. Returns number completed."""
        cluster_size = self.timing.get_cluster_size()
        self.logger.info(
            f"Starting task cluster",
            details={"cluster_size": cluster_size, "activity_level": self.timing.get_activity_level()}
        )

        completed = 0
        for i in range(cluster_size):
            task = self._select_task()

            # Pre-task thinking delay
            think_delay = self.timing.get_think_delay()
            self.logger.timing_delay(think_delay, "pre-task thinking")
            time.sleep(think_delay)

            # Execute task
            if self._execute_task(task):
                completed += 1

            # Inter-task delay (except for last task)
            if i < cluster_size - 1:
                task_delay = self.timing.get_task_delay()
                self.logger.timing_delay(task_delay, "inter-task delay")
                time.sleep(task_delay)

        self.logger.info(
            f"Cluster completed",
            details={"completed": completed, "total": cluster_size}
        )
        return completed

    def run(self):
        """Main run loop."""
        config = {
            "model": MODEL_ID,
            "timing_config": "default",
            "task_count": len(TASKS)
        }
        self.logger.session_start(config=config)

        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] SMOL-PHASE Agent starting")
        print(f"Model: {MODEL_ID}")
        print(f"Log file: {self.logger.get_log_path()}")
        print(f"Activity level: {self.timing.get_activity_level()}")

        iteration = 0

        try:
            while True:
                iteration += 1
                print(f"\n{'='*60}")
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Iteration {iteration}")
                print(f"Activity level: {self.timing.get_activity_level()}")
                print(f"{'='*60}")

                # Run a cluster of tasks
                self._run_cluster()

                # Check if we should take a longer break
                if self.timing.should_take_break(self._tasks_completed):
                    break_duration = self.timing.get_break_duration()
                    self.logger.timing_delay(break_duration, "extended break")
                    print(f"Taking extended break: {break_duration/60:.1f} minutes")
                    time.sleep(break_duration)
                    self._tasks_completed = 0  # Reset counter after break
                else:
                    # Normal cluster delay
                    cluster_delay = self.timing.get_cluster_delay()
                    self.logger.timing_delay(cluster_delay, "inter-cluster delay")
                    print(f"Cluster delay: {cluster_delay/60:.1f} minutes")

                    # Break into chunks for responsiveness
                    chunks = int(cluster_delay / 30)
                    for chunk in range(chunks):
                        time.sleep(30)
                        remaining = cluster_delay - (chunk + 1) * 30
                        if remaining > 30 and chunk % 2 == 1:
                            print(f"  {remaining:.0f}s remaining...")

                    # Sleep remainder
                    remainder = cluster_delay % 30
                    if remainder > 0:
                        time.sleep(remainder)

        except KeyboardInterrupt:
            print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Agent stopped by user")
        except Exception as e:
            self.logger.error(f"Fatal error: {e}", fatal=False, exception=e)
            raise
        finally:
            self.logger.session_end(summary={
                "iterations": iteration,
                "tasks_completed": self._tasks_completed
            })
            self.logger.close()
            print("Session ended. Logs saved.")


def main():
    """Entry point."""
    # Determine log directory
    log_dir = os.getenv("SMOL_PHASE_LOG_DIR")
    if not log_dir:
        # Default to deployed_sups/SMOL-PHASE/logs
        script_dir = Path(__file__).parent
        if "deployed_sups" in str(script_dir):
            log_dir = str(script_dir / "logs")
        else:
            log_dir = str(script_dir.parent.parent / "deployed_sups" / "SMOL-PHASE" / "logs")

    agent = SmolPhaseAgent(log_dir=log_dir)
    agent.run()


if __name__ == "__main__":
    main()
