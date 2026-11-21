"""Mention repeatability: pure curve + word levels, context matching, URL de-duplication, and the guarantee that it
never changes a relationship status and never shows a percentage."""

from __future__ import annotations

import json
import sys

import pytest
from sqlalchemy import select

from app import repeatability as rep
from app.db import get_session_factory
from app.models import Relationship
from tests.conftest import EVM_A
from tests.test_leads import P, _start, make_user  # noqa: F401  (fixtures: mocked autouse comes from test_leads)
from tests.test_leads import mocked  # noqa: F401

NAME_PAGE = "<html><head><title>t</title></head><body><p>Jan Testowy sprzedaje. Wpłata na ETH: {a}</p></body></html>"
OTHER_PAGE = "<html><head><title>t</title></head><body><p>Anna Inna sprzedaje. Wpłata na ETH: {a}</p></body></html>"


# ----------------------------------------------------------------------------------------------- pure curve
@pytest.mark.parametrize("n,points", [(0, 0), (1, 8), (2, 16), (10, 80), (11, 82), (15, 90), (20, 100 - 2), (50, 98)])
def test_curve_is_eight_per_url_then_two_and_capped(n, points):
    assert rep.repeatability_points(n) == points


@pytest.mark.parametrize("n,word", [
    (0, None), (1, "very low"), (2, "very low"), (3, "low"), (4, "low"), (5, "moderate"), (7, "moderate"),
    (8, "high"), (10, "high"), (11, "high"), (12, "high"), (13, "very high"), (19, "very high"),
    (20, "almost certain repeatability"), (100, "almost certain repeatability"),
])
def test_word_levels_match_the_agreed_thresholds(n, word):
    assert rep.repeatability_level(n) == word


def test_levels_never_decrease_with_more_urls():
    order = [None, "very low", "low", "moderate", "high", "very high", rep.ALMOST_CERTAIN]
    ranks = [order.index(rep.repeatability_level(n)) for n in range(0, 40)]
    assert ranks == sorted(ranks)


def test_summary_has_no_numeric_score_and_always_carries_the_disclaimer():
    out = rep.summarize(["https://a.example.org/x", "https://b.example.org/y"], ["https://a.example.org/x"])
    assert out["level"] == "very low" and out["distinct_urls"] == 2
    assert set(out) == {"distinct_urls", "pages_with_address", "level", "disclaimer"}
    assert "not ownership" in out["disclaimer"] and "%" not in json.dumps(out)


# ----------------------------------------------------------------------------------------------- URL de-duplication
def test_same_page_is_counted_once_regardless_of_fragment_tracking_www_scheme_and_slash():
    urls = [
        "https://www.example.org/ad/1", "http://example.org/ad/1/", "https://example.org/ad/1#comments",
        "https://example.org/ad/1?utm_source=x&fbclid=y", "https://example.org/ad/1?b=2&a=1", "https://example.org/ad/1?a=1&b=2",
    ]
    assert len(rep.distinct_pages(urls)) == 2  # /ad/1 and /ad/1?a=1&b=2
    assert rep.distinct_pages([None, "", "https://x.example.org/p"]) == {"x.example.org/p"}


# ----------------------------------------------------------------------------------------------- context matching
@pytest.mark.parametrize("ctx,name,login,expected", [
    ("…Jan Testowy sprzedaje 0xabc…", "Jan Testowy", None, True),
    ("…TESTOWY JAN sprzedaje…", "Jan Testowy", None, True),  # case + word order
    ("…Jan sprzedaje…", "Jan Testowy", None, False),  # a bare first name never matches
    ("…Testowy sprzedaje…", "Jan Testowy", None, False),
    ("…Łukasz Żółć…", "lukasz zolc", None, True),  # diacritics folded
    ("…Jan Testowyński…", "Jan Testowy", None, False),  # whole tokens only
    ("…user kryptofan99 pisze…", None, "kryptofan99", True),
    ("…user @kryptofan99 pisze…", None, "@kryptofan99", True),
    ("…user kryptofan999 pisze…", None, "kryptofan99", False),  # whole token only
    ("…ab…", None, "ab", False),  # too short to be meaningful
    ("", "Jan Testowy", "x1y2z3", False),
])
def test_context_mentions(ctx, name, login, expected):
    assert rep.context_mentions(ctx, name=name, login=login) is expected


# ----------------------------------------------------------------------------------------------- through the API
def _fetch_url(client, h, inv, url):
    r = client.post(f"{P}/fetch", json={"investigation_id": inv, "url": url}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _set_page(monkeypatch, html):
    monkeypatch.setattr(sys.modules["tests.test_leads"], "PAGE", html.format(a=EVM_A))


def _cand(fetch):
    return next(c for c in fetch["page"]["address_candidates"] if c["network"] == "evm")


def test_repeatability_counts_distinct_pages_that_put_the_name_next_to_the_address(client, admin_headers, monkeypatch):
    inv = _start(client, admin_headers, "Jan Testowy")
    _set_page(monkeypatch, NAME_PAGE)
    r1 = _fetch_url(client, admin_headers, inv, "https://ads.example.org/ad/1")
    assert _cand(r1)["repeatability"]["distinct_urls"] == 1 and _cand(r1)["repeatability"]["level"] == "very low"
    r1b = _fetch_url(client, admin_headers, inv, "https://www.ads.example.org/ad/1/?utm_source=x#c")  # the same page
    assert _cand(r1b)["repeatability"]["distinct_urls"] == 1
    for i in (2, 3):
        r = _fetch_url(client, admin_headers, inv, f"https://ads.example.org/ad/{i}")
    rep3 = _cand(r)["repeatability"]
    assert rep3["distinct_urls"] == 3 and rep3["level"] == "low" and rep3["pages_with_address"] == 3
    assert "not ownership" in rep3["disclaimer"]


def test_pages_that_show_the_address_without_the_name_do_not_raise_repeatability(client, admin_headers, monkeypatch):
    inv = _start(client, admin_headers, "Jan Testowy")
    _set_page(monkeypatch, OTHER_PAGE)
    r = _fetch_url(client, admin_headers, inv, "https://ads.example.org/ad/9")
    out = _cand(r)["repeatability"]
    assert out["distinct_urls"] == 0 and out["level"] is None and out["pages_with_address"] == 1


def test_session_state_exposes_it_per_tracked_wallet_and_never_changes_relationship_status(client, admin_headers, monkeypatch):
    inv = _start(client, admin_headers, "Jan Testowy")
    _set_page(monkeypatch, NAME_PAGE)
    f = _fetch_url(client, admin_headers, inv, "https://ads.example.org/ad/1")
    body = {"investigation_id": inv, "fetch_run_id": f["tool_run_id"], "network": "evm", "address": EVM_A, "rationale": "Ad quotes this payment address"}
    t = client.post(f"{P}/track", json=body, headers=admin_headers).json()

    def statuses():
        with get_session_factory()() as s:
            return sorted((r.relationship_type, r.status) for r in s.scalars(select(Relationship)))

    before = statuses()
    for i in range(2, 12):  # 11 pages in total -> "high"
        _fetch_url(client, admin_headers, inv, f"https://ads.example.org/ad/{i}")
    st = client.get(f"{P}/sessions/{inv}", headers=admin_headers).json()
    w = next(x for x in st["wallets"] if x["entity_id"] == t["wallet_entity_id"])
    assert w["repeatability"]["distinct_urls"] == 11 and w["repeatability"]["level"] == "high"
    assert statuses() == before  # repeatability changed nothing about the relationship
    assert "%" not in json.dumps(w["repeatability"]) and "confidence" not in json.dumps(st).lower()


def test_login_in_context_counts_when_the_login_is_the_session_term(client, admin_headers, monkeypatch):
    from app.models import Investigation

    inv = _start(client, admin_headers, "Jan Testowy")
    with get_session_factory()() as s:
        row = s.get(Investigation, inv)
        row.scope = {**row.scope, "login": "kryptofan99"}
        s.commit()
    _set_page(monkeypatch, "<html><body><p>Konto kryptofan99 przyjmuje wpłaty: {a}</p></body></html>")
    out = _cand(_fetch_url(client, admin_headers, inv, "https://ads.example.org/ad/5"))["repeatability"]
    assert out["distinct_urls"] == 1


# ----------------------------------------------------------------------------------------------- text extraction fidelity
def test_html_to_text_keeps_polish_letters_without_a_declared_charset_and_separates_title_from_body():
    from app.providers.webfetch import html_to_text

    raw = "<html><head><title>Ogłoszenie</title></head><body>Łukasz Żółć sprzedaje</body></html>".encode()
    title, text, _ = html_to_text(raw)
    assert title == "Ogłoszenie" and "Łukasz Żółć sprzedaje" in text
    assert "Ogłoszenie\nŁukasz" in text.replace("\n\n", "\n")  # the title does not run into the first word of the body
    latin = "<html><body>Łukasz</body></html>".encode("cp1250")
    assert "Łukasz" in html_to_text(latin, "windows-1250")[1]
