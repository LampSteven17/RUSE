from time import sleep
import os
import random

from ..utility.base_workflow import BaseWorkflow
from ..utility.webdriver_helper import WebDriverHelper
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from selenium.common.exceptions import ElementNotInteractableException

# LLM augmentation - only used for M4/M5 configurations
def _use_llm_augmentation():
    """Check if LLM augmentation should be used (M4/M5 configs)."""
    return os.environ.get("HYBRID_LLM_BACKEND") is not None

WORKFLOW_NAME = 'BrowseYouTube'
WORKFLOW_DESCRIPTION = 'Browse Youtube'

DEFAULT_INPUT_WAIT_TIME = 2
MIN_WATCH_TIME = 2 # Minimum amount of time to watch a video, in seconds
MAX_WATCH_TIME = 150 # Maximum amount of time to watch a video, in seconds
MIN_WAIT_TIME = 2 # Minimum amount of time to wait after searching, in seconds
MAX_WAIT_TIME = 5 # Maximum amount of time to wait after searching, in seconds
MAX_SUGGESTED_VIDEOS = 10

SEARCH_LIST = 'browse_youtube.txt'

def load():
    return YoutubeSearch()


class YoutubeSearch(BaseWorkflow):

    def __init__(self, input_wait_time=DEFAULT_INPUT_WAIT_TIME):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION, driver=None)

        self.input_wait_time = input_wait_time
        self.search_list = self._load_search_list()
        # video_pool: PHASE-emitted content.youtube_video_pool (Phase 1). When
        # set, skips the search-engine step and navigates directly to
        # /watch?v={id}. Subsequent watch + suggested-video clicks still
        # work the same. None = fall back to search flow.
        self.video_pool = None

    def action(self, extra=None, logger=None):
        if self.driver is None:
            self.driver = WebDriverHelper()
        self._search_web(logger=logger)

    """ PRIVATE """

    def _search_web(self, logger=None):
        # Phase 1: PHASE video_pool, when set, replaces the search→click step
        # with a direct /watch?v={id} navigation. The watch + suggested-video
        # logic below is reused as-is — same DOM, same elements.
        if self.video_pool:
            video_id = random.choice(self.video_pool)
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            if logger:
                logger.decision(
                    choice="youtube_video_direct",
                    selected=video_id,
                    context=f"Video from PHASE pool ({len(self.video_pool)} videos)",
                    method="phase_video_pool"
                )
                logger.step_start("navigate", category="video", message=video_url)
            try:
                self.driver.driver.get(video_url)
                if logger:
                    logger.step_success("navigate")
            except Exception as e:
                if logger:
                    logger.step_error("navigate", str(e), exception=e)
                raise
            sleep(random.randrange(MIN_WAIT_TIME, MAX_WAIT_TIME))
        else:
            use_llm = _use_llm_augmentation()
            random_search = self._get_random_search()

            # Log search term decision
            if logger:
                logger.decision(
                    choice="youtube_search_term",
                    selected=random_search,
                    context="LLM-generated YouTube search" if use_llm else "YouTube video search query",
                    method="llm" if use_llm else "random"
                )

            # Navigate to youtube
            if logger:
                logger.step_start("navigate", category="video", message="https://www.youtube.com")
            try:
                self.driver.driver.get('https://www.youtube.com')
                if logger:
                    logger.step_success("navigate")
            except Exception as e:
                if logger:
                    logger.step_error("navigate", str(e), exception=e)
                raise
            sleep(random.randrange(MIN_WAIT_TIME, MAX_WAIT_TIME))

            # Perform a youtube search
            if logger:
                logger.step_start("search", category="video", message=random_search)
            try:
                search_element = self.driver.driver.find_element(By.CSS_SELECTOR, 'input#search') # search bar
                search_element.send_keys(random_search)
                search_element.submit()
                if logger:
                    logger.step_success("search")
            except Exception as e:
                if logger:
                    logger.step_error("search", str(e), exception=e)
                raise
            sleep(random.randrange(MIN_WAIT_TIME, MAX_WAIT_TIME))

            # Click on a random video from the search results
            if logger:
                logger.step_start("select_result", category="video", message="Selecting video from search results")
            try:
                WebDriverWait(self.driver.driver, 10).until(EC.presence_of_all_elements_located((By.ID, "video-title")))
                search_results = self.driver.driver.find_elements(By.ID, "video-title")
                video_index = random.randrange(0, len(search_results)-1)
                if logger:
                    logger.decision(
                        choice="video_selection",
                        selected=str(video_index),
                        context=f"Video from {len(search_results)} search results",
                        method="random"
                    )
                search_results[video_index].click()
                if logger:
                    logger.step_success("select_result")
            except Exception as e:
                if logger:
                    logger.step_error("select_result", str(e), exception=e)
                raise

        # Watch video
        watch_time = random.randrange(MIN_WATCH_TIME, MAX_WATCH_TIME)
        if logger:
            logger.decision(
                choice="watch_duration",
                selected=str(watch_time),
                context=f"Seconds to watch video ({MIN_WATCH_TIME}-{MAX_WATCH_TIME}s range)",
                method="random"
            )
            logger.step_start("play_video", category="video", message=f"Watching for {watch_time}s")
        sleep(watch_time)
        if logger:
            logger.step_success("play_video")

        # Click on suggested videos
        num_suggested = random.randrange(0, MAX_SUGGESTED_VIDEOS)
        if logger:
            logger.decision(
                choice="suggested_videos_count",
                selected=str(num_suggested),
                context=f"Number of suggested videos to click (max: {MAX_SUGGESTED_VIDEOS})",
                method="random"
            )
        for i in range(num_suggested):
            sleep(random.randrange(MIN_WAIT_TIME, MAX_WAIT_TIME))
            if logger:
                logger.step_start("click", category="video", message=f"Suggested video {i+1}/{num_suggested}")
            try:
                suggested_videos = self.driver.driver.find_elements(By.ID, "video-title")
                if not suggested_videos:
                    # Fail-loud: empty list almost always means the DOM
                    # selector ('id=video-title') doesn't match this page.
                    # Likely YouTube changed the sidebar markup OR (Phase 1
                    # path) direct-URL navigation lands on a page whose
                    # sidebar uses different IDs than the search-results
                    # path. Either way audit/[WARNING] grep surfaces it.
                    msg = (
                        f"[WARNING] BrowseYouTube suggested_videos empty — "
                        f"selector By.ID 'video-title' returned 0 elements on "
                        f"{self.driver.driver.current_url} (pool_mode="
                        f"{'phase' if self.video_pool else 'search'})"
                    )
                    print(msg)
                    if logger:
                        logger.step_error("click", msg)
                    break
                # Pre-existing off-by-one (randrange(0, len-1)) would crash
                # on len==1 with ValueError. Use the full len.
                suggested_videos[random.randrange(0, len(suggested_videos))].click()
                if logger:
                    logger.step_success("click")
            except ElementNotInteractableException as e:
                if logger:
                    logger.step_error("click", str(e), exception=e)
                pass

    def _get_random_search(self):
        """Get a search query - uses LLM for M4/M5, random from file for M1."""
        if _use_llm_augmentation():
            from augmentations.content import llm_search_query
            return llm_search_query("YouTube videos, music, tutorials, entertainment, or educational content")
        search_term = random.choice(self._load_search_list()).rstrip('\n')
        return search_term

    @staticmethod
    def _load_search_list():
        with open(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..',
                                               'data', SEARCH_LIST))) as f:
            wordlist = f.readlines()
        return wordlist

