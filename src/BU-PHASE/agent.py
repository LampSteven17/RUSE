#!/usr/bin/env python3
"""
BU-PHASE Agent

Browser-use agent with PHASE timing improvements and comprehensive logging.
Uses browser-use with Ollama for local LLM inference.

Features:
- PHASE timing with time-of-day activity awareness
- Comprehensive JSON-Lines logging for experiment analysis
- Task clustering with realistic delays
- Fresh browser session per task for crash resistance
- Graceful error handling and session management
"""

import os
import sys
import asyncio
import time
import random
from datetime import datetime
from pathlib import Path

# Add common modules to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from browser_use import Agent
from browser_use.browser.session import BrowserSession
from langchain_ollama import ChatOllama
from common.logging.agent_logger import AgentLogger
from common.timing.phase_timing import PhaseTiming, PhaseTimingConfig


# Configuration
MODEL_NAME = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

# Browser task definitions
BROWSER_TASKS = [
    # Search engine tasks
    "Go to google.com and search for 'how to remove specific text from a file'",
    "Visit google.com and search for 'Python programming tutorials'",
    "Navigate to google.com and search for 'best productivity apps 2024'",
    "Search google.com for 'what is my ip address'",

    # News browsing
    "Visit cnn.com and look at the top headlines",
    "Go to bbc.com and check world news",
    "Navigate to reuters.com and browse technology news",

    # Social media simulation
    "Visit reddit.com and browse the front page",
    "Go to twitter.com and look at trending topics",

    # YouTube browsing
    "Go to youtube.com and search for 'Python tutorial'",
    "Visit youtube.com and search for 'tech news'",
    "Navigate to youtube.com and look at trending videos",

    # E-commerce browsing
    "Visit amazon.com and search for 'laptop'",
    "Go to ebay.com and browse electronics",

    # Educational sites
    "Go to wikipedia.org and search for 'artificial intelligence'",
    "Visit github.com and browse trending repositories",

    # Tech company sites
    "Visit microsoft.com and check products",
    "Go to apple.com and browse new releases",

    # Entertainment
    "Go to imdb.com and check movie ratings",
    "Visit spotify.com and check music",

    # Utility sites
    "Visit weather.com and check forecast",
    "Go to stackoverflow.com and browse questions",
]


class BuPhaseAgent:
    """Browser-use agent with PHASE timing and comprehensive logging."""

    def __init__(self, log_dir: str = None):
        """Initialize the BU-PHASE agent."""
        self.logger = AgentLogger(
            agent_type="BU-PHASE",
            log_dir=log_dir
        )
        self.timing = PhaseTiming()

        # Initialize LLM
        self.logger.info(f"Initializing LLM model: {MODEL_NAME}")
        try:
            self.llm = ChatOllama(model=MODEL_NAME)
            # Test connection
            response = self.llm.invoke("Say OK")
            if not response.content:
                raise RuntimeError("LLM returned empty response")
            self.logger.info("LLM model initialized successfully")
        except Exception as e:
            self.logger.error(f"Failed to initialize LLM: {e}", fatal=True)

        self._tasks_completed = 0

    def _select_task(self) -> str:
        """Select a browser task to execute."""
        task = random.choice(BROWSER_TASKS)
        self.logger.decision(
            choice="task_selection",
            options=BROWSER_TASKS[:5],  # Log sample of options
            selected=task,
            method="random"
        )
        return task

    async def _execute_task(self, task: str) -> bool:
        """Execute a single browser task."""
        self.logger.workflow_start(task[:50])  # Truncate for log

        start_time = time.time()
        success = False
        browser_session = None

        try:
            # Create fresh browser session for this task
            self.logger.browser_action("create_session", "chromium")
            browser_session = BrowserSession(
                headless=True,
                channel="chromium",
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-extensions',
                    '--disable-gpu'
                ]
            )

            # Create agent
            agent = Agent(
                task=task,
                llm=self.llm,
                browser_session=browser_session,
            )

            self.logger.llm_request(
                action="browser_task",
                input_data={"task": task},
                model=MODEL_NAME
            )

            # Run the task
            result = await agent.run(max_steps=10)

            duration_ms = int((time.time() - start_time) * 1000)
            self.logger.llm_response(
                output="Task completed" if result else "No result",
                duration_ms=duration_ms,
                model=MODEL_NAME
            )

            success = result is not None
            self._tasks_completed += 1

        except asyncio.TimeoutError:
            self.logger.error("Task timed out", fatal=False)
        except Exception as e:
            self.logger.error(f"Task failed: {e}", fatal=False, exception=e)

        finally:
            # Clean up browser session
            if browser_session:
                try:
                    self.logger.browser_action("close_session", "cleanup")
                except:
                    pass

            duration_ms = int((time.time() - start_time) * 1000)
            self.logger.workflow_end(
                task[:50],
                success=success,
                duration_ms=duration_ms
            )

        return success

    async def _run_cluster(self) -> int:
        """Run a cluster of browser tasks. Returns number completed."""
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
            await asyncio.sleep(think_delay)

            # Execute task
            if await self._execute_task(task):
                completed += 1

            # Inter-task delay (except for last task)
            if i < cluster_size - 1:
                task_delay = self.timing.get_task_delay()
                self.logger.timing_delay(task_delay, "inter-task delay")
                await asyncio.sleep(task_delay)

        self.logger.info(
            f"Cluster completed",
            details={"completed": completed, "total": cluster_size}
        )
        return completed

    async def run(self):
        """Main async run loop."""
        config = {
            "model": MODEL_NAME,
            "timing_config": "default",
            "task_count": len(BROWSER_TASKS),
            "browser": "chromium (fresh session per task)"
        }
        self.logger.session_start(config=config)

        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] BU-PHASE Agent starting")
        print(f"Model: {MODEL_NAME}")
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
                await self._run_cluster()

                # Check if we should take a longer break
                if self.timing.should_take_break(self._tasks_completed):
                    break_duration = self.timing.get_break_duration()
                    self.logger.timing_delay(break_duration, "extended break")
                    print(f"Taking extended break: {break_duration/60:.1f} minutes")
                    await asyncio.sleep(break_duration)
                    self._tasks_completed = 0  # Reset counter after break
                else:
                    # Normal cluster delay
                    cluster_delay = self.timing.get_cluster_delay()
                    self.logger.timing_delay(cluster_delay, "inter-cluster delay")
                    print(f"Cluster delay: {cluster_delay/60:.1f} minutes")

                    # Break into chunks for responsiveness
                    chunks = int(cluster_delay / 30)
                    for chunk in range(chunks):
                        await asyncio.sleep(30)
                        remaining = cluster_delay - (chunk + 1) * 30
                        if remaining > 30 and chunk % 2 == 1:
                            print(f"  {remaining:.0f}s remaining...")

                    # Sleep remainder
                    remainder = cluster_delay % 30
                    if remainder > 0:
                        await asyncio.sleep(remainder)

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
    log_dir = os.getenv("BU_PHASE_LOG_DIR")
    if not log_dir:
        # Default to deployed_sups/BU-PHASE/logs
        script_dir = Path(__file__).parent
        if "deployed_sups" in str(script_dir):
            log_dir = str(script_dir / "logs")
        else:
            log_dir = str(script_dir.parent.parent / "deployed_sups" / "BU-PHASE" / "logs")

    agent = BuPhaseAgent(log_dir=log_dir)
    asyncio.run(agent.run())


if __name__ == "__main__":
    main()
