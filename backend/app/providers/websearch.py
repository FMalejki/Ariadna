"""Web search provider (lead discovery): Tavily (primary, official API) with ddgs (unofficial, keyless) fallback.

STATUS: IMPLEMENTED. Verified with mocked HTTP in tests; live use is analyst-initiated only.

Honesty rules (spec 4.2 / 6):
* Search results are `tool_generated_discovery` LEADS - they are not evidence of anything about a person.
* `tavily` is an official keyed API (https://docs.tavily.com/documentation/api-reference/endpoint/search).
* `ddgs` is an UNOFFICIAL scraper of DuckDuckGo (https://pypi.org/project/ddgs/). It can break at any time and may
  violate the search engine's terms. It is only used (a) when the analyst explicitly selects it, or (b) in `auto`
  mode when Tavily is not configured / unavailable - and then the result states `fallback` and the reason, so the
  substitution is never silent.
* Search snippets are untrusted external text; contact data (e-mail/phone) is redacted before storage.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx

from app.domain import EvidenceType
from app.extract import redact_contact_data
from app.normalize import InvalidReference, Reference, normalize_url
from app.providers.base import (
    HealthStatus,
    IntelligenceProvider,
    NormalizedObservation,
    ProviderContext,
    ProviderError,
    ProviderInput,
    ProviderInputError,
    ProviderUnavailable,
    RawResult,
)

BACKENDS = ("auto", "tavily", "ddgs")
MAX_QUERY_LEN = 300


def _ddgs_search(query: str, max_results: int, timeout: float) -> list[dict]:
    """Isolated so tests can replace it; imports ddgs lazily so a missing package degrades honestly."""
    try:
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException
    except ImportError as exc:
        raise ProviderUnavailable("the 'ddgs' package is not installed (pip install ddgs)") from exc
    try:
        return list(DDGS(timeout=int(timeout)).text(query, max_results=max_results))
    except RatelimitException as exc:
        raise ProviderError("ddgs: rate limited by the search engine (unofficial backend)") from exc
    except TimeoutException as exc:
        raise ProviderError("ddgs: timeout") from exc
    except DDGSException as exc:
        raise ProviderError(f"ddgs: {str(exc)[:200]}") from exc


def _tavily_search(client: httpx.Client, base_url: str, api_key: str, query: str, max_results: int) -> list[dict]:
    body: dict[str, Any] = {"query": query, "search_depth": "basic", "max_results": max_results, "include_answer": False}
    if '"' in query:
        body["exact_match"] = True
    try:
        resp = client.post(f"{base_url.rstrip('/')}/search", json=body, headers={"Authorization": f"Bearer {api_key}"})
    except httpx.TimeoutException as exc:
        raise ProviderError("tavily: timeout") from exc
    except httpx.HTTPError as exc:
        raise ProviderError(f"tavily: transport error {type(exc).__name__}") from exc
    if resp.status_code == 401:
        raise ProviderUnavailable("tavily: API key rejected (HTTP 401); check OSINT_TAVILY_API_KEY")
    if resp.status_code in (432, 433):
        raise ProviderUnavailable(f"tavily: plan/credit limit reached (HTTP {resp.status_code})")
    if resp.status_code == 429:
        raise ProviderError(f"tavily: rate limited (HTTP 429), Retry-After={resp.headers.get('Retry-After', '?')}")
    if resp.status_code != 200:
        raise ProviderError(f"tavily: HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        return list(resp.json().get("results") or [])
    except ValueError as exc:
        raise ProviderError("tavily: non-JSON response") from exc


def _clean(items: list[dict], source: str) -> list[dict]:
    """Normalize both backends to one shape; drop non-http(s)/non-public URLs and duplicates; redact contact data."""
    out: list[dict] = []
    seen: set[str] = set()
    for i, it in enumerate(items):
        url = it.get("url") or it.get("href") or ""
        try:
            nu = normalize_url(url)
        except InvalidReference:
            continue
        if nu.key in seen:
            continue
        seen.add(nu.key)
        out.append(
            {
                "rank": len(out) + 1,
                "title": redact_contact_data(str(it.get("title") or ""))[:300],
                "url": nu.canonical_url,
                "domain": nu.domain,
                "snippet": redact_contact_data(str(it.get("content") or it.get("body") or ""))[:600],
                "score": it.get("score"),
                "backend": source,
            }
        )
    return out


class WebSearchProvider(IntelligenceProvider):
    name = "web_search"
    version = "1.0.0"
    kind = "http_api"
    category = "web_collection"
    description = "Analyst-approved web search for leads (Tavily primary; ddgs = UNOFFICIAL keyless fallback). Results are leads, not evidence."
    capabilities = ["web_search"]
    evidence_class = EvidenceType.TOOL_GENERATED_DISCOVERY
    docs_url = "https://docs.tavily.com/documentation/api-reference/endpoint/search"

    def supports(self, ref: Reference) -> bool:
        return False  # analyst-initiated only; never routed by the discovery engine

    def validate_input(self, data: ProviderInput) -> None:
        q = str(data.payload.get("query") or "").strip()
        if not q:
            raise ProviderInputError("query is required")
        if len(q) > MAX_QUERY_LEN:
            raise ProviderInputError(f"query exceeds {MAX_QUERY_LEN} characters")
        if data.payload.get("backend", "auto") not in BACKENDS:
            raise ProviderInputError(f"backend must be one of {BACKENDS}")

    def execute(self, data: ProviderInput, ctx: ProviderContext) -> RawResult:
        s = ctx.settings
        query = str(data.payload["query"]).strip()
        requested = data.payload.get("backend") or s.search_default_backend
        n = max(1, min(int(data.payload.get("max_results") or s.search_max_results), 20))
        used, fallback = requested, None
        if requested in ("auto", "tavily"):
            used = "tavily"
            try:
                if not s.tavily_api_key:
                    raise ProviderUnavailable("tavily: no API key configured (set OSINT_TAVILY_API_KEY)")
                raw_items = _tavily_search(ctx.client(), s.tavily_base_url, s.tavily_api_key, query, n)
            except ProviderError as exc:
                if requested == "tavily":
                    raise
                fallback = {"from": "tavily", "reason": str(exc)}
                used = "ddgs"
                raw_items = _ddgs_search(query, n, s.provider_http_timeout_seconds)
        else:
            raw_items = _ddgs_search(query, n, s.provider_http_timeout_seconds)
        results = _clean(raw_items, used)
        doc = {
            "query": query, "backend_requested": requested, "backend_used": used, "fallback": fallback,
            "unofficial_backend": used == "ddgs", "retrieved_at": (ctx.now or datetime.now(UTC)).isoformat(),
            "results": results,
            "note": "Wyniki wyszukiwania to tropy (tool_generated_discovery). Nie ustalają faktów o żadnej osobie ani adresie.",
        }
        return RawResult(
            raw=json.dumps(doc, sort_keys=True, ensure_ascii=False).encode(), parsed=doc,
            log=f"backend={used} results={len(results)}" + (f" fallback_reason={fallback['reason'][:200]}" if fallback else ""),
            parameters={"query": query, "backend": used, "unofficial_backend": used == "ddgs"},
        )

    def normalize_result(self, raw: RawResult, data: ProviderInput, ctx: ProviderContext) -> list[NormalizedObservation]:
        return []  # leads are kept as the raw artifact of the ToolRun; nothing enters the knowledge graph until the analyst acts

    def health_check(self, ctx: ProviderContext) -> HealthStatus:
        s = ctx.settings
        parts = [f"tavily: {'key configured' if s.tavily_api_key else 'no key'}"]
        try:
            import ddgs  # noqa: F401

            parts.append("ddgs: installed (unofficial)")
            ddgs_ok = True
        except ImportError:
            parts.append("ddgs: not installed")
            ddgs_ok = False
        if s.tavily_api_key:
            return HealthStatus("healthy", "; ".join(parts))
        return HealthStatus("degraded" if ddgs_ok else "unavailable", "; ".join(parts))
