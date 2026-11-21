"""Knowledge endpoints: entities, sources, observations, relationships, claims, matches, merges, governance, evidence."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.api.deps import SessionDep, admin, analyst, viewer
from app.artifacts import get_store
from app.domain import ClaimReview
from app.llm import claims as llm_claims
from app.llm.client import LLMDisabled, LLMError, get_client
from app.models import (
    Artifact,
    Claim,
    Entity,
    EntityMerge,
    MatchCandidate,
    Observation,
    ObservationEntity,
    Relationship,
    Source,
    ToolRun,
)
from app.normalize import InvalidReference, classify_seed
from app.security import current_user
from app.models import User
from app.services import entities as ent_svc
from app.services import governance as gov
from app.services import profile as prof
from app.services import relationships as rel_svc

router = APIRouter()


# ---------- entities ----------
@router.get("/entities", tags=["entities"], dependencies=[Depends(viewer)])
def list_entities(session: SessionDep, q: str | None = None, entity_type: str | None = None, limit: int = 50, offset: int = 0):
    rows = ent_svc.search_entities(session, q, entity_type, min(limit, 200), offset)
    return [prof.entity_dict(e) for e in rows]


@router.get("/entities/lookup", tags=["entities"], dependencies=[Depends(viewer)])
def lookup_entity(session: SessionDep, identifier: str):
    """Resolve a URL / handle / address to a known entity (or report that it is unknown) without creating anything."""
    try:
        ref = classify_seed(identifier)
    except InvalidReference as exc:
        raise HTTPException(422, str(exc)) from exc
    ent = ent_svc.find_entity(session, ref.canonical_id)
    return {"canonical_identifier": ref.canonical_id, "entity_type": ref.entity_type, "known": ent is not None,
            "entity_id": ent_svc.resolve_id(session, ent.id) if ent else None}


@router.get("/entities/{entity_id}", tags=["entities"], dependencies=[Depends(viewer)])
def entity_profile(entity_id: str, session: SessionDep, as_of: datetime | None = None):
    p = prof.profile(session, entity_id, as_of)
    if p is None:
        raise HTTPException(404, "entity not found")
    return p


@router.get("/entities/{entity_id}/timeline", tags=["entities"], dependencies=[Depends(viewer)])
def entity_timeline(entity_id: str, session: SessionDep, as_of: datetime | None = None, limit: int = 200):
    if session.get(Entity, entity_id) is None:
        raise HTTPException(404, "entity not found")
    return prof.timeline(session, entity_id, as_of, min(limit, 500))


class DeleteIn(BaseModel):
    reason: str = Field(min_length=3)


@router.delete("/entities/{entity_id}", tags=["governance"])
def delete_entity(entity_id: str, body: DeleteIn, session: SessionDep, user: User = Depends(admin)):
    try:
        ent = gov.delete_entity(session, entity_id, user.username, body.reason)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return prof.entity_dict(ent)


# ---------- sources ----------
def _source_dict(s: Source, n_obs: int | None = None) -> dict:
    return {
        "id": s.id, "source_type": s.source_type, "canonical_key": s.canonical_key, "canonical_url": s.canonical_url,
        "display_name": s.display_name, "provider": s.provider, "access_method": s.access_method,
        "collection_status": s.collection_status, "collection_restrictions": s.collection_restrictions, "reliability": s.reliability,
        "first_discovered_at": s.first_discovered_at.isoformat(),
        "last_observed_at": s.last_observed_at.isoformat() if s.last_observed_at else None,
        "last_checked_at": s.last_checked_at.isoformat() if s.last_checked_at else None,
        "observation_count": n_obs,
    }


@router.get("/sources", tags=["sources"], dependencies=[Depends(viewer)])
def list_sources(session: SessionDep, q: str | None = None, source_type: str | None = None, collection_status: str | None = None, limit: int = 50, offset: int = 0):
    stmt = select(Source, func.count(Observation.id)).outerjoin(Observation, Observation.source_id == Source.id).group_by(Source.id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Source.canonical_key).like(like), func.lower(Source.display_name).like(like)))
    if source_type:
        stmt = stmt.where(Source.source_type == source_type)
    if collection_status:
        stmt = stmt.where(Source.collection_status == collection_status)
    rows = session.execute(stmt.order_by(Source.last_observed_at.desc().nullslast()).limit(min(limit, 200)).offset(offset)).all()
    return [_source_dict(s, n) for s, n in rows]


@router.get("/sources/stale", tags=["sources"], dependencies=[Depends(viewer)])
def stale_sources(session: SessionDep):
    return prof.stale_sources(session)


@router.get("/sources/{source_id}", tags=["sources"], dependencies=[Depends(viewer)])
def get_source(source_id: str, session: SessionDep):
    s = session.get(Source, source_id)
    if s is None:
        raise HTTPException(404, "source not found")
    obs = session.scalars(select(Observation).where(Observation.source_id == s.id).order_by(Observation.seq.desc(), Observation.observed_at.desc()).limit(100)).all()
    return {**_source_dict(s, len(obs)), "observations": [prof.obs_summary(o, s) for o in obs]}


class SourceCorrection(BaseModel):
    reliability: dict | None = None
    restrictions: str | None = None
    display_name: str | None = None


@router.patch("/sources/{source_id}", tags=["governance"])
def correct_source(source_id: str, body: SourceCorrection, session: SessionDep, user: User = Depends(analyst)):
    try:
        return _source_dict(gov.correct_source(session, source_id, user.username, **body.model_dump()))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/sources/{source_id}", tags=["governance"])
def delete_source(source_id: str, body: DeleteIn, session: SessionDep, user: User = Depends(admin)):
    try:
        return gov.delete_source(session, source_id, user.username, body.reason)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


# ---------- observations / evidence ----------
@router.get("/observations", tags=["observations"], dependencies=[Depends(viewer)])
def list_observations(
    session: SessionDep, source_id: str | None = None, entity_id: str | None = None, observation_type: str | None = None, limit: int = 50, offset: int = 0
):
    stmt = select(Observation)
    if source_id:
        stmt = stmt.where(Observation.source_id == source_id)
    if observation_type:
        stmt = stmt.where(Observation.observation_type == observation_type)
    if entity_id:
        cluster = ent_svc.cluster_ids(session, entity_id)
        stmt = stmt.where(Observation.id.in_(select(ObservationEntity.observation_id).where(ObservationEntity.entity_id.in_(cluster))))
    rows = session.scalars(stmt.order_by(Observation.observed_at.desc()).limit(min(limit, 200)).offset(offset)).all()
    return [prof.obs_summary(o) for o in rows]


@router.get("/observations/{observation_id}", tags=["observations"], dependencies=[Depends(viewer)])
def get_observation(observation_id: str, session: SessionDep):
    o = session.get(Observation, observation_id)
    if o is None:
        raise HTTPException(404, "observation not found")
    src = session.get(Source, o.source_id)
    ents = session.execute(
        select(Entity, ObservationEntity.role).join(ObservationEntity, ObservationEntity.entity_id == Entity.id).where(ObservationEntity.observation_id == o.id)
    ).all()
    claims = session.scalars(select(Claim).where(Claim.observation_id == o.id, Claim.superseded_by.is_(None))).all()
    return {
        **prof.obs_summary(o, src),
        "entities": [{"id": ent_svc.resolve_id(session, e.id), "role": role, "label": e.display_name, "canonical_identifier": e.canonical_identifier} for e, role in ents],
        "claims": [_claim_dict(c) for c in claims],
    }


@router.get("/evidence/tool-runs", tags=["evidence"], dependencies=[Depends(viewer)])
def tool_runs(session: SessionDep, provider: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0):
    stmt = select(ToolRun)
    if provider:
        stmt = stmt.where(ToolRun.provider_name == provider)
    if status:
        stmt = stmt.where(ToolRun.status == status)
    rows = session.scalars(stmt.order_by(ToolRun.started_at.desc()).limit(min(limit, 200)).offset(offset))
    return [
        {"id": r.id, "provider": r.provider_name, "provider_version": r.provider_version, "target": r.input_key, "status": r.status,
         "started_at": r.started_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None,
         "error": r.error, "observation_count": len(r.observation_ids or []), "raw_artifact_id": r.raw_artifact_id}
        for r in rows
    ]


@router.get("/evidence/artifacts/{artifact_id}", tags=["evidence"], dependencies=[Depends(viewer)])
def artifact_meta(artifact_id: str, session: SessionDep):
    a = session.get(Artifact, artifact_id)
    if a is None:
        raise HTTPException(404, "artifact not found")
    return {"id": a.id, "content_hash": a.content_hash, "content_type": a.content_type, "size": a.size, "captured_at": a.captured_at.isoformat(),
            "capture_method": a.capture_method, "purged": a.purged_at is not None, "metadata": a.meta}


@router.get("/evidence/artifacts/{artifact_id}/content", tags=["evidence"], dependencies=[Depends(analyst)])
def artifact_content(artifact_id: str, session: SessionDep):
    """Raw preserved bytes, served as an attachment and NEVER rendered inline (untrusted external content)."""
    a = session.get(Artifact, artifact_id)
    if a is None or a.purged_at is not None:
        raise HTTPException(404, "artifact not found or purged")
    return Response(
        content=get_store().get(a.storage_location), media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{a.content_hash}.bin"', "X-Content-Type-Options": "nosniff", "X-Original-Content-Type": a.content_type},
    )


# ---------- relationships ----------
@router.get("/relationships", tags=["relationships"], dependencies=[Depends(viewer)])
def list_relationships(session: SessionDep, status: str | None = None, relationship_type: str | None = None, review_status: str | None = None, entity_id: str | None = None, limit: int = 50, offset: int = 0):
    stmt = select(Relationship)
    if status:
        stmt = stmt.where(Relationship.status == status)
    if relationship_type:
        stmt = stmt.where(Relationship.relationship_type == relationship_type)
    if review_status:
        stmt = stmt.where(Relationship.review_status == review_status)
    if entity_id:
        cluster = ent_svc.cluster_ids(session, entity_id)
        stmt = stmt.where(or_(Relationship.source_entity_id.in_(cluster), Relationship.target_entity_id.in_(cluster)))
    rows = session.scalars(stmt.order_by(Relationship.updated_at.desc()).limit(min(limit, 200)).offset(offset)).all()
    return [
        {"id": r.id, "relationship_type": r.relationship_type, "status": r.status, "review_status": r.review_status, "source_entity_id": r.source_entity_id,
         "target_entity_id": r.target_entity_id, "first_observed_at": r.first_observed_at.isoformat() if r.first_observed_at else None,
         "last_observed_at": r.last_observed_at.isoformat() if r.last_observed_at else None}
        for r in rows
    ]


@router.get("/relationships/{relationship_id}", tags=["relationships"], dependencies=[Depends(viewer)])
def relationship_detail(relationship_id: str, session: SessionDep):
    r = session.get(Relationship, relationship_id)
    if r is None:
        raise HTTPException(404, "relationship not found")
    return prof.relationship_detail(session, r)


class ReviewIn(BaseModel):
    decision: str
    note: str = ""


@router.post("/relationships/{relationship_id}/review", tags=["relationships"])
def review_relationship(relationship_id: str, body: ReviewIn, session: SessionDep, user: User = Depends(analyst)):
    r = session.get(Relationship, relationship_id)
    if r is None:
        raise HTTPException(404, "relationship not found")
    try:
        rel_svc.review_relationship(session, r, body.decision, user.username, body.note)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.flush()
    return prof.relationship_detail(session, r)


# ---------- match candidates & merges ----------
@router.get("/match-candidates", tags=["entities"], dependencies=[Depends(viewer)])
def list_match_candidates(session: SessionDep, status: str | None = "pending", limit: int = 100):
    stmt = select(MatchCandidate)
    if status:
        stmt = stmt.where(MatchCandidate.status == status)
    out = []
    for c in session.scalars(stmt.order_by(MatchCandidate.created_at.desc()).limit(min(limit, 300))):
        a, b = session.get(Entity, c.entity_a_id), session.get(Entity, c.entity_b_id)
        out.append({"id": c.id, "rule": c.rule, "explanation": c.explanation, "status": c.status,
                    "entity_a": prof.entity_dict(a) if a else None, "entity_b": prof.entity_dict(b) if b else None,
                    "decided_by": c.decided_by})
    return out


class MatchDecision(BaseModel):
    decision: str
    winner_id: str | None = None


@router.post("/match-candidates/{candidate_id}/decide", tags=["entities"])
def decide_match(candidate_id: str, body: MatchDecision, session: SessionDep, user: User = Depends(analyst)):
    c = session.get(MatchCandidate, candidate_id)
    if c is None:
        raise HTTPException(404, "candidate not found")
    try:
        ent_svc.decide_match(session, c, body.decision, user.username, body.winner_id)
    except ent_svc.ResolutionError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": c.id, "status": c.status, "decided_by": c.decided_by}


@router.get("/merges", tags=["entities"], dependencies=[Depends(viewer)])
def list_merges(session: SessionDep, include_reversed: bool = False, limit: int = 100):
    stmt = select(EntityMerge)
    if not include_reversed:
        stmt = stmt.where(EntityMerge.reversed_at.is_(None))
    return [
        {"id": m.id, "winner_id": m.winner_id, "loser_id": m.loser_id, "method": m.method, "reason": m.reason, "merged_by": m.merged_by,
         "merged_at": m.merged_at.isoformat(), "reversed_at": m.reversed_at.isoformat() if m.reversed_at else None}
        for m in session.scalars(stmt.order_by(EntityMerge.merged_at.desc()).limit(min(limit, 300)))
    ]


class ReverseIn(BaseModel):
    note: str = ""


@router.post("/merges/{merge_id}/reverse", tags=["entities"])
def reverse_merge(merge_id: str, body: ReverseIn, session: SessionDep, user: User = Depends(analyst)):
    try:
        m = ent_svc.reverse_merge(session, merge_id, user.username, body.note)
    except ent_svc.ResolutionError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": m.id, "reversed_at": m.reversed_at.isoformat() if m.reversed_at else None, "reversed_by": m.reversed_by}


# ---------- claims (LLM) ----------
def _claim_dict(c: Claim) -> dict:
    return {
        "id": c.id, "observation_id": c.observation_id, "claim_type": c.claim_type, "subject_reference": c.subject_reference, "predicate": c.predicate,
        "object_reference": c.object_reference, "supporting_text": c.supporting_text, "model": f"{c.model_provider}/{c.model_name}",
        "prompt_version": c.prompt_version, "review_status": c.review_status, "verification_status": c.verification_status,
        "superseded_by": c.superseded_by, "created_at": c.created_at.isoformat(),
    }


@router.get("/claims", tags=["claims"], dependencies=[Depends(viewer)])
def list_claims(session: SessionDep, review_status: str | None = None, observation_id: str | None = None, include_superseded: bool = False, limit: int = 100, offset: int = 0):
    stmt = select(Claim)
    if review_status:
        stmt = stmt.where(Claim.review_status == review_status)
    if observation_id:
        stmt = stmt.where(Claim.observation_id == observation_id)
    if not include_superseded:
        stmt = stmt.where(Claim.superseded_by.is_(None))
    return [_claim_dict(c) for c in session.scalars(stmt.order_by(Claim.created_at.desc()).limit(min(limit, 300)).offset(offset))]


@router.post("/claims/{claim_id}/review", tags=["claims"])
def review_claim(claim_id: str, body: ReviewIn, session: SessionDep, user: User = Depends(analyst)):
    c = session.get(Claim, claim_id)
    if c is None:
        raise HTTPException(404, "claim not found")
    try:
        llm_claims.review_claim(session, c, body.decision, user.username)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _claim_dict(c)


@router.get("/llm/status", tags=["claims"], dependencies=[Depends(viewer)])
def llm_status():
    client = get_client()
    return {"enabled": client.provider != "none", "provider": client.provider, "model": client.model, "prompt_version": llm_claims.prompts.CLAIMS_PROMPT_VERSION}


@router.post("/observations/{observation_id}/extract-claims", tags=["claims"])
def extract_claims(observation_id: str, session: SessionDep, user: User = Depends(analyst)):
    obs = session.get(Observation, observation_id)
    if obs is None:
        raise HTTPException(404, "observation not found")
    client = get_client()
    if client.provider == "none":
        raise HTTPException(503, "LLM is not configured; claim extraction is unavailable")
    res = llm_claims.extract_claims(session, client, obs, user.username)
    if res.error:
        raise HTTPException(502, res.error)
    return {"created": [_claim_dict(c) for c in res.created], "rejected": res.rejected,
            "notice": "Claims are unverified and pending review. They are not facts and create no relationships."}


@router.post("/entities/{entity_id}/summary", tags=["claims"])
def entity_summary(entity_id: str, session: SessionDep, user: User = Depends(analyst)):
    ent = session.get(Entity, entity_id)
    if ent is None:
        raise HTTPException(404, "entity not found")
    client = get_client()
    if client.provider == "none":
        raise HTTPException(503, "LLM is not configured; summaries are unavailable")
    cluster = ent_svc.cluster_ids(session, ent.id)
    obs = session.scalars(
        select(Observation).join(ObservationEntity, ObservationEntity.observation_id == Observation.id)
        .where(ObservationEntity.entity_id.in_(cluster)).order_by(Observation.observed_at.desc()).limit(25)
    ).all()
    if not obs:
        raise HTTPException(409, "no observations to summarize")
    try:
        return llm_claims.summarize_entity(client, ent.display_name or ent.canonical_identifier, list(obs))
    except (ValueError, LLMError, LLMDisabled) as exc:
        raise HTTPException(502, str(exc)) from exc


__all__ = ["router", "ClaimReview", "current_user"]
