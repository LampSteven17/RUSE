"""DownloadFileTool — bulk-bytes file fetch for SmolAgents.

Adds high-resp_bytes / long-duration TLS flow shapes to the SUP's conn.log.
Realistic workstation activity (downloading PDFs, package binaries, images,
doc archives) produces these features; without a download verb, SmolAgents
emits only thin DDG search flows + small visit_webpage HTML fetches.

Per-call shape:
  - 1 TCP/443 (or /80) flow per call to the chosen URL's host.
  - resp_bytes ranges from ~50 KB (small icons) to MAX_BYTES (~10 MB).
  - duration scales w/ payload size + network conditions.
  - Adds id.resp_h diversity since URLs span ~25 distinct hosts.

Caps + safeties:
  - MAX_BYTES_PER_CALL stops chunked iteration before a runaway download
    pins the SUP's disk or RAM.
  - Total wall time bounded by HTTP read timeout.
  - URL must come from CURATED_URLS (the LLM cannot pass arbitrary URLs).
    Whitelisting prevents the tool being abused as a free outbound proxy
    and keeps the conn.log target distribution predictable for PHASE.

Future PHASE knobs (not consumed today, documented for v3):
  - diversity.background_services.downloads_per_hour: [24 ints] — fire
    downloads on a fixed schedule outside the LLM, mirrors http_head_per_hour.
  - content.download_categories: {"image": w, "pdf": w, "binary": w} —
    bias URL pool by MIME, mirrors content.site_categories.
  - behavior.download_max_bytes: int — per-call cap override.
"""
import random
import time
from urllib.parse import urlparse

import requests
from smolagents import Tool


# Curated public URLs — stable, varied MIME, varied size. The LLM picks
# from CURATED_URLS by index or omits a URL (then we pick at random). Direct
# arbitrary URLs are rejected so the tool can't be abused as an outbound
# proxy and so PHASE-side analytics see a predictable resp_h distribution.
CURATED_URLS = [
    # === small images (~5-100 KB) ===
    "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/PNG_transparency_demonstration_1.png/280px-PNG_transparency_demonstration_1.png",
    "https://www.python.org/static/community_logos/python-powered-w-100x40.png",
    "https://en.wikipedia.org/static/images/icons/wikipedia.png",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/8/87/PDF_file_icon.svg/200px-PDF_file_icon.svg.png",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a7/Camera-photo.svg/240px-Camera-photo.svg.png",

    # === medium images (~200 KB - 2 MB) ===
    "https://upload.wikimedia.org/wikipedia/commons/thumb/0/05/Felis_catus-cat_on_snow.jpg/1280px-Felis_catus-cat_on_snow.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/7/74/A-Cat.jpg/1280px-A-Cat.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/9/95/Pia_de_los_Mares.jpg/1280px-Pia_de_los_Mares.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b6/Image_created_with_a_mobile_phone.png/1280px-Image_created_with_a_mobile_phone.png",

    # === text / books (~100 KB - 1 MB) ===
    "https://www.gutenberg.org/files/1342/1342-0.txt",     # Pride and Prejudice (~700 KB)
    "https://www.gutenberg.org/files/11/11-0.txt",         # Alice in Wonderland (~170 KB)
    "https://www.gutenberg.org/files/2701/2701-0.txt",     # Moby Dick (~1.2 MB)
    "https://www.gutenberg.org/files/84/84-0.txt",         # Frankenstein (~430 KB)
    "https://www.gutenberg.org/files/74/74-0.txt",         # Tom Sawyer (~410 KB)

    # === PDFs / docs (~100 KB - 5 MB) ===
    "https://www.w3.org/TR/PNG/iso_8859-1.txt",
    "https://www.rfc-editor.org/rfc/rfc2616.txt",          # HTTP/1.1 (~430 KB)
    "https://www.rfc-editor.org/rfc/rfc7540.txt",          # HTTP/2 (~360 KB)
    "https://www.rfc-editor.org/rfc/rfc9110.txt",          # HTTP semantics (~530 KB)
    "https://www.ietf.org/archive/id/draft-ietf-quic-transport-34.txt",  # QUIC (~470 KB)

    # === source archives / mid-size binaries (~1-10 MB) ===
    "https://www.gnu.org/software/hello/manual/hello.pdf",
    "https://nodejs.org/dist/latest-v20.x/SHASUMS256.txt",
    "https://nodejs.org/dist/index.json",
    "https://pypi.org/simple/",                            # HTML index, ~10+ MB; capped by MAX_BYTES

    # === firmware / docs ===
    "https://www.kernel.org/doc/html/latest/_sources/index.rst.txt",
    "https://docs.python.org/3/archives/python-3.13.0-docs-text.tar.bz2",  # large; capped
]


class DownloadFileTool(Tool):
    """Download a file from a curated URL list. Returns a result summary string.

    Tool inputs:
      url: optional. If provided, must be one of CURATED_URLS. Pass
           omit/None to let the tool pick a random URL. Arbitrary user-
           supplied URLs are rejected — keeps the conn.log target
           distribution predictable + prevents proxy abuse.

    Tool output:
      A short string of the form
        "downloaded <url> -> <bytes> bytes (<content_type>) in <ms>ms"
      so the LLM can report success in its workflow chain. The actual
      content is discarded — the goal is the conn.log shape, not the data.
    """

    name = "download_file"
    description = (
        "Download a file (image, PDF, text, or binary) from a curated public "
        "URL list. Use this when researching a topic and you want to fetch a "
        "referenced document or asset. Either pass a URL from the curated set "
        "or omit the URL to fetch a random one."
    )
    inputs = {
        "url": {
            "type": "string",
            "description": (
                "Optional URL to download. Must be from the curated set. "
                "If omitted or empty, a random URL is picked."
            ),
            "nullable": True,
        }
    }
    output_type = "string"

    MAX_BYTES_PER_CALL = 10 * 1024 * 1024  # 10 MB cap
    READ_TIMEOUT_SECONDS = 30
    CONNECT_TIMEOUT_SECONDS = 10
    USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) RUSE-DownloadTool/1.0"

    def forward(self, url: str = None) -> str:
        # URL whitelist: empty/None = pick at random; explicit URL must
        # be in CURATED_URLS. Non-whitelisted URLs are rejected (not
        # forwarded with a warning) — quiet refusal w/ a string message
        # so the LLM doesn't keep retrying the same bad URL.
        if not url:
            url = random.choice(CURATED_URLS)
        elif url not in CURATED_URLS:
            return (
                f"download_file refused: url not in curated list. "
                f"Pick one of (examples): {CURATED_URLS[0]} | "
                f"{CURATED_URLS[5]} | {CURATED_URLS[15]}"
            )

        start = time.monotonic()
        try:
            resp = requests.get(
                url,
                stream=True,
                timeout=(self.CONNECT_TIMEOUT_SECONDS, self.READ_TIMEOUT_SECONDS),
                headers={"User-Agent": self.USER_AGENT},
                allow_redirects=True,
            )
        except requests.RequestException as e:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return f"download_file error ({type(e).__name__}): {str(e)[:120]} ({elapsed_ms}ms)"

        host = urlparse(url).netloc
        content_type = resp.headers.get("Content-Type", "unknown").split(";")[0]
        try:
            received = 0
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                received += len(chunk)
                if received >= self.MAX_BYTES_PER_CALL:
                    # Cap reached — abort the rest of the body. Server-side
                    # this looks like a normal client closing the conn early
                    # (RSTO in Zeek), still produces a real flow.
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
        return (
            f"downloaded {host} -> {received} bytes ({content_type}) "
            f"in {elapsed_ms}ms"
        )
