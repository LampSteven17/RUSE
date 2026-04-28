"""MCHP WhoisLookup workflow — TCP/43 socket lookup for the no-LLM brain.

Feedback-only (registered by MCHPAgent._load_workflows only when the
deploy has a behavior.json). MCHP has no LLM, so the domain pick is
random.choice from FALLBACK_DOMAINS — no picker call. The conn.log
signature (one TCP/43 SF flow per invocation) is identical to the
LLM-driven smolagents/BU workflows; the only difference is which
domain the SNI hint reflects.

Future PHASE knob (not consumed today, see common.network.whois):
  content.whois_domain_pool: [str]
"""
from __future__ import annotations

import random

from ..utility.base_workflow import BaseWorkflow
from common.network.whois import FALLBACK_DOMAINS, whois_lookup


WORKFLOW_NAME = "WhoisLookup"
WORKFLOW_DESCRIPTION = "Look up WHOIS info for a relevant domain"


def load():
    return WhoisLookupWorkflow()


class WhoisLookupWorkflow(BaseWorkflow):
    def __init__(self):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION)
        # Set by future MCHPAgent config-reload from PHASE
        # content.whois_domain_pool. None → use FALLBACK_DOMAINS.
        self.domain_pool = None

    def action(self, extra=None, logger=None):
        pool = self.domain_pool or FALLBACK_DOMAINS
        domain = random.choice(pool)
        if logger:
            logger.step_start("whois_lookup", category="browser",
                              message=f"WHOIS for {domain}")
        result = whois_lookup(domain)
        success = result.startswith("%") or "domain" in result.lower()
        if logger:
            if success:
                logger.step_success("whois_lookup")
            else:
                logger.step_error("whois_lookup", result[:80])
        return result
