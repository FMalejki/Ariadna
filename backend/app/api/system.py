"""Auth + system endpoints: health, readiness, settings, overview, audit."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text

from app.api.deps import SessionDep, admin, current_user, viewer
from app.config import get_settings
from app.db import utcnow
from app.models import (
    AuditLog,
    Claim,
    DiscoveryTask,
    Entity,
    MatchCandidate,
    Observation,
    Provider,
    Relationship,
    Source,
    ToolRun,
    User,
)
from app.security import authenticate, create_token
from app.services import profile as prof

router = APIRouter()


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/auth/login", tags=["auth"])
def login(body: LoginIn, session: SessionDep):
    user = authenticate(session, body.username, body.password)
    if user is None:
        raise HTTPException(401, "Invalid credentials")
    return {"access_token": create_token(user), "token_type": "bearer", "role": user.role, "username": user.username}


@router.get("/auth/me", tags=["auth"])
def me(user: User = Depends(current_user)):
    return {"id": user.id, "username": user.username, "role": user.role}


@router.get("/health", tags=["system"])
def health():
    return {"status": "ok"}


@router.get("/ready", tags=["system"])
def ready(session: SessionDep):
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - depends on infra
        raise HTTPException(503, f"database not ready: {type(exc).__name__}") from exc
    return {"status": "ready"}


@router.get("/settings", tags=["system"], dependencies=[Depends(viewer)])
def settings_view():
    return get_settings().public_view()


def _count(session, model, *where) -> int:
    return session.scalar(select(func.count()).select_from(model).where(*where)) or 0


@router.get("/stats/overview", tags=["system"], dependencies=[Depends(viewer)])
def overview(session: SessionDep):
    day_ago = utcnow() - timedelta(days=1)
    rel_by_status = dict(session.execute(select(Relationship.status, func.count()).group_by(Relationship.status)).all())
    ent_by_type = dict(
        session.execute(select(Entity.entity_type, func.count()).where(Entity.status == "active").group_by(Entity.entity_type)).all()
    )
    task_by_status = dict(session.execute(select(DiscoveryTask.status, func.count()).group_by(DiscoveryTask.status)).all())
    runs = session.scalars(select(ToolRun).order_by(ToolRun.started_at.desc()).limit(8)).all()
    return {
        "totals": {
            "entities": _count(session, Entity, Entity.status == "active"),
            "sources": _count(session, Source),
            "observations": _count(session, Observation),
            "relationships": _count(session, Relationship),
        },
        "entities_by_type": ent_by_type,
        "relationships_by_status": rel_by_status,
        "tasks_by_status": task_by_status,
        "attention": {
            "tasks_pending_approval": task_by_status.get("pending_approval", 0),
            "match_candidates_pending": _count(session, MatchCandidate, MatchCandidate.status == "pending"),
            "claims_pending_review": _count(session, Claim, Claim.review_status == "pending", Claim.superseded_by.is_(None)),
            "contradicted_relationships": rel_by_status.get("contradicted", 0),
            "stale_or_unavailable_sources": len(prof.stale_sources(session, 500)),
            "dead_tasks": task_by_status.get("dead", 0),
        },
        "last_24h": prof.what_changed_since(session, day_ago),
        "recent_runs": [
            {"id": r.id, "provider": r.provider_name, "target": r.input_key, "status": r.status, "started_at": r.started_at.isoformat(), "error": r.error}
            for r in runs
        ],
        "providers": [
            {"name": p.name, "enabled": p.enabled, "implementation_status": p.implementation_status, "last_health": p.last_health}
            for p in session.scalars(select(Provider).order_by(Provider.name))
        ],
    }


@router.get("/audit", tags=["system"], dependencies=[Depends(admin)])
def audit_log(session: SessionDep, limit: int = 100, offset: int = 0, action: str | None = None):
    stmt = select(AuditLog).order_by(AuditLog.at.desc())
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = session.scalars(stmt.limit(min(limit, 500)).offset(offset))
    return [{"id": a.id, "at": a.at.isoformat(), "actor": a.actor, "action": a.action, "target_type": a.target_type, "target_id": a.target_id, "details": a.details} for a in rows]
