"""Governance workflows (spec 16): source deletion / correction, data export, retention.

Deletion REMOVES the stored observation payloads and raw artifacts of a source (privacy/takedown) but keeps a
tombstone + audit entry. Relationships that lose all supporting evidence become REDACTED rather than silently
disappearing, so the graph never contains edges without evidence.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.artifacts import get_store
from app.audit import audit
from app.db import utcnow
from app.domain import CollectionStatus, RelationshipStatus, ReviewStatus
from app.models import (
    Artifact,
    Claim,
    DiscoveryTask,
    Entity,
    EntityAlias,
    Investigation,
    InvestigationItem,
    Observation,
    ObservationEntity,
    Relationship,
    RelationshipEvidence,
    Source,
)
from app.services import relationships as rel_svc


def delete_source(session: Session, source_id: str, actor: str, reason: str) -> dict:
    src = session.get(Source, source_id)
    if src is None:
        raise LookupError("unknown source")
    if not reason.strip():
        raise ValueError("a deletion reason is required")
    obs_ids = list(session.scalars(select(Observation.id).where(Observation.source_id == source_id)))
    artifacts = set(session.scalars(select(Observation.raw_artifact_id).where(Observation.source_id == source_id, Observation.raw_artifact_id.is_not(None))))
    rel_ids = set(session.scalars(select(RelationshipEvidence.relationship_id).where(RelationshipEvidence.observation_id.in_(obs_ids or [""]))))
    if obs_ids:
        session.execute(delete(RelationshipEvidence).where(RelationshipEvidence.observation_id.in_(obs_ids)))
        session.execute(delete(Claim).where(Claim.observation_id.in_(obs_ids)))
        session.execute(delete(ObservationEntity).where(ObservationEntity.observation_id.in_(obs_ids)))
        session.execute(delete(InvestigationItem).where(InvestigationItem.item_type == "observation", InvestigationItem.item_id.in_(obs_ids)))
        session.execute(EntityAlias.__table__.update().where(EntityAlias.observation_id.in_(obs_ids)).values(observation_id=None))
        session.execute(DiscoveryTask.__table__.update().where(DiscoveryTask.originating_observation_id.in_(obs_ids)).values(originating_observation_id=None))
    # artifacts shared with observations of other sources are kept
    purged = 0
    for aid in artifacts:
        if session.scalar(select(Observation.id).where(Observation.raw_artifact_id == aid, Observation.source_id != source_id).limit(1)):
            continue
        art = session.get(Artifact, aid)
        if art is not None and art.purged_at is None:
            session.execute(Observation.__table__.update().where(Observation.raw_artifact_id == aid).values(raw_artifact_id=None))
            get_store().delete(art.storage_location)
            art.purged_at = utcnow()
            purged += 1
    if obs_ids:
        # observations are immutable records; deletion is the one sanctioned exception and is audited
        session.execute(delete(Observation).where(Observation.id.in_(obs_ids)))
    redacted = 0
    for rid in rel_ids:
        rel = session.get(Relationship, rid)
        if rel is None:
            continue
        remaining = session.scalar(select(RelationshipEvidence.id).where(RelationshipEvidence.relationship_id == rid).limit(1))
        if remaining is None:
            rel.status = RelationshipStatus.REDACTED.value
            rel.valid_to = rel.valid_to or utcnow()
            redacted += 1
        else:
            rel_svc.recompute(session, rel)
    src.collection_status = CollectionStatus.RESTRICTED.value
    src.collection_restrictions = f"deleted by {actor}: {reason}"
    src.meta = {**src.meta, "deleted": True, "deleted_at": utcnow().isoformat()}
    audit(session, actor, "source.delete", "source", source_id, reason=reason, observations_removed=len(obs_ids), artifacts_purged=purged, relationships_redacted=redacted)
    return {"source_id": source_id, "observations_removed": len(obs_ids), "artifacts_purged": purged, "relationships_redacted": redacted}


def correct_source(session: Session, source_id: str, actor: str, *, reliability: dict | None = None, restrictions: str | None = None, display_name: str | None = None) -> Source:
    src = session.get(Source, source_id)
    if src is None:
        raise LookupError("unknown source")
    changes = {}
    if reliability is not None:
        src.reliability, changes["reliability"] = reliability, reliability
    if restrictions is not None:
        src.collection_restrictions, changes["restrictions"] = restrictions, restrictions
        if restrictions:
            src.collection_status = CollectionStatus.RESTRICTED.value
    if display_name is not None:
        src.display_name, changes["display_name"] = display_name, display_name
    audit(session, actor, "source.correct", "source", source_id, **changes)
    return src


def delete_entity(session: Session, entity_id: str, actor: str, reason: str) -> Entity:
    ent = session.get(Entity, entity_id)
    if ent is None:
        raise LookupError("unknown entity")
    if not reason.strip():
        raise ValueError("a deletion reason is required")
    ent.status = "deleted"
    for rel in session.scalars(select(Relationship).where((Relationship.source_entity_id == ent.id) | (Relationship.target_entity_id == ent.id))):
        rel.review_status = ReviewStatus.REJECTED.value
        rel.status = RelationshipStatus.REJECTED.value
    audit(session, actor, "entity.delete", "entity", ent.id, reason=reason)
    return ent


def export_investigation(session: Session, investigation_id: str, actor: str) -> dict:
    """Full export of an investigation's stored data (analyst notes kept separate from public observations)."""
    from app.services import profile as prof

    inv = session.get(Investigation, investigation_id)
    if inv is None:
        raise LookupError("unknown investigation")
    items = list(session.scalars(select(InvestigationItem).where(InvestigationItem.investigation_id == inv.id)))
    ent_ids = [i.item_id for i in items if i.item_type == "entity"]
    obs_ids = [i.item_id for i in items if i.item_type == "observation"]
    entities = [prof.entity_dict(e) for e in session.scalars(select(Entity).where(Entity.id.in_(ent_ids or [""])))]
    observations = [prof.obs_summary(o) for o in session.scalars(select(Observation).where(Observation.id.in_(obs_ids or [""])))]
    rels = session.scalars(select(Relationship).where(Relationship.source_entity_id.in_(ent_ids or [""]), Relationship.target_entity_id.in_(ent_ids or [""])))
    relationships = [prof.relationship_detail(session, r) for r in rels]
    audit(session, actor, "investigation.export", "investigation", inv.id, entities=len(entities), observations=len(observations))
    return {
        "exported_at": utcnow().isoformat(), "exported_by": actor,
        "investigation": {"id": inv.id, "name": inv.name, "description": inv.description, "scope": inv.scope, "created_by": inv.created_by},
        "analyst_notes": {"notes": inv.notes, "note": "Analyst-generated; not a public-source observation."},
        "public_source_data": {"entities": entities, "observations": observations, "relationships": relationships},
    }
