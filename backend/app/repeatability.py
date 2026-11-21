"""Mention repeatability: on how many DIFFERENT pages does the same name/login appear next to the same address?

STATUS: IMPLEMENTED (pure functions; wired into the Lead Search state by services/leads.py).

This is NOT a confidence score, NOT a probability and NOT evidence of ownership:
* it is derived on read from stored page observations - nothing is persisted and the relationship status is
  never changed by it (status stays a function of evidence only, see docs/LLM_WIKI/EVIDENCE_MODEL.md);
* different URLs may copy the same content (scrapers, mirrors, re-posts), so many URLs can still be one source;
* only the (at most two) short context snippets kept per address per page are inspected, so it can under-count,
  never over-count.

Only a WORD is shown to the analyst (never a percentage). The internal curve below exists to keep the thresholds
monotone and explainable: +8 per distinct URL up to 10 URLs (10 -> 80), then +2 per URL (11 -> 82, 20 -> 98).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from app.normalize import InvalidReference, normalize_url

MIN_TERM_LEN = 3
DISCLAIMER = (
    "Mention repeatability, not ownership. Different URLs may copy the same content, and a page that places a name "
    "next to an address proves only that the page says so."
)

# (upper bound exclusive on the internal points, word)
_LEVELS: tuple[tuple[int, str], ...] = (
    (20, "very low"), (40, "low"), (60, "moderate"), (85, "high"), (99, "very high"),
)
ALMOST_CERTAIN_FROM = 20  # distinct URLs
ALMOST_CERTAIN = "almost certain repeatability"


def repeatability_points(distinct_urls: int) -> int:
    """Internal curve (never shown): 8 per URL up to 10 URLs, then 2 per URL, capped at 98."""
    n = max(0, int(distinct_urls))
    if n <= 10:
        return 8 * n
    return min(98, 80 + 2 * (n - 10))


def repeatability_level(distinct_urls: int) -> str | None:
    """Word for a number of distinct URLs. None for 0 (no page places the name/login next to the address)."""
    if distinct_urls <= 0:
        return None
    if distinct_urls >= ALMOST_CERTAIN_FROM:
        return ALMOST_CERTAIN
    pts = repeatability_points(distinct_urls)
    return next(word for upper, word in _LEVELS if pts < upper)


def fold(text: str) -> str:
    """Case/diacritic/whitespace-insensitive form used only for matching (Polish letters included)."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).casefold()
    t = t.replace("ł", "l")  # NFKD does not decompose it
    return re.sub(r"\s+", " ", t).strip()


def context_mentions(context: str, *, name: str | None = None, login: str | None = None) -> bool:
    """Does this short context snippet contain the full name (either order for two words) or the login as a whole token?

    A bare first name or surname never matches, and neither does a login shorter than MIN_TERM_LEN."""
    ctx = fold(context)
    if not ctx:
        return False
    if name:
        n = fold(name)
        words = n.split(" ")
        if len(words) >= 2:
            variants = {n}
            if len(words) == 2:
                variants.add(f"{words[1]} {words[0]}")
            for v in variants:
                if re.search(rf"(?<![^\W_]){re.escape(v)}(?![^\W_])", ctx):
                    return True
    if login:
        lg = fold(login).lstrip("@")
        if len(lg) >= MIN_TERM_LEN and re.search(rf"(?<![^\W_]){re.escape(lg)}(?![^\W_])", ctx):
            return True
    return False


def page_key(url: str | None) -> str | None:
    """Same page = same key: scheme, `www.`, fragment, trailing slash and tracking parameters are ignored."""
    if not url:
        return None
    try:
        return normalize_url(url).key
    except InvalidReference:
        return url.strip().split("#", 1)[0] or None


def distinct_pages(urls: Iterable[str | None]) -> set[str]:
    return {k for k in (page_key(u) for u in urls) if k}


def summarize(urls_with_term: Iterable[str | None], urls_with_address: Iterable[str | None]) -> dict:
    """API shape. `distinct_urls` is a plain count of pages; there is deliberately no numeric score."""
    with_term, with_addr = distinct_pages(urls_with_term), distinct_pages(urls_with_address)
    n = len(with_term)
    return {
        "distinct_urls": n,
        "pages_with_address": max(len(with_addr), n),
        "level": repeatability_level(n),
        "disclaimer": DISCLAIMER,
    }
