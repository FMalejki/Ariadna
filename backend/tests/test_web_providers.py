"""web_search (Tavily / ddgs fallback) and web_fetch (SSRF, robots, size, no-scrape list, redaction). No network access.

`resolve_public_ips` and `_ddgs_search` are replaced; the pinned-IP request is routed by its Host header."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from app.config import Settings
from app.models import Observation, ToolRun
from app.providers import webfetch as wf
from app.providers import websearch as ws
from app.providers.base import ProviderContext, ProviderError, ProviderInput, ProviderInputError, ProviderUnavailable
from app.providers.registry import sync_providers
from app.providers.webfetch import WebFetchProvider, website_reference
from app.providers.websearch import WebSearchProvider
from app.services import runner
from tests.conftest import EVM_A

SOL = "C8H4v4c2eA6njjgzvWSrCpLdYg3hWSygoVsi4RkUrzjV"


def _row(session, name):
    return next(p for p in sync_providers(session) if p.name == name)


def _http(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------------------------------ search
class TestWebSearch:
    TAVILY = {"results": [
        {"title": "Ad for laptop", "url": "https://olx.example.org/ad/1?utm_source=x", "content": "call 600 123 456 or a@b.co wallet " + EVM_A, "score": 0.9},
        {"title": "dup", "url": "https://olx.example.org/ad/1", "content": "dup", "score": 0.1},
        {"title": "local", "url": "http://127.0.0.1/admin", "content": "internal", "score": 0.1},
    ]}

    def _run(self, session, handler, payload, **settings):
        s = Settings(**settings)
        ctx = ProviderContext(settings=s, http=_http(handler))
        return runner.run_provider(session, _row(session, "web_search"), ProviderInput(reference=None, payload={"target": "search:t", **payload}), ctx=ctx)

    def test_tavily_success_normalizes_dedups_drops_private_and_redacts(self, session):
        seen = {}

        def handler(req: httpx.Request):
            seen["auth"], seen["body"] = req.headers.get("authorization"), json.loads(req.content)
            return httpx.Response(200, json=self.TAVILY)

        out = self._run(session, handler, {"query": '"Jan Test" solana'}, tavily_api_key="k-test")
        assert out.error is None and seen["auth"] == "Bearer k-test"
        assert seen["body"]["exact_match"] is True and seen["body"]["max_results"] == 10
        run = session.get(ToolRun, out.tool_run.id)
        from app.services import leads

        doc = leads.run_doc(session, run)
        assert doc["backend_used"] == "tavily" and doc["unofficial_backend"] is False and doc["fallback"] is None
        assert len(doc["results"]) == 1  # duplicate and 127.0.0.1 dropped
        snip = doc["results"][0]["snippet"]
        assert "a@b.co" not in snip and "123 456" not in snip and EVM_A in snip
        assert "utm_source" not in doc["results"][0]["url"]

    def test_search_results_never_enter_the_graph(self, session):
        self._run(session, lambda r: httpx.Response(200, json=self.TAVILY), {"query": "q"}, tavily_api_key="k")
        assert session.scalar(select(Observation.id)) is None

    @pytest.mark.parametrize("code,unavailable", [(401, True), (432, True), (433, True), (429, False), (500, False)])
    def test_tavily_errors_are_classified_when_backend_is_forced(self, session, code, unavailable):
        out = self._run(session, lambda r: httpx.Response(code), {"query": "q", "backend": "tavily"}, tavily_api_key="k")
        assert out.error and out.unavailable is unavailable and out.retryable is (not unavailable)

    def test_auto_falls_back_to_ddgs_and_records_why(self, session, monkeypatch):
        monkeypatch.setattr(ws, "_ddgs_search", lambda q, n, t: [{"title": "T", "href": "https://example.org/p", "body": "b"}])
        out = self._run(session, lambda r: httpx.Response(401), {"query": "q"}, tavily_api_key="bad")
        from app.services import leads

        doc = leads.run_doc(session, out.tool_run)
        assert doc["backend_used"] == "ddgs" and doc["unofficial_backend"] is True
        assert doc["fallback"]["from"] == "tavily" and "401" in doc["fallback"]["reason"]

    def test_auto_without_key_falls_back_and_says_so(self, session, monkeypatch):
        monkeypatch.setattr(ws, "_ddgs_search", lambda q, n, t: [{"title": "T", "href": "https://example.org/p", "body": "b"}])
        out = self._run(session, lambda r: httpx.Response(500), {"query": "q"}, tavily_api_key=None)
        from app.services import leads

        doc = leads.run_doc(session, out.tool_run)
        assert doc["backend_used"] == "ddgs" and "no API key" in doc["fallback"]["reason"]

    def test_forced_tavily_without_key_does_not_silently_use_ddgs(self, session, monkeypatch):
        monkeypatch.setattr(ws, "_ddgs_search", lambda *a: pytest.fail("ddgs must not be used when tavily was requested"))
        out = self._run(session, lambda r: httpx.Response(500), {"query": "q", "backend": "tavily"}, tavily_api_key=None)
        assert out.unavailable and "no API key" in out.error

    def test_ddgs_failure_is_reported(self, session, monkeypatch):
        def boom(*a):
            raise ProviderError("ddgs: rate limited by the search engine (unofficial backend)")

        monkeypatch.setattr(ws, "_ddgs_search", boom)
        out = self._run(session, lambda r: httpx.Response(500), {"query": "q", "backend": "ddgs"})
        assert out.error and "rate limited" in out.error and out.retryable

    def test_input_validation(self):
        p = WebSearchProvider()
        for payload in ({"query": ""}, {"query": "x" * 301}, {"query": "ok", "backend": "bing"}):
            with pytest.raises(ProviderInputError):
                p.validate_input(ProviderInput(reference=None, payload=payload))

    def test_never_routed_by_discovery(self):
        assert WebSearchProvider().supports(None) is False  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------ fetch
PAGE = f"""<html><head><title>Sprzedam laptop</title><script>var x="{EVM_A}";</script></head>
<body><p>Laptop 1500 zł. Kontakt: sprzedawca@example.com, tel. 600 123 456.</p>
<p>Wpłata na Solana: {SOL} (lub ETH {EVM_A})</p><style>.a{{}}</style></body></html>"""


def _site(pages: dict[str, tuple[int, str, str]], robots: str | None = "User-agent: *\nAllow: /"):
    """pages: path -> (status, content-type, body). Routed on the Host header (the URL host is the pinned IP)."""

    def handler(req: httpx.Request):
        host = req.headers["host"]
        if req.url.path == "/robots.txt":
            return httpx.Response(404) if robots is None else httpx.Response(200, text=robots, headers={"content-type": "text/plain"})
        status, ctype, body = pages.get(f"{host}{req.url.path}", (404, "text/plain", "nope"))
        headers = {"content-type": ctype}
        if status in (301, 302):
            headers["location"] = body
            body = ""
        return httpx.Response(status, headers=headers, content=body.encode())

    return handler


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(wf, "resolve_public_ips", lambda host, port: ["93.184.216.34"])


class TestWebFetch:
    def _run(self, session, url, handler, **settings):
        ctx = ProviderContext(settings=Settings(**settings), http=_http(handler))
        return runner.run_provider(session, _row(session, "web_fetch"), ProviderInput(reference=website_reference(url), payload={"url": url}), ctx=ctx)

    def test_fetch_extracts_addresses_with_context_and_drops_contact_data(self, session, public_dns):
        out = self._run(session, "https://ads.example.org/ad/1", _site({"ads.example.org/ad/1": (200, "text/html; charset=utf-8", PAGE)}))
        assert out.error is None, out.error
        obs = session.scalar(select(Observation).where(Observation.observation_type == "web_page"))
        p = obs.normalized_payload
        nets = {c["network"]: c for c in p["address_candidates"]}
        assert set(nets) == {"solana", "evm"}
        ctx_text = " ".join(" ".join(c["contexts"]) for c in nets.values())
        assert "sprzedawca@example.com" not in ctx_text and "600 123 456" not in ctx_text
        assert "script" not in ctx_text and p["title"] == "Sprzedam laptop"
        assert "body" not in p and "text" not in p  # page body is not stored
        assert nets["evm"]["occurrences"] == 1  # the copy inside <script> is not page text

    def test_fetch_does_not_create_wallet_entities_by_itself(self, session, public_dns):
        self._run(session, "https://ads.example.org/ad/1", _site({"ads.example.org/ad/1": (200, "text/html", PAGE)}))
        from app.models import Entity

        types = {e.entity_type for e in session.scalars(select(Entity))}
        assert types == {"website"}

    def test_request_is_pinned_to_the_resolved_ip_with_host_header(self, session, monkeypatch):
        monkeypatch.setattr(wf, "resolve_public_ips", lambda host, port: ["93.184.216.34"])
        urls = []

        def handler(req):
            urls.append((req.url.host, req.headers["host"], req.extensions.get("sni_hostname")))
            return _site({"ads.example.org/": (200, "text/html", "<p>hi</p>")})(req)

        self._run(session, "https://ads.example.org/", handler)
        assert urls and all(h == "93.184.216.34" and host == "ads.example.org" and sni == "ads.example.org" for h, host, sni in urls)

    @pytest.mark.parametrize("ip", ["10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.1.1", "::1", "fd00::1"])
    def test_ssrf_non_public_resolution_is_refused_and_nothing_is_requested(self, session, monkeypatch, ip):
        import socket

        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))])
        calls = []
        out = self._run(session, "https://evil.example.org/", lambda r: calls.append(r) or httpx.Response(200))
        assert out.unavailable and "non-public" in out.error and calls == []

    @pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/latest/meta-data", "http://[::1]/", "ftp://example.org/x", "http://user:pw@example.org/"])
    def test_ssrf_literal_and_scheme_rejected_at_input(self, url):
        with pytest.raises(Exception):
            website_reference(url)

    def test_redirect_to_private_target_is_refused(self, session, public_dns):
        pages = {"ads.example.org/go": (302, "text/html", "http://169.254.169.254/latest/meta-data")}
        out = self._run(session, "https://ads.example.org/go", _site(pages))
        assert out.unavailable and "redirect" in out.error

    def test_redirect_chain_is_followed_and_revalidated(self, session, public_dns):
        pages = {"a.example.org/1": (301, "text/html", "https://b.example.org/2"), "b.example.org/2": (200, "text/plain", f"pay to {EVM_A}")}
        out = self._run(session, "https://a.example.org/1", _site(pages))
        assert out.error is None
        p = session.scalar(select(Observation).where(Observation.observation_type == "web_page")).normalized_payload
        assert p["redirects"] == ["https://b.example.org/2"] and p["address_candidates"][0]["address"].lower() == EVM_A.lower()

    def test_too_many_redirects(self, session, public_dns):
        pages = {f"a.example.org/{i}": (302, "text/html", f"https://a.example.org/{i + 1}") for i in range(20)}
        out = self._run(session, "https://a.example.org/0", _site(pages), fetch_max_redirects=3)
        assert out.unavailable and "redirects" in out.error

    def test_robots_disallow_blocks_the_page_request(self, session, public_dns):
        seen = []

        def handler(req):
            seen.append(req.url.path)
            return _site({"ads.example.org/ad/1": (200, "text/html", PAGE)}, robots="User-agent: *\nDisallow: /ad/")(req)

        out = self._run(session, "https://ads.example.org/ad/1", handler)
        assert out.unavailable and "robots.txt" in out.error and "/ad/1" not in seen

    def test_robots_missing_means_allowed_but_server_error_means_not_verifiable(self, session, public_dns):
        ok = self._run(session, "https://ads.example.org/ad/1", _site({"ads.example.org/ad/1": (200, "text/html", PAGE)}, robots=None))
        assert ok.error is None

        def handler(req):
            return httpx.Response(503) if req.url.path == "/robots.txt" else httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>x</p>")

        bad = self._run(session, "https://other.example.org/p", handler)
        assert bad.unavailable and "robots" in bad.error

    @pytest.mark.parametrize("url", ["https://x.com/someone/status/1", "https://twitter.com/a", "https://t.me/channel", "https://www.instagram.com/a/", "https://pl.linkedin.com/in/a"])
    def test_no_scrape_platforms_are_refused_without_a_request(self, session, public_dns, url):
        calls = []
        out = self._run(session, url, lambda r: calls.append(r) or httpx.Response(200))
        assert out.unavailable and "no-scrape" in out.error and calls == []

    def test_size_cap_truncates_and_flags(self, session, public_dns):
        big = "<p>" + ("lorem ipsum " * 5000) + f" {EVM_A}</p>"
        out = self._run(session, "https://ads.example.org/big", _site({"ads.example.org/big": (200, "text/html", big)}), fetch_max_bytes=2000)
        assert out.error is None
        p = session.scalar(select(Observation).where(Observation.observation_type == "web_page")).normalized_payload
        assert p["truncated"] is True and p["bytes_read"] <= 2000 and p["address_candidates"] == []  # address was beyond the cap

    @pytest.mark.parametrize("ctype", ["application/pdf", "image/png", "application/octet-stream"])
    def test_non_text_content_is_refused(self, session, public_dns, ctype):
        out = self._run(session, "https://ads.example.org/f", _site({"ads.example.org/f": (200, ctype, "x")}))
        assert out.unavailable and "content type" in out.error

    @pytest.mark.parametrize("code,unavailable", [(403, True), (404, True), (410, True), (429, False), (503, False)])
    def test_http_status_mapping(self, session, public_dns, code, unavailable):
        out = self._run(session, "https://ads.example.org/f", _site({"ads.example.org/f": (code, "text/html", "x")}))
        assert out.error and out.unavailable is unavailable

    def test_input_validation(self):
        p = WebFetchProvider()
        with pytest.raises(ProviderInputError):
            p.validate_input(ProviderInput(reference=None, payload={"url": "https://example.org/"}))
        with pytest.raises(ProviderInputError):
            p.validate_input(ProviderInput(reference=website_reference("https://example.org/"), payload={"url": ""}))
        assert p.supports(website_reference("https://example.org/")) is False

    def test_dns_failure_is_a_retryable_error(self, session, monkeypatch):
        import socket

        def fail(*a, **k):
            raise socket.gaierror("no such host")

        monkeypatch.setattr(socket, "getaddrinfo", fail)
        out = self._run(session, "https://nonexistent.example.org/", lambda r: httpx.Response(200))
        assert out.error and out.retryable and not out.unavailable
