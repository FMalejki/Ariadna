"""Discovery start with a full name and/or a platform-less login (lead-automation-plan sub-task 5). Synthetic data only."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models import Entity, Investigation
from app.normalize import InvalidReference, classify_username
from app.services import discovery as disc
from app.services import leads
from tests.conftest import EVM_A, SOL_ADDR

P = "/api/v1"


def _post(client, headers, **body):
    body.setdefault("name", "inv")
    return client.post(f"{P}/investigations", json=body, headers=headers)


def _entities(session) -> int:
    return session.scalar(select(func.count()).select_from(Entity))


# --------------------------------------------------------------------------- classify_username


def test_login_is_platform_less_and_lowercased():
    ref = classify_username("Jan_Kowalski.99")
    assert ref.canonical_id == "username:jan_kowalski.99"
    assert ref.platform is None and ref.url is None
    assert ref.attrs == {"handle": "jan_kowalski.99"}
    assert ref.entity_type == "social_account"


def test_leading_at_sign_is_tolerated():
    assert classify_username("@alice_x").canonical_id == "username:alice_x"


@pytest.mark.parametrize("bad", ["ab", "a" * 33, "bad login", "-flag", ".hidden", "ala/ma", "zażółć", "a@b.pl"])
def test_invalid_logins_are_rejected(bad):
    with pytest.raises(InvalidReference):
        classify_username(bad)


# --------------------------------------------------------------------------- API


def test_login_creates_platform_less_entity_and_pending_sherlock_task(client, admin_headers):
    r = _post(client, admin_headers, login="Alice_Test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["seed"]["canonical_identifier"] == "username:alice_test"
    assert body["investigation"]["scope"]["login"] == "alice_test"
    sherlock = [t for t in body["tasks"] if t["provider"] == "sherlock"]
    assert sherlock, body["tasks"]
    # username_lookup has no allow policy by default -> never runs without approval
    assert sherlock[0]["status"] == "pending_approval"
    assert body["executed"] == []


def test_name_only_creates_no_entity(client, admin_headers, session):
    before = _entities(session)
    r = _post(client, admin_headers, full_name="Jan Testowy")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["seed"] is None and body["tasks"] == []
    assert body["investigation"]["scope"]["subject"] == "Jan Testowy"
    assert "login" not in body["investigation"]["scope"]
    session.expire_all()
    assert _entities(session) == before


def test_name_and_login_store_both_terms(client, admin_headers, session):
    r = _post(client, admin_headers, full_name="Jan Testowy", login="jan_t")
    assert r.status_code == 200, r.text
    inv = session.get(Investigation, r.json()["investigation"]["id"])
    assert leads._terms(inv) == ("Jan Testowy", "jan_t")  # picked up by mention_repeatability


def test_seed_and_login_both_get_entities(client, admin_headers):
    r = _post(client, admin_headers, seed="https://x.com/alice", login="alice_test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["seed"]["canonical_identifier"] == "x:alice"  # the seed stays the primary reference
    assert any(t["target"] == "username:alice_test" for t in body["tasks"])


def test_bare_word_as_seed_is_422_even_with_a_valid_login(client, admin_headers):
    # a bare word is not a deterministic seed; the login field is the supported way to give a platform-less handle
    r = _post(client, admin_headers, seed="alice_test", login="alice_test")
    assert r.status_code == 422


@pytest.mark.parametrize("bad", ["ab", "-x-", "has space", "x" * 40])
def test_invalid_login_is_422_and_writes_nothing(client, admin_headers, session, bad):
    before = session.scalar(select(func.count()).select_from(Investigation))
    r = _post(client, admin_headers, login=bad)
    assert r.status_code == 422
    assert "login" in r.json()["detail"].lower()
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Investigation)) == before


def test_nothing_provided_is_422(client, admin_headers):
    assert _post(client, admin_headers).status_code == 422
    assert _post(client, admin_headers, seed="  ", login=" ", full_name="").status_code == 422


def test_blank_values_are_ignored_when_another_is_given(client, admin_headers):
    r = _post(client, admin_headers, seed="", login="alice_test", full_name="  ")
    assert r.status_code == 200, r.text
    assert "subject" not in r.json()["investigation"]["scope"]


# --------------------------------------------------------------------------- backward compatibility


@pytest.mark.parametrize(
    "seed,hint,canonical_prefix",
    [
        ("https://x.com/alice", None, "x:alice"),
        (EVM_A, None, "addr:evm:"),
        (SOL_ADDR, None, "addr:solana:"),
        ("@alice", "x", "x:alice"),
    ],
)
def test_old_seed_inputs_still_work(client, admin_headers, seed, hint, canonical_prefix):
    kw = {"platform_hint": hint} if hint else {}
    r = _post(client, admin_headers, seed=seed, **kw)
    assert r.status_code == 200, r.text
    assert r.json()["seed"]["canonical_identifier"].startswith(canonical_prefix)


def test_bare_handle_without_hint_is_still_rejected_as_seed(client, admin_headers):
    assert _post(client, admin_headers, seed="@alice").status_code == 422


def test_service_requires_some_input(session):
    with pytest.raises(InvalidReference):
        disc.start_investigation(session, name="n", actor="t")
