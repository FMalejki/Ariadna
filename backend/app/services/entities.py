"""Entity registry + conservative entity resolution (spec 3.1.E).

Identity is only ever established by:
  1. exact canonical identifier (unique constraint)           -> same row
  2. deterministic network resolution (provider proved it)    -> reversible merge
  3. analyst decision (accept MatchCandidate / manual merge)  -> reversible merge
Similarity (e.g. same username string on two platforms) only creates a pending MatchCandidate.

Merges are SOFT: `merged_into_id` is set and relationships are NOT rewritten. Read paths resolve ids through
`resolve_id` / `cluster_ids`, so reversing a merge restores exactly the previous state.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import utcnow
from app.domain import EntityStatus, MatchStatus, ResolutionStatus
from app.models import Entity, EntityAlias, EntityMerge, MatchCandidate
from app.normalize import Reference


class ResolutionError(ValueError):
    pass


def get_or_create_entity(session: Session, ref: Reference, observed_at: datetime | None) -> tuple[Entity, bool]:
    ent = session.scalar(select(Entity).where(Entity.canonical_identifier == ref.canonical_id))
    created = False
    if ent is None:
        ent = Entity(
            entity_type=ref.entity_type,
            canonical_identifier=ref.canonical_id,
            display_name=ref.display_name,
            meta={k: v for k, v in {"platform": ref.platform, "network": ref.network, "url": ref.url, **ref.attrs}.items() if v is not None},
            resolution_status=ResolutionStatus.RESOLVED.value,
        )
        session.add(ent)
        session.flush()
        created = True
    if observed_at is not None:
        if ent.first_observed_at is None or observed_at < ent.first_observed_at:
            ent.first_observed_at = observed_at
        if ent.last_observed_at is None or observed_at > ent.last_observed_at:
            ent.last_observed_at = observed_at
    return ent, created


def resolve_id(session: Session, entity_id: str) -> str:
    """Follow soft-merge pointers to the surviving entity id."""
    seen: set[str] = set()
    current = entity_id
    while current not in seen:
        seen.add(current)
        nxt = session.scalar(select(Entity.merged_into_id).where(Entity.id == current))
        if not nxt:
            return current
        current = nxt
    raise ResolutionError("merge cycle detected")  # guarded against at merge time; defensive


def cluster_ids(session: Session, entity_id: str) -> list[str]:
    """The surviving entity plus every entity soft-merged (transitively) into it."""
    root = resolve_id(session, entity_id)
    ids = [root]
    frontier = [root]
    while frontier:
        children = list(session.scalars(select(Entity.id).where(Entity.merged_into_id.in_(frontier))))
        children = [c for c in children if c not in ids]
        ids.extend(children)
        frontier = children
    return ids


def merge_entities(
    session: Session,
    winner: Entity,
    loser: Entity,
    *,
    reason: str,
    method: str,
    actor: str,
    evidence_observation_ids: list[str] | None = None,
) -> EntityMerge:
    if winner.id == loser.id:
        raise ResolutionError("cannot merge an entity into itself")
    if winner.entity_type != loser.entity_type and method != "deterministic_network_resolution":
        raise ResolutionError("refusing to merge entities of different types")
    if resolve_id(session, winner.id) == loser.id:
        raise ResolutionError("merge would create a cycle")
    if loser.merged_into_id:
        raise ResolutionError("loser is already merged")
    if not reason.strip():
        raise ResolutionError("a merge needs an explicit reason")
    loser.status = EntityStatus.MERGED.value
    loser.merged_into_id = winner.id
    merge = EntityMerge(
        winner_id=winner.id, loser_id=loser.id, reason=reason, method=method, merged_by=actor,
        evidence_observation_ids=evidence_observation_ids or [],
    )
    session.add(merge)
    alias = session.scalar(select(EntityAlias).where(EntityAlias.entity_id == winner.id, EntityAlias.alias == loser.canonical_identifier))
    if alias is None:
        session.add(
            EntityAlias(
                entity_id=winner.id, alias=loser.canonical_identifier, alias_type="merged_identifier",
                basis=f"{method}: {reason}", confirmed_by=actor,
                observation_id=(evidence_observation_ids or [None])[0],
            )
        )
    for ts in (loser.first_observed_at, loser.last_observed_at):
        if ts is not None:
            if winner.first_observed_at is None or ts < winner.first_observed_at:
                winner.first_observed_at = ts
            if winner.last_observed_at is None or ts > winner.last_observed_at:
                winner.last_observed_at = ts
    session.flush()
    audit(session, actor, "entity.merge", "entity", winner.id, loser=loser.id, method=method, reason=reason)
    return merge


def reverse_merge(session: Session, merge_id: str, actor: str, note: str = "") -> EntityMerge:
    merge = session.get(EntityMerge, merge_id)
    if merge is None:
        raise ResolutionError("unknown merge")
    if merge.reversed_at is not None:
        raise ResolutionError("merge already reversed")
    loser = session.get(Entity, merge.loser_id)
    assert loser is not None
    loser.status = EntityStatus.ACTIVE.value
    loser.merged_into_id = None
    merge.reversed_at = utcnow()
    merge.reversed_by = actor
    alias = session.scalar(
        select(EntityAlias).where(EntityAlias.entity_id == merge.winner_id, EntityAlias.alias == loser.canonical_identifier)
    )
    if alias is not None:
        session.delete(alias)
    session.flush()
    audit(session, actor, "entity.merge_reversed", "entity", merge.winner_id, loser=loser.id, note=note)
    return merge


def propose_match(session: Session, a: Entity, b: Entity, rule: str, explanation: str) -> tuple[MatchCandidate, bool]:
    """Queue a pending identity candidate. Order-normalized so (a,b) and (b,a) are the same candidate."""
    first, second = sorted((a.id, b.id))
    existing = session.scalar(
        select(MatchCandidate).where(
            MatchCandidate.entity_a_id == first, MatchCandidate.entity_b_id == second, MatchCandidate.rule == rule
        )
    )
    if existing is not None:
        return existing, False
    cand = MatchCandidate(entity_a_id=first, entity_b_id=second, rule=rule, explanation=explanation)
    session.add(cand)
    session.flush()
    for ent in (a, b):
        if ent.resolution_status == ResolutionStatus.RESOLVED.value:
            ent.resolution_status = ResolutionStatus.CANDIDATE.value
    return cand, True


def decide_match(session: Session, cand: MatchCandidate, decision: str, actor: str, winner_id: str | None = None) -> MatchCandidate:
    if cand.status != MatchStatus.PENDING.value:
        raise ResolutionError("candidate already decided")
    if decision not in (MatchStatus.ACCEPTED.value, MatchStatus.REJECTED.value):
        raise ResolutionError("decision must be accepted | rejected")
    cand.status = decision
    cand.decided_by = actor
    cand.decided_at = utcnow()
    if decision == MatchStatus.ACCEPTED.value:
        a, b = session.get(Entity, cand.entity_a_id), session.get(Entity, cand.entity_b_id)
        assert a is not None and b is not None
        win, lose = (a, b) if (winner_id in (None, a.id)) else (b, a)
        if win.entity_type != lose.entity_type:
            # Accepting a cross-type candidate records a confirmed alias instead of merging unlike things.
            session.add(
                EntityAlias(entity_id=win.id, alias=lose.canonical_identifier, alias_type="analyst_confirmed",
                            basis=f"analyst accepted match ({cand.rule}): {cand.explanation}", confirmed_by=actor)
            )
        else:
            merge_entities(session, win, lose, reason=f"Analyst accepted candidate: {cand.explanation}",
                           method="human_reviewed", actor=actor)
    audit(session, actor, f"match.{decision}", "match_candidate", cand.id, rule=cand.rule)
    return cand


def find_entity(session: Session, identifier: str) -> Entity | None:
    """Look up by canonical id, falling back to confirmed aliases."""
    ent = session.scalar(select(Entity).where(Entity.canonical_identifier == identifier))
    if ent is not None:
        return ent
    alias = session.scalar(select(EntityAlias).where(EntityAlias.alias == identifier))
    return session.get(Entity, alias.entity_id) if alias else None


def search_entities(session: Session, q: str | None, entity_type: str | None, limit: int, offset: int):
    stmt = select(Entity).where(Entity.status != EntityStatus.DELETED.value)
    if entity_type:
        stmt = stmt.where(Entity.entity_type == entity_type)
    if q:
        like = f"%{q.lower()}%"
        from sqlalchemy import func

        stmt = stmt.where(or_(func.lower(Entity.canonical_identifier).like(like), func.lower(Entity.display_name).like(like)))
    return list(session.scalars(stmt.order_by(Entity.last_observed_at.desc().nullslast(), Entity.created_at.desc()).limit(limit).offset(offset)))
