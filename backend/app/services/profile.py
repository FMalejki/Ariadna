"""Entity intelligence profile, timeline, point-in-time knowledge, evidence detail (spec 5.3, 6, 9).

Everything returned links back to observation ids so the UI can always navigate to the original evidence.
Statements are separated by kind: raw observation, claim, deterministically verified event, relationship.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import utcnow
from app.domain import EntityStatus, ObservationType, SupportStatus, TaskStatus
from app.models import (
    Artifact,
    Claim,
    DiscoveryTask,
    Entity,
    EntityAlias,
    EntityMerge,
    MatchCandidate,
    Observation,
    ObservationEntity,
    Provider,
    Relationship,
    RelationshipEvidence,
    Source,
    ToolRun,
)
from app.services import entities as ent_svc
from app.services import relationships as rel_svc


def obs_summary(o: Observation, source: Source | None = None) -> dict:
    return {
        "id": o.id, "source_id": o.source_id, "source": (source.display_name or source.canonical_key) if source else None,
        "source_url": source.canonical_url if source else None, "observation_type": o.observation_type, "seq": o.seq,
        "observed_at": o.observed_at.isoformat(), "published_at": o.published_at.isoformat() if o.published_at else None,
        "retrieved_at": o.retrieved_at.isoformat(), "evidence_type": o.evidence_type, "parser_version": o.parser_version,
        "provider_id": o.provider_id, "tool_run_id": o.tool_run_id, "raw_artifact_id": o.raw_artifact_id,
        "payload": o.normalized_payload,
    }


def _stale(src: Source) -> bool:
    ref = src.last_observed_at or src.first_discovered_at
    return (utcnow() - ref).days > get_settings().stale_after_days


def entity_dict(e: Entity) -> dict:
    return {
        "id": e.id, "entity_type": e.entity_type, "canonical_identifier": e.canonical_identifier, "display_name": e.display_name,
        "description": e.description, "status": e.status, "resolution_status": e.resolution_status, "metadata": e.meta,
        "merged_into_id": e.merged_into_id,
        "first_observed_at": e.first_observed_at.isoformat() if e.first_observed_at else None,
        "last_observed_at": e.last_observed_at.isoformat() if e.last_observed_at else None,
        "created_at": e.created_at.isoformat(), "updated_at": e.updated_at.isoformat(),
    }


def relationship_detail(session: Session, rel: Relationship) -> dict:
    """The answer to 'why does the system believe these are related?' (spec 6)."""
    rows = session.execute(
        select(RelationshipEvidence, Observation, Source, Provider)
        .join(Observation, Observation.id == RelationshipEvidence.observation_id)
        .join(Source, Source.id == Observation.source_id)
        .outerjoin(Provider, Provider.id == Observation.provider_id)
        .where(RelationshipEvidence.relationship_id == rel.id)
        .order_by(Observation.observed_at)
    ).all()
    evidence = []
    for ev, obs, src, prov in rows:
        superseded = rel_svc.superseded_at(session, obs)
        evidence.append(
            {
                "id": ev.id, "evidence_type": ev.evidence_type, "evidence_strength": rel_svc.EVIDENCE_STRENGTH.get(ev.evidence_type),
                "support_status": ev.support_status, "explanation": ev.explanation, "confidence_basis": ev.confidence_basis,
                "created_at": ev.created_at.isoformat(), "stale": superseded is not None, "superseded_at": superseded.isoformat() if superseded else None,
                "observation": obs_summary(obs, src), "provider": {"name": prov.name, "version": prov.version} if prov else None,
                "source_reliability": src.reliability, "source_collection_status": src.collection_status,
            }
        )
    s, t = session.get(Entity, rel.source_entity_id), session.get(Entity, rel.target_entity_id)
    return {
        "id": rel.id, "relationship_type": rel.relationship_type, "status": rel.status, "review_status": rel.review_status,
        "reviewed_by": rel.reviewed_by, "reviewed_at": rel.reviewed_at.isoformat() if rel.reviewed_at else None,
        "source_entity": entity_dict(s) if s else None, "target_entity": entity_dict(t) if t else None,
        "first_observed_at": rel.first_observed_at.isoformat() if rel.first_observed_at else None,
        "last_observed_at": rel.last_observed_at.isoformat() if rel.last_observed_at else None,
        "valid_from": rel.valid_from.isoformat() if rel.valid_from else None, "valid_to": rel.valid_to.isoformat() if rel.valid_to else None,
        "resolution_method": rel.resolution_method,
        "manually_reviewed": rel.review_status != "unreviewed",
        "has_contradiction": any(e["support_status"] == SupportStatus.CONTRADICTS.value for e in evidence),
        "is_stale": bool(evidence) and all(e["stale"] for e in evidence if e["support_status"] == "supports"),
        "evidence": evidence,
        "originating_observation_id": evidence[0]["observation"]["id"] if evidence else None,
    }


def timeline(session: Session, entity_id: str, as_of: datetime | None = None, limit: int = 200) -> list[dict]:
    cluster = ent_svc.cluster_ids(session, entity_id)
    events: list[dict] = []
    obs_rows = session.execute(
        select(Observation, Source, ObservationEntity.role)
        .join(ObservationEntity, ObservationEntity.observation_id == Observation.id)
        .join(Source, Source.id == Observation.source_id)
        .where(ObservationEntity.entity_id.in_(cluster))
        .order_by(Observation.observed_at.desc())
        .limit(limit)
    ).all()
    for o, s, role in obs_rows:
        if as_of and o.retrieved_at > as_of:
            continue
        verified = o.observation_type == ObservationType.BLOCKCHAIN_TRANSACTION.value
        events.append(
            {
                "kind": "observation", "at": o.observed_at.isoformat(), "observation_id": o.id, "role": role, "seq": o.seq,
                "title": f"{o.observation_type} via {s.display_name or s.canonical_key}",
                "observation_type": o.observation_type, "evidence_type": o.evidence_type, "verified_onchain": verified,
                "published_at": o.published_at.isoformat() if o.published_at else None, "retrieved_at": o.retrieved_at.isoformat(),
            }
        )
    rels = session.scalars(
        select(Relationship).where((Relationship.source_entity_id.in_(cluster)) | (Relationship.target_entity_id.in_(cluster)))
    ).all()
    for r in rels:
        if r.first_observed_at and not (as_of and r.created_at > as_of):
            events.append({"kind": "relationship_first_observed", "at": r.first_observed_at.isoformat(), "relationship_id": r.id,
                           "title": f"{r.relationship_type} first observed", "status": r.status})
        if r.valid_to:
            events.append({"kind": "relationship_ended", "at": r.valid_to.isoformat(), "relationship_id": r.id,
                           "title": f"{r.relationship_type} no longer shown by its source", "status": r.status})
    for m in session.scalars(select(EntityMerge).where(EntityMerge.winner_id.in_(cluster) | EntityMerge.loser_id.in_(cluster))):
        events.append({"kind": "merge", "at": m.merged_at.isoformat(), "merge_id": m.id, "title": f"entity merge ({m.method})", "reversed": m.reversed_at is not None})
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]


def profile(session: Session, entity_id: str, as_of: datetime | None = None) -> dict | None:
    ent = session.get(Entity, entity_id)
    if ent is None or ent.status == EntityStatus.DELETED.value:
        return None
    if ent.merged_into_id:
        ent = session.get(Entity, ent_svc.resolve_id(session, entity_id))  # type: ignore[assignment]
        assert ent is not None
    cluster = ent_svc.cluster_ids(session, ent.id)

    obs_q = (
        select(Observation, Source)
        .join(ObservationEntity, ObservationEntity.observation_id == Observation.id)
        .join(Source, Source.id == Observation.source_id)
        .where(ObservationEntity.entity_id.in_(cluster))
        .order_by(Observation.observed_at.desc())
    )
    if as_of:
        obs_q = obs_q.where(Observation.retrieved_at <= as_of)
    seen: set[str] = set()
    observations = []
    for o, s in session.execute(obs_q.limit(300)).all():
        if o.id not in seen:
            seen.add(o.id)
            observations.append(obs_summary(o, s))

    rels_q = select(Relationship).where((Relationship.source_entity_id.in_(cluster)) | (Relationship.target_entity_id.in_(cluster)))
    if as_of:
        rels_q = rels_q.where(Relationship.created_at <= as_of)
    rel_rows = list(session.scalars(rels_q.order_by(Relationship.last_observed_at.desc().nullslast()).limit(300)))
    other_ids = {r.target_entity_id if r.source_entity_id in cluster else r.source_entity_id for r in rel_rows}
    others = {e.id: e for e in session.scalars(select(Entity).where(Entity.id.in_(other_ids)))} if other_ids else {}
    relationships = []
    for r in rel_rows:
        outgoing = r.source_entity_id in cluster
        other = others.get(r.target_entity_id if outgoing else r.source_entity_id)
        if other is None:
            continue
        relationships.append(
            {
                "id": r.id, "relationship_type": r.relationship_type, "direction": "out" if outgoing else "in", "status": r.status,
                "review_status": r.review_status, "other": {"id": ent_svc.resolve_id(session, other.id), "label": other.display_name, "entity_type": other.entity_type, "canonical_identifier": other.canonical_identifier},
                "first_observed_at": r.first_observed_at.isoformat() if r.first_observed_at else None,
                "last_observed_at": r.last_observed_at.isoformat() if r.last_observed_at else None,
            }
        )

    sources = session.execute(
        select(Source, func.count(Observation.id))
        .join(Observation, Observation.source_id == Source.id)
        .join(ObservationEntity, ObservationEntity.observation_id == Observation.id)
        .where(ObservationEntity.entity_id.in_(cluster))
        .group_by(Source.id)
    ).all()
    claims = session.scalars(
        select(Claim).where(Claim.observation_id.in_([o["id"] for o in observations] or [""]), Claim.superseded_by.is_(None)).limit(200)
    ).all()
    verified_events = [o for o in observations if o["observation_type"] == ObservationType.BLOCKCHAIN_TRANSACTION.value]
    failed_lookups = [o for o in observations if o["observation_type"] == ObservationType.BLOCKCHAIN_LOOKUP_FAILED.value]
    pending_tasks = session.scalars(
        select(DiscoveryTask).where(
            DiscoveryTask.target_entity_id.in_(cluster),
            DiscoveryTask.status.in_([TaskStatus.PENDING_APPROVAL.value, TaskStatus.QUEUED.value, TaskStatus.RUNNING.value, TaskStatus.FAILED.value]),
        )
    ).all()
    aliases = session.scalars(select(EntityAlias).where(EntityAlias.entity_id.in_(cluster))).all()
    candidates = session.scalars(
        select(MatchCandidate).where(
            (MatchCandidate.entity_a_id.in_(cluster)) | (MatchCandidate.entity_b_id.in_(cluster)), MatchCandidate.status == "pending"
        )
    ).all()
    merges = session.scalars(select(EntityMerge).where(EntityMerge.winner_id.in_(cluster), EntityMerge.reversed_at.is_(None))).all()
    contradicted = [r for r in relationships if r["status"] == "contradicted"]
    return {
        "entity": entity_dict(ent),
        "as_of": as_of.isoformat() if as_of else None,
        "known_identifiers": [ent.canonical_identifier] + [a.alias for a in aliases],
        "aliases": [{"alias": a.alias, "type": a.alias_type, "basis": a.basis, "confirmed_by": a.confirmed_by} for a in aliases],
        "merged_entities": [{"merge_id": m.id, "loser_id": m.loser_id, "reason": m.reason, "method": m.method, "merged_by": m.merged_by} for m in merges],
        "match_candidates": [{"id": c.id, "other_entity_id": c.entity_b_id if c.entity_a_id in cluster else c.entity_a_id, "rule": c.rule, "explanation": c.explanation} for c in candidates],
        "sources": [{"id": s.id, "label": s.display_name or s.canonical_key, "source_type": s.source_type, "canonical_url": s.canonical_url,
                     "collection_status": s.collection_status, "stale": _stale(s), "observation_count": n,
                     "last_observed_at": s.last_observed_at.isoformat() if s.last_observed_at else None} for s, n in sources],
        "observations": observations,
        "relationships": relationships,
        "claims": [{"id": c.id, "claim_type": c.claim_type, "predicate": c.predicate, "subject_reference": c.subject_reference, "object_reference": c.object_reference,
                    "supporting_text": c.supporting_text, "observation_id": c.observation_id, "model": f"{c.model_provider}/{c.model_name}", "prompt_version": c.prompt_version,
                    "review_status": c.review_status, "verification_status": c.verification_status} for c in claims],
        "verified_onchain_events": verified_events,
        "failed_verifications": failed_lookups,
        "contradictions": contradicted,
        "pending_discovery_tasks": [{"id": t.id, "provider": t.provider_name, "status": t.status, "target": t.target_identifier, "depth": t.depth} for t in pending_tasks],
        "timeline": timeline(session, ent.id, as_of, 100),
        "last_update": max([o["retrieved_at"] for o in observations], default=None),
        "evidence_notice": "Observations are what a source stated or a provider returned; only entries under verified_onchain_events were checked deterministically against a blockchain provider.",
    }


def what_changed_since(session: Session, since: datetime) -> dict:
    """New knowledge since a point in time (acceptance: 'what was added after the previous investigation')."""
    n_obs = session.scalar(select(func.count()).select_from(Observation).where(Observation.created_at > since)) or 0
    n_ent = session.scalar(select(func.count()).select_from(Entity).where(Entity.created_at > since)) or 0
    n_rel = session.scalar(select(func.count()).select_from(Relationship).where(Relationship.created_at > since)) or 0
    return {"since": since.isoformat(), "new_observations": n_obs, "new_entities": n_ent, "new_relationships": n_rel}


def stale_sources(session: Session, limit: int = 100) -> list[dict]:
    cutoff = utcnow().replace(microsecond=0)
    days = get_settings().stale_after_days
    out = []
    for s in session.scalars(select(Source).order_by(Source.last_observed_at.asc().nullsfirst()).limit(limit * 3)):
        age = (cutoff - (s.last_observed_at or s.first_discovered_at)).days
        if s.collection_status in ("unavailable", "restricted", "stale") or age > days:
            out.append({"id": s.id, "label": s.display_name or s.canonical_key, "collection_status": s.collection_status, "age_days": age})
        if len(out) >= limit:
            break
    return out


__all__ = ["profile", "timeline", "relationship_detail", "entity_dict", "obs_summary", "what_changed_since", "stale_sources", "Artifact", "ToolRun"]
