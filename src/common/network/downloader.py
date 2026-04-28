"""File-download helper — shared by all three brains' download_files workflows.

Streams a file via requests, caps total bytes, returns a one-line summary
string. The actual content is discarded — the goal is conn.log shape
(high resp_bytes, varied SNI hosts, real TLS flows), not the data.

Used by:
  - brains/smolagents/workflows/download_files.py (LLM-picked-URL)
  - brains/browseruse/workflows/download_files.py (LLM-picked-URL)
  - brains/mchp/app/workflows/download_files.py (pre-existing scripted
    workflow with its own xkcd/wiki/NIST helpers — does not import this
    module today; kept for reference if MCHP ever consolidates onto the
    shared helper)

Future PHASE knob (not consumed today, documented for reference):
  content.download_url_pool: [str]    — per-target curated URL list,
                                        replaces FALLBACK_URLS at runtime.
  content.download_size_pref:         — bucket pool by size, bias picks.
    {"small": w, "medium": w, "large": w}
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

import requests


MAX_BYTES_PER_CALL = 10 * 1024 * 1024  # 10 MB cap
READ_TIMEOUT_SECONDS = 30
CONNECT_TIMEOUT_SECONDS = 10
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) RUSE-DownloadHelper/1.0"

# Curated public URL pool — stable hosts, varied MIME types and sizes.
# Bucketed for download_size_pref: the LLM picker can pass a SUBSET when
# PHASE writes a size preference. Today: full pool exposed.
SMALL_URLS = [
    "https://www.python.org/static/community_logos/python-powered-w-100x40.png",
    "https://en.wikipedia.org/static/images/icons/wikipedia.png",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/8/87/PDF_file_icon.svg/200px-PDF_file_icon.svg.png",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a7/Camera-photo.svg/240px-Camera-photo.svg.png",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/PNG_transparency_demonstration_1.png/280px-PNG_transparency_demonstration_1.png",
]

MEDIUM_URLS = [
    "https://upload.wikimedia.org/wikipedia/commons/thumb/0/05/Felis_catus-cat_on_snow.jpg/1280px-Felis_catus-cat_on_snow.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/7/74/A-Cat.jpg/1280px-A-Cat.jpg",
    "https://www.gutenberg.org/files/1342/1342-0.txt",         # Pride and Prejudice (~700 KB)
    "https://www.gutenberg.org/files/11/11-0.txt",             # Alice (~170 KB)
    "https://www.gutenberg.org/files/84/84-0.txt",             # Frankenstein (~430 KB)
    "https://www.gutenberg.org/files/74/74-0.txt",             # Tom Sawyer (~410 KB)
    "https://www.rfc-editor.org/rfc/rfc2616.txt",              # HTTP/1.1 (~430 KB)
    "https://www.rfc-editor.org/rfc/rfc7540.txt",              # HTTP/2 (~360 KB)
    "https://www.rfc-editor.org/rfc/rfc9110.txt",              # HTTP semantics (~530 KB)
    "https://www.ietf.org/archive/id/draft-ietf-quic-transport-34.txt",
]

LARGE_URLS = [
    "https://www.gutenberg.org/files/2701/2701-0.txt",         # Moby Dick (~1.2 MB)
    "https://www.gnu.org/software/hello/manual/hello.pdf",
    "https://nodejs.org/dist/latest-v20.x/SHASUMS256.txt",
    "https://nodejs.org/dist/index.json",
    "https://pypi.org/simple/",                                # ~10+ MB; capped
    "https://docs.python.org/3/archives/python-3.13.0-docs-text.tar.bz2",  # capped
    "https://www.kernel.org/doc/html/latest/_sources/index.rst.txt",
]

# Flat pool used when no size_pref is specified. Order: small first
# (most common workstation pattern is small-asset fetches).
FALLBACK_URLS = SMALL_URLS + MEDIUM_URLS + LARGE_URLS


def download_file(url: str, max_bytes: int = MAX_BYTES_PER_CALL,
                  read_timeout: float = READ_TIMEOUT_SECONDS,
                  connect_timeout: float = CONNECT_TIMEOUT_SECONDS) -> str:
    """Stream a file from url, discard content, return one-line summary.

    Returns: "downloaded <host> -> <bytes> bytes (<content_type>) in <ms>ms"
    or an "error: ..." string. Always returns a string — never raises.
    """
    if not url:
        return "download_file error: empty url"
    start = time.monotonic()
    try:
        resp = requests.get(
            url,
            stream=True,
            timeout=(connect_timeout, read_timeout),
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
        )
    except requests.RequestException as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return f"download_file error ({type(e).__name__}): {str(e)[:120]} ({elapsed_ms}ms)"

    host = urlparse(url).netloc
    content_type = resp.headers.get("Content-Type", "unknown").split(";")[0]
    received = 0
    try:
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            received += len(chunk)
            if received >= max_bytes:
                # Cap reached — abort body. RSTO in Zeek; flow still real.
                break
    except requests.RequestException as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return (
            f"download_file partial ({type(e).__name__} after {received} bytes): "
            f"{str(e)[:80]} ({elapsed_ms}ms)"
        )
    finally:
        try:
            resp.close()
        except Exception:
            pass

    elapsed_ms = int((time.monotonic() - start) * 1000)
    return f"downloaded {host} -> {received} bytes ({content_type}) in {elapsed_ms}ms"
