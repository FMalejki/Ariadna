"""HTTP API through TestClient: auth, RBAC, end-to-end flow, evidence access, provider config. Synthetic data only."""

from __future__ import annotations

import pytest

from tests.conftest import EVM_A, make_user

P = "/api/v1"
AUTH = "synthetic test fixture, no real data"


def _create_inv(client, headers, seed="https://x.com/alice", name="inv", **kw):
    r = client.post(f"{P}/investigations", json={"name": name, "seed": seed, **kw}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _import(client, headers, inv_id, records, batch="b1"):
    r = client.post(f"{P}/investigations/{inv_id}/import", json={"batch_id": batch, "authorization_note": AUTH, "records": records}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


REC = {"url": "https://x.com/alice", "bio": f"wallet {EVM_A} site https://example.com/home", "observed_at": "2026-01-01T00:00:00Z"}


# ------------------------------------------------------------------------------------- auth / RBAC
def test_health_is_public_and_openapi_is_served(client):
    assert client.get(f"{P}/health").json() == {"status": "ok"}
    spec = client.get(f"{P}/openapi.json").json()
    assert "/api/v1/investigations" in spec["paths"] and "/api/v1/graph/neighbors/{entity_id}" in spec["paths"]


def test_login_and_me(client):
    assert client.post(f"{P}/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    r = client.post(f"{P}/auth/login", json={"username": "admin", "password": "test-admin-password"})
    token = r.json()["access_token"]
    me = client.get(f"{P}/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["username"] == "admin" and me["role"] == "admin"


@pytest.mark.parametrize("path", ["/entities", "/sources", "/relationships", "/investigations", "/providers", "/stats/overview", "/graph/view", "/wiki/pages"])
def test_endpoints_require_authentication(client, path):
    assert client.get(f"{P}{path}").status_code == 401
    assert client.get(f"{P}{path}", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_rbac_viewer_cannot_write_analyst_cannot_admin(client, admin_headers):
    viewer, analyst = make_user("viewer"), make_user("analyst")
    body = {"name": "x", "seed": "https://x.com/alice"}
    assert client.get(f"{P}/investigations", headers=viewer).status_code == 200
    assert client.post(f"{P}/investigations", json=body, headers=viewer).status_code == 403
    assert client.post(f"{P}/investigations", json=body, headers=analyst).status_code == 200
    assert client.patch(f"{P}/providers/sherlock", json={"enabled": False}, headers=analyst).status_code == 403
    assert client.get(f"{P}/audit", headers=analyst).status_code == 403
    assert client.get(f"{P}/audit", headers=admin_headers).status_code == 200
    # artifact content (raw untrusted bytes) is not available to viewers
    assert client.get(f"{P}/evidence/artifacts/nope/content", headers=viewer).status_code == 403


def test_invalid_seed_is_422(client, admin_headers):
    r = client.post(f"{P}/investigations", json={"name": "x", "seed": "definitely not a seed"}, headers=admin_headers)
    assert r.status_code == 422


# ------------------------------------------------------------------------------------- end-to-end
def test_full_flow_import_graph_evidence_and_review(client, admin_headers):
    created = _create_inv(client, admin_headers)
    inv_id, seed_id = created["investigation"]["id"], created["seed"]["entity_id"]
    assert created["seed"]["canonical_identifier"] == "x:alice"
    # username lookup is not auto-approved -> waits for a human
    sher = next(t for t in created["tasks"] if t["provider"] == "sherlock")
    assert sher["status"] == "pending_approval"

    stats = _import(client, admin_headers, inv_id, [REC])["stats"]
    assert stats["observations_created"] == 1 and stats["relationships_created"] >= 2

    # graph: bounded initial view and progressive neighbours
    view = client.get(f"{P}/graph/view", params={"roots": seed_id, "limit": 10}, headers=admin_headers).json()
    assert any(n["id"] == seed_id for n in view["nodes"]) and len(view["nodes"]) <= 10
    nb = client.get(f"{P}/graph/neighbors/{seed_id}", headers=admin_headers).json()
    labels = {n["canonical_identifier"] for n in nb["nodes"]}
    assert f"addr:evm:{EVM_A}" in labels and "website:example.com/home" in labels
    assert nb["edges"]
    # nodes carry pointers for "open page / open in explorer" links
    wallet = next(n for n in nb["nodes"] if n["canonical_identifier"] == f"addr:evm:{EVM_A}")
    assert wallet["network"] == "evm" and wallet["address"] == EVM_A.lower()
    site = next(n for n in nb["nodes"] if n["canonical_identifier"] == "website:example.com/home")
    assert site["url"] and site["url"].startswith("http")

    # every edge exposes evidence
    edge = next(e for e in nb["edges"] if e.get("relationship_type") == "ACCOUNT_PROFILE_REFERENCES_ADDRESS")
    detail = client.get(f"{P}/relationships/{edge['id']}", headers=admin_headers).json()
    assert detail["status"] in ("proposed", "supported") and detail["evidence"]
    ev = detail["evidence"][0]
    assert ev["evidence_type"] and ev["observation"]["id"] and ev["explanation"]

    # analyst review keeps evidence and records the decision
    rv = client.post(f"{P}/relationships/{edge['id']}/review", json={"decision": "accepted", "note": "checked"}, headers=admin_headers).json()
    assert rv["review_status"] == "accepted" and len(rv["evidence"]) == len(detail["evidence"]) + 1

    # profile + timeline + search
    prof = client.get(f"{P}/entities/{seed_id}", headers=admin_headers).json()
    assert prof["entity"]["canonical_identifier"] == "x:alice"
    assert client.get(f"{P}/entities/{seed_id}/timeline", headers=admin_headers).status_code == 200
    s = client.get(f"{P}/search", params={"q": "alice"}, headers=admin_headers).json()
    assert s["entities"]
    ov = client.get(f"{P}/stats/overview", headers=admin_headers).json()
    assert ov["totals"]["observations"] == 1 and ov["attention"]["tasks_pending_approval"] >= 1


def test_graph_path_between_entities(client, admin_headers):
    created = _create_inv(client, admin_headers)
    _import(client, admin_headers, created["investigation"]["id"], [REC])
    a = client.get(f"{P}/entities/lookup", params={"identifier": "https://x.com/alice"}, headers=admin_headers).json()["entity_id"]
    w = client.get(f"{P}/entities/lookup", params={"identifier": EVM_A}, headers=admin_headers).json()["entity_id"]
    assert a and w
    path = client.get(f"{P}/graph/path", params={"start": a, "end": w}, headers=admin_headers)
    assert path.status_code == 200 and path.json()


def test_lookup_unknown_entity_reports_unknown_without_creating(client, admin_headers):
    r = client.get(f"{P}/entities/lookup", params={"identifier": "https://x.com/nobody"}, headers=admin_headers).json()
    assert r["known"] is False and r["entity_id"] is None
    assert client.get(f"{P}/entities", params={"q": "nobody"}, headers=admin_headers).json() == []


def test_second_investigation_reuses_knowledge_via_api(client, admin_headers):
    first = _create_inv(client, admin_headers, name="one")
    _import(client, admin_headers, first["investigation"]["id"], [REC])
    second = _create_inv(client, admin_headers, name="two")
    assert second["seed"]["entity_id"] == first["seed"]["entity_id"]  # same entity, not a duplicate
    again = _import(client, admin_headers, second["investigation"]["id"], [REC], batch="b2")["stats"]
    assert again["observations_created"] == 0 and again["observations_duplicate"] == 1
    detail = client.get(f"{P}/investigations/{second['investigation']['id']}", headers=admin_headers).json()
    assert "reused" in str(detail)  # reused items are reported as such


def test_decide_task_via_api(client, admin_headers):
    created = _create_inv(client, admin_headers)
    sher = next(t for t in created["tasks"] if t["provider"] == "sherlock")
    r = client.post(f"{P}/discovery/tasks/{sher['id']}/decision", json={"approve": False, "reason": "no"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert client.post(f"{P}/discovery/tasks/{sher['id']}/decision", json={"approve": True}, headers=admin_headers).status_code == 409


def test_match_candidate_decide_and_merge_reverse_via_api(client, admin_headers):
    created = _create_inv(client, admin_headers)
    inv_id = created["investigation"]["id"]
    # two different platforms with the same handle -> candidate only
    _import(client, admin_headers, inv_id, [
        {"url": "https://x.com/alice", "bio": "see https://github.com/alice", "observed_at": "2026-01-01T00:00:00Z"},
    ])
    cands = client.get(f"{P}/match-candidates", headers=admin_headers).json()
    # (candidates only come from tool-detected username matches; plain mentions never create merges)
    assert client.get(f"{P}/merges", headers=admin_headers).json() == []
    for c in cands:
        assert c["status"] == "pending"


def test_artifact_content_is_served_as_attachment(client, admin_headers):
    created = _create_inv(client, admin_headers)
    out = _import(client, admin_headers, created["investigation"]["id"], [REC])
    runs = client.get(f"{P}/evidence/tool-runs", headers=admin_headers).json()
    art_id = next(r["raw_artifact_id"] for r in runs if r["raw_artifact_id"])
    meta = client.get(f"{P}/evidence/artifacts/{art_id}", headers=admin_headers).json()
    assert meta["content_hash"] and meta["purged"] is False
    r = client.get(f"{P}/evidence/artifacts/{art_id}/content", headers=admin_headers)
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment") and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-type"] == "application/octet-stream"
    assert out["tool_run_id"]


def test_import_requires_authorization_note(client, admin_headers):
    created = _create_inv(client, admin_headers)
    r = client.post(f"{P}/investigations/{created['investigation']['id']}/import", json={"records": [REC]}, headers=admin_headers)
    assert r.status_code == 422
    r = client.post(f"{P}/investigations/{created['investigation']['id']}/import", json={"authorization_note": "", "records": [REC]}, headers=admin_headers)
    assert r.status_code == 422


# ------------------------------------------------------------------------------------- providers
def test_placeholder_provider_cannot_be_enabled(client, admin_headers):
    provs = {p["name"]: p for p in client.get(f"{P}/providers", headers=admin_headers).json()}
    for name in ("maigret", "spiderfoot", "maltego"):
        assert provs[name]["implementation_status"] == "placeholder" and provs[name]["enabled"] is False
        assert client.patch(f"{P}/providers/{name}", json={"enabled": True}, headers=admin_headers).status_code == 409
    assert client.patch(f"{P}/providers/sherlock", json={"rate_limit_per_minute": 5}, headers=admin_headers).json()["rate_limit_per_minute"] == 5


def test_provider_health_reports_honestly_when_unconfigured(client, admin_headers):
    h = client.post(f"{P}/providers/evm_rpc/health", headers=admin_headers).json()
    assert h["last_health"]["status"] in ("unavailable", "degraded", "healthy")
    m = client.post(f"{P}/providers/maigret/health", headers=admin_headers).json()
    assert m["last_health"]["status"] == "unavailable"


def test_settings_do_not_leak_secrets(client, admin_headers):
    text = str(client.get(f"{P}/settings", headers=admin_headers).json())
    assert "test-admin-password" not in text and "test-secret-test-secret" not in text


# ------------------------------------------------------------------------------------- governance
def test_delete_source_via_api_requires_admin_and_reason(client, admin_headers):
    created = _create_inv(client, admin_headers)
    _import(client, admin_headers, created["investigation"]["id"], [REC])
    src = next(s for s in client.get(f"{P}/sources", params={"q": "alice"}, headers=admin_headers).json() if s["canonical_key"] == "x:alice")
    analyst = make_user("analyst")
    assert client.request("DELETE", f"{P}/sources/{src['id']}", json={"reason": "takedown"}, headers=analyst).status_code == 403
    assert client.request("DELETE", f"{P}/sources/{src['id']}", json={"reason": "x"}, headers=admin_headers).status_code == 422
    r = client.request("DELETE", f"{P}/sources/{src['id']}", json={"reason": "takedown request"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["observations_removed"] == 1


def test_export_and_notes(client, admin_headers):
    created = _create_inv(client, admin_headers)
    inv_id = created["investigation"]["id"]
    _import(client, admin_headers, inv_id, [REC])
    assert client.put(f"{P}/investigations/{inv_id}/notes", json={"notes": "my private note"}, headers=admin_headers).status_code == 200
    exp = client.get(f"{P}/investigations/{inv_id}/export", headers=admin_headers).json()
    assert exp["analyst_notes"]["notes"] == "my private note"
    assert "my private note" not in str(exp["public_source_data"])
