"""SmolAgents DownloadFiles workflow — LLM picks a URL, helper does the fetch.

Feedback-only workflow. Loop selects per content.workflow_weights; LLM
makes ONE completion call to pick a URL from the curated pool. The HTTP
fetch is deterministic — no CodeAgent loop, no tool dispatch.

Result: each invocation produces one TLS (or HTTP) flow with high
resp_bytes to a curated host. PHASE supplies a per-target url_pool;
workflow falls back to common.network.downloader.FALLBACK_URLS when
PHASE hasn't supplied one.

PHASE schema (consumed today):
  content.download_url_pool: list[str]  — verbatim pool the LLM picks from.

PHASE schema (informational, NOT consumed):
  content.download_size_pref           — schema marks as informational
    {"small": w, "medium": w, ...}     metadata only; ignored by RUSE.
"""
from __future__ import annotations

import random
import re
from typing import Optional, TYPE_CHECKING

from smolagents import LiteLLMModel

from brains.smolagents.workflows.base import SmolWorkflow
from common.config.model_config import get_model, get_num_ctx
from common.network.downloader import (
    FALLBACK_URLS, download_file, download_with_outcome,
    select_pool_subset, pick_outcome, parse_download_summary,
)

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger


WORKFLOW_NAME = "DownloadFiles"
WORKFLOW_DESCRIPTION = "Download a relevant file from the curated pool"

PICKER_PROMPT_TEMPLATE = (
    "Choose ONE URL to download from this list. "
    "Reply with ONLY the URL, no preamble, no quotes.\n\n"
    "URLs:\n{url_list}"
)

_URL_RE = re.compile(r"https?://\S+")


def load(model: str = None, prompts=None):
    return DownloadFilesWorkflow(model=model)


class DownloadFilesWorkflow(SmolWorkflow):
    def __init__(self, model: str = None):
        super().__init__(
            name=WORKFLOW_NAME,
            description=WORKFLOW_DESCRIPTION,
            category="browser",
        )
        self.model_name = get_model(model)
        self._llm = None
        # Set by SmolAgentLoop._apply_brain_specific_config from
        # content.download_url_pool when PHASE supplies it. May be list[str]
        # (legacy flat) OR dict[str, list[str]] (Phase 4 bucketed); None →
        # module-level FALLBACK_URLS.
        self.url_pool = None
        # Phase 4: size_mix selects which bucket of url_pool to draw from;
        # outcome_mix picks success/http_404/etc. None → all success on flat pool.
        self.size_mix: Optional[dict] = None
        self.outcome_mix: Optional[dict] = None

    def _get_llm(self):
        if self._llm is None:
            self._llm = LiteLLMModel(
                model_id=f"ollama/{self.model_name}",
                num_ctx=get_num_ctx(),
            )
        return self._llm

    def _pick_url(self, pool: list[str], logger: Optional["AgentLogger"]) -> str:
        url_list = "\n".join(f"- {u}" for u in pool)
        msg = PICKER_PROMPT_TEMPLATE.format(url_list=url_list)
        try:
            resp = self._get_llm()([{"role": "user", "content": msg}])
            text = (getattr(resp, "content", "") or "").strip()
        except Exception as e:
            # Loud failure: surface the LLM error so audit can see the picker
            # is broken. Workflow still produces a conn via random fallback.
            print(f"[ERROR] DownloadFiles LLM picker failed: {type(e).__name__}: {e}")
            if logger:
                logger.warning(
                    f"DownloadFiles LLM picker failed: {type(e).__name__}: {e}"
                )
            return random.choice(pool)

        match = _URL_RE.search(text)
        if match:
            candidate = match.group(0).rstrip(".,);:'\"")
            if candidate in pool:
                return candidate
            # Hallucinated URL not in pool — fall back to random rather
            # than fetching arbitrary content. Log loudly so we know the
            # LLM is straying from the supplied pool.
            print(f"[WARNING] DownloadFiles LLM picked URL not in pool: "
                  f"{candidate[:80]} — falling back to random.choice")
            if logger:
                logger.warning(
                    f"DownloadFiles LLM strayed from pool: {candidate[:80]}"
                )
        return random.choice(pool)

    def action(self, extra=None, logger: Optional["AgentLogger"] = None):
        # Phase 4: bucket-select first (when pool is dict + size_mix shipped),
        # then LLM picks within bucket. Legacy flat pool path unchanged.
        pool = select_pool_subset(self.url_pool, self.size_mix)
        url = self._pick_url(pool, logger)
        outcome = pick_outcome(self.outcome_mix)
        if logger:
            logger.decision(
                choice="download_url",
                options=pool[:5],
                selected=url,
                context=(f"LLM-picked URL from {len(pool)}-url subset "
                         f"(outcome={outcome})"),
                method="llm_picker",
            )
        result = download_with_outcome(url, outcome=outcome)
        # success outcome reports "downloaded ..."; http_404 returns the same
        # prefix on a real-but-404 fetch. Differentiate via http_404 in the
        # bogus URL path appearing in result.
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
        self._llm = None
