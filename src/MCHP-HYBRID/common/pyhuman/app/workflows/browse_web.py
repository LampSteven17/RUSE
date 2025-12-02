"""
MCHP-HYBRID Web Browse Workflow

Simulates web browsing using LLM for intelligent site and link selection.
Original MCHP timing and control flow preserved.
"""

import os
import random
from time import sleep

from ..utility.base_workflow import BaseWorkflow
from ..utility.webdriver_helper import WebDriverHelper
from ..utility.llm_content import llm_select
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException
from selenium.common.exceptions import InvalidArgumentException
from selenium.common.exceptions import TimeoutException

WORKFLOW_NAME = 'WebBrowser'
WORKFLOW_DESCRIPTION = 'Select a website and browse intelligently (LLM-enhanced)'

MAX_NAVIGATION_CLICKS = 15
MAX_SLEEP_TIME = 20
DEFAULT_TIMEOUT = 45


def load():
    driver = WebDriverHelper()
    return WebBrowse(driver=driver)


class WebBrowse(BaseWorkflow):

    def __init__(self, driver, max_sleep_time=MAX_SLEEP_TIME,
                 max_navigation_clicks=MAX_NAVIGATION_CLICKS,
                 default_timeout=DEFAULT_TIMEOUT):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION, driver=driver)

        self.max_sleep_time = max_sleep_time
        self.max_navigation_clicks = max_navigation_clicks
        self.default_timeout = default_timeout
        self.website_list = self._load_website_list()

    def action(self, extra=None):
        self._web_browse()

    """ PRIVATE """

    def _web_browse(self):
        self.driver.driver.set_page_load_timeout(self.default_timeout)
        self._browse(self._get_random_website())
        sleep(random.randint(1, self.max_sleep_time))
        self._navigate_website()

    def _get_random_website(self):
        """Use LLM to select an interesting website from the list."""
        return llm_select(self.website_list, "interesting website for browsing")

    def _browse(self, random_website):
        print("Browsing to", random_website.rstrip())
        try:
            self.driver.driver.get('https://' + random_website.strip())
        except TimeoutException as error:
            print(f"Timeout loading {random_website.rstrip()}: {error}")
        except WebDriverException as error:
            print(f"Error loading {random_website.rstrip()}: {error}")
        except Exception as error:
            print(f"Error loading {random_website.rstrip()}: {error}")

    @staticmethod
    def _load_website_list():
        wordlist = []
        with open(os.path.abspath(os.path.join(
            os.path.dirname(__file__), '..', '..', 'data', 'websites.txt'
        )), 'r') as f:
            for line in f:
                wordlist.append(line.strip())
        return wordlist

    def _navigate_website(self):
        """Browse the currently loaded website with intelligent link selection."""
        navigation_clicks = random.randrange(0, self.max_navigation_clicks)
        for num_click in range(1, navigation_clicks):
            clickables = self.driver.driver.find_elements(By.TAG_NAME, ("a"))
            # If there's nothing to click, stop navigating this page
            if len(clickables) == 0:
                print(f"... {num_click}. No clickable elements were found")
                return

            # Get all URLs and use LLM to select intelligently
            urls = []
            for c in clickables:
                url = c.get_attribute("href")
                if url:
                    urls.append(url)

            if not urls:
                print(f"... {num_click}. No valid URLs found")
                continue

            # Use LLM to select an interesting link
            selected_url = llm_select(urls, "relevant and interesting link")

            try:
                self.driver.driver.get(selected_url)
                print(f"... {num_click}. Navigated to {selected_url}")
                sleep(random.randint(1, self.max_sleep_time))
            except TimeoutException as error:
                print(f"Timeout loading {selected_url}: {error}")
            except InvalidArgumentException as error:
                print(f"Error loading {selected_url}: {error}")
            except Exception as error:
                print(f"Error loading {selected_url}: {error}")
