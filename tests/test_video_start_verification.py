"""Playback initiation tests; no browser, provider traffic, or five-minute sleep."""

import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from phase_workflow.workflows import (
    SeleniumResourceWorkflows,
    _confirm_video_start,
    _inspect_video_end,
    play_video_with_chromium,
)


def state(time=0, **changes):
    return dict(ready=True, time=time, paused=False, ended=False, error=None,
                video_present=True, player_error=None) | changes


class PlaybackInitiationTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def check(self, sample, play, timeout=30):
        return _confirm_video_start(
            sample, play, timeout, sleeper=self.sleep,
            monotonic=lambda: self.now,
        )

    def test_waits_for_readiness_then_calls_play_once_and_stops_at_advancement(self):
        sample = Mock(side_effect=[state(ready=False), state(), state(), state(.1)])
        play = Mock(return_value=None)
        self.check(sample, play)
        play.assert_called_once_with(29.5)
        self.assertEqual(sample.call_count, 4)
        self.assertEqual(self.now, 1)

    def test_readiness_timeout_never_calls_play(self):
        play = Mock()
        with self.assertRaisesRegex(RuntimeError, "setup timeout"):
            self.check(Mock(return_value=state(ready=False)), play)
        play.assert_not_called()
        self.assertEqual(self.now, 30)

    def test_play_rejection_is_failure(self):
        play = Mock(return_value="play() rejected")
        with self.assertRaisesRegex(RuntimeError, r"play\(\) rejected"):
            self.check(Mock(return_value=state()), play)
        play.assert_called_once()

    def test_ready_but_no_media_time_advancement_fails(self):
        play = Mock(return_value=None)
        with self.assertRaisesRegex(RuntimeError, "setup timeout"):
            self.check(Mock(return_value=state()), play)
        play.assert_called_once_with(30)
        self.assertEqual(self.now, 30)

    def test_paused_media_does_not_confirm_start(self):
        sample = Mock(side_effect=lambda: state(self.now, paused=True))
        with self.assertRaisesRegex(RuntimeError, "setup timeout"):
            self.check(sample, Mock(return_value=None))

    def test_readiness_play_and_advancement_share_one_budget(self):
        def play(remaining):
            self.assertEqual(remaining, 30)
            self.now += remaining
        with self.assertRaisesRegex(RuntimeError, "setup timeout"):
            self.check(Mock(return_value=state()), play)
        self.assertEqual(self.now, 30)

    def test_media_error_and_ended_fail_before_play(self):
        for sample in [state(error=3), state(ended=True)]:
            with self.subTest(sample=sample):
                play = Mock()
                with self.assertRaisesRegex(RuntimeError, "media error or ended"):
                    self.check(Mock(return_value=sample), play)
                play.assert_not_called()

    def test_browser_error_propagates(self):
        with self.assertRaisesRegex(OSError, "browser disconnected"):
            self.check(Mock(side_effect=OSError("browser disconnected")), Mock())


class BrowserVideoWiringTests(unittest.TestCase):
    def setUp(self):
        self.task = SimpleNamespace(resource={
            "kind": "youtube_video", "video_id": "assigned-video", "play_seconds": 300,
        })

    def test_selenium_success_and_failures_always_close_owned_driver(self):
        for failure in [None, "play() rejected", "play() timed out", OSError("driver lost")]:
            with self.subTest(failure=failure):
                driver = Mock(spec=["timeouts", "get", "find_element",
                                    "execute_script", "execute_async_script", "quit"])
                driver.timeouts.script = 30
                driver.execute_script.side_effect = [state(), state(.1), state(300)]
                if isinstance(failure, Exception):
                    driver.execute_async_script.side_effect = failure
                else:
                    driver.execute_async_script.return_value = failure
                sleep = Mock()
                workflow = SeleniumResourceWorkflows(lambda: driver, sleeper=sleep)
                if failure is None:
                    self.assertTrue(workflow.video_viewing(self.task).completed)
                    sleep.assert_called_once_with(300)
                else:
                    with self.assertRaises((RuntimeError, OSError)):
                        workflow.video_viewing(self.task)
                    sleep.assert_not_called()
                driver.get.assert_called_once_with(
                    "https://www.youtube.com/watch?v=assigned-video"
                )
                driver.execute_async_script.assert_called_once()
                driver.quit.assert_called_once()

    def test_chromium_success_and_failures_preserve_duration_and_cleanup(self):
        for failure in [None, "play() rejected", "play() timed out", OSError("browser lost")]:
            with self.subTest(failure=failure):
                page = Mock()
                page.evaluate.return_value = state(300)
                video = page.wait_for_selector.return_value
                video.evaluate.side_effect = [state(), failure, state(.1)]
                browser = Mock()
                browser.new_page.return_value = page
                playwright = Mock()
                playwright.chromium.launch.return_value = browser
                context = Mock()
                context.__enter__ = Mock(return_value=playwright)
                context.__exit__ = Mock(return_value=False)
                api = ModuleType("playwright.sync_api")
                api.sync_playwright = Mock(return_value=context)
                config = ModuleType("brains.browseruse.config")
                config.CHROMIUM_ARGS = []
                with patch.dict("sys.modules", {
                    "playwright": ModuleType("playwright"),
                    "playwright.sync_api": api,
                    "brains.browseruse.config": config,
                }):
                    if failure is None:
                        self.assertTrue(play_video_with_chromium(self.task))
                        page.wait_for_timeout.assert_called_once_with(300000)
                        self.assertEqual(video.evaluate.call_count, 3)
                    else:
                        with self.assertRaises((RuntimeError, OSError)):
                            play_video_with_chromium(self.task)
                        page.wait_for_timeout.assert_not_called()
                page.goto.assert_called_once_with(
                    "https://www.youtube.com/watch?v=assigned-video",
                    wait_until="domcontentloaded",
                )
                page.wait_for_selector.assert_called_once_with("video")
                browser.close.assert_called_once()


class FinalVideoInspectionTests(unittest.TestCase):
    def test_one_sample_without_explicit_failure_passes_without_playback_claim(self):
        sample = Mock(return_value=state(0, paused=True))
        self.assertEqual(_inspect_video_end(sample)['time'], 0)
        sample.assert_called_once_with()

    def test_media_overlay_missing_video_and_inspection_errors_fail(self):
        for result in (state(error=3), state(player_error='Something went wrong.'),
                       state(video_present=False), OSError('browser disconnected')):
            with self.subTest(result=result):
                sample = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
                with self.assertRaises((RuntimeError, OSError)):
                    _inspect_video_end(sample)
                sample.assert_called_once_with()

    def test_selenium_final_check_follows_wait_and_failure_keeps_cleanup(self):
        for result in (state(300), state(error=3),
                       state(0, paused=True, player_error='Something went wrong. Refresh or try again later.'),
                       OSError('inspection disconnected')):
            with self.subTest(result=result):
                trace = []
                driver = Mock(spec=['timeouts', 'get', 'find_element',
                                    'execute_script', 'execute_async_script', 'quit'])
                driver.timeouts.script = 30
                samples = iter((state(), state(.1), result))
                def read(*args):
                    value = next(samples)
                    if value is result:
                        trace.append('final inspection')
                    if isinstance(value, Exception):
                        raise value
                    return value
                driver.execute_script.side_effect = read
                driver.execute_async_script.return_value = None
                driver.quit.side_effect = lambda: trace.append('cleanup')
                def wait(seconds):
                    self.assertEqual(seconds, 300)
                    trace.append('assigned wait')
                workflow = SeleniumResourceWorkflows(lambda: driver, sleeper=wait)
                task = SimpleNamespace(resource=dict(kind='youtube_video', video_id='assigned', play_seconds=300))
                if result == state(300):
                    self.assertTrue(workflow.video_viewing(task).completed)
                else:
                    with self.assertRaises((RuntimeError, OSError)):
                        workflow.video_viewing(task)
                self.assertEqual(trace, ['assigned wait', 'final inspection', 'cleanup'])
                driver.execute_async_script.assert_called_once()
                self.assertEqual(driver.execute_script.call_count, 3)
                driver.get.assert_called_once_with('https://www.youtube.com/watch?v=assigned')

    def test_playwright_final_check_follows_wait_and_failure_keeps_cleanup(self):
        for result in (state(300), state(error=3),
                       state(0, paused=True, player_error='Something went wrong. Refresh or try again later.'),
                       OSError('inspection disconnected')):
            with self.subTest(result=result):
                trace = []
                page, browser, playwright = Mock(), Mock(), Mock()
                video = page.wait_for_selector.return_value
                video.evaluate.side_effect = [state(), None, state(.1)]
                def inspect(script):
                    trace.append('final inspection')
                    if isinstance(result, Exception):
                        raise result
                    return result
                page.evaluate.side_effect = inspect
                page.wait_for_timeout.side_effect = lambda ms: trace.append(('assigned wait', ms))
                browser.new_page.return_value = page
                browser.close.side_effect = lambda: trace.append('cleanup')
                playwright.chromium.launch.return_value = browser
                context = Mock(__enter__=Mock(return_value=playwright), __exit__=Mock(return_value=False))
                api = ModuleType('playwright.sync_api')
                api.sync_playwright = Mock(return_value=context)
                config = ModuleType('brains.browseruse.config')
                config.CHROMIUM_ARGS = []
                task = SimpleNamespace(resource=dict(kind='youtube_video', video_id='assigned', play_seconds=300))
                with patch.dict('sys.modules', {'playwright': ModuleType('playwright'),
                        'playwright.sync_api': api, 'brains.browseruse.config': config}):
                    if result == state(300):
                        self.assertTrue(play_video_with_chromium(task))
                    else:
                        with self.assertRaises((RuntimeError, OSError)):
                            play_video_with_chromium(task)
                self.assertEqual(trace, [('assigned wait', 300000), 'final inspection', 'cleanup'])
                page.evaluate.assert_called_once()
                self.assertEqual(video.evaluate.call_count, 3)
                page.goto.assert_called_once_with('https://www.youtube.com/watch?v=assigned', wait_until='domcontentloaded')


if __name__ == "__main__":
    unittest.main()
