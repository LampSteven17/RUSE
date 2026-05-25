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
from common.network.downloader import (
    FALLBACK_URLS, download_file, download_with_outcome,
    select_pool_subset, pick_outcome, parse_download_summary,
)

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
        # url_pool may be list[str] (legacy) OR dict[str, list[str]] (Phase 4).
        self.url_pool = None
        # Phase 4: PHASE behavior.download.{size_mix, outcome_mix} — None
        # falls through to legacy success-only behavior on a flat pool.
        self.size_mix: Optional[dict] = None
        self.outcome_mix: Optional[dict] = None

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
        # Phase 4: bucket-select via size_mix when pool is dict, then LLM
        # picks within bucket. Legacy flat-pool path unchanged.
        pool = select_pool_subset(self.url_pool, self.size_mix)
        url = self._pick_url(pool, logger)
        outcome = pick_outcome(self.outcome_mix)
        if logger:
            logger.decision(
                choice="download_url",
                options=pool[:5],
                selected=url,
                context=(f"LLM-picked URL from {len(pool)}-url subset "
                         f"(outcome={outcome}, BU)"),
                method="llm_picker",
            )
        result = download_with_outcome(url, outcome=outcome)
        success = result.startswith("downloaded ")
        if logger:
            logger.step_start("download_file", category="browser",
                              message=f"download {url[:60]}")
            info = parse_download_summary(result)
            details = {"url": url, "outcome": outcome,
                       "bytes": info["bytes"],
                       "content_type": info["content_type"],
                       "elapsed_ms": info["elapsed_ms"]}
            if success:
                logger.step_success("download_file", message=result[:200],
                                    details=details, duration_ms=info["elapsed_ms"])
            else:
                logger.step_error("download_file", result[:120], details=details)
        return result, success

    def cleanup(self):
        pass
