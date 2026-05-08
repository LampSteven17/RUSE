"""Controls-mode runner — deterministic search-and-fetch floor.

Activated when behavior.json _metadata.mode == "controls". Consumes the
CONTROLS shape:

  timing.active_minute_windows           [[s, e), ...]   single 60-min slot
  timing.target_conn_per_minute_during_active            rate target
  timing.hard_fence_seconds              60              tail no-spawn zone
  content.url_queries                    ["news", ...]   round-robin queries
  content.page_fetch_interval_seconds    30              fixed pacing
  behavior.tool_pool                     ["web_browse"]  required entry
  behavior.enable_download / enable_whois              False
  behavior.max_steps / page_dwell / keep_alive_probability / navigation_clicks

Loop semantics:
  - while active_minute_now ∈ any window AND remaining > hard_fence:
      every page_fetch_interval_seconds → fetch next query via
      https://www.google.com/search?q=<query> (round-robin)
  - outside windows → sleep until next start, capped at 5 min
  - within hard_fence_seconds of window_end → stop new fetches; sleep through end

Brain-agnostic on purpose: same code runs whether the SUP was provisioned as
B0/B0C/M1/S0/S0C. Cross-deploy diff is bit-identical because the only
inputs are the PHASE-emitted CONTROLS schema fields.
"""
from datetime import datetime, timezone
from time import sleep, monotonic
from urllib.parse import quote_plus

import requests

from common.behavioral_config import (
    BehavioralConfig, MODE_CONTROLS,
    load_behavioral_config, resolve_behavioral_config_dir,
)


# Google is the most-likely-to-be-fetched generic search engine; using a
# fixed endpoint per query keeps the on-the-wire signature deterministic.
SEARCH_URL_TEMPLATE = "https://www.google.com/search?q={q}"

# User-Agent that looks like an ordinary Chrome — controls is a "floor"
# baseline, not a stealth measurement; just don't get blocked outright.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Cap on a single sleep-until-next-window — keeps config reload responsive.
WAIT_CAP_S = 5 * 60


def _now_minute_of_day_utc() -> int:
    n = datetime.now(timezone.utc)
    return n.hour * 60 + n.minute


def _now_seconds_into_minute() -> float:
    n = datetime.now(timezone.utc)
    return n.second + n.microsecond / 1_000_000.0


def _current_window(windows):
    m = _now_minute_of_day_utc()
    for s, e in windows:
        if s <= m < e:
            return (s, e)
    return None


def _seconds_until_next_start(windows):
    if not windows:
        return float("inf")
    m = _now_minute_of_day_utc()
    sec_into = _now_seconds_into_minute()
    for s, _e in windows:
        if s > m:
            return (s - m) * 60.0 - sec_into
    # Wrap to first window tomorrow.
    return ((1440 - m) + windows[0][0]) * 60.0 - sec_into


def _seconds_until_window_end(window):
    if window is None:
        return None
    _s, e = window
    m = _now_minute_of_day_utc()
    return (e - m) * 60.0 - _now_seconds_into_minute()


def _fetch_one(query: str, logger=None) -> bool:
    url = SEARCH_URL_TEMPLATE.format(q=quote_plus(query))
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        if logger:
            logger.info(f"[controls] fetch q={query!r} status={r.status_code}",
                        details={"query": query, "status": r.status_code,
                                 "bytes": len(r.content)})
        return True
    except Exception as e:
        if logger:
            logger.warning(f"[controls] fetch q={query!r} failed: {e}")
        return False


def run_controls(config_key: str, behavior_config_dir=None, logger=None) -> None:
    """Entry point. Loads behavior.json, asserts mode==controls, runs the
    deterministic search-and-fetch loop until interrupted."""
    resolved = resolve_behavioral_config_dir(config_key, override_dir=behavior_config_dir)
    fc: BehavioralConfig = load_behavioral_config(resolved, config_key)
    if fc.mode != MODE_CONTROLS:
        raise RuntimeError(
            f"run_controls invoked with non-controls behavior.json "
            f"(mode={fc.mode!r}); call site must dispatch on mode")

    queries = fc.url_queries or []
    interval = int(fc.page_fetch_interval_seconds or 30)
    windows = [list(w) for w in (fc.active_minute_windows or [])]
    fence = int(fc.hard_fence_seconds or 60)

    if not queries or not windows:
        msg = (f"[controls] config_key={config_key} missing required fields "
               f"(url_queries={queries!r}, windows={windows!r}) — exiting")
        if logger:
            logger.error(msg)
        raise RuntimeError(msg)

    if logger:
        logger.info(f"[controls] starting config_key={config_key}",
                    details={"queries": queries, "interval_s": interval,
                             "windows": windows, "hard_fence_s": fence})

    q_idx = 0
    while True:
        cw = _current_window(windows)
        if cw is None:
            wait = min(_seconds_until_next_start(windows), WAIT_CAP_S)
            wait = max(wait, 1.0)
            if logger:
                logger.info(f"[controls] outside windows — "
                            f"sleeping {wait/60:.1f}min")
            sleep(wait)
            continue

        remaining = _seconds_until_window_end(cw) or 0.0
        if remaining <= fence:
            # Within the hard-fence tail — sleep through window end.
            if logger:
                logger.info(f"[controls] within {fence}s of window end "
                            f"({remaining:.0f}s left) — sleeping through")
            sleep(remaining + 1.0)
            continue

        query = queries[q_idx % len(queries)]
        q_idx += 1
        _fetch_one(query, logger=logger)
        sleep(interval)
