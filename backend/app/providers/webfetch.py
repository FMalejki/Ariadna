"""Analyst-selected page fetch (lead workflow): SSRF-safe, robots.txt-respecting, size-limited, privacy-preserving.

STATUS: IMPLEMENTED. Verified with mocked HTTP in tests and once against a live page (result in docs/LLM_WIKI/sessions/).

Safety properties (each is covered by a test):
* Only a URL the analyst explicitly selected is requested. Nothing is crawled; links on the page are not followed.
* SSRF: every hop's hostname is resolved first and refused unless ALL addresses are globally routable. The request
  is then sent to the resolved IP (pinned; Host header + TLS SNI keep the original name), so a DNS answer cannot
  change between check and connect. Redirects are followed manually (limited) and re-validated on every hop.
  Residual risk: none for the pinned hop itself; run the backend without access to internal networks as defence in depth.
* robots.txt (RFC 9309 semantics): disallow -> not fetched; 4xx -> allowed; unreachable / 5xx -> treated as disallow.
* Platforms that must not be scraped (X, Telegram, Instagram, Facebook, TikTok, LinkedIn by default) are refused;
  the honest alternative is authorized manual import (spec 4.2). Not silently substituted.
* Size cap (decoded bytes) and a wall-clock deadline. Only text/html, application/xhtml+xml and text/plain.
* The page body is NOT stored. Only: URL, title, content hash, and for each blockchain address found a short,
  contact-redacted context snippet (app/extract.py). E-mails, phone numbers and other personal contact data are dropped.
* Page text is untrusted data: it is never executed, never sent to an LLM here, and never creates graph entities by itself
  (`text_fields` stays empty; an address becomes a wallet entity only when the analyst decides to track it).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.config import Settings
from app.domain import EntityType, EvidenceType, ObservationType, SourceType
from app.extract import extract_address_candidates, redact_contact_data
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
    SourceRef,
)

ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
MAX_TEXT_CHARS = 400_000
ROBOTS_MAX_BYTES = 500_000
_REDIRECT = {301, 302, 303, 307, 308}
_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "tr", "td", "th", "table", "section", "article", "header", "footer", "h1", "h2",
    "h3", "h4", "h5", "h6", "pre", "blockquote", "main", "aside", "nav", "dd", "dt", "form", "address", "title", "head",
}


def website_reference(url: str) -> Reference:
    """A WEBSITE reference for exactly the URL the analyst chose (never re-classified as a profile/explorer entity)."""
    u = normalize_url(url)
    return Reference(
        EntityType.WEBSITE.value, f"website:{u.key}", u.key, SourceType.WEBSITE.value, url, url=u.canonical_url,
        attrs={"host": u.domain, "path": u.path},
    )


def is_blocked_domain(domain: str, blocked: list[str]) -> str | None:
    d = domain.lower()
    for b in blocked:
        b = b.lower().lstrip(".")
        if d == b or d.endswith("." + b):
            return b
    return None


# ----------------------------------------------------------------------------------------------- SSRF guard


def resolve_public_ips(host: str, port: int) -> list[str]:
    """Resolve `host`; refuse unless every answer is a globally routable address. Replaceable in tests."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise DnsError(f"DNS resolution failed for {host}") from exc
    ips: list[str] = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise ProviderUnavailable(f"{host} resolves to a non-public address ({ip}); refusing (SSRF protection)")
        if str(ip) not in ips:
            ips.append(str(ip))
    if not ips:
        raise DnsError(f"DNS resolution returned no addresses for {host}")
    return ips


class DnsError(ProviderError):
    """Name resolution failed (retryable); distinct so robots.txt handling does not mislabel it as 'disallowed'."""


@dataclass
class _Response:
    status: int
    headers: httpx.Headers
    body: bytes
    truncated: bool
    url: str


class GuardedFetcher:
    def __init__(self, client: httpx.Client, settings: Settings):
        self.client = client
        self.s = settings
        self._robots: dict[str, RobotFileParser | None] = {}
        self.deadline = time.monotonic() + max(5.0, settings.fetch_timeout_seconds * 2)

    # -- single pinned request, no redirect following
    def _once(self, url: str, max_bytes: int, accept: str) -> _Response:
        u = normalize_url(url)  # re-validates scheme / credentials / private literals on every hop
        parts = urlsplit(u.canonical_url)
        scheme = parts.scheme
        port = parts.port or (443 if scheme == "https" else 80)
        ip = resolve_public_ips(u.host, port)[0]
        host_ip = f"[{ip}]" if ":" in ip else ip
        default_port = port == (443 if scheme == "https" else 80)
        pinned = f"{scheme}://{host_ip}:{port}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")
        headers = {
            "Host": u.host if default_port else f"{u.host}:{port}", "User-Agent": self.s.fetch_user_agent,
            "Accept": accept, "Accept-Encoding": "gzip, deflate",
        }
        ext = {"sni_hostname": u.host} if scheme == "https" else {}
        chunks: list[bytes] = []
        total = 0
        truncated = False
        try:
            with self.client.stream("GET", pinned, headers=headers, extensions=ext) as resp:
                status, resp_headers = resp.status_code, resp.headers
                if status == 200:
                    for chunk in resp.iter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            chunks.append(chunk[: max_bytes - (total - len(chunk))])
                            truncated = True
                            break
                        chunks.append(chunk)
                        if time.monotonic() > self.deadline:
                            truncated = True
                            break
        except httpx.TimeoutException as exc:
            raise ProviderError(f"fetch timeout for {u.host}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"fetch transport error for {u.host}: {type(exc).__name__}") from exc
        return _Response(status, resp_headers, b"".join(chunks), truncated, u.canonical_url)

    # -- follows redirects manually, re-checking policy on every hop
    def get(self, url: str, max_bytes: int, accept: str, redirects: list[str]) -> _Response:
        current = url
        for _ in range(self.s.fetch_max_redirects + 1):
            self._check_allowed(current)
            resp = self._once(current, max_bytes, accept)
            if resp.status in _REDIRECT and resp.headers.get("location"):
                nxt = urljoin(resp.url, resp.headers["location"])
                try:
                    normalize_url(nxt)
                except InvalidReference as exc:
                    raise ProviderUnavailable(f"redirect to a disallowed target refused: {exc}") from exc
                redirects.append(nxt)
                current = nxt
                continue
            return resp
        raise ProviderUnavailable(f"more than {self.s.fetch_max_redirects} redirects; refusing")

    def _check_allowed(self, url: str) -> None:
        u = normalize_url(url)
        blocked = is_blocked_domain(u.domain, self.s.fetch_blocked_domains)
        if blocked:
            raise ProviderUnavailable(
                f"{blocked} is on the no-scrape list (platform content must come through authorized manual import, not page fetching)"
            )
        if not self._robots_allows(u.canonical_url, u.host, urlsplit(u.canonical_url).scheme):
            raise ProviderUnavailable(f"robots.txt of {u.host} disallows fetching this URL (or could not be verified); not fetched")

    def _robots_allows(self, url: str, host: str, scheme: str) -> bool:
        key = f"{scheme}://{host}"
        if key not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            try:
                r = self._once(f"{key}/robots.txt", ROBOTS_MAX_BYTES, "text/plain")
                hops = 0
                while r.status in _REDIRECT and r.headers.get("location") and hops < 3:
                    nxt = urljoin(r.url, r.headers["location"])
                    normalize_url(nxt)
                    if is_blocked_domain(normalize_url(nxt).domain, self.s.fetch_blocked_domains):
                        break
                    r, hops = self._once(nxt, ROBOTS_MAX_BYTES, "text/plain"), hops + 1
                if r.status == 200:
                    parser.parse(r.body.decode("utf-8", "replace").splitlines())  # type: ignore[union-attr]
                elif 400 <= r.status < 500:
                    parser.parse([])  # no robots.txt => everything allowed (RFC 9309)
                else:
                    parser = None  # 5xx / odd redirects => cannot verify => treat as disallow
            except (ProviderUnavailable, DnsError):
                raise  # SSRF refusal / DNS failure is the real reason; do not mislabel it as a robots.txt decision
            except (ProviderError, InvalidReference):
                parser = None  # robots.txt unreachable => cannot verify => treat as disallow
            self._robots[key] = parser
        parser = self._robots[key]
        if parser is None:
            return False
        return parser.can_fetch(self.s.fetch_user_agent.split("/")[0], url)


# ----------------------------------------------------------------------------------------------- HTML -> text


def _fallback_text(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    text = re.sub(r"(?is)<(script|style|noscript|template)\b.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"[ \t\r\f\v]+", " ", text)


def _charset(header_charset: str | None, raw: bytes) -> str | None:
    """Declared charset if any; else UTF-8 when the bytes are valid UTF-8 (lxml would otherwise guess Latin-1 and
    turn Polish letters into mojibake, which also breaks name matching); else None (let lxml decide)."""
    if header_charset:
        return header_charset
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return None


def html_to_text(raw: bytes, header_charset: str | None = None) -> tuple[str | None, str, list[str]]:
    """(title, visible text, absolute http(s) link targets). Never executes anything."""
    from lxml import etree
    from lxml import html as lhtml

    enc = _charset(header_charset, raw)
    try:
        doc = lhtml.document_fromstring(raw, parser=lhtml.HTMLParser(encoding=enc) if enc else None)
    except (etree.ParserError, ValueError, etree.XMLSyntaxError):
        return None, _fallback_text(raw), []
    title_el = doc.find(".//title")
    title = title_el.text_content().strip() if title_el is not None else None
    hrefs = [h.strip() for h in doc.xpath("//a/@href") if isinstance(h, str) and re.match(r"(?i)^https?://", h.strip())]
    for bad in doc.xpath("//script|//style|//noscript|//template|//svg|//iframe|//object|//embed"):
        bad.drop_tree()
    for el in doc.iter():
        if isinstance(el.tag, str) and el.tag.lower() in _BLOCK_TAGS:
            el.tail = "\n" + (el.tail or "")
    return title, doc.text_content(), hrefs


# ----------------------------------------------------------------------------------------------- provider


@dataclass
class FetchedPage:
    doc: dict = field(default_factory=dict)


class WebFetchProvider(IntelligenceProvider):
    name = "web_fetch"
    version = "1.0.0"
    kind = "http_api"
    category = "web_collection"
    description = "Fetch ONE analyst-selected public page (robots.txt, SSRF and size guarded) and extract blockchain address mentions with redacted context."
    capabilities = ["web_fetch", "address_extraction"]
    evidence_class = EvidenceType.DIRECT_SOURCE_STATEMENT
    docs_url = "https://www.rfc-editor.org/rfc/rfc9309"

    def supports(self, ref: Reference) -> bool:
        return False  # analyst-initiated only; the discovery engine never fetches pages on its own

    def validate_input(self, data: ProviderInput) -> None:
        url = str(data.payload.get("url") or "").strip()
        if not url:
            raise ProviderInputError("url is required")
        try:
            normalize_url(url)
        except InvalidReference as exc:
            raise ProviderInputError(str(exc)) from exc
        if data.reference is None or data.reference.entity_type != EntityType.WEBSITE.value:
            raise ProviderInputError("web_fetch needs a website reference built from the selected URL")

    def execute(self, data: ProviderInput, ctx: ProviderContext) -> RawResult:
        s = ctx.settings
        url = normalize_url(str(data.payload["url"])).canonical_url
        client = ctx.http or httpx.Client(timeout=s.fetch_timeout_seconds, follow_redirects=False)
        redirects: list[str] = []
        fetcher = GuardedFetcher(client, s)
        resp = fetcher.get(url, s.fetch_max_bytes, "text/html,application/xhtml+xml,text/plain;q=0.8", redirects)
        if resp.status in (401, 403):
            raise ProviderUnavailable(f"access refused by the site (HTTP {resp.status}); not bypassed")
        if resp.status in (404, 410):
            raise ProviderUnavailable(f"page not found (HTTP {resp.status})")
        if resp.status == 429 or resp.status >= 500:
            raise ProviderError(f"site returned HTTP {resp.status}")
        if resp.status != 200:
            raise ProviderUnavailable(f"unexpected HTTP {resp.status}")
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype not in ALLOWED_CONTENT_TYPES:
            raise ProviderUnavailable(f"unsupported content type {ctype or 'unknown'!r}; only HTML/plain text pages are read")
        if ctype == "text/plain":
            title, text, hrefs = None, resp.body.decode("utf-8", "replace"), []
        else:
            m = re.search(r"charset=([\w.:-]+)", resp.headers.get("content-type", ""), re.I)
            try:
                title, text, hrefs = html_to_text(resp.body, m.group(1) if m else None)
            except LookupError:  # unknown charset name in the header
                title, text, hrefs = html_to_text(resp.body)
        text = text[:MAX_TEXT_CHARS]
        hrefs = [h for h in hrefs[:2000]]
        candidates = extract_address_candidates(text + ("\n" + "\n".join(hrefs) if hrefs else ""))
        doc = {
            "url": url, "final_url": resp.url, "redirects": redirects, "status": resp.status, "content_type": ctype,
            "title": redact_contact_data(title or "")[:200] or None, "bytes_read": len(resp.body), "truncated": resp.truncated,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "text_chars": len(text),
            "robots": "allowed", "address_candidates": [c.as_dict() for c in candidates],
            "privacy": "page body not stored; e-mail/phone-like strings are redacted from context snippets; only addresses, snippets and the URL are kept",
        }
        return RawResult(
            raw=json.dumps(doc, sort_keys=True, ensure_ascii=False).encode(), content_type="application/json", parsed=doc,
            log=f"HTTP 200 {ctype} bytes={len(resp.body)} candidates={len(candidates)} truncated={resp.truncated}",
            parameters={"url": url, "final_url": resp.url, "user_agent": s.fetch_user_agent},
        )

    def normalize_result(self, raw: RawResult, data: ProviderInput, ctx: ProviderContext) -> list[NormalizedObservation]:
        ref = data.reference
        assert ref is not None
        doc = raw.parsed
        payload = {k: v for k, v in doc.items() if k != "privacy"}
        payload["kind"] = ObservationType.WEB_PAGE.value
        return [
            NormalizedObservation(
                source=SourceRef(SourceType.WEBSITE.value, ref.canonical_id, ref.url, ref.canonical_id, doc.get("title") or ref.display_name, "http_fetch"),
                subject=ref, observation_type=ObservationType.WEB_PAGE.value, observed_at=ctx.now or datetime.now(UTC), payload=payload,
                external_id=ref.canonical_id,
                text_fields=[],  # deliberate: addresses become entities only through an explicit analyst decision
                snapshot=True,
            )
        ]

    def health_check(self, ctx: ProviderContext) -> HealthStatus:
        try:
            import lxml  # noqa: F401
        except ImportError:
            return HealthStatus("degraded", "lxml not installed; text extraction falls back to a tag-stripping regex")
        return HealthStatus("healthy", "local component; requests are made only for analyst-selected URLs")
