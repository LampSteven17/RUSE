"""YouTube helpers — availability guard (all brains) + yt-dlp media streamer (Smol).

Two concerns:
  - Dead/private-video guard: PHASE's content.youtube_video_pool rots (videos get
    deleted/privated over time). is_youtube_available + pick_available_video skip
    those before a brain navigates/streams. Used by MCHP/BU/Smol BrowseYouTube.
  - Smol streaming: Smol has no browser; stream_youtube_video uses yt-dlp to resolve
    the real media URL and fetch it (capped) so Smol generates genuine googlevideo
    CDN traffic over HTTP — in-character with its HTTP/CodeAgent nature. MCHP/BU
    stream via their own browsers (Firefox/Chromium) and don't use this.

yt-dlp is imported lazily so this module also loads on MCHP/BU VMs (no yt-dlp there).
"""
from __future__ import annotations

import random
import time
from urllib.parse import urlparse

import requests


OEMBED = "https://www.youtube.com/oembed"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) RUSE-YouTubeHelper/1.0"
STREAM_MAX_BYTES = 30 * 1024 * 1024   # cap a single stream — real traffic, not a hoard
STREAM_CHUNK = 64 * 1024


def is_youtube_available(video_id: str, timeout: int = 5) -> bool:
    """True if the video is public/watchable. YouTube oEmbed: HTTP 401 (private) /
    404 (deleted) -> unavailable; 200 / 403 (embedding disabled but watchable) ->
    available. Any network error -> True (defensive: never block a workflow on a
    transient failure)."""
    try:
        r = requests.get(
            OEMBED,
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            headers={"User-Agent": USER_AGENT}, timeout=timeout,
        )
        return r.status_code not in (401, 404)
    except requests.RequestException:
        return True


def pick_available_video(pool, tries: int = 4):
    """Pick a random available video id from `pool`, skipping dead/private ones.
    Returns the first available pick; if all `tries` come back dead, returns the
    last tried (caller still handles the page/stream gracefully). None for empty."""
    if not pool:
        return None
    chosen = None
    for _ in range(tries):
        chosen = random.choice(pool)
        if is_youtube_available(chosen):
            return chosen
    return chosen


def stream_youtube_video(video_id: str, seconds: int, logger=None) -> dict:
    """Stream a video's real media over HTTP for ~`seconds` (byte-capped), making
    genuine googlevideo CDN traffic without a browser — for Smol. yt-dlp resolves
    the direct media URL; requests fetches it (content discarded — the goal is the
    conn.log/stream shape). Returns {outcome, host, bytes, elapsed_ms}."""
    import yt_dlp  # lazy: only Smol VMs ship yt-dlp
    start = time.time()
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True,
                               "format": "worst[ext=mp4]/worst"}) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        media_url = info.get("url")
        if not media_url:
            fmts = info.get("requested_formats") or info.get("formats") or []
            media_url = fmts[-1].get("url") if fmts else None
        if not media_url:
            return {"outcome": "no_media_url", "bytes": 0,
                    "elapsed_ms": int((time.time() - start) * 1000)}
        host = urlparse(media_url).hostname or ""
        total = 0
        deadline = start + max(1, seconds)
        with requests.get(media_url, stream=True, headers={"User-Agent": USER_AGENT},
                          timeout=30) as r:
            for chunk in r.iter_content(STREAM_CHUNK):
                total += len(chunk)
                if total >= STREAM_MAX_BYTES or time.time() >= deadline:
                    break
        res = {"outcome": "ok", "host": host, "bytes": total,
               "elapsed_ms": int((time.time() - start) * 1000)}
        if logger:
            logger.info(f"[youtube] streamed {total} bytes from {host}", details=res)
        return res
    except Exception as e:
        res = {"outcome": "error", "bytes": 0, "error": str(e)[:200],
               "elapsed_ms": int((time.time() - start) * 1000)}
        if logger:
            logger.warning(f"[youtube] stream failed: {str(e)[:120]}", details=res)
        return res
