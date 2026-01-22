import os
import random
import pyautogui
from lorem.text import TextLorem
from time import sleep
from ..utility.base_workflow import BaseWorkflow


WORKFLOW_NAME = 'OpenOfficeWriter'
WORKFLOW_DESCRIPTION = 'Create documents with Apache OpenOffice Writer (Windows)'
DEFAULT_WAIT_TIME = 2
OPEN_OFFICE_PATH = r"C:\Program Files (x86)\OpenOffice 4\program\soffice"

def load():
    return OpenOfficeWriter()

class OpenOfficeWriter(BaseWorkflow):

    def __init__(self, default_wait_time=DEFAULT_WAIT_TIME, open_office_path=OPEN_OFFICE_PATH):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION)
        self.default_wait_time = default_wait_time
        self.open_office_path = open_office_path

    def action(self, extra=None, logger=None):
        self._create_document(logger=logger)

    def _create_document(self, logger=None):
        # Semantic step: Create document
        if logger:
            logger.step_start("create_document", category="office",
                              message="Creating new OpenOffice Writer document")

        if logger:
            logger.step_start("open_application", category="office",
                              message="OpenOffice Writer")
        self._new_document()
        if logger:
            logger.step_success("open_application")

        # Semantic step: Edit content
        if logger:
            logger.step_start("edit_content", category="office",
                              message="Typing paragraphs and sentences")
        # Type random paragrahs and sentences
        for i in range(0, random.randint(2,10)):
            random.choice([pyautogui.typewrite(TextLorem().paragraph()), pyautogui.typewrite(TextLorem().sentence())])
            pyautogui.press('enter')
        sleep(self.default_wait_time)
        if logger:
            logger.step_success("edit_content")

        # Semantic step: Format and modify
        if logger:
            logger.step_start("format_and_modify", category="office",
                              message="Performing random document actions")
        # Randomly perform actions
        for i in range(0, random.randint(6,15)):
            random.choice([self._save_pdf,
                           self._write_sentence,
                           self._write_paragraph,
                           self._copy_paste,
                           self._insert_comment,
                           self._find,
                           self._delete_text,
                           self._format_text])()
            sleep(self.default_wait_time)
        if logger:
            logger.step_success("format_and_modify")

        # Semantic step: Save document
        if logger:
            logger.step_start("save_document", category="office",
                              message="Saving and closing document")
        # Save and quit the document
        self._save_quit()
        if logger:
            logger.step_success("save_document")

        if logger:
            logger.step_success("create_document")

    def _insert_comment(self):
        pyautogui.hotkey('ctrl', 'alt', 'c') # insert comment
        pyautogui.typewrite(TextLorem().sentence()) # type random sentence
        pyautogui.press('esc') # finish commenting
        sleep(self.default_wait_time)

    def _find(self):
        pyautogui.hotkey('ctrl', 'f') # open Find & Replace
        sleep(self.default_wait_time)
        pyautogui.typewrite(TextLorem()._word()) # type random word
        sleep(self.default_wait_time)
        pyautogui.press('enter') 
        sleep(self.default_wait_time)
        pyautogui.hotkey('alt','y') # close pop up box that may appear
        sleep(self.default_wait_time)
        pyautogui.hotkey('alt','c') # close Find & Replace
        sleep(self.default_wait_time)

    def _copy_paste(self):
        self._select_text()
        sleep(self.default_wait_time)
        pyautogui.hotkey('ctrl', 'c') # copy to clipboard
        sleep(self.default_wait_time)
        pyautogui.press('backspace') # delete text
        sleep(self.default_wait_time)
        pyautogui.typewrite(TextLorem().paragraph()) # write text
        sleep(self.default_wait_time)
        pyautogui.press('enter') # insert new line
        pyautogui.press('enter') # insert new line
        pyautogui.hotkey('ctrl', 'v') # paste from clipboard
        sleep(self.default_wait_time)

    def _select_text(self):
        selection_params = [
            ['ctrl'  , 'home'], # go to beginning of document
            ['shift' , 'left'], # move cursor & select to left
            ['shift' , 'up'] # move cursor & select up
        ]
        pyautogui.hotkey(*random.choice(selection_params)) 

    def _format_text(self):
        self._select_text()
        sleep(self.default_wait_time)
        formatting_params = [['ctrl','1'], # Apply heading 1 style
                             ['ctrl','2'], # Apply heading 2 style
                             ['ctrl','3'], # Apply heading 3 style
                             ['ctrl','d'], # Double underline
                             ['ctrl','e'], # Center
                             ['ctrl','5']] # Set 1.5 line spacing
        pyautogui.hotkey(*random.choice(formatting_params))
        sleep(self.default_wait_time)

    def _delete_text(self):
        pyautogui.hotkey('ctrl', 'shift', 'delete') # Delete text to beginning of line
        pyautogui.hotkey('ctrl', 'backspace') # Delete text to beginning of word 

    def _save_pdf(self):
        # Export a pdf
        pyautogui.hotkey('alt','f') # select to File
        pyautogui.hotkey('alt','d') # select to Export as PDF
        pyautogui.press('enter') # choose Export as PDF
        pyautogui.hotkey('alt','x') # choose Export
        pyautogui.typewrite(TextLorem(wsep='-', srange=(1,3)).sentence()[:-1]) # type random file name
        sleep(self.default_wait_time)
        pyautogui.press('enter') # press enter
        sleep(self.default_wait_time)
        pyautogui.hotkey('alt','y') # choose "yes" if a popup asks if you'd like to overwrite another file

    def _new_document(self):
        # Open new document in OpenOffice
        os.startfile(self.open_office_path) # open OpenOffice
        sleep(self.default_wait_time)
        pyautogui.press('d') # choose document editing
        sleep(self.default_wait_time)
        # pyautogui.hotkey('ctrl','shift', 'j') # full screen mode

    def _save_quit(self):
        pyautogui.hotkey('ctrl', 's') # save
        sleep(self.default_wait_time)
        pyautogui.typewrite(TextLorem(wsep='-', srange=(1,3)).sentence()[:-1]) # type random file name
        sleep(self.default_wait_time)
        pyautogui.press('enter') 
        pyautogui.hotkey('alt','y') # choose "yes" if a popup asks if you'd like to overwrite another file
        sleep(self.default_wait_time)
        pyautogui.hotkey('ctrl','q') # quit OpenOffice

    def _write_paragraph(self):
        pyautogui.typewrite(TextLorem().paragraph())

    def _write_sentence(self):
        pyautogui.typewrite(TextLorem().sentence())