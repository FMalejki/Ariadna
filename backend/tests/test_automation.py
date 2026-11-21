"""Lead automation (stages A-E): dependencies, limits, abort, the stage-C context rule, audit, policy skips and honest failures.

Synthetic data; mocked HTTP (same pattern as test_leads.py). No real person, wallet or site is represented."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from app.config import Settings
from app.db import get_session_factory
from app.models import AuditLog, AutomationRun, Entity, Relationship
from app.providers import webfetch as wf
from app.providers import websearch as ws
from app.providers.base import ProviderContext, RawResult
from app.providers.sherlock import SherlockProvider
from app.services import automation as auto
from app.services import leads
from app.services.leads import LeadError
from tests.conftest import EVM_A, EVM_B, make_user

P = "/api/v1/automation"
SOL = "C8H4v4c2eA6njjgzvWSrCpLdYg3hWSygoVsi4RkUrzjV"
NAME = "Jan Testowy"
GOOD = "https://ads.example.org/ad/1"
SECOND = "https://ads.example.org/ad/2"
CLOSED = "https://closed.example.org/page"
BLOCKED = "https://t.me/someone"
PROFILE = "https://profiles.example.org/u/jt_login"
FILLER = "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor. " * 8  # > 2 * CONTEXT_RADIUS
PAGE_1 = (
    f"<html><head><title>Ad 1</title></head><body><p>Sprzedaje {NAME}, wplaty na ETH: {EVM_A}</p>"
    f"<p>{FILLER}</p><p>Inny sprzedawca, SOL {SOL}</p><p>{FILLER}</p><p>Strona autora: {NAME}</p></body></html>"
)
PAGE_2 = f"<html><body><p>Kontakt: jt_login prosze o wplate {EVM_B}</p></body></html>"
PAGE_FIRST_NAME_ONLY = f"<html><body><p>Jan zaprasza. Wplata: {EVM_B}</p></body></html>"
SEARCH_URLS = [GOOD, CLOSED, BLOCKED, GOOD + "#frag"]
CSV = (
    "username,name,url_main,url_user,exists,http_status,response_time_s\n"
    f"jt_login,Example,https://profiles.example.org,{PROFILE},Claimed,200,0.1\n"
    "jt_login,Other,https://other.example.org,https://other.example.org/jt_login,Available,404,0.1\n"
)
STATE = {"pages": {GOOD: PAGE_1, SECOND: PAGE_2}, "fetched": [], "search_urls": SEARCH_URLS}


def _handler(req: httpx.Request):
    host = req.headers.get("host", req.url.host)
    if host == "api.tavily.test":
        return httpx.Response(200, json={"results": [{"title": "t", "url": u, "content": "c", "score": 0.5} for u in STATE["search_urls"]]})
    if host in ("ads.example.org", "profiles.example.org", "closed.example.org"):
        if req.url.path == "/robots.txt":
            body = "User-agent: *\nDisallow: /" if host == "closed.example.org" else "User-agent: *\nAllow: /"
            return httpx.Response(200, text=body, headers={"content-type": "text/plain"})
        url = f"https://{host}{req.url.path}"
        STATE["fetched"].append(url)
        page = STATE["pages"].get(url)
        if page is None:
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=page.encode())
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
    STATE.update({"pages": {GOOD: PAGE_1, SECOND: PAGE_2}, "fetched": [], "search_urls": SEARCH_URLS})
    settings = Settings(
        tavily_api_key="k-test", tavily_base_url="https://api.tavily.test", evm_rpc_urls={"ethereum": "https://rpc.example.org"},
        evm_explorer_apis={"ethereum": "https://scout.example.org/api"}, btc_esplora_url=None,
        sherlock_binary="definitely-not-installed-sherlock",
    )
    http = httpx.Client(transport=httpx.MockTransport(_handler))
    monkeypatch.setattr(leads, "make_ctx", lambda: ProviderContext(settings=settings, http=http))
    monkeypatch.setattr(wf, "resolve_public_ips", lambda host, port: ["93.184.216.34"])
    monkeypatch.setattr(ws, "_ddgs_search", lambda *a: pytest.fail("ddgs not expected"))
    monkeypatch.setattr("app.services.automation.get_settings", lambda: settings)


def _run(session, stages, *, limits=None, queries=("q1",), name=NAME, login=None):
    run = auto.start_run(session, actor="tester", stages=list(stages), limits=limits, full_name=name, login=login, queries=list(queries))
    session.commit()
    auto.advance_run(session, run)
    session.commit()
    return run


def _log(run, **kw):
    return [e for e in run.log if all(e[k] == v for k, v in kw.items())]


# ----------------------------------------------------------------------------------------------- validation


@pytest.mark.parametrize("stages", [["C"], ["D"], ["E"], ["A", "C"], ["B", "D"], ["B", "C", "E"], []])
def test_dependencies_are_enforced(session, stages):
    with pytest.raises(LeadError):
        auto.validate_stages(stages)


def test_valid_combinations_and_unknown_stage():
    assert auto.validate_stages(["e", "d", "c", "b"]) == ["B", "C", "D", "E"]
    assert auto.validate_stages(["A"]) == ["A"]
    with pytest.raises(LeadError):
        auto.validate_stages(["Z"])


def test_limits_have_defaults_and_hard_caps():
    assert auto.resolve_limits(None) == {"max_queries": 6, "max_pages": 15, "max_addresses": 30, "max_seconds": 600}
    assert auto.resolve_limits({"max_pages": 3})["max_pages"] == 3
    for bad in ({"max_pages": 0}, {"max_pages": 999}, {"nope": 1}, {"max_pages": "5"}):
        with pytest.raises(LeadError):
            auto.resolve_limits(bad)


def test_stage_c_rule_needs_full_name_or_login_never_a_bare_first_name():
    ctx = "x " * 20
    assert auto.match_term(f"{ctx} sprzedaje Jan Testowy ETH 0xabc", name=NAME, login=None) == "full name"
    assert auto.match_term("kontakt Testowy Jan, adres", name=NAME, login=None) == "full name"
    assert auto.match_term("kontakt jt_login adres", name=None, login="jt_login") == "login"
    assert auto.match_term("Jan zaprasza, adres", name=NAME, login=None) is None
    assert auto.match_term("pan Testowy zaprasza", name=NAME, login=None) is None
    assert auto.match_term("xjt_loginx", name=None, login="jt_login") is None


def test_start_requires_a_name_or_login_and_stage_a_needs_a_login(session):
    with pytest.raises(LeadError):
        auto.start_run(session, actor="t", stages=["B"], full_name=None, login=None)
    with pytest.raises(LeadError):
        auto.start_run(session, actor="t", stages=["A"], full_name=NAME, login=None)
    with pytest.raises(LeadError, match="invalid login"):
        auto.start_run(session, actor="t", stages=["A"], login="a b")


# ----------------------------------------------------------------------------------------------- stage B


def test_stage_b_alone_creates_no_wallets_and_logs_policy_skips(session):
    run = _run(session, ["B"])
    assert run.status == "completed", run.stop_reason
    assert not session.scalars(select(Entity).where(Entity.entity_type == "wallet")).all()
    assert _log(run, stage="B", action="fetch", outcome="skipped_policy", target=BLOCKED)  # no-scrape list: not even requested
    closed = _log(run, stage="B", action="fetch", outcome="skipped_policy", target=CLOSED)
    assert closed and "robots.txt" in closed[0]["detail"]
    assert BLOCKED not in "".join(STATE["fetched"]) and not any("closed.example.org/page" in u for u in STATE["fetched"])
    ok = _log(run, stage="B", action="fetch", outcome="ok", target=GOOD)
    assert len(ok) == 1  # the #fragment duplicate is not fetched twice
    assert run.counters["queries"] == 1 and run.counters["pages"] == 2  # good + robots-closed attempt; blocked costs nothing


def test_stage_b_lists_candidates_but_tracks_nothing(session):
    run = _run(session, ["B"])
    state = auto.run_state(session, run.id)
    assert {c["network"] for c in state["candidates"]} == {"evm", "solana"}
    assert all(c["tracked"] is False for c in state["candidates"])


# ----------------------------------------------------------------------------------------------- stage C


def test_stage_c_tracks_only_addresses_with_the_name_in_context(session):
    run = _run(session, ["B", "C"])
    assert run.status == "completed", run.stop_reason
    wallets = session.scalars(select(Entity).where(Entity.entity_type == "wallet")).all()
    assert {w.meta["address"].lower() for w in wallets} == {EVM_A.lower()}  # SOL: the name is on the page but far away
    assert _log(run, stage="C", action="track", outcome="ok")
    summary = _log(run, stage="C", action="rule")[0]["detail"]
    assert "1 address(es)" in summary and "1 stay candidates" in summary


def test_stage_c_never_creates_ownership_and_is_not_analyst_accepted(session):
    run = _run(session, ["B", "C"])
    types = {r.relationship_type for r in session.scalars(select(Relationship))}
    assert types == {"PAGE_REFERENCES_ENTITY"}
    rel = session.scalar(select(Relationship))
    assert rel.review_status != "accepted" and rel.reviewed_by is None  # nobody reviewed it
    assert rel.status == "supported"  # derived from the page evidence only, never raised by the automation
    assert not any(("OWN" in t or "CONTROL" in t) for t in types)
    assert run.status == "completed"


def test_stage_c_login_in_context_tracks_and_bare_first_name_does_not(session):
    STATE["search_urls"] = [SECOND, "https://ads.example.org/ad/3"]
    STATE["pages"]["https://ads.example.org/ad/3"] = PAGE_FIRST_NAME_ONLY
    run = _run(session, ["B", "C"], login="jt_login")
    wallets = session.scalars(select(Entity).where(Entity.entity_type == "wallet")).all()
    assert {w.meta["address"].lower() for w in wallets} == {EVM_B.lower()}
    tracked = _log(run, stage="C", action="track", outcome="ok")
    assert len(tracked) == 1 and "login" in tracked[0]["detail"]
    # EVM_B appears on both pages; it is tracked once (page 2 has the login), the first-name-only page does not add a second wallet
    assert len(session.scalars(select(Relationship)).all()) == 1


def test_stage_c_audits_every_automatic_track_with_an_explicit_rationale(session):
    _run(session, ["B", "C"])
    rows = session.scalars(select(AuditLog).where(AuditLog.action == "automation.track")).all()
    assert len(rows) == 1
    assert rows[0].details["matched"] == "full name" and "Automatic rule" in rows[0].details["rationale"]
    assert "not ownership" in rows[0].details["rationale"]
    assert session.scalar(select(AuditLog).where(AuditLog.action == "automation.start")) is not None
    assert session.scalar(select(AuditLog).where(AuditLog.action == "automation.finish")).details["status"] == "completed"
    assert session.scalar(select(AuditLog).where(AuditLog.action == "lead.track")).details["automatic"] is True


# ----------------------------------------------------------------------------------------------- stages D / E


def test_stage_d_looks_up_only_wallets_tracked_in_this_run(session):
    # a wallet tracked earlier by someone else must not be touched
    other = _run(session, ["B", "C"])
    assert other.status == "completed"
    before = {t.id for t in session.scalars(select(Entity)).all()}
    run = _run(session, ["B", "C", "D"])
    assert run.status == "completed", run.stop_reason
    d = _log(run, stage="D", action="onchain")
    assert len(d) == 1 and d[0]["outcome"] == "ok"
    assert {t.id for t in session.scalars(select(Entity)).all()} - before <= {e.id for e in session.scalars(select(Entity).where(Entity.entity_type == "website"))}
    assert {r.relationship_type for r in session.scalars(select(Relationship))} == {"PAGE_REFERENCES_ENTITY"}  # D adds no relationship


def test_stage_e_expands_one_step_without_duplicates_and_without_ownership(session):
    run = _run(session, ["B", "C", "D", "E"])
    assert run.status == "completed", run.stop_reason
    types = [r.relationship_type for r in session.scalars(select(Relationship))]
    assert sorted(types) == ["PAGE_REFERENCES_ENTITY", "WALLET_TRANSACTED_WITH"]
    rel = session.scalar(select(Relationship).where(Relationship.relationship_type == "WALLET_TRANSACTED_WITH"))
    assert rel.status == "verified"
    wallets = {w.meta["address"].lower() for w in session.scalars(select(Entity).where(Entity.entity_type == "wallet"))}
    assert wallets == {EVM_A.lower(), EVM_B.lower()}  # one step: the counterparty is NOT expanded further
    assert run.counters["expanded"] == 1
    # a second identical run adds nothing new
    again = _run(session, ["B", "C", "D", "E"])
    assert again.counters["expanded"] == 0
    assert len(session.scalars(select(Relationship).where(Relationship.relationship_type == "WALLET_TRANSACTED_WITH")).all()) == 1


def test_stage_d_provider_unavailable_is_logged_honestly(session, monkeypatch):
    s = Settings(tavily_api_key="k-test", tavily_base_url="https://api.tavily.test", evm_rpc_urls={}, evm_explorer_apis={}, sherlock_binary="nope-sherlock")
    http = httpx.Client(transport=httpx.MockTransport(_handler))
    monkeypatch.setattr(leads, "make_ctx", lambda: ProviderContext(settings=s, http=http))
    run = _run(session, ["B", "C", "D"])
    assert run.status == "completed"
    d = _log(run, stage="D", action="onchain")
    assert d and d[0]["outcome"] in ("unavailable", "failed") and d[0]["detail"]
    assert run.counters["skipped"] + run.counters["failed"] >= 1


# ----------------------------------------------------------------------------------------------- stage A


def test_stage_a_alone_reports_unavailable_sherlock_and_nothing_else(session):
    run = _run(session, ["A"], name=None, login="jt_login")
    assert run.status == "completed"
    a = _log(run, stage="A", action="sherlock")
    assert a[0]["outcome"] == "unavailable" and a[0]["detail"]  # not replaced by another source
    assert STATE["fetched"] == []


def test_stage_a_profile_urls_feed_stage_b_through_the_normal_fetch_policy(session, monkeypatch):
    monkeypatch.setattr(
        SherlockProvider, "execute",
        lambda self, data, ctx: RawResult(raw=CSV.encode(), content_type="text/csv", exit_code=0, parameters={"username": "jt_login"}),
    )
    STATE["search_urls"] = []
    STATE["pages"][PROFILE] = f"<html><body><p>jt_login wplaty: {EVM_A}</p></body></html>"
    run = _run(session, ["A", "B", "C"], name=None, login="jt_login")
    assert run.status == "completed", run.stop_reason
    assert _log(run, stage="A", action="sherlock", outcome="ok")
    assert _log(run, stage="B", action="fetch", outcome="ok", target=PROFILE)
    assert {w.meta["address"].lower() for w in session.scalars(select(Entity).where(Entity.entity_type == "wallet"))} == {EVM_A.lower()}
    # Sherlock hits are username-string matches: no identity edge was created by the automation
    assert "OWNS" not in {r.relationship_type for r in session.scalars(select(Relationship))}


# ----------------------------------------------------------------------------------------------- limits / abort


def test_page_limit_stops_the_run_with_a_stored_reason(session):
    STATE["search_urls"] = [GOOD, SECOND, "https://ads.example.org/ad/3"]
    run = _run(session, ["B"], limits={"max_pages": 1})
    assert run.status == "stopped_by_limit"
    assert "page limit reached (1)" in run.stop_reason
    assert run.counters["pages"] == 1
    assert _log(run, action="finish", outcome="stopped_by_limit")


def test_query_limit_stops_the_run(session):
    run = _run(session, ["B"], limits={"max_queries": 1}, queries=("q1", "q2", "q3"))
    assert run.status == "stopped_by_limit" and "query limit reached (1)" in run.stop_reason
    assert run.counters["queries"] == 1


def test_address_limit_stops_the_run(session):
    STATE["pages"][GOOD] = f"<html><body><p>{NAME} ETH {EVM_A}</p><p>{NAME} ETH {EVM_B}</p></body></html>"
    run = _run(session, ["B", "C"], limits={"max_addresses": 1})
    assert run.status == "stopped_by_limit" and "address limit reached (1)" in run.stop_reason
    assert run.counters["addresses"] == 1


def test_time_limit_stops_the_run(session):
    run = auto.start_run(session, actor="t", stages=["B"], full_name=NAME, queries=["q"], limits={"max_seconds": 1})
    run.status = "running"
    run.started_at = run.created_at.replace(year=2000)
    session.commit()
    auto.advance_run(session, run)
    assert run.status == "stopped_by_limit" and "time limit" in run.stop_reason


def test_abort_before_start_and_during_a_run(session):
    run = auto.start_run(session, actor="t", stages=["B"], full_name=NAME, queries=["q"])
    session.commit()
    auto.abort_run(session, run.id, "tester")
    assert run.status == "aborted" and run.finished_at is not None
    with pytest.raises(LeadError):  # already final
        auto.abort_run(session, run.id, "tester")
    running = auto.start_run(session, actor="t", stages=["B"], full_name=NAME, queries=["q1", "q2"])
    session.commit()
    auto.advance_run(session, running, budget_seconds=0)  # picked up, nothing executed yet
    assert running.status == "running"
    auto.abort_run(session, running.id, "tester")
    auto.advance_run(session, running)
    assert running.status == "aborted" and running.counters["queries"] == 0
    assert session.scalar(select(AuditLog).where(AuditLog.action == "automation.abort")) is not None


def test_an_unexpected_error_fails_the_run_and_is_recorded(session, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(leads, "search", boom)
    run = _run(session, ["B"])
    assert run.status == "failed" and "kaboom" in run.stop_reason


def test_advance_due_runs_is_resumable_and_skips_finished_runs(session):
    run = auto.start_run(session, actor="t", stages=["B"], full_name=NAME, queries=["q"])
    session.commit()
    assert auto.advance_due_runs(session) == 1
    session.refresh(run)
    assert run.status == "completed"
    assert auto.advance_due_runs(session) == 0


# ----------------------------------------------------------------------------------------------- API


def test_api_start_read_abort_and_permissions(client, admin_headers, session):
    cfg = client.get(f"{P}/config", headers=admin_headers).json()
    assert [s["id"] for s in cfg["stages"]] == list("ABCDE") and cfg["default_limits"]["max_queries"] == 6

    bad = client.post(f"{P}/runs", json={"full_name": NAME, "stages": ["C"]}, headers=admin_headers)
    assert bad.status_code == 422 and "requires stage B" in bad.json()["detail"]

    viewer = make_user("viewer")
    assert client.post(f"{P}/runs", json={"full_name": NAME, "stages": ["B"]}, headers=viewer).status_code == 403
    assert client.get(f"{P}/runs", headers=viewer).status_code == 200

    r = client.post(f"{P}/runs", json={"full_name": NAME, "stages": ["B", "C"], "queries": ["q1"], "limits": {"max_pages": 5}}, headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued" and body["limits"]["max_pages"] == 5 and body["limits"]["max_queries"] == 6
    rid = body["id"]

    assert client.post(f"{P}/runs/{rid}/abort", headers=viewer).status_code == 403
    assert client.get(f"{P}/runs/missing", headers=admin_headers).status_code == 404
    assert any(x["id"] == rid for x in client.get(f"{P}/runs", params={"investigation_id": body["investigation_id"]}, headers=admin_headers).json()["runs"])

    ab = client.post(f"{P}/runs/{rid}/abort", headers=admin_headers).json()
    assert ab["status"] == "aborted"
    assert client.post(f"{P}/runs/{rid}/abort", headers=admin_headers).status_code == 409
    assert any(e["action"] == "abort" for e in client.get(f"{P}/runs/{rid}", headers=admin_headers).json()["log"])


def test_api_run_executed_by_the_worker_is_readable(client, admin_headers):
    r = client.post(f"{P}/runs", json={"full_name": NAME, "stages": ["B", "C", "D", "E"], "queries": ["q1"]}, headers=admin_headers).json()
    with get_session_factory()() as s:
        assert auto.advance_due_runs(s) == 1
    st = client.get(f"{P}/runs/{r['id']}", headers=admin_headers).json()
    assert st["status"] == "completed" and st["tracked_count"] == 1
    assert {c["address"].lower(): c["tracked"] for c in st["candidates"] if c["network"] == "evm"} == {EVM_A.lower(): True}
    inv = client.get(f"/api/v1/leads/sessions/{r['investigation_id']}", headers=admin_headers).json()
    assert {w["address"].lower() for w in inv["wallets"]} == {EVM_A.lower(), EVM_B.lower()}


def test_run_record_is_persisted_in_the_database(session):
    run = _run(session, ["B"])
    with get_session_factory()() as other:
        row = other.get(AutomationRun, run.id)
        assert row.status == "completed" and row.config["stages"] == ["B"] and row.log
