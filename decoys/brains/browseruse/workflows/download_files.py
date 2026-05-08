"""BrowserUse DownloadFiles workflow — LLM picks a URL, helper does the fetch.

Mirrors brains/smolagents/workflows/download_files.py. LLM picker goes
via the local Ollama HTTP API (loopback 127.0.0.1:11434, invisible to
Zeek). The actual fetch is deterministic via requests.get streaming with
a 10MB cap. Bypasses browser_use.Agent + Playwright entirely — produces
one TLS flow per call, no browser overhead.

PHASE schema (consumed today):
  content.download_url_pool: list[str]  — verbatim pool the LLM picks from.

PHASE schema (informational, NOT consumed):
  content.download_size_pref           — schema marks as informational
                                         metadata only; ignored by RUSE.
"""
from __future__ import annotations

import random
import re
from typing import Optional, TYPE_CHECKING

import requests

from brains.browseruse.workflows.base import BUWorkflow
from common.config.model_config import get_model
from common.network.downloader import FALLBACK_URLS, download_file

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger


WORKFLOW_NAME = "DownloadFiles"
WORKFLOW_DESCRIPTION = "Download a relevant file from the curated pool"

OLLAMA_HOST = "http://127.0.0.1:11434"
OLLAMA_TIMEOUT_SECONDS = 60

PICKER_PROMPT_TEMPLATE = (
    "Choose ONE URL to download from this list. "
    "Reply with ONLY the URL, no preamble, no quotes.\n\n"
    "URLs:\n{url_list}"
)

_URL_RE = re.compile(r"https?://\S+")


def load(model: str = None, prompts=None, headless: bool = True, max_steps: int = 10):
    return DownloadFilesWorkflow(model=model)


class DownloadFilesWorkflow(BUWorkflow):
    def __init__(self, model: str = None):
        super().__init__(
            name=WORKFLOW_NAME,
            description=WORKFLOW_DESCRIPTION,
            category="browser",
        )
        self.model_name = get_model(model)
        self.url_pool: Optional[list[str]] = None

    def _ollama_pick(self, prompt: str, logger: Optional["AgentLogger"]) -> str:
        """One-shot Ollama chat completion. Loud failure: prints + logs on error."""
        try:
            resp = requests.post(
                f"{OLLAMA_HOST}/api/chat",
                json={
                    "model": self.model_name,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"num_ctx": 4096, "temperature": 0.7},
                },
                timeout=OLLAMA_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            return (resp.json().get("message", {}).get("content", "") or "").strip()
        except (requests.RequestException, ValueError) as e:
            print(f"[ERROR] BU DownloadFiles Ollama pick failed: "
                  f"{type(e).__name__}: {e}")
            if logger:
                logger.warning(
                    f"BU DownloadFiles Ollama pick failed: "
                    f"{type(e).__name__}: {e}"
                )
            return ""

    def _pick_url(self, pool: list[str], logger: Optional["AgentLogger"]) -> str:
        msg = PICKER_PROMPT_TEMPLATE.format(
            url_list="\n".join(f"- {u}" for u in pool)
        )
        text = self._ollama_pick(msg, logger)
        if text:
            match = _URL_RE.search(text)
            if match:
                candidate = match.group(0).rstrip(".,);:'\"")
                if candidate in pool:
                    return candidate
                print(f"[WARNING] BU DownloadFiles LLM picked URL not in pool: "
                      f"{candidate[:80]} — falling back to random.choice")
                if logger:
                    logger.warning(
                        f"BU DownloadFiles LLM strayed from pool: "
                        f"{candidate[:80]}"
                    )
        return random.choice(pool)

    def action(self, extra=None, logger: Optional["AgentLogger"] = None):
        pool = self.url_pool or FALLBACK_URLS
        url = self._pick_url(pool, logger)
        if logger:
            logger.decision(
                choice="download_url",
                options=pool[:5],
                selected=url,
                context=f"LLM-picked URL from pool of {len(pool)} (BU)",
                method="llm_picker",
            )
        result = download_file(url)
        success = result.startswith("downloaded ")
        if logger:
            logger.step_start("download_file", category="browser",
                              message=f"download {url[:60]}")
            if success:
                logger.step_success("download_file")
            else:
                logger.step_error("download_file", result[:80])
        return result, success

    def cleanup(self):
        pass
