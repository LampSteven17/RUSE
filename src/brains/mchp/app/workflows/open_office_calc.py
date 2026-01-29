import os
import random
import pyautogui
from lorem.text import TextLorem
from time import sleep
from ..utility.base_workflow import BaseWorkflow


# LLM augmentation - only used for M4/M5 configurations
def _use_llm_augmentation():
    """Check if LLM augmentation should be used (M4/M5 configs)."""
    return os.environ.get("HYBRID_LLM_BACKEND") is not None


def _get_sentence():
    """Get a sentence - uses LLM for M4/M5, TextLorem for M1."""
    if _use_llm_augmentation():
        from augmentations.content import llm_sentence
        return llm_sentence()
    return TextLorem().sentence()


def _get_word():
    """Get a word - uses LLM for M4/M5, TextLorem for M1."""
    if _use_llm_augmentation():
        from augmentations.content import llm_word
        return llm_word()
    return TextLorem()._word()


def _get_filename():
    """Get a filename - uses LLM for M4/M5, TextLorem for M1."""
    if _use_llm_augmentation():
        from augmentations.content import llm_filename
        return llm_filename()
    return TextLorem(wsep='-', srange=(1,3)).sentence()[:-1]


WORKFLOW_NAME = 'OpenOfficeCalc'
WORKFLOW_DESCRIPTION = 'Create spreadsheets with Apache OpenOffice Calc (Windows)'
DEFAULT_WAIT_TIME = 2
OPEN_OFFICE_PATH = r"C:\Program Files (x86)\OpenOffice 4\program\soffice"

def load():
    return OpenOfficeCalc()

class OpenOfficeCalc(BaseWorkflow):

    def __init__(self, default_wait_time=DEFAULT_WAIT_TIME, open_office_path=OPEN_OFFICE_PATH):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION)
        self.default_wait_time = default_wait_time
        self.open_office_path = open_office_path

    def action(self, extra=None, logger=None):
        self._create_spreadsheet(logger=logger)

    def _create_spreadsheet(self, logger=None):
        # Open application
        if logger:
            logger.step_start("open_application", category="office", message="OpenOffice Calc")
        try:
            self._new_spreadsheet()
            if logger:
                logger.step_success("open_application")
        except Exception as e:
            if logger:
                logger.step_error("open_application", str(e), exception=e)
            raise

        # Navigate and insert table
        if logger:
            logger.step_start("edit_content", category="office", message="Inserting table data")
        try:
            self._move_to_cell([random.choice('abcde'),random.randrange(6)]) # move to random cell, given column & row parameters
            sleep(1)
            self._insert_table()
            sleep(1)
            if logger:
                logger.step_success("edit_content")
        except Exception as e:
            if logger:
                logger.step_error("edit_content", str(e), exception=e)
            raise

        # Insert comment
        if logger:
            logger.step_start("add_comment", category="office", message="Adding cell comment")
        try:
            self._move_to_cell([random.choice('abcdefghijkl'),random.randrange(15)]) # move to random cell, given column & row parameters
            self._insert_comment()
            sleep(3)
            if logger:
                logger.step_success("add_comment")
        except Exception as e:
            if logger:
                logger.step_error("add_comment", str(e), exception=e)
            raise

        # Save and quit
        if logger:
            logger.step_start("save_document", category="office", message="Saving spreadsheet")
        try:
            self._save_quit()
            if logger:
                logger.step_success("save_document")
        except Exception as e:
            if logger:
                logger.step_error("save_document", str(e), exception=e)
            raise

    def _insert_comment(self):
        pyautogui.hotkey('ctrl', 'alt', 'c') # insert comment
        pyautogui.typewrite(_get_sentence()) # type random sentence
        pyautogui.press('esc') # finish commenting
        sleep(self.default_wait_time)

    def _new_spreadsheet(self):
        os.startfile(self.open_office_path) # start OpenOffice
        sleep(self.default_wait_time)
        pyautogui.press('s') # choose new spreadsheet
        sleep(self.default_wait_time)

    def _save_quit(self):
        pyautogui.hotkey('ctrl', 's') # save
        sleep(self.default_wait_time)
        pyautogui.typewrite(_get_filename()) # type random file name
        sleep(self.default_wait_time)
        pyautogui.press('enter')
        pyautogui.hotkey('alt','y') # choose "yes" if a popup asks if you'd like to overwrite another file
        sleep(self.default_wait_time)
        pyautogui.hotkey('ctrl','q') # quit OpenOffice

    def _move_to_cell(self, cell_coordinate):
        pyautogui.press('f5') # open navigator
        pyautogui.hotkey('ctrl','a') # select column value
        pyautogui.write(str(cell_coordinate[0])) # enter new column value
        pyautogui.press('tab') # select row value
        pyautogui.write(str(cell_coordinate[1])) # enter new row value
        pyautogui.press('enter')
        pyautogui.press('f5') # close navigator

    def _insert_table(self):
        row_length = random.randint(3,10)
        for i in range(0, row_length): # create header row for a table
            pyautogui.write(_get_word()) # type a random word
            pyautogui.press('tab')
        for j in range(0, random.randint(3,10)):
            pyautogui.press('enter')
            for k in range(0, row_length):
                pyautogui.write(str(random.randint(0,10000))) # type a random number
                pyautogui.press('tab')