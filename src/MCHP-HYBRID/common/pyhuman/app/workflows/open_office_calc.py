"""
MCHP-HYBRID OpenOffice Calc Workflow

Creates spreadsheets with Apache OpenOffice Calc using LLM-generated content.
Original MCHP timing and control flow preserved, TextLorem replaced with LLM.
"""

import os
import random
import pyautogui
from time import sleep
from ..utility.base_workflow import BaseWorkflow
from ..utility.llm_content import llm_sentence, llm_filename, llm_spreadsheet_headers, llm_comment


WORKFLOW_NAME = 'OpenOfficeCalc'
WORKFLOW_DESCRIPTION = 'Create spreadsheets with Apache OpenOffice Calc (LLM-enhanced)'
DEFAULT_WAIT_TIME = 2
OPEN_OFFICE_PATH = "C:\\Program Files (x86)\\OpenOffice 4\\program\\soffice"


def load():
    return OpenOfficeCalc()


class OpenOfficeCalc(BaseWorkflow):

    def __init__(self, default_wait_time=DEFAULT_WAIT_TIME, open_office_path=OPEN_OFFICE_PATH):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION)
        self.default_wait_time = default_wait_time
        self.open_office_path = open_office_path

    def action(self, extra=None):
        self._create_spreadsheet()

    def _create_spreadsheet(self):
        self._new_spreadsheet()
        # Move to random cell, given column & row parameters
        self._move_to_cell([random.choice('abcde'), random.randrange(6)])
        sleep(1)
        self._insert_table()
        sleep(1)
        # Move to random cell, given column & row parameters
        self._move_to_cell([random.choice('abcdefghijkl'), random.randrange(15)])
        self._insert_comment()
        sleep(3)
        self._save_quit()

    def _insert_comment(self):
        pyautogui.hotkey('ctrl', 'alt', 'c')  # insert comment
        pyautogui.typewrite(llm_comment("spreadsheet"))  # LLM-generated comment
        pyautogui.press('esc')  # finish commenting
        sleep(self.default_wait_time)

    def _new_spreadsheet(self):
        os.startfile(self.open_office_path)  # start OpenOffice
        sleep(self.default_wait_time)
        pyautogui.press('s')  # choose new spreadsheet
        sleep(self.default_wait_time)

    def _save_quit(self):
        pyautogui.hotkey('ctrl', 's')  # save
        sleep(self.default_wait_time)
        pyautogui.typewrite(llm_filename())  # LLM-generated filename
        sleep(self.default_wait_time)
        pyautogui.press('enter')
        pyautogui.hotkey('alt', 'y')  # choose "yes" if a popup asks if you'd like to overwrite
        sleep(self.default_wait_time)
        pyautogui.hotkey('ctrl', 'q')  # quit OpenOffice

    def _move_to_cell(self, cell_coordinate):
        pyautogui.press('f5')  # open navigator
        pyautogui.hotkey('ctrl', 'a')  # select column value
        pyautogui.write(str(cell_coordinate[0]))  # enter new column value
        pyautogui.press('tab')  # select row value
        pyautogui.write(str(cell_coordinate[1]))  # enter new row value
        pyautogui.press('enter')
        pyautogui.press('f5')  # close navigator

    def _insert_table(self):
        row_length = random.randint(3, 10)
        # Use LLM to generate column headers
        headers = llm_spreadsheet_headers(row_length)
        for header in headers:  # create header row for a table
            pyautogui.write(header)  # LLM-generated header
            pyautogui.press('tab')
        # Data rows remain random numbers (not content generation)
        for j in range(0, random.randint(3, 10)):
            pyautogui.press('enter')
            for k in range(0, row_length):
                pyautogui.write(str(random.randint(0, 10000)))  # type a random number
                pyautogui.press('tab')
