"""
M0 Runner - Wraps upstream MITRE pyhuman with DOLOS logging.

This module runs the original, unmodified MITRE pyhuman code while
capturing stdout and parsing workflow events for DOLOS semantic analysis.

The M0 configuration is the control - the pyhuman code MUST NOT be modified.
Only this wrapper should be changed to improve logging integration.

Output: deployed_sups/M0/logs/session_*.jsonl (same format as M1, B*, S* agents)
"""
import subprocess
import re
import sys
import time
from datetime import datetime

from common.logging.agent_logger import AgentLogger


class M0LogParser:
    """Parse M0 stdout into DOLOS LogEvents."""

    # M0 prints workflow display strings like:
    # [14:23:45] GoogleSearcher: Searching for "python tutorials"
    WORKFLOW_PATTERN = re.compile(r"^\[[\d:]+\]\s*(\w+):")

    # Also match simpler format
    SIMPLE_PATTERN = re.compile(r"^(\w+Workflow|\w+Searcher|\w+Browser):")

    def __init__(self, logger: AgentLogger):
        self.logger = logger
        self.current_workflow = None
        self.workflow_start_time = None

    def parse_line(self, line: str):
        """Parse a line of M0 stdout output."""
        # Try to detect workflow start from M0 display output
        match = self.WORKFLOW_PATTERN.match(line) or self.SIMPLE_PATTERN.match(line)

        if match:
            # End previous workflow if any
            if self.current_workflow:
                duration_ms = int((time.time() - self.workflow_start_time) * 1000)
                self.logger.workflow_end(
                    self.current_workflow,
                    success=True,
                    duration_ms=duration_ms
                )

            # Start new workflow
            self.current_workflow = match.group(1)
            self.workflow_start_time = time.time()
            self.logger.workflow_start(
                self.current_workflow,
                params={"source": "m0_stdout", "agent_type": "M0"}
            )

        # Log all output as info
        self.logger.info(line, details={"stream": "stdout"})

    def finalize(self):
        """Finalize any pending workflow."""
        if self.current_workflow:
            duration_ms = int((time.time() - self.workflow_start_time) * 1000)
            self.logger.workflow_end(
                self.current_workflow,
                success=True,
                duration_ms=duration_ms
            )


def main():
    """Run M0 (upstream MITRE pyhuman) with DOLOS logging wrapper."""
    logger = AgentLogger(agent_type="M0")
    logger.session_start(config={
        "upstream": "https://github.com/mitre/human",
        "behavior": "M0",
        "modified": False,
        "description": "Upstream MITRE pyhuman (control)"
    })

    parser = M0LogParser(logger)

    # Run the upstream pyhuman
    # Note: The upstream code is expected to be at /opt/human/pyhuman
    try:
        proc = subprocess.Popen(
            ["xvfb-run", "-a", "python3", "human.py"],
            cwd="/opt/human/pyhuman",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1  # Line buffered
        )

        print(f"[{datetime.now().strftime('%H:%M:%S')}] M0 started (PID: {proc.pid})")

        for line in proc.stdout:
            line = line.strip()
            if line:
                parser.parse_line(line)
                print(line)  # Pass through to systemd logs

        proc.wait()
        parser.finalize()

        logger.session_end(summary={
            "exit_code": proc.returncode,
            "status": "completed" if proc.returncode == 0 else "error"
        })

        return proc.returncode

    except FileNotFoundError as e:
        error_msg = f"M0 upstream pyhuman not found at /opt/human/pyhuman: {e}"
        print(f"ERROR: {error_msg}")
        logger.error(error_msg, fatal=True)
        logger.session_end(summary={"exit_code": -1, "error": error_msg})
        return 1

    except KeyboardInterrupt:
        print("\nM0 interrupted by user")
        logger.info("M0 stopped by user (KeyboardInterrupt)")
        parser.finalize()
        logger.session_end(summary={"exit_code": -2, "status": "interrupted"})
        return 0

    except Exception as e:
        error_msg = str(e)
        print(f"ERROR: {error_msg}")
        logger.error(error_msg, fatal=True, exception=e)
        logger.session_end(summary={"exit_code": -1, "error": error_msg})
        return 1


if __name__ == "__main__":
    sys.exit(main())
