import os
from importlib import import_module
from time import sleep, time

from ..utility.base_workflow import BaseWorkflow


WORKFLOW_NAME = 'MicrosoftPaint'
WORKFLOW_DESCRIPTION = 'Create a blank MS Paint file (Windows)'

DEFAULT_INPUT_WAIT_TIME = 2
DEFAULT_PAINT_PATH = paint_path = r'C:\Windows\System32\mspaint.exe'


def load():
    pyautogui = import_module('pyautogui')
    return msPaint(pyautogui=pyautogui)


class msPaint(BaseWorkflow):

    def __init__(self, pyautogui, input_wait_time=DEFAULT_INPUT_WAIT_TIME, paint_path=DEFAULT_PAINT_PATH):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION)

        self.pyautogui = pyautogui
        self.input_wait_time = input_wait_time
        self.paint_path = paint_path

    def action(self, extra=None, logger=None):
        self._ms_paint(logger=logger)

    """ PRIVATE """

    def _ms_paint(self, logger=None):
        # Open MS Paint
        if logger:
            logger.step_start("open_application", category="office", message="mspaint.exe")
        try:
            os.startfile(self.paint_path)
            self.pyautogui.getWindowsWithTitle('Paint')
            sleep(self.input_wait_time)
            if logger:
                logger.step_success("open_application")
        except Exception as e:
            if logger:
                logger.step_error("open_application", str(e), exception=e)
            raise

        # Save file
        if logger:
            logger.step_start("save_document", category="office", message="Saving paint file")
        try:
            self.pyautogui.hotkey('ctrl', 's')
            file_name = int(time())
            sleep(self.input_wait_time)
            self.pyautogui.typewrite(str(file_name))
            sleep(self.input_wait_time)
            self.pyautogui.press('enter')
            sleep(self.input_wait_time)
            if logger:
                logger.step_success("save_document")
        except Exception as e:
            if logger:
                logger.step_error("save_document", str(e), exception=e)
            raise

        # Close application
        if logger:
            logger.step_start("close_application", category="office", message="Closing MS Paint")
        try:
            self.pyautogui.getWindowsWithTitle('Paint')
            self.pyautogui.hotkey('alt', 'f4')
            if logger:
                logger.step_success("close_application")
        except Exception as e:
            if logger:
                logger.step_error("close_application", str(e), exception=e)
            raise
