"""Lead workflow through the HTTP API: every step is an explicit, audited analyst decision. Synthetic data; mocked HTTP.

Central assertions: a page mention is NOT ownership, nothing is created without the analyst's decision, no OWNS-like edge exists."""

from __future__ import annotations

import json
import sys

import httpx
import pytest
from sqlalchemy import select

from app.config import Settings
from app.db import get_session_factory
from app.models import AuditLog, Entity, Relationship, RelationshipEvidence, ToolRun
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


# ---------------------------------------------------------------------------------- graph "expand neighbours"
def _wallet_id(client, h, inv, address=EVM_A):
    f = _fetch(client, h, inv)
    body = {"investigation_id": inv, "fetch_run_id": f["tool_run_id"], "network": "evm", "address": address, "rationale": "Ad quotes this payment address"}
    return client.post(f"{P}/track", json=body, headers=h).json()["wallet_entity_id"]


def _counts():
    with get_session_factory()() as s:
        return (
            len(list(s.scalars(select(Entity.id)))), len(list(s.scalars(select(Relationship.id)))),
            len(list(s.scalars(select(RelationshipEvidence.id)))), len(list(s.scalars(select(ToolRun.id)).all())),
        )


def test_graph_expand_looks_up_once_then_reuses_stored_data_without_duplicates(client, admin_headers):
    inv = _start(client, admin_headers)
    wid = _wallet_id(client, admin_headers, inv)
    first = client.post(f"{P}/graph-expand", json={"entity_id": wid}, headers=admin_headers).json()
    assert first["lookup"]["reused_prior_run"] is False and first["lookup"]["status"] == "succeeded"
    assert len(first["added"]) == 1 and first["added"][0]["address"] == EVM_B and first["linked_new"] == 1
    assert first["added"][0]["relationship_status"] == "verified"
    after_first = _counts()

    second = client.post(f"{P}/graph-expand", json={"entity_id": wid}, headers=admin_headers).json()
    assert second["lookup"]["reused_prior_run"] is True and second["lookup"]["tool_run_id"] == first["lookup"]["tool_run_id"]
    assert second["linked_new"] == 0 and second["added"][0]["already_present"] is True
    assert _counts() == after_first  # no new entity, relationship, evidence row or provider run

    with get_session_factory()() as s:
        types = {r.relationship_type for r in s.scalars(select(Relationship))}
        assert types == {"PAGE_REFERENCES_ENTITY", "WALLET_TRANSACTED_WITH"} and not any(("OWN" in t or "CONTROL" in t) for t in types)


def test_graph_expand_reuses_a_lookup_made_in_lead_search_and_is_one_step(client, admin_headers):
    inv = _start(client, admin_headers)
    wid = _wallet_id(client, admin_headers, inv)
    o = client.post(f"{P}/onchain", json={"investigation_id": inv, "entity_id": wid}, headers=admin_headers).json()
    runs_before = _counts()[3]
    r = client.post(f"{P}/graph-expand", json={"entity_id": wid}, headers=admin_headers).json()
    assert r["lookup"]["reused_prior_run"] is True and r["lookup"]["tool_run_id"] == o["tool_run_id"]
    assert _counts()[3] == runs_before  # no provider call was made
    # the new counterparty was only linked, not looked up itself (one step)
    with get_session_factory()() as s:
        b = s.scalar(select(Entity).where(Entity.canonical_identifier == f"addr:ethereum:{EVM_B.lower()}"))
        assert b is not None
        assert {t.provider_name for t in s.scalars(select(ToolRun))} == {"web_fetch", "evm_rpc"}


def test_graph_expand_limits_to_twenty_counterparties(client, admin_headers, monkeypatch):
    items = [
        {"hash": f"0xt{i}", "from": {"hash": EVM_A, "is_contract": False}, "to": {"hash": "0x" + f"{i + 1:040x}", "is_contract": False},
         "value": "1", "timestamp": "2026-01-01T00:00:00Z", "result": "success", "block_number": i}
        for i in range(25)
    ]
    base = leads.make_ctx()
    http = httpx.Client(transport=httpx.MockTransport(lambda req: (
        httpx.Response(200, json={"items": items, "next_page_params": None}) if req.headers.get("host") == "scout.example.org" and req.url.path.endswith("/transactions")
        else _handler(req)
    )))
    base.settings.onchain_history_limit = 50
    monkeypatch.setattr(leads, "make_ctx", lambda: ProviderContext(settings=base.settings, http=http))
    inv = _start(client, admin_headers)
    wid = _wallet_id(client, admin_headers, inv)
    r = client.post(f"{P}/graph-expand", json={"entity_id": wid}, headers=admin_headers).json()
    assert len(r["added"]) == 20 and r["skipped_over_limit"] == 5


def test_graph_expand_without_onchain_data_adds_nothing_and_says_so(client, admin_headers):
    inv = _start(client, admin_headers)
    f = _fetch(client, admin_headers, inv)
    t = client.post(f"{P}/track", json={"investigation_id": inv, "fetch_run_id": f["tool_run_id"], "network": "solana", "address": SOL, "rationale": "payment address quoted in the ad"}, headers=admin_headers).json()
    before = _counts()[:3]
    r = client.post(f"{P}/graph-expand", json={"entity_id": t["wallet_entity_id"]}, headers=admin_headers).json()
    assert r["added"] == [] and r["lookup"]["unavailable"] is True and "nothing was added" in r["note"]
    assert _counts()[:3] == before


def test_graph_expand_requires_analyst_and_a_wallet(client, admin_headers):
    viewer = make_user("viewer")
    assert client.post(f"{P}/graph-expand", json={"entity_id": "x"}, headers=viewer).status_code == 403
    assert client.post(f"{P}/graph-expand", json={"entity_id": "nope"}, headers=admin_headers).status_code == 404


# ---------------------------------------------------------------------------------- "load more" history pages
EVM_C = "0x" + "c" * 40
EVM_D = "0x" + "d" * 40


def _tx(h, other, n=1):
    return {"hash": h, "from": {"hash": EVM_A, "is_contract": False}, "to": {"hash": other, "is_contract": False}, "value": "1",
            "timestamp": "2026-01-01T00:00:00Z", "result": "success", "block_number": n}


def _paged(monkeypatch, pages):
    """Blockscout mock: no query -> pages[0]; `?page=k` -> pages[k]. Returns the list of requested query strings."""
    base = leads.make_ctx()
    seen: list[str] = []

    def handler(req: httpx.Request):
        if req.headers.get("host") == "scout.example.org" and req.url.path.endswith("/transactions"):
            seen.append(req.url.query.decode())
            k = int(req.url.params.get("page", 0))
            nxt = {"page": str(k + 1), "block_number": 100 - k, "index": 3} if k + 1 < len(pages) else None
            return httpx.Response(200, json={"items": pages[k], "next_page_params": nxt})
        return _handler(req)

    monkeypatch.setattr(leads, "make_ctx", lambda: ProviderContext(settings=base.settings, http=httpx.Client(transport=httpx.MockTransport(handler))))
    return seen


def _second_wallet_page(monkeypatch):
    """The mocked ad also quotes EVM_D, so a second wallet can be tracked from it."""
    monkeypatch.setattr(sys.modules[__name__], "PAGE", PAGE.replace("<p>SOL", f"<p>ETH2: {EVM_D}</p><p>SOL"))


def _more(client, h, inv, wid, run_id):
    return client.post(f"{P}/onchain/more", json={"investigation_id": inv, "entity_id": wid, "lookup_run_id": run_id}, headers=h)


def test_load_more_fetches_next_page_as_separate_run_without_superseding_page_one(client, admin_headers, monkeypatch):
    seen = _paged(monkeypatch, [[_tx("0xp1", EVM_B, 1)], [_tx("0xp2", EVM_C, 2)], [_tx("0xp3", EVM_D, 3)]])
    inv = _start(client, admin_headers)
    wid = _wallet_id(client, admin_headers, inv)
    first = client.post(f"{P}/onchain", json={"investigation_id": inv, "entity_id": wid}, headers=admin_headers).json()
    n1 = first["view"]["networks"][0]
    assert n1["has_more"] is True and n1["history_truncated"] is True and n1["is_page"] is False

    p2 = _more(client, admin_headers, inv, wid, first["lookup_run_id"])
    assert p2.status_code == 200, p2.text
    p2 = p2.json()
    assert p2["status"] == "succeeded" and p2["reused_prior_run"] is False and p2["lookup_run_id"] != first["lookup_run_id"]
    n2 = p2["view"]["networks"][0]
    assert [t["hash"] for t in n2["recent_transactions"]] == ["0xp2"] and n2["is_page"] is True and n2["has_more"] is True
    assert [c["address"] for c in n2["counterparties"]] == [EVM_C]
    assert any("page=1" in q and "block_number=100" in q for q in seen)  # the provider's next_page_params were sent back

    p3 = _more(client, admin_headers, inv, wid, p2["lookup_run_id"]).json()  # chain from the latest page
    assert p3["view"]["networks"][0]["has_more"] is False
    assert _more(client, admin_headers, inv, wid, p3["lookup_run_id"]).status_code == 409  # nothing further

    with get_session_factory()() as s:  # page one is intact: own run, own observation, never superseded
        runs = list(s.scalars(select(ToolRun).where(ToolRun.provider_name == "evm_rpc")))
        assert len({r.input_key for r in runs}) == 3 and sum(1 for r in runs if "#page:" in r.input_key) == 2
        from app.models import Observation

        obs = list(s.scalars(select(Observation).where(Observation.observation_type == "blockchain_address")))
        assert len(obs) == 3 and {o.seq for o in obs} == {1}
        acts = [a.action for a in s.scalars(select(AuditLog))]
        assert acts.count("lead.onchain.load_more") == 2  # the refused 3rd call (no cursor) is not audited as a load


def test_load_more_is_idempotent_and_page_one_is_still_reused(client, admin_headers, monkeypatch):
    seen = _paged(monkeypatch, [[_tx("0xp1", EVM_B)], [_tx("0xp2", EVM_C)]])
    inv = _start(client, admin_headers)
    wid = _wallet_id(client, admin_headers, inv)
    first = client.post(f"{P}/onchain", json={"investigation_id": inv, "entity_id": wid}, headers=admin_headers).json()
    a = _more(client, admin_headers, inv, wid, first["lookup_run_id"]).json()
    calls = len(seen)
    b = _more(client, admin_headers, inv, wid, first["lookup_run_id"]).json()
    assert b["reused_prior_run"] is True and b["lookup_run_id"] == a["lookup_run_id"] and len(seen) == calls  # no second node query
    again = client.post(f"{P}/onchain", json={"investigation_id": inv, "entity_id": wid}, headers=admin_headers).json()
    assert again["reused_prior_run"] is True and again["lookup_run_id"] == first["lookup_run_id"]  # page 2 did not hijack page 1


def test_load_more_guards(client, admin_headers, monkeypatch):
    _paged(monkeypatch, [[_tx("0xp1", EVM_B)], [_tx("0xp2", EVM_C)]])
    inv = _start(client, admin_headers)
    wid = _wallet_id(client, admin_headers, inv)
    first = client.post(f"{P}/onchain", json={"investigation_id": inv, "entity_id": wid}, headers=admin_headers).json()
    viewer = make_user("viewer")
    _second_wallet_page(monkeypatch)
    assert _more(client, viewer, inv, wid, first["lookup_run_id"]).status_code == 403
    assert _more(client, admin_headers, inv, wid, "nope").status_code == 404
    other = _wallet_id(client, admin_headers, inv, address=EVM_D)
    assert _more(client, admin_headers, inv, other, first["lookup_run_id"]).status_code == 409  # that run is about another address
    assert _more(client, admin_headers, "nope", wid, first["lookup_run_id"]).status_code == 404


def test_load_more_unavailable_source_is_reported_not_replaced(client, admin_headers, monkeypatch):
    _paged(monkeypatch, [[_tx("0xp1", EVM_B)], [_tx("0xp2", EVM_C)]])
    inv = _start(client, admin_headers)
    wid = _wallet_id(client, admin_headers, inv)
    first = client.post(f"{P}/onchain", json={"investigation_id": inv, "entity_id": wid}, headers=admin_headers).json()
    base = leads.make_ctx()
    base.settings.evm_explorer_apis = {}
    monkeypatch.setattr(leads, "make_ctx", lambda: base)
    r = _more(client, admin_headers, inv, wid, first["lookup_run_id"]).json()
    assert r["status"] == "unavailable" and "OSINT_EVM_EXPLORER_APIS" in r["error"] and "view" not in r


def test_expand_cites_the_page_a_counterparty_was_seen_in(client, admin_headers, monkeypatch):
    _paged(monkeypatch, [[_tx("0xp1", EVM_B)], [_tx("0xp2", EVM_C)]])
    inv = _start(client, admin_headers)
    wid = _wallet_id(client, admin_headers, inv)
    first = client.post(f"{P}/onchain", json={"investigation_id": inv, "entity_id": wid}, headers=admin_headers).json()
    p2 = _more(client, admin_headers, inv, wid, first["lookup_run_id"]).json()
    body = {"investigation_id": inv, "entity_id": wid, "lookup_run_id": first["lookup_run_id"], "counterparties": [
        {"network": "ethereum", "address": EVM_B, "direction": "sent_to"},
        {"network": "ethereum", "address": EVM_C, "direction": "sent_to", "lookup_run_id": p2["lookup_run_id"]},
    ]}
    r = client.post(f"{P}/expand", json=body, headers=admin_headers).json()
    assert [a["address"] for a in r["added"]] == [EVM_B, EVM_C] and r["skipped"] == []
    # EVM_C is NOT in the first page: citing page 1 for it must be refused (the evidence has to contain it)
    bad = {**body, "counterparties": [{"network": "ethereum", "address": EVM_C, "direction": "sent_to"}]}
    assert client.post(f"{P}/expand", json=bad, headers=admin_headers).json()["skipped"][0]["reason"].startswith("not a counterparty")
    # a run about another wallet cannot be cited
    _second_wallet_page(monkeypatch)
    other = _wallet_id(client, admin_headers, inv, address=EVM_D)
    assert client.post(f"{P}/expand", json={**body, "entity_id": other}, headers=admin_headers).status_code == 409
