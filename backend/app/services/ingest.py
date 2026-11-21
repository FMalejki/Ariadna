"""Ingestion pipeline: NormalizedObservation -> persistent knowledge (spec 3 / 5 / 6 / 15 steps 4-6, 10).

Provider output ALWAYS passes through here; providers never write to the database.

Guarantees
* Idempotent: re-ingesting an unchanged payload for the same (source, observation_type) creates nothing.
* Append-only: a changed payload creates observation seq+1; older observations are kept. Relationships whose
  only support came from superseded versions become HISTORICAL (valid_to set).
* Every relationship has >=1 RelationshipEvidence row pointing at a real observation.
* Identity: only exact canonical identifiers are reused. Similarity creates MatchCandidates, nothing more.
* Conflicts: exclusive relationships (one sender per tx, one author per post) that disagree are BOTH kept and
  marked CONTRADICTED, with cross-referencing contradiction evidence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain import (
    CollectionStatus,
    EntityType,
    EvidenceType,
    ObservationEntityRole,
    ObservationType,
    ProcessingStatus,
    RelationshipType,
    SupportStatus,
)
from app.models import (
    Entity,
    InvestigationItem,
    Observation,
    ObservationEntity,
    Provider,
    Relationship,
    RelationshipEvidence,
    Source,
)
from app.normalize import PARSER_VERSION, Reference, domain_reference, extract_references
from app.providers.base import Link, NormalizedObservation
from app.services import entities as ent_svc
from app.services import relationships as rel_svc

_ACCOUNTISH = {EntityType.SOCIAL_ACCOUNT.value, EntityType.PUBLIC_PROFILE.value, EntityType.CHANNEL.value}
_ADDRESSISH = {EntityType.WALLET.value, EntityType.TOKEN.value, EntityType.CONTRACT.value}
_PAGEISH = {EntityType.WEBSITE.value, EntityType.DOMAIN.value, EntityType.DOCUMENT.value}

# Relationships where a target can have only ONE source: disagreement => both contradicted.
EXCLUSIVE_SOURCE_TYPES = {
    RelationshipType.WALLET_EXECUTED_TRANSACTION.value,
    RelationshipType.ACCOUNT_PUBLISHED_POST.value,
    RelationshipType.CHANNEL_PUBLISHED_POST.value,
}


def derive_relationship(subject_type: str, ref_type: str) -> str | None:
    """Deterministic rule table: what does 'subject's observation mentions ref' mean? None = no edge."""
    R = RelationshipType
    if subject_type in _ACCOUNTISH:
        if ref_type in _ACCOUNTISH:
            return R.ACCOUNT_LINKS_TO_CHANNEL.value if ref_type == EntityType.CHANNEL.value else R.ACCOUNT_MENTIONS_ACCOUNT.value
        if ref_type == EntityType.WEBSITE.value:
            return R.ACCOUNT_LINKS_TO_WEBSITE.value
        if ref_type in _ADDRESSISH:
            return R.ACCOUNT_PROFILE_REFERENCES_ADDRESS.value
        return None
    if subject_type == EntityType.POST.value:
        if ref_type == EntityType.SOCIAL_ACCOUNT.value or ref_type == EntityType.PUBLIC_PROFILE.value:
            return R.POST_MENTIONS_ACCOUNT.value
        if ref_type == EntityType.CHANNEL.value:
            return R.POST_LINKS_TO_CHANNEL.value
        if ref_type == EntityType.WEBSITE.value:
            return R.POST_LINKS_TO_WEBSITE.value
        if ref_type == EntityType.WALLET.value:
            return R.POST_CONTAINS_WALLET.value
        if ref_type in (EntityType.TOKEN.value, EntityType.CONTRACT.value):
            return R.POST_MENTIONS_TOKEN.value
        if ref_type == EntityType.TRANSACTION.value:
            return R.POST_REFERENCES_TRANSACTION.value
        return None
    if subject_type in _PAGEISH:
        if ref_type in _PAGEISH or ref_type in _ACCOUNTISH or ref_type in _ADDRESSISH or ref_type == EntityType.TRANSACTION.value:
            return R.PAGE_REFERENCES_ENTITY.value
    return None


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


@dataclass
class IngestStats:
    observations_created: int = 0
    observations_duplicate: int = 0
    sources_created: int = 0
    sources_reused: int = 0
    entities_created: int = 0
    entities_reused: int = 0
    relationships_created: int = 0
    evidence_added: int = 0
    match_candidates: int = 0
    contradictions: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Discovered:
    entity: Entity
    reference: Reference
    observation: Observation
    is_new_entity: bool


@dataclass
class IngestResult:
    stats: IngestStats = field(default_factory=IngestStats)
    observation_ids: list[str] = field(default_factory=list)  # newly created
    touched_observation_ids: list[str] = field(default_factory=list)  # created OR matched as duplicate
    discovered: list[Discovered] = field(default_factory=list)


def get_or_create_source(session: Session, ns: NormalizedObservation, provider_name: str) -> tuple[Source, bool]:
    sref = ns.source
    stmt = select(Source).where(Source.source_type == sref.source_type, Source.canonical_key == sref.canonical_key)
    src = session.scalar(stmt)
    if src is not None:
        return src, False
    try:
        with session.begin_nested():
            src = Source(
                source_type=sref.source_type, canonical_key=sref.canonical_key, canonical_url=sref.canonical_url,
                external_identifier=sref.external_identifier, display_name=sref.display_name, provider=provider_name,
                access_method=sref.access_method, collection_status=CollectionStatus.PENDING.value,
            )
            session.add(src)
            session.flush()
        return src, True
    except IntegrityError:
        return session.scalars(stmt).one(), False


def _add_item(session: Session, investigation_id: str | None, item_type: str, item_id: str, origin: str) -> None:
    if investigation_id is None:
        return
    exists = session.scalar(
        select(InvestigationItem.id).where(
            InvestigationItem.investigation_id == investigation_id,
            InvestigationItem.item_type == item_type,
            InvestigationItem.item_id == item_id,
        )
    )
    if exists is None:
        session.add(InvestigationItem(investigation_id=investigation_id, item_type=item_type, item_id=item_id, origin=origin))


def _mark_exclusive_conflicts(session: Session, rel: Relationship, new_obs: Observation, stats: IngestStats) -> None:
    if rel.relationship_type not in EXCLUSIVE_SOURCE_TYPES:
        return
    mine = set(ent_svc.cluster_ids(session, rel.source_entity_id))
    others = session.scalars(
        select(Relationship).where(
            Relationship.target_entity_id == rel.target_entity_id,
            Relationship.relationship_type == rel.relationship_type,
            Relationship.id != rel.id,
        )
    ).all()
    for other in others:
        if other.source_entity_id in mine:
            continue
        other_obs = session.scalar(
            select(Observation)
            .join(RelationshipEvidence, RelationshipEvidence.observation_id == Observation.id)
            .where(
                RelationshipEvidence.relationship_id == other.id,
                RelationshipEvidence.support_status == SupportStatus.SUPPORTS.value,
            )
            .order_by(Observation.observed_at.desc())
            .limit(1)
        )
        if other_obs is None:
            continue
        a_name = session.get(Entity, rel.source_entity_id).canonical_identifier  # type: ignore[union-attr]
        b_name = session.get(Entity, other.source_entity_id).canonical_identifier  # type: ignore[union-attr]
        why = f"Conflicting sources for an exclusive relationship ({rel.relationship_type}): {a_name} vs {b_name}"
        _, a_new = rel_svc.add_evidence(
            session, rel, other_obs, evidence_type=EvidenceType.CONTRADICTORY.value, explanation=why,
            basis="exclusive-relationship conflict rule", support=SupportStatus.CONTRADICTS.value,
        )
        _, b_new = rel_svc.add_evidence(
            session, other, new_obs, evidence_type=EvidenceType.CONTRADICTORY.value, explanation=why,
            basis="exclusive-relationship conflict rule", support=SupportStatus.CONTRADICTS.value,
        )
        if a_new or b_new:
            stats.contradictions += 1
        rel_svc.recompute(session, other)


def ingest(
    session: Session,
    items: list[NormalizedObservation],
    *,
    provider: Provider,
    tool_run_id: str | None,
    raw_artifact_id: str | None,
    evidence_default: str,
    investigation_id: str | None = None,
    retrieved_at: datetime | None = None,
    actor: str = "system",
) -> IngestResult:
    result = IngestResult()
    now = retrieved_at or utcnow()
    for ns in items:
        _ingest_one(session, ns, result, provider, tool_run_id, raw_artifact_id, evidence_default, investigation_id, now)
    return result


def _ingest_one(
    session: Session,
    ns: NormalizedObservation,
    result: IngestResult,
    provider: Provider,
    tool_run_id: str | None,
    raw_artifact_id: str | None,
    evidence_default: str,
    investigation_id: str | None,
    now: datetime,
) -> None:
    st = result.stats
    source, src_created = get_or_create_source(session, ns, provider.name)
    st.sources_created += int(src_created)
    st.sources_reused += int(not src_created)
    source.last_checked_at = now

    payload_hash = _hash({"t": ns.observation_type, "p": ns.payload, "pub": ns.published_at})
    latest = session.scalar(
        select(Observation)
        .where(Observation.source_id == source.id, Observation.observation_type == ns.observation_type)
        .order_by(Observation.seq.desc())
        .limit(1)
    )
    if latest is not None and latest.payload_hash == payload_hash:
        st.observations_duplicate += 1
        result.touched_observation_ids.append(latest.id)
        _add_item(session, investigation_id, "source", source.id, "reused")
        _add_item(session, investigation_id, "observation", latest.id, "reused")
        subj_ent = ent_svc.find_entity(session, ns.subject.canonical_id)
        if subj_ent is not None:
            _add_item(session, investigation_id, "entity", subj_ent.id, "reused")
        return

    seq = 1 if latest is None else latest.seq + 1
    ev_type = ns.evidence_type or evidence_default
    obs = Observation(
        source_id=source.id, provider_id=provider.id, tool_run_id=tool_run_id, external_id=ns.external_id,
        observation_type=ns.observation_type, observed_at=ns.observed_at, published_at=ns.published_at,
        retrieved_at=now, raw_artifact_id=raw_artifact_id, normalized_payload=ns.payload,
        content_hash=_hash({"payload": payload_hash, "seq": seq}), payload_hash=payload_hash, seq=seq,
        parser_version=PARSER_VERSION, processing_status=ProcessingStatus.PENDING.value, evidence_type=ev_type,
    )
    session.add(obs)
    session.flush()
    st.observations_created += 1
    result.observation_ids.append(obs.id)
    result.touched_observation_ids.append(obs.id)

    source.last_observed_at = max(filter(None, [source.last_observed_at, ns.observed_at]))
    source.collection_status = (
        CollectionStatus.UNAVAILABLE.value if ns.source_unavailable else CollectionStatus.ACTIVE.value
    )

    # ---- subject entity
    subject, created = ent_svc.get_or_create_entity(session, ns.subject, ns.observed_at)
    st.entities_created += int(created)
    st.entities_reused += int(not created)
    if source.entity_id is None and source.canonical_key == ns.subject.canonical_id:
        source.entity_id = subject.id
    session.add(ObservationEntity(observation_id=obs.id, entity_id=subject.id, role=ObservationEntityRole.SUBJECT.value))
    _add_item(session, investigation_id, "source", source.id, "discovered" if src_created else "reused")
    _add_item(session, investigation_id, "observation", obs.id, "discovered")
    _add_item(session, investigation_id, "entity", subject.id, "discovered" if created else "reused")

    if ns.observation_type in (ObservationType.BLOCKCHAIN_TRANSACTION.value, ObservationType.BLOCKCHAIN_LOOKUP_FAILED.value):
        verified = ns.observation_type == ObservationType.BLOCKCHAIN_TRANSACTION.value
        subject.meta = {
            **subject.meta,
            "verification": {"status": "verified" if verified else "not_found", "observation_id": obs.id, "at": now.isoformat()},
        }
    # ---- deterministic type refinements and network resolutions (provider-proven facts)
    for upd in ns.entity_type_updates:
        target, _ = ent_svc.get_or_create_entity(session, upd.reference, ns.observed_at)
        if target.entity_type in (EntityType.WALLET.value,) and upd.new_type != target.entity_type:
            target.entity_type = upd.new_type
            target.meta = {**target.meta, **upd.meta, "type_refined_by_observation": obs.id}
    for nr in ns.network_resolutions:
        old = ent_svc.find_entity(session, nr.from_identifier)
        new, new_created = ent_svc.get_or_create_entity(session, nr.to_reference, ns.observed_at)
        st.entities_created += int(new_created)
        if old is not None and old.id != new.id and old.merged_into_id is None and new.merged_into_id is None:
            ent_svc.merge_entities(
                session, new, old, reason=nr.reason, method="deterministic_network_resolution",
                actor="system", evidence_observation_ids=[obs.id],
            )

    # ---- references: explicit links + deterministic extraction from text
    links: list[Link] = list(ns.links)
    explicit_ids = {(l.reference.canonical_id) for l in links}
    text = "\n".join(t for t in ns.text_fields if t)
    if text:
        refs, _skipped = extract_references(text, ns.platform_hint)
        for ref in refs:
            if ref.canonical_id not in explicit_ids and ref.canonical_id != ns.subject.canonical_id:
                links.append(
                    Link(
                        ref, None, "out",
                        explanation=f"The observed text contains {ref.display_name} ({ref.entity_type})",
                        basis=f"deterministic extraction (parser {PARSER_VERSION}); the source states the reference, it does not verify it",
                    )
                )

    touched_rels: dict[str, Relationship] = {}
    for link in links:
        ref = link.reference
        if ref.canonical_id == ns.subject.canonical_id:
            continue
        target, t_created = ent_svc.get_or_create_entity(session, ref, ns.observed_at)
        st.entities_created += int(t_created)
        st.entities_reused += int(not t_created)
        if session.scalar(
            select(ObservationEntity.id).where(
                ObservationEntity.observation_id == obs.id, ObservationEntity.entity_id == target.id,
                ObservationEntity.role == ObservationEntityRole.MENTIONED.value,
            )
        ) is None:
            session.add(
                ObservationEntity(
                    observation_id=obs.id, entity_id=target.id, role=ObservationEntityRole.MENTIONED.value,
                    extraction_method=link.basis.split(" (")[0][:60] or "deterministic_parser",
                )
            )
        _add_item(session, investigation_id, "entity", target.id, "discovered" if t_created else "reused")
        if link.follow_up:
            result.discovered.append(Discovered(target, ref, obs, t_created))

        rel_type = link.relationship_type or derive_relationship(subject.entity_type, target.entity_type)
        if link.match_candidate_rule:
            _, new_cand = ent_svc.propose_match(
                session, subject, target, link.match_candidate_rule,
                link.explanation or f"{subject.canonical_identifier} and {target.canonical_identifier} share a username string; identity not established",
            )
            st.match_candidates += int(new_cand)
        if rel_type is None:
            continue
        a, b = (subject, target) if link.direction == "out" else (target, subject)
        a_id, b_id = ent_svc.resolve_id(session, a.id), ent_svc.resolve_id(session, b.id)
        rel, r_created = rel_svc.get_or_create_relationship(session, a_id, b_id, rel_type, link.basis or "deterministic rule")
        st.relationships_created += int(r_created)
        _, ev_new = rel_svc.add_evidence(
            session, rel, obs, evidence_type=link.evidence_type or ev_type,
            explanation=link.explanation or f"{subject.canonical_identifier} observation references {target.canonical_identifier}",
            basis=link.basis or "deterministic rule",
        )
        st.evidence_added += int(ev_new)
        touched_rels[rel.id] = rel

        if target.entity_type == EntityType.WEBSITE.value and target.meta.get("host"):
            dref = domain_reference(target.meta["host"])
            dom, d_created = ent_svc.get_or_create_entity(session, dref, ns.observed_at)
            st.entities_created += int(d_created)
            drel, dr_created = rel_svc.get_or_create_relationship(
                session, ent_svc.resolve_id(session, target.id), dom.id, RelationshipType.WEBSITE_HAS_DOMAIN.value, "deterministic URL parse"
            )
            st.relationships_created += int(dr_created)
            _, dev = rel_svc.add_evidence(
                session, drel, obs, evidence_type=ev_type, explanation=f"URL host is {dom.display_name}",
                basis="deterministic URL parse",
            )
            st.evidence_added += int(dev)
            touched_rels[drel.id] = drel

    for rel in touched_rels.values():
        _mark_exclusive_conflicts(session, rel, obs, st)

    # ---- supersession: relationships supported only by older versions of this (source, type) may now be historical
    if seq > 1:
        older_rels = session.scalars(
            select(Relationship)
            .join(RelationshipEvidence, RelationshipEvidence.relationship_id == Relationship.id)
            .join(Observation, Observation.id == RelationshipEvidence.observation_id)
            .where(Observation.source_id == source.id, Observation.observation_type == ns.observation_type, Observation.seq < seq)
        ).unique()
        for rel in older_rels:
            touched_rels[rel.id] = rel
    for rel in touched_rels.values():
        rel_svc.recompute(session, rel)
    obs.processing_status = ProcessingStatus.PROCESSED.value
