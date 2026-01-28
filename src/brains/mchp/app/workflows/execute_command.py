import subprocess

from ..utility.base_workflow import BaseWorkflow


WORKFLOW_NAME = 'ExecuteCommand'
WORKFLOW_DESCRIPTION = 'Execute custom commands'


def load():
    return ExecuteCommand()


class ExecuteCommand(BaseWorkflow):

    def __init__(self):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION)

    @staticmethod
    def action(extra=None, logger=None):
        if not extra:
            if logger:
                logger.warning("ExecuteCommand called with no commands")
            return
        for c in extra:
            if logger:
                logger.step_start("execute_command", category="shell", message=c)
            try:
                subprocess.Popen(c, shell=True)
                if logger:
                    logger.step_success("execute_command")
            except Exception as e:
                if logger:
                    logger.step_error("execute_command", str(e), exception=e)
                raise
