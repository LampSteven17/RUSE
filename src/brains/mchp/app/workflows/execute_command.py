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
        for c in extra:
            if logger:
                logger.gui_action("execute_command", target=c)
            subprocess.Popen(c, shell=True)
