"""SmolAgents WhoisLookup workflow — LLM picks a domain, helper does TCP/43.

Behavior-driven workflow (registered by loader.py only when is_behavior=True).
The loop selects this workflow per content.workflow_weights; the LLM then
makes ONE small completion call to pick a relevant domain. The actual
TCP/43 socket call is deterministic — the LLM doesn't run a CodeAgent
loop, just picks the input string.

Result: each invocation produces exactly one TCP/43 SF flow to
whois.iana.org with a varied resp_h hint via the chosen domain.

Future PHASE knob (not consumed today):
  content.whois_domain_pool: [str]  — replaces FALLBACK_DOMAINS at runtime.
"""
from __future__ import annotations

import random
import re
from typing import Optional, TYPE_CHECKING

from smolagents import LiteLLMModel

from brains.smolagents.workflows.base import SmolWorkflow
from common.config.model_config import get_model, get_num_ctx
from common.network.whois import FALLBACK_DOMAINS, whois_lookup

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger


WORKFLOW_NAME = "WhoisLookup"
WORKFLOW_DESCRIPTION = "Look up WHOIS info for a relevant domain"

PICKER_PROMPT_TEMPLATE = (
    "Choose ONE domain name from this list to look up via WHOIS for "
    "general web research. Reply with ONLY the domain (no preamble, "
    "no quotes, no punctuation).\n\n"
    "Domains:\n{domain_list}"
)

# Permissive parser — accept anything that looks like a domain on the
# first non-empty line of the LLM response.
_DOMAIN_RE = re.compile(r"\b([a-zA-Z0-9][a-zA-Z0-9-]{0,61}\.[a-zA-Z]{2,24}(?:\.[a-zA-Z]{2,24})?)\b")


def load(model: str = None, prompts=None):
    return WhoisLookupWorkflow(model=model)


class WhoisLookupWorkflow(SmolWorkflow):
    def __init__(self, model: str = None):
        super().__init__(
            name=WORKFLOW_NAME,
            description=WORKFLOW_DESCRIPTION,
            category="browser",
        )
        self.model_name = get_model(model)
        self._llm = None
        # Set by SmolAgentLoop._apply_brain_specific_config from
        # content.whois_domain_pool when PHASE supplies it. None → use
        # FALLBACK_DOMAINS.
        self.domain_pool: Optional[list[str]] = None

    def _get_llm(self):
        if self._llm is None:
            self._llm = LiteLLMModel(
                model_id=f"ollama/{self.model_name}",
                num_ctx=get_num_ctx(),
            )
        return self._llm

    def _pick_domain(self, logger: Optional["AgentLogger"]) -> str:
        pool = self.domain_pool or FALLBACK_DOMAINS
        domain_list = "\n".join(f"- {d}" for d in pool)
        msg = PICKER_PROMPT_TEMPLATE.format(domain_list=domain_list)
        try:
            resp = self._get_llm()([{"role": "user", "content": msg}])
            text = (getattr(resp, "content", "") or "").strip()
        except Exception as e:
            print(f"[ERROR] WhoisLookup LLM picker failed: "
                  f"{type(e).__name__}: {e}")
            if logger:
                logger.warning(
                    f"WhoisLookup LLM picker failed: {type(e).__name__}: {e}"
                )
            return random.choice(pool)

        match = _DOMAIN_RE.search(text)
        if match:
            candidate = match.group(1).lower()
            if candidate in pool:
                return candidate
            # Tolerant: any well-formed domain still produces a real WHOIS
            # row. Log loudly so we know LLM strayed from the supplied pool.
            if "." in candidate and len(candidate) < 64:
                print(f"[WARNING] WhoisLookup LLM picked domain not in pool: "
                      f"{candidate} — using anyway (well-formed)")
                if logger:
                    logger.warning(
                        f"WhoisLookup LLM strayed from pool: {candidate}"
                    )
                return candidate
        return random.choice(pool)

    def action(self, extra=None, logger: Optional["AgentLogger"] = None):
        domain = self._pick_domain(logger)
        if logger:
            logger.decision(
                choice="whois_domain",
                options=(self.domain_pool or FALLBACK_DOMAINS)[:5],
                selected=domain,
                context="LLM-picked domain for WHOIS lookup",
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
        self._llm = None
