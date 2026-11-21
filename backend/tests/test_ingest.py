"""Ingestion pipeline: idempotency, versioning, dedup, evidence, contradictions, historical status. Synthetic data."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models import Entity, EntityMerge, MatchCandidate, Observation, Relationship, RelationshipEvidence
from app.services import discovery as disc
from tests.conftest import EVM_A

AUTH = "synthetic test fixture, no real data"


def _inv(session, seed="https://x.com/alice", name="t"):
    inv, ref, tasks = disc.start_investigation(session, name=name, seed=seed, actor="tester")
    session.flush()
    return inv


def _import(session, inv, records, batch="b1"):
    out = disc.import_observations(session, inv, {"batch_id": batch, "authorization_note": AUTH, "records": records}, actor="tester")
    session.flush()
    return out


def _count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_import_creates_observation_entities_and_evidenced_relationship(session):
    inv = _inv(session)
    out = _import(session, inv, [{"url": "https://x.com/alice", "bio": f"wallet {EVM_A} site https://example.com/home", "observed_at": "2026-01-01T00:00:00Z"}])
    assert out.error is None
    assert out.stats["observations_created"] == 1
    rels = session.scalars(select(Relationship)).all()
    types = {r.relationship_type for r in rels}
    assert "ACCOUNT_PROFILE_REFERENCES_ADDRESS" in types
    assert "ACCOUNT_LINKS_TO_WEBSITE" in types
    for r in rels:  # every relationship has at least one evidence row
        assert session.scalar(select(func.count()).select_from(RelationshipEvidence).where(RelationshipEvidence.relationship_id == r.id)) >= 1
    ref_rel = next(r for r in rels if r.relationship_type == "ACCOUNT_PROFILE_REFERENCES_ADDRESS")
    # a mention in a bio is a statement by the source: supported at most, never "verified"
    assert ref_rel.status in ("proposed", "supported")


def test_repeated_ingestion_is_idempotent(session):
    inv = _inv(session)
    rec = [{"url": "https://x.com/alice", "bio": f"wallet {EVM_A}", "observed_at": "2026-01-01T00:00:00Z"}]
    first = _import(session, inv, rec)
    counts = (_count(session, Observation), _count(session, Entity), _count(session, Relationship), _count(session, RelationshipEvidence))
    second = _import(session, inv, rec)
    assert first.stats["observations_created"] == 1
    assert second.stats["observations_created"] == 0
    assert second.stats["observations_duplicate"] == 1
    assert counts == (_count(session, Observation), _count(session, Entity), _count(session, Relationship), _count(session, RelationshipEvidence))


def test_entity_dedup_across_url_variants(session):
    inv = _inv(session)
    _import(session, inv, [
        {"url": "https://twitter.com/Alice", "bio": "hi", "observed_at": "2026-01-01T00:00:00Z"},
        {"url": "https://x.com/alice/", "bio": "hi", "observed_at": "2026-01-01T00:00:00Z"},
    ])
    assert session.scalar(select(func.count()).select_from(Entity).where(Entity.canonical_identifier == "x:alice")) == 1


def test_changed_payload_appends_new_version_never_overwrites(session):
    inv = _inv(session)
    _import(session, inv, [{"url": "https://x.com/alice", "bio": "v1", "observed_at": "2026-01-01T00:00:00Z"}], "b1")
    _import(session, inv, [{"url": "https://x.com/alice", "bio": "v2", "observed_at": "2026-02-01T00:00:00Z"}], "b2")
    rows = session.scalars(select(Observation).where(Observation.observation_type == "social_profile").order_by(Observation.seq)).all()
    assert [r.seq for r in rows] == [1, 2]
    assert rows[0].normalized_payload["bio"] == "v1"  # original preserved


def test_relationship_becomes_historical_when_newer_snapshot_drops_it(session):
    inv = _inv(session)
    _import(session, inv, [{"url": "https://x.com/alice", "bio": f"wallet {EVM_A}", "observed_at": "2026-01-01T00:00:00Z"}], "b1")
    rel = session.scalar(select(Relationship).where(Relationship.relationship_type == "ACCOUNT_PROFILE_REFERENCES_ADDRESS"))
    assert rel.status != "historical"
    _import(session, inv, [{"url": "https://x.com/alice", "bio": "wallet removed", "observed_at": "2026-03-01T00:00:00Z"}], "b2")
    session.refresh(rel)
    assert rel.status == "historical"
    assert rel.valid_to is not None
    assert session.scalar(select(func.count()).select_from(RelationshipEvidence).where(RelationshipEvidence.relationship_id == rel.id)) >= 1  # evidence kept


def test_conflicting_sources_for_exclusive_relationship_are_contradicted(session):
    """The same post attributed to two different accounts by two sources: both claims flagged, neither silently wins."""
    inv = _inv(session)
    # Post ids are platform-scoped (x:post:555), so these two URLs name the SAME post under two different authors.
    _import(session, inv, [{"url": "https://x.com/alice/status/555", "text": "hello", "observed_at": "2026-01-01T00:00:00Z"}], "b1")
    _import(session, inv, [{"url": "https://x.com/mallory/status/555", "text": "hello", "observed_at": "2026-01-02T00:00:00Z"}], "b2")
    contradicted = lambda: session.scalars(  # noqa: E731
        select(Relationship).where(Relationship.relationship_type == "ACCOUNT_PUBLISHED_POST", Relationship.status == "contradicted")
    ).all()
    assert len(contradicted()) == 2
    # Same rule through the ingest layer with a second, distinct post.
    from datetime import UTC, datetime

    from app.normalize import classify_url
    from app.providers.base import Link, NormalizedObservation, SourceRef
    from app.providers.registry import sync_providers
    from app.services import ingest as ingest_svc

    prov = next(p for p in sync_providers(session) if p.name == "manual_import")
    post = classify_url("https://x.com/alice/status/777")
    alice, mallory = classify_url("https://x.com/alice"), classify_url("https://x.com/mallory")

    def obs(src_key, author):
        return NormalizedObservation(
            source=SourceRef("web", src_key, None, src_key, src_key), subject=post, observation_type="web_page",
            observed_at=datetime(2026, 1, 1, tzinfo=UTC), payload={"claims_author": author.canonical_id},
            links=[Link(author, "ACCOUNT_PUBLISHED_POST", direction="in", explanation=f"{src_key} says {author.display_name} wrote it", basis="test")],
        )

    ingest_svc.ingest(session, [obs("mirror-one", alice)], provider=prov, tool_run_id=None, raw_artifact_id=None, evidence_default="direct_source_statement")
    res = ingest_svc.ingest(session, [obs("mirror-two", mallory)], provider=prov, tool_run_id=None, raw_artifact_id=None, evidence_default="direct_source_statement")
    assert res.stats.contradictions >= 1
    assert len(contradicted()) == 4  # 2 from the import stage + 2 for post 777


def test_similar_usernames_are_never_merged(session):
    inv = _inv(session)
    _import(session, inv, [
        {"url": "https://x.com/alice", "bio": "a", "observed_at": "2026-01-01T00:00:00Z"},
        {"url": "https://x.com/alice_", "bio": "b", "observed_at": "2026-01-01T00:00:00Z"},
        {"url": "https://t.me/alicechannel", "bio": "c", "observed_at": "2026-01-01T00:00:00Z"},
    ])
    assert _count(session, EntityMerge) == 0
    assert session.scalar(select(func.count()).select_from(Entity).where(Entity.merged_into_id.is_not(None))) == 0


def test_import_requires_authorization_note(session):
    inv = _inv(session)
    out = disc.import_observations(session, inv, {"records": [{"url": "https://x.com/alice"}]}, actor="tester")
    assert out.error and "authorization_note" in out.error
    assert _count(session, Observation) == 0


def test_unclassifiable_import_url_is_skipped_not_stored(session):
    inv = _inv(session)
    out = _import(session, inv, [{"url": "https://t.me/+PrivateInvite", "bio": "x", "observed_at": "2026-01-01T00:00:00Z"}])
    assert out.stats["observations_created"] == 0
