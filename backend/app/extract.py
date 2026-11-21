"""Address candidate extraction with privacy-preserving context (lead workflow).

Deterministic (no LLM). For every blockchain address found in a page's text it keeps ONLY:
  * the address + network,
  * a short context snippet around it (<= CONTEXT_RADIUS characters each side) with e-mail addresses, phone-like
    numbers and other candidate addresses' neighbours' contact data redacted,
  * the number of occurrences.
Everything else on the page (names, phone numbers, e-mails, street addresses) is discarded. The page body itself is
not stored. A candidate is a MENTION on a page, never proof of ownership (spec 1.3 / 6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.normalize import AddressSpan, find_address_spans

CONTEXT_RADIUS = 120
MAX_CONTEXTS_PER_ADDRESS = 2
MAX_CANDIDATES = 50

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# phone-like: optional +, 9+ digits with common separators; alnum neighbours excluded so address bodies are never touched
_PHONE = re.compile(r"(?<![0-9A-Za-z])\+?\d[\d \-().]{7,}\d(?![0-9A-Za-z])")
_WS = re.compile(r"\s+")


def redact_contact_data(text: str) -> str:
    text = _EMAIL.sub("[email removed]", text)
    text = _PHONE.sub("[phone removed]", text)
    return _WS.sub(" ", text).strip()


@dataclass
class AddressCandidate:
    network: str
    address: str
    occurrences: int = 0
    contexts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"network": self.network, "address": self.address, "occurrences": self.occurrences, "contexts": list(self.contexts)}


def _context(text: str, span: AddressSpan) -> str:
    before = redact_contact_data(text[max(0, span.start - CONTEXT_RADIUS) : span.start])
    after = redact_contact_data(text[span.end : span.end + CONTEXT_RADIUS])
    return f"{'…' if span.start > CONTEXT_RADIUS else ''}{before} {span.raw} {after}…".strip()


def extract_address_candidates(text: str) -> list[AddressCandidate]:
    """Group address mentions by (network, address); order by first appearance."""
    found: dict[tuple[str, str], AddressCandidate] = {}
    for span in find_address_spans(text):
        key = (span.network, span.address)
        cand = found.get(key)
        if cand is None:
            if len(found) >= MAX_CANDIDATES:
                continue
            cand = found[key] = AddressCandidate(span.network, span.address)
        cand.occurrences += 1
        if len(cand.contexts) < MAX_CONTEXTS_PER_ADDRESS:
            ctx = _context(text, span)
            if ctx not in cand.contexts:
                cand.contexts.append(ctx)
    return list(found.values())
