"""BrowserUse WhoisLookup workflow — LLM picks a domain, helper does TCP/43.

Mirrors brains/smolagents/workflows/whois_lookup.py architecturally:
loop selects per content.workflow_weights, ONE Ollama chat call picks a
domain, deterministic socket call hits whois.iana.org:43.

Bypasses browser_use.Agent entirely — the browser can't speak port 43
and BU's fixed action vocabulary doesn't include WHOIS. The picker LLM
call goes via the local Ollama HTTP API on 127.0.0.1:11434 (loopback,
not visible to Zeek), so the only conn.log row this workflow produces
is the TCP/43 SF flow.
"""
from __future__ import annotations

import random
import re
from typing import Optional, TYPE_CHECKING

import requests

from brains.browseruse.workflows.base import BUWorkflow
from common.config.model_config import get_model
from common.network.whois import FALLBACK_DOMAINS, whois_lookup

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger


WORKFLOW_NAME = "WhoisLookup"
WORKFLOW_DESCRIPTION = "Look up WHOIS info for a relevant domain"

OLLAMA_HOST = "http://127.0.0.1:11434"
OLLAMA_TIMEOUT_SECONDS = 60

PICKER_PROMPT_TEMPLATE = (
    "Choose ONE domain name from this list to look up via WHOIS for "
    "general web research. Reply with ONLY the domain (no preamble, "
    "no quotes, no punctuation).\n\n"
    "Domains:\n{domain_list}"
)

_DOMAIN_RE = re.compile(r"\b([a-zA-Z0-9][a-zA-Z0-9-]{0,61}\.[a-zA-Z]{2,24}(?:\.[a-zA-Z]{2,24})?)\b")


def load(model: str = None, prompts=None, headless: bool = True, max_steps: int = 10):
    return WhoisLookupWorkflow(model=model)


class WhoisLookupWorkflow(BUWorkflow):
    def __init__(self, model: str = None):
        super().__init__(
            name=WORKFLOW_NAME,
            description=WORKFLOW_DESCRIPTION,
            category="browser",
        )
        self.model_name = get_model(model)
        self.domain_pool: Optional[list[str]] = None  # set by BrowserUseLoop._apply_brain_specific_config

    def _ollama_pick(self, prompt: str, logger: Optional["AgentLogger"]) -> str:
        """One-shot Ollama chat completion for content picking. Loopback,
        invisible to Zeek. Returns the model's text response. Loud failure:
        prints + logs on error."""
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
            print(f"[ERROR] BU WhoisLookup Ollama pick failed: "
                  f"{type(e).__name__}: {e}")
            if logger:
                logger.warning(
                    f"BU WhoisLookup Ollama pick failed: "
                    f"{type(e).__name__}: {e}"
                )
            return ""

    def _pick_domain(self, logger: Optional["AgentLogger"]) -> str:
        pool = self.domain_pool or FALLBACK_DOMAINS
        msg = PICKER_PROMPT_TEMPLATE.format(
            domain_list="\n".join(f"- {d}" for d in pool)
        )
        text = self._ollama_pick(msg, logger)
        if text:
            match = _DOMAIN_RE.search(text)
            if match:
                candidate = match.group(1).lower()
                if candidate in pool:
                    return candidate
                # Tolerant: any well-formed domain still produces a real
                # WHOIS row. Log loudly so we know LLM strayed.
                if "." in candidate and len(candidate) < 64:
                    print(f"[WARNING] BU WhoisLookup LLM picked domain not in "
                          f"pool: {candidate} — using anyway (well-formed)")
                    return candidate
        return random.choice(pool)

    def action(self, extra=None, logger: Optional["AgentLogger"] = None):
        domain = self._pick_domain(logger)
        if logger:
            logger.decision(
                choice="whois_domain",
                options=(self.domain_pool or FALLBACK_DOMAINS)[:5],
                selected=domain,
                context="LLM-picked domain for WHOIS lookup (BU)",
                method="llm_picker",
            )
        result = whois_lookup(domain)
        success = result.startswith("%") or "domain" in result.lower()
        if logger:
            logger.step_start("whois_lookup", category="browser",
                              message=f"WHOIS for {domain}")
            if success:
                logger.step_success("whois_lookup")
            else:
                logger.step_error("whois_lookup", result[:80])
        return result, success

    def cleanup(self):
        pass
