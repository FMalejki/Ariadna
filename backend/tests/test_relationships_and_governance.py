"""Relationship status policy, analyst review, governance (delete/export). Synthetic data only."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app import artifacts
from app.models import Artifact, AuditLog, Claim, Entity, Observation, Relationship, RelationshipEvidence, Source
from app.services import discovery as disc
from app.services import governance as gov
from app.services import relationships as rel_svc
from tests.conftest import EVM_A

AUTH = "synthetic test fixture, no real data"
D = "direct_source_statement"


def _inv(session, seed="https://x.com/alice"):
    inv, _, _ = disc.start_investigation(session, name="t", seed=seed, actor="tester")
    session.flush()
    return inv


def _imp(session, inv, records, batch="b"):
    out = disc.import_observations(session, inv, {"batch_id": batch, "authorization_note": AUTH, "records": records}, actor="tester")
    session.flush()
    assert out.error is None, out.error
    return out


def _rel(session, rtype):
    return session.scalar(select(Relationship).where(Relationship.relationship_type == rtype))


def _evidence_types(session, rel):
    return {e.evidence_type for e in session.scalars(select(RelationshipEvidence).where(RelationshipEvidence.relationship_id == rel.id))}


def test_single_source_statement_is_supported_not_verified(session):
    _imp(session, _inv(session), [{"url": "https://x.com/alice", "bio": f"wallet {EVM_A}", "observed_at": "2026-01-01T00:00:00Z"}])
    rel = _rel(session, "ACCOUNT_PROFILE_REFERENCES_ADDRESS")
    assert rel.status in ("proposed", "supported") and rel.status != "verified"


def test_two_independent_sources_corroborate(session):
    inv = _inv(session)
    _imp(session, inv, [
        {"url": "https://x.com/alice", "bio": f"wallet {EVM_A}", "observed_at": "2026-01-01T00:00:00Z"},
        {"url": "https://x.com/alice2", "bio": f"wallet {EVM_A}", "observed_at": "2026-01-01T00:00:00Z"},
    ])
    # different accounts -> different relationships; same account cited by a second source needs a second source row
    rels = session.scalars(select(Relationship).where(Relationship.relationship_type == "ACCOUNT_PROFILE_REFERENCES_ADDRESS")).all()
    assert len(rels) == 2
    # no similarity-based merge happened
    assert session.scalar(select(Entity).where(Entity.canonical_identifier == "x:alice")) is not None
    assert session.scalar(select(Entity).where(Entity.canonical_identifier == "x:alice2")) is not None


def test_compute_status_matrix(session):
    inv = _inv(session)
    _imp(session, inv, [{"url": "https://x.com/alice", "bio": f"wallet {EVM_A}", "observed_at": "2026-01-01T00:00:00Z"}])
    rel = _rel(session, "ACCOUNT_PROFILE_REFERENCES_ADDRESS")
    obs = session.scalars(select(Observation)).first()

    def clear():
        for e in session.scalars(select(RelationshipEvidence).where(RelationshipEvidence.relationship_id == rel.id)):
            session.delete(e)
        session.flush()

    clear()
    assert rel_svc.compute_status(session, rel)[0] == "proposed"  # no supporting evidence

    rel_svc.add_evidence(session, rel, obs, evidence_type="tool_generated_discovery", explanation="t", basis="t")
    assert rel_svc.compute_status(session, rel)[0] == "proposed"  # tool discovery alone is never enough

    rel_svc.add_evidence(session, rel, obs, evidence_type="llm_extracted_claim", explanation="t", basis="t")
    assert rel_svc.compute_status(session, rel)[0] == "proposed"  # LLM output alone is never enough

    rel_svc.add_evidence(session, rel, obs, evidence_type=D, explanation="t", basis="t")
    assert rel_svc.compute_status(session, rel)[0] == "supported"

    rel_svc.add_evidence(session, rel, obs, evidence_type="deterministic_blockchain_verification", explanation="t", basis="t")
    assert rel_svc.compute_status(session, rel)[0] == "verified"

    rel_svc.add_evidence(session, rel, obs, evidence_type="contradictory_evidence", explanation="t", basis="t", support="contradicts")
    assert rel_svc.compute_status(session, rel)[0] == "contradicted"  # contradiction dominates; evidence is kept

    rel.review_status = "rejected"
    assert rel_svc.compute_status(session, rel)[0] == "rejected"


def test_corroboration_requires_two_distinct_sources(session):
    inv = _inv(session)
    _imp(session, inv, [{"url": "https://x.com/alice", "bio": f"wallet {EVM_A}", "observed_at": "2026-01-01T00:00:00Z"}])
    rel = _rel(session, "ACCOUNT_PROFILE_REFERENCES_ADDRESS")
    obs1 = session.scalars(select(Observation)).first()
    other_src = Source(source_type="document", canonical_key="doc:independent", display_name="independent doc")
    session.add(other_src)
    session.flush()
    obs2 = Observation(
        source_id=other_src.id, observation_type="import_record", observed_at=datetime(2026, 1, 2, tzinfo=UTC), normalized_payload={"x": 1},
        content_hash="c" * 64, payload_hash="d" * 64, parser_version="t", evidence_type=D,
    )
    session.add(obs2)
    session.flush()
    for e in session.scalars(select(RelationshipEvidence).where(RelationshipEvidence.relationship_id == rel.id)):
        session.delete(e)
    session.flush()
    rel_svc.add_evidence(session, rel, obs1, evidence_type=D, explanation="t", basis="t")
    rel_svc.add_evidence(session, rel, obs1, evidence_type="independent_corroboration", explanation="same source again", basis="t")
    assert rel_svc.compute_status(session, rel)[0] == "supported"  # one source twice is not corroboration
    rel_svc.add_evidence(session, rel, obs2, evidence_type=D, explanation="t", basis="t")
    assert rel_svc.compute_status(session, rel)[0] == "corroborated"


def test_review_accept_adds_analyst_evidence_and_reject_keeps_evidence(session):
    inv = _inv(session)
    _imp(session, inv, [{"url": "https://x.com/alice", "bio": f"wallet {EVM_A}", "observed_at": "2026-01-01T00:00:00Z"}])
    rel = _rel(session, "ACCOUNT_PROFILE_REFERENCES_ADDRESS")
    n_before = len(_evidence_types(session, rel))
    rel_svc.review_relationship(session, rel, "accepted", "analyst", "checked manually")
    assert "analyst_confirmed" in _evidence_types(session, rel) and rel.status == "supported"
    rel_svc.review_relationship(session, rel, "rejected", "analyst")
    assert rel.status == "rejected" and "analyst_confirmed" in _evidence_types(session, rel)  # nothing deleted
    assert len(_evidence_types(session, rel)) >= n_before
    with pytest.raises(ValueError):
        rel_svc.review_relationship(session, rel, "maybe", "analyst")


def test_changed_profile_makes_removed_link_historical_but_keeps_it(session):
    inv = _inv(session)
    _imp(session, inv, [{"url": "https://x.com/alice", "bio": f"wallet {EVM_A}", "observed_at": "2026-01-01T00:00:00Z"}], batch="b1")
    _imp(session, inv, [{"url": "https://x.com/alice", "bio": "no wallet any more", "observed_at": "2026-02-01T00:00:00Z"}], batch="b2")
    rel = _rel(session, "ACCOUNT_PROFILE_REFERENCES_ADDRESS")
    assert rel.status == "historical" and rel.valid_to is not None
    assert session.scalars(select(Observation)).all().__len__() == 2


# ------------------------------------------------------------------------------------- governance


def test_delete_source_removes_observations_redacts_relationships_and_purges_artifact(session):
    inv = _inv(session)
    _imp(session, inv, [{"url": "https://x.com/alice", "bio": f"wallet {EVM_A}", "observed_at": "2026-01-01T00:00:00Z"}])
    src = session.scalar(select(Source).where(Source.canonical_key == "x:alice"))
    obs = session.scalars(select(Observation).where(Observation.source_id == src.id)).all()
    art = session.get(Artifact, obs[0].raw_artifact_id)
    loc = art.storage_location
    assert artifacts.get_store().get(loc)
    with pytest.raises(ValueError):
        gov.delete_source(session, src.id, "admin", "  ")
    res = gov.delete_source(session, src.id, "admin", "takedown request")
    session.flush()
    assert res["observations_removed"] == len(obs) and res["artifacts_purged"] == 1
    assert session.scalars(select(Observation).where(Observation.source_id == src.id)).all() == []
    assert session.get(Artifact, art.id).purged_at is not None
    with pytest.raises(Exception):
        artifacts.get_store().get(loc)
    rel = _rel(session, "ACCOUNT_PROFILE_REFERENCES_ADDRESS")
    assert rel.status == "redacted"
    assert session.scalar(select(AuditLog).where(AuditLog.action == "source.delete")) is not None


def test_delete_source_keeps_relationships_with_remaining_evidence(session):
    inv = _inv(session)
    _imp(session, inv, [
        {"url": "https://x.com/alice", "bio": "see https://example.com/home", "observed_at": "2026-01-01T00:00:00Z"},
        {"url": "https://x.com/bob", "bio": "see https://example.com/home", "observed_at": "2026-01-01T00:00:00Z"},
    ])
    alice = session.scalar(select(Source).where(Source.canonical_key == "x:alice"))
    gov.delete_source(session, alice.id, "admin", "test")
    session.flush()
    alive = session.scalars(select(Relationship).where(Relationship.relationship_type == "ACCOUNT_LINKS_TO_WEBSITE", Relationship.status != "redacted")).all()
    assert len(alive) == 1  # bob's link survives


def test_delete_entity_rejects_its_relationships_and_is_audited(session):
    inv = _inv(session)
    _imp(session, inv, [{"url": "https://x.com/alice", "bio": f"wallet {EVM_A}", "observed_at": "2026-01-01T00:00:00Z"}])
    ent = session.scalar(select(Entity).where(Entity.canonical_identifier == "x:alice"))
    gov.delete_entity(session, ent.id, "admin", "requested removal")
    assert ent.status == "deleted"
    assert all(r.status == "rejected" for r in session.scalars(select(Relationship).where(Relationship.source_entity_id == ent.id)))
    with pytest.raises(ValueError):
        gov.delete_entity(session, ent.id, "admin", "")


def test_export_investigation_separates_notes_from_public_data(session):
    inv = _inv(session)
    inv.notes = "analyst private hypothesis"
    _imp(session, inv, [{"url": "https://x.com/alice", "bio": f"wallet {EVM_A}", "observed_at": "2026-01-01T00:00:00Z"}])
    out = gov.export_investigation(session, inv.id, "admin")
    assert out["analyst_notes"]["notes"] == "analyst private hypothesis"
    assert "analyst private hypothesis" not in str(out["public_source_data"])
    assert out["public_source_data"]["observations"] and out["public_source_data"]["relationships"]
