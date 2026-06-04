"""Shared BrowserUse Chromium launch args.

Single source of truth for the `BrowserSession(args=...)` Chromium flags — the
four `_get_browser_session()` methods (agent.py + the three workflow files) all
import this instead of re-listing the args (they had drifted as copy-paste).

`--autoplay-policy=no-user-gesture-required` lets YouTube videos actually play
(confirmed 2026-06-04: without it Chromium loads the page but the player never
starts; with it the video streams).
"""

CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--disable-gpu",
    "--autoplay-policy=no-user-gesture-required",
]
