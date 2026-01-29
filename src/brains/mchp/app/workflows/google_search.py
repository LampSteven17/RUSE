from time import sleep
import os
import random

from ..utility.base_workflow import BaseWorkflow
from ..utility.webdriver_helper import WebDriverHelper
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains

# LLM augmentation - only used for M4/M5 configurations
def _use_llm_augmentation():
    """Check if LLM augmentation should be used (M4/M5 configs)."""
    return os.environ.get("HYBRID_LLM_BACKEND") is not None

WORKFLOW_NAME = 'GoogleSearcher'
WORKFLOW_DESCRIPTION = 'Search for something on Google'
DEFAULT_WAIT_TIME = 2
MAX_PAGES = 5
MAX_NAVIGATION_CLICKS = 5
SEARCH_LIST = 'google_searches.txt'


def load():
    return GoogleSearch()


class GoogleSearch(BaseWorkflow):

    def __init__(self, input_wait_time=DEFAULT_WAIT_TIME):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION, driver=None)

        self.input_wait_time = input_wait_time
        self.search_list = self._load_search_list()

    def action(self, extra=None, logger=None):
        if self.driver is None:
            self.driver = WebDriverHelper()
        self._search_web(logger=logger)

    """ PRIVATE """

    def _search_web(self, logger=None):
        use_llm = _use_llm_augmentation()
        random_search = self._get_random_search()
        # Log search term selection
        if logger:
            if use_llm:
                logger.decision(
                    choice="search_term",
                    selected=random_search.rstrip(),
                    context="LLM-generated search query",
                    method="llm"
                )
            else:
                logger.decision(
                    choice="search_term",
                    options=self.search_list[:5] if len(self.search_list) > 5 else self.search_list,
                    selected=random_search.rstrip(),
                    context=f"Search term from {len(self.search_list)} options",
                    method="random"
                )
        try:
            # Semantic step: Perform search
            if logger:
                logger.step_start("perform_search", category="browser",
                                  message=f"Searching Google for: {random_search.rstrip()}")

            # Navigate to google.com
            if logger:
                logger.step_start("navigate", category="browser",
                                  message="https://www.google.com")
            self.driver.driver.get('https://www.google.com')
            assert 'Google' in self.driver.driver.title
            if logger:
                logger.step_success("navigate")
            sleep(DEFAULT_WAIT_TIME)

            # Randomly choose whether to google a search term or click lucky button
            action_options = ["search-term", "lucky"]
            chosen_action = random.choice(action_options)

            # Log the action choice decision
            if logger:
                logger.decision(
                    choice="search_action",
                    options=action_options,
                    selected=chosen_action,
                    context="Google search behavior",
                    method="random"
                )

            if chosen_action == "search-term":
                self._google_search(random_search, logger=logger)
                sleep(DEFAULT_WAIT_TIME)
                self._browse_search_results(logger=logger)
                sleep(DEFAULT_WAIT_TIME)
                self._click_on_search_result(logger=logger)
            elif chosen_action == "lucky":
                self._hover_click_feeling_lucky(logger=logger)

            if logger:
                logger.step_success("perform_search")

            sleep(DEFAULT_WAIT_TIME)

            # Semantic step: Explore page
            if logger:
                logger.step_start("explore_page", category="browser",
                                  message="Navigating and exploring webpage")
            self._navigate_webpage(logger=logger)
            if logger:
                logger.step_success("explore_page")

        except Exception as e:
            print('Error performing google search %s: %s' % (random_search.rstrip(), e))
            if logger:
                # Error the current step if any
                if logger._current_step:
                    logger.step_error(logger._current_step, str(e), exception=e)
                else:
                    logger.step_error("perform_search", str(e), category="browser", exception=e)

    def _click_on_search_result(self, logger=None):
        print(".... Clicking on search result")
        if logger:
            logger.step_start("click_search_result", category="browser",
                              message="Clicking on search result")
        search_result = WebDriverWait(self.driver.driver, 15).until(EC.visibility_of_any_elements_located((By.CLASS_NAME, "yuRUbf")))[0]
        ActionChains(self.driver.driver).move_to_element(search_result).click(search_result).perform()
        if logger:
            logger.step_success("click_search_result")

    def _browse_search_results(self, logger=None):
        # Click through search result pages
        print(".... Browsing search results")
        num_pages = random.randint(0, MAX_PAGES)
        if logger:
            logger.decision(
                choice="pages_to_browse",
                selected=str(num_pages),
                context=f"Number of search result pages to view (max: {MAX_PAGES})",
                method="random"
            )
            logger.step_start("browse_results", category="browser",
                              message=f"Browsing {num_pages} search result pages")
        for _ in range(0, num_pages):
            next_button = WebDriverWait(self.driver.driver, 15).until(EC.visibility_of_any_elements_located((By.LINK_TEXT, "Next")))[0]
            ActionChains(self.driver.driver).move_to_element(next_button).click(next_button).perform()
            sleep(DEFAULT_WAIT_TIME)
        if logger:
            logger.step_success("browse_results")

    def _google_search(self, random_search, logger=None):
        print(".... Googling:", random_search.rstrip())
        if logger:
            logger.step_start("enter_search_query", category="browser",
                              message=random_search.rstrip())
        elem = self.driver.driver.find_element(By.NAME,'q')
        elem.clear()
        sleep(self.input_wait_time)
        elem.send_keys(random_search)
        self.driver.driver.execute_script("window.scrollTo(0, document.body.Height)")
        if logger:
            logger.step_success("enter_search_query")

    def _hover_click_feeling_lucky(self, logger=None):
        print(".... Hovering & clicking 'I'm Feeling lucky' button")
        if logger:
            logger.step_start("click_feeling_lucky", category="browser",
                              message="Clicking 'I'm Feeling Lucky' button")
        element = WebDriverWait(self.driver.driver, 15).until(EC.visibility_of_any_elements_located((By. CSS_SELECTOR, '[name="btnI"][type="submit"]')))[0]
        ActionChains(self.driver.driver).move_to_element(element).click(element).perform()
        if logger:
            logger.step_success("click_feeling_lucky")

    def _navigate_webpage(self, logger=None):
        # Navigate webpage
        navigation_clicks = random.randrange(0, MAX_NAVIGATION_CLICKS)
        print(".... Navigating and highlighting web page", navigation_clicks, "times")
        if logger:
            logger.decision(
                choice="navigation_clicks",
                selected=str(navigation_clicks),
                context=f"Number of links to click (max: {MAX_NAVIGATION_CLICKS})",
                method="random"
            )
        for click_num in range(0, navigation_clicks):
            clickables = self.driver.driver.find_elements(By.TAG_NAME, ("a"))
            if len(clickables) == 0:
                return
            clickable = random.choice(clickables)
            if logger:
                logger.decision(
                    choice="link_selection",
                    selected=clickable.get_attribute("href") or "unknown",
                    context=f"Link {click_num+1}/{navigation_clicks} from {len(clickables)} options",
                    method="random"
                )
            url = clickable.get_attribute("href") or "unknown"
            step_name = f"nav_click_{click_num}"
            try:
                if logger:
                    logger.step_start(step_name, category="browser", message=url)
                self._highlight(clickable)
                self.driver.driver.execute_script("arguments[0].target='_self';", clickable)
                clickable.click()
                print("........ successful navigation")
                if logger:
                    logger.step_success(step_name)
            except Exception as e:
                print("........ X unsuccessful navigation")
                if logger:
                    logger.step_error(step_name, str(e), exception=e)
                pass

    def _get_random_search(self):
        """Get a search query - uses LLM for M4/M5, random from file for M1."""
        if _use_llm_augmentation():
            from augmentations.content import llm_search_query
            return llm_search_query("general information, technology, news, or everyday topics")
        return random.choice(self.search_list)

    def _highlight(self, element):
        driver = element._parent
        def apply_style(s):
            driver.execute_script("arguments[0].setAttribute('style', arguments[1]);",
                                element, s)
        original_style = element.get_attribute('style')
        apply_style("border: 10px solid red;")
        sleep(DEFAULT_WAIT_TIME)
        apply_style(original_style)

    @staticmethod
    def _load_search_list():
        with open(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..',
                                               'data', SEARCH_LIST))) as f:
            wordlist = f.readlines()
        return wordlist

