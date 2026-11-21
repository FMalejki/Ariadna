"""Lead workflow through the HTTP API: every step is an explicit, audited analyst decision. Synthetic data; mocked HTTP.

Central assertions: a page mention is NOT ownership, nothing is created without the analyst's decision, no OWNS-like edge exists."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from app.config import Settings
from app.db import get_session_factory
from app.models import AuditLog, Entity, Relationship
from app.providers import webfetch as wf
from app.providers import websearch as ws
from app.providers.base import ProviderContext
from app.services import leads
from tests.conftest import EVM_A, EVM_B, make_user

P = "/api/v1/leads"
SOL = "C8H4v4c2eA6njjgzvWSrCpLdYg3hWSygoVsi4RkUrzjV"
PAGE_URL = "https://ads.example.org/ad/42"
PAGE = f"<html><head><title>Ad 42</title></head><body><p>Sprzedam rower. mail x@y.co tel 600 111 222. ETH: {EVM_A}</p><p>SOL {SOL}</p></body></html>"


def _handler(req: httpx.Request):
    host = req.headers.get("host", req.url.host)
    if host == "api.tavily.test":
        return httpx.Response(200, json={"results": [{"title": "Ad 42", "url": PAGE_URL, "content": "rower " + EVM_A, "score": 0.8}]})
    if host == "ads.example.org":
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", headers={"content-type": "text/plain"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=PAGE.encode())
    if host == "scout.example.org":
        if req.url.path.endswith("/transactions"):
            return httpx.Response(200, json={"items": [
                {"hash": "0xt1", "from": {"hash": EVM_A, "is_contract": False}, "to": {"hash": EVM_B, "is_contract": False}, "value": "1", "timestamp": "2026-01-01T00:00:00Z", "result": "success", "block_number": 1}
            ], "next_page_params": None})
        return httpx.Response(200, json={"coin_balance": "2000000000000000000", "is_contract": False, "ens_domain_name": None, "is_scam": False, "reputation": "ok"})
    if host == "rpc.example.org":
        m = json.loads(req.content)["method"]
        res = {"eth_getCode": "0x", "eth_getTransactionCount": "0x3", "eth_getBalance": "0x1bc16d674ec80000"}.get(m)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": res})
    return httpx.Response(404)


@pytest.fixture(autouse=True)
def mocked(monkeypatch):
    settings = Settings(
        tavily_api_key="k-test", tavily_base_url="https://api.tavily.test", evm_rpc_urls={"ethereum": "https://rpc.example.org"},
        evm_explorer_apis={"ethereum": "https://scout.example.org/api"}, btc_esplora_url=None,
    )
    http = httpx.Client(transport=httpx.MockTransport(_handler))
    monkeypatch.setattr(leads, "make_ctx", lambda: ProviderContext(settings=settings, http=http))
    monkeypatch.setattr(wf, "resolve_public_ips", lambda host, port: ["93.184.216.34"])
    monkeypatch.setattr(ws, "_ddgs_search", lambda *a: pytest.fail("ddgs not expected"))


def _start(client, h, subject="Jan Testowy"):
    r = client.post(f"{P}/sessions", json={"subject": subject}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["investigation_id"]


def _fetch(client, h, inv):
    r = client.post(f"{P}/fetch", json={"investigation_id": inv, "url": PAGE_URL}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_query_proposal_is_deterministic_and_quotes_the_name(client, admin_headers):
    r = client.post(f"{P}/queries/propose", json={"subject": 'Jan "Testowy"', "extra_terms": ["pump"]}, headers=admin_headers)
    qs = [q["query"] for q in r.json()["queries"]]
    assert qs[0] == '"Jan Testowy"' and '"Jan Testowy" solana' in qs and '"Jan Testowy" pump' in qs
    assert any("site:olx.pl" in q for q in qs)
    assert r.json() == client.post(f"{P}/queries/propose", json={"subject": 'Jan "Testowy"', "extra_terms": ["pump"]}, headers=admin_headers).json()


def test_config_exposes_ddgs_caveat_and_no_secret(client, admin_headers, monkeypatch):
    from app.api import leads as leads_api

    monkeypatch.setattr(leads_api, "get_settings", lambda: Settings(tavily_api_key="k-test"))
    body = client.get(f"{P}/config", headers=admin_headers).json()
    assert body["tavily_configured"] is True and "UNOFFICIAL" in body["ddgs_notice"]
    assert "k-test" not in json.dumps(body)


def test_viewer_can_read_but_not_act(client, admin_headers):
    inv = _start(client, admin_headers)
    viewer = make_user("viewer")
    assert client.post(f"{P}/queries/propose", json={"subject": "x"}, headers=viewer).status_code == 200
    assert client.get(f"{P}/sessions/{inv}", headers=viewer).status_code == 200
    for path, body in (
        ("sessions", {"subject": "x"}), ("search", {"investigation_id": inv, "query": "q"}), ("fetch", {"investigation_id": inv, "url": PAGE_URL}),
        ("track", {"investigation_id": inv, "fetch_run_id": "x", "network": "evm", "address": EVM_A, "rationale": "long enough rationale"}),
        ("onchain", {"investigation_id": inv, "entity_id": "x"}),
        ("expand", {"investigation_id": inv, "entity_id": "x", "lookup_run_id": "y", "counterparties": [{"network": "evm", "address": EVM_B, "direction": "sent_to"}]}),
    ):
        assert client.post(f"{P}/{path}", json=body, headers=viewer).status_code == 403, path
    assert client.post(f"{P}/search", json={"investigation_id": inv, "query": "q"}).status_code in (401, 403)


def test_search_returns_leads_and_creates_nothing_in_the_graph(client, admin_headers):
    inv = _start(client, admin_headers)
    r = client.post(f"{P}/search", json={"investigation_id": inv, "query": '"Jan Testowy" wallet'}, headers=admin_headers).json()
    assert r["status"] == "succeeded" and r["backend_used"] == "tavily" and r["unofficial_backend"] is False
    assert r["results"][0]["url"] == PAGE_URL
    with get_session_factory()() as s:
        assert s.scalar(select(Entity.id)) is None
    detail = client.get(f"{P}/runs/{r['tool_run_id']}", headers=admin_headers).json()
    assert detail["results"][0]["url"] == PAGE_URL  # results can be re-read after a reload


def test_full_flow_search_fetch_track_onchain_expand(client, admin_headers):
    inv = _start(client, admin_headers)
    client.post(f"{P}/search", json={"investigation_id": inv, "query": "q"}, headers=admin_headers)

    f = _fetch(client, admin_headers, inv)
    assert f["status"] == "succeeded"
    nets = {c["network"]: c for c in f["page"]["address_candidates"]}
    assert set(nets) == {"evm", "solana"}
    ctxt = " ".join(nets["evm"]["contexts"])
    assert "x@y.co" not in ctxt and "600 111 222" not in ctxt

    # nothing tracked yet: only the page exists
    with get_session_factory()() as s:
        assert {e.entity_type for e in s.scalars(select(Entity))} == {"website"}

    # track requires a real rationale
    base = {"investigation_id": inv, "fetch_run_id": f["tool_run_id"], "network": "evm", "address": nets["evm"]["address"]}
    assert client.post(f"{P}/track", json={**base, "rationale": "short"}, headers=admin_headers).status_code == 422
    # ... and only for an address that was actually extracted from that page
    assert client.post(f"{P}/track", json={**base, "address": EVM_B, "rationale": "long enough rationale"}, headers=admin_headers).status_code == 404

    t = client.post(f"{P}/track", json={**base, "rationale": "Ad names the seller and quotes this payment address"}, headers=admin_headers).json()
    assert t["relationship_type"] == "PAGE_REFERENCES_ENTITY" and "does not establish" in t["note"]
    with get_session_factory()() as s:
        types = {r.relationship_type for r in s.scalars(select(Relationship))}
        assert types == {"PAGE_REFERENCES_ENTITY"}
        assert not any(("OWN" in x or "CONTROL" in x) for x in types)
        assert {e.entity_type for e in s.scalars(select(Entity))} == {"website", "wallet"}  # solana candidate NOT tracked
        assert s.scalar(select(AuditLog).where(AuditLog.action == "lead.track")).details["rationale"].startswith("Ad names")

    # on-chain
    o = client.post(f"{P}/onchain", json={"investigation_id": inv, "entity_id": t["wallet_entity_id"]}, headers=admin_headers).json()
    assert o["status"] == "succeeded", o
    net = o["view"]["networks"][0]
    assert net["network"] == "ethereum" and net["balance"].startswith("2") and net["counterparties"][0]["address"] == EVM_B
    assert net["recent_transactions"][0]["direction"] == "sent"

    # repeated lookup reuses the stored run (knowledge reuse), refresh forces a new one
    again = client.post(f"{P}/onchain", json={"investigation_id": inv, "entity_id": t["wallet_entity_id"]}, headers=admin_headers).json()
    assert again["reused_prior_run"] is True and again["tool_run_id"] == o["tool_run_id"]
    assert client.post(f"{P}/onchain", json={"investigation_id": inv, "entity_id": t["wallet_entity_id"], "refresh": True}, headers=admin_headers).json()["reused_prior_run"] is False

    # expand: only counterparties that are in the cited lookup; fake ones are skipped
    cps = [{"network": "ethereum", "address": EVM_B, "direction": "sent_to"}, {"network": "ethereum", "address": "0x" + "99" * 20, "direction": "sent_to"}]
    e = client.post(f"{P}/expand", json={"investigation_id": inv, "entity_id": t["wallet_entity_id"], "lookup_run_id": o["lookup_run_id"], "counterparties": cps}, headers=admin_headers).json()
    assert len(e["added"]) == 1 and e["added"][0]["relationship_status"] == "verified" and len(e["skipped"]) == 1
    with get_session_factory()() as s:
        rel = s.scalar(select(Relationship).where(Relationship.relationship_type == "WALLET_TRANSACTED_WITH"))
        assert rel is not None
        assert {r.relationship_type for r in s.scalars(select(Relationship))} == {"PAGE_REFERENCES_ENTITY", "WALLET_TRANSACTED_WITH"}

    st = client.get(f"{P}/sessions/{inv}", headers=admin_headers).json()
    assert {w["address"].lower() for w in st["wallets"]} == {EVM_A.lower(), EVM_B.lower()}
    assert {r["provider"] for r in st["runs"]} >= {"web_search", "web_fetch", "evm_rpc"}


def test_onchain_unavailable_is_reported_honestly(client, admin_headers):
    inv = _start(client, admin_headers)
    f = _fetch(client, admin_headers, inv)
    # a Solana wallet while no Solana RPC is configured
    t = client.post(f"{P}/track", json={"investigation_id": inv, "fetch_run_id": f["tool_run_id"], "network": "solana", "address": SOL, "rationale": "payment address quoted in the ad"}, headers=admin_headers).json()
    o = client.post(f"{P}/onchain", json={"investigation_id": inv, "entity_id": t["wallet_entity_id"]}, headers=admin_headers).json()
    assert o["status"] == "unavailable" and o["unavailable"] is True and "OSINT_SOLANA_RPC_URL" in o["error"] and "view" not in o


def test_fetch_of_blocked_or_private_target_is_reported_not_bypassed(client, admin_headers):
    inv = _start(client, admin_headers)
    r = client.post(f"{P}/fetch", json={"investigation_id": inv, "url": "https://x.com/someone"}, headers=admin_headers).json()
    assert r["status"] == "unavailable" and "no-scrape" in r["error"] and r["page"] is None
    assert client.post(f"{P}/fetch", json={"investigation_id": inv, "url": "http://127.0.0.1/"}, headers=admin_headers).status_code == 422


def test_unknown_investigation_and_run_are_404(client, admin_headers):
    assert client.post(f"{P}/search", json={"investigation_id": "nope", "query": "q"}, headers=admin_headers).status_code == 404
    assert client.get(f"{P}/runs/nope", headers=admin_headers).status_code == 404
