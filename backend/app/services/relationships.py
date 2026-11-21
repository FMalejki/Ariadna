"""Relationship status policy - deterministic and explainable (spec 6 / 6.1).

Status is DERIVED from evidence rows; there is no stored free-floating confidence number. Source reliability,
evidence strength and relationship status stay separate concepts:

  source reliability   -> Source.reliability (metadata, not used to compute status)
  evidence strength    -> EVIDENCE_STRENGTH below (category ordering, explainable)
  relationship status  -> compute_status() below

Precedence (first match wins):
  1. analyst rejected                                  -> REJECTED
  2. >=1 supporting AND >=1 contradicting evidence      -> CONTRADICTED   (both are preserved and displayed)
     only contradicting evidence                        -> CONTRADICTED
  3. supporting evidence exists but every supporting
     observation was superseded by a newer observation
     of the same source/type that no longer shows it     -> HISTORICAL     (valid_to set)
  4. >=1 deterministic blockchain verification          -> VERIFIED
  5. supporting evidence from >=2 distinct sources
     among "strong" categories                           -> CORROBORATED
  6. >=1 supporting evidence of a strong category        -> SUPPORTED
  7. otherwise (tool discovery / LLM / unverified only)  -> PROPOSED
Strong categories: direct source statement, official platform data, deterministic blockchain verification,
independent corroboration, analyst-confirmed.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain import EvidenceType, RelationshipStatus, ReviewStatus, SupportStatus
from app.models import Observation, Relationship, RelationshipEvidence

STRONG = {
    EvidenceType.DIRECT_SOURCE_STATEMENT.value,
    EvidenceType.OFFICIAL_PLATFORM_DATA.value,
    EvidenceType.DETERMINISTIC_BLOCKCHAIN.value,
    EvidenceType.INDEPENDENT_CORROBORATION.value,
    EvidenceType.ANALYST_CONFIRMED.value,
}

# Ordinal explanation of evidence categories (higher = stronger). Used for display/sorting only.
EVIDENCE_STRENGTH = {
    EvidenceType.DETERMINISTIC_BLOCKCHAIN.value: 6,
    EvidenceType.ANALYST_CONFIRMED.value: 6,
    EvidenceType.OFFICIAL_PLATFORM_DATA.value: 5,
    EvidenceType.INDEPENDENT_CORROBORATION.value: 4,
    EvidenceType.DIRECT_SOURCE_STATEMENT.value: 3,
    EvidenceType.TOOL_GENERATED_DISCOVERY.value: 2,
    EvidenceType.LLM_EXTRACTED_CLAIM.value: 1,
    EvidenceType.UNVERIFIED_CANDIDATE.value: 1,
    EvidenceType.CONTRADICTORY.value: 0,
}


def superseded_at(session: Session, obs: Observation) -> datetime | None:
    """When a newer version (higher seq) of the same source+type replaced this one (None = still current)."""
    return session.scalar(
        select(func.min(Observation.observed_at)).where(
            Observation.source_id == obs.source_id,
            Observation.observation_type == obs.observation_type,
            Observation.seq > obs.seq,
        )
    )


def compute_status(session: Session, rel: Relationship) -> tuple[str, datetime | None]:
    """Return (status, valid_to)."""
    if rel.review_status == ReviewStatus.REJECTED.value:
        return RelationshipStatus.REJECTED.value, rel.valid_to
    rows = session.execute(
        select(RelationshipEvidence, Observation)
        .join(Observation, Observation.id == RelationshipEvidence.observation_id)
        .where(RelationshipEvidence.relationship_id == rel.id)
    ).all()
    supports = [(e, o) for e, o in rows if e.support_status == SupportStatus.SUPPORTS.value]
    contradicts = [(e, o) for e, o in rows if e.support_status == SupportStatus.CONTRADICTS.value]
    if contradicts:
        return RelationshipStatus.CONTRADICTED.value, rel.valid_to
    if not supports:
        return RelationshipStatus.PROPOSED.value, rel.valid_to
    current = [(e, o) for e, o in supports if superseded_at(session, o) is None]
    analyst = any(e.evidence_type == EvidenceType.ANALYST_CONFIRMED.value for e, _ in supports)
    if not current and not analyst:
        ends = [superseded_at(session, o) for _, o in supports]
        return RelationshipStatus.HISTORICAL.value, max(d for d in ends if d is not None)
    live = supports if analyst else current
    if any(e.evidence_type == EvidenceType.DETERMINISTIC_BLOCKCHAIN.value for e, _ in live):
        return RelationshipStatus.VERIFIED.value, None
    strong = [(e, o) for e, o in live if e.evidence_type in STRONG]
    if len({o.source_id for _, o in strong}) >= 2:
        return RelationshipStatus.CORROBORATED.value, None
    if strong:
        return RelationshipStatus.SUPPORTED.value, None
    return RelationshipStatus.PROPOSED.value, None


def recompute(session: Session, rel: Relationship) -> Relationship:
    status, valid_to = compute_status(session, rel)
    rel.status = status
    rel.valid_to = valid_to
    rel.updated_at = utcnow()
    return rel


def add_evidence(
    session: Session,
    rel: Relationship,
    observation: Observation,
    *,
    evidence_type: str,
    explanation: str,
    basis: str,
    support: str = SupportStatus.SUPPORTS.value,
) -> tuple[RelationshipEvidence, bool]:
    existing = session.scalar(
        select(RelationshipEvidence).where(
            RelationshipEvidence.relationship_id == rel.id,
            RelationshipEvidence.observation_id == observation.id,
            RelationshipEvidence.evidence_type == evidence_type,
            RelationshipEvidence.support_status == support,
        )
    )
    if existing is not None:
        return existing, False
    ev = RelationshipEvidence(
        relationship_id=rel.id,
        observation_id=observation.id,
        evidence_type=evidence_type,
        explanation=explanation,
        support_status=support,
        confidence_basis=basis,
    )
    session.add(ev)
    session.flush()
    obs_t = observation.observed_at
    if rel.first_observed_at is None or obs_t < rel.first_observed_at:
        rel.first_observed_at = obs_t
        if rel.valid_from is None or obs_t < rel.valid_from:
            rel.valid_from = obs_t
    if rel.last_observed_at is None or obs_t > rel.last_observed_at:
        rel.last_observed_at = obs_t
    return ev, True


def get_or_create_relationship(
    session: Session, source_entity_id: str, target_entity_id: str, rel_type: str, method: str
) -> tuple[Relationship, bool]:
    rel = session.scalar(
        select(Relationship).where(
            Relationship.source_entity_id == source_entity_id,
            Relationship.target_entity_id == target_entity_id,
            Relationship.relationship_type == rel_type,
        )
    )
    if rel is not None:
        return rel, False
    rel = Relationship(
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        relationship_type=rel_type,
        resolution_method=method,
        status=RelationshipStatus.PROPOSED.value,
    )
    session.add(rel)
    session.flush()
    return rel, True


def review_relationship(session: Session, rel: Relationship, decision: str, actor: str, note: str = "") -> Relationship:
    """Analyst review. `accepted` adds ANALYST_CONFIRMED evidence (needs an observation to cite); `rejected`
    sets REJECTED without deleting any evidence."""
    if decision not in (ReviewStatus.ACCEPTED.value, ReviewStatus.REJECTED.value, ReviewStatus.UNREVIEWED.value):
        raise ValueError("decision must be accepted | rejected | unreviewed")
    rel.review_status = decision
    rel.reviewed_by = actor
    rel.reviewed_at = utcnow()
    if decision == ReviewStatus.ACCEPTED.value:
        anchor = session.scalar(
            select(Observation)
            .join(RelationshipEvidence, RelationshipEvidence.observation_id == Observation.id)
            .where(RelationshipEvidence.relationship_id == rel.id)
            .order_by(Observation.observed_at.desc())
            .limit(1)
        )
        if anchor is None:
            raise ValueError("cannot accept a relationship that has no supporting observation")
        add_evidence(
            session, rel, anchor, evidence_type=EvidenceType.ANALYST_CONFIRMED.value,
            explanation=f"Analyst {actor} accepted this relationship. {note}".strip(),
            basis="human review",
        )
    recompute(session, rel)
    return rel
