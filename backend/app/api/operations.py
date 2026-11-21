"""Operational endpoints: discovery, investigations, graph, providers, policies, wiki."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import SessionDep, admin, analyst, csv_set, viewer
from app.audit import audit
from app.config import get_settings
from app.db import utcnow
from app.domain import PolicyAction
from app.models import (
    DiscoveryTask,
    Entity,
    Investigation,
    InvestigationItem,
    InvestigationView,
    Observation,
    Provider,
    ProviderUsage,
    SourcePolicy,
    User,
)
from app.normalize import InvalidReference
from app.providers.base import ProviderContext
from app.providers.registry import get_provider, sync_providers
from app.services import discovery as disc
from app.services import entities as ent_svc
from app.services import governance as gov
from app.services import graph as graph_svc
from app.services import profile as prof
from app.services import wiki as wiki_svc

router = APIRouter()


# ---------- providers ----------
def _provider_dict(row: Provider) -> dict:
    impl = get_provider(row.name)
    return {
        "id": row.id, "name": row.name, "version": row.version, "kind": row.kind, "description": row.description,
        "capabilities": row.capabilities, "evidence_class": row.evidence_class, "implementation_status": row.implementation_status,
        "category": impl.category, "enabled": row.enabled, "rate_limit_per_minute": row.rate_limit_per_minute, "daily_quota": row.daily_quota,
        "last_health": row.last_health, "last_health_at": row.last_health_at.isoformat() if row.last_health_at else None,
        "docs_url": impl.docs_url,
    }


@router.get("/providers", tags=["providers"], dependencies=[Depends(viewer)])
def list_providers(session: SessionDep):
    return [_provider_dict(p) for p in sorted(sync_providers(session), key=lambda p: p.name)]


class ProviderConfigIn(BaseModel):
    enabled: bool | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=10_000)
    daily_quota: int | None = Field(default=None, ge=1, le=1_000_000)


@router.patch("/providers/{name}", tags=["providers"])
def configure_provider(name: str, body: ProviderConfigIn, session: SessionDep, user: User = Depends(admin)):
    row = next((p for p in sync_providers(session) if p.name == name), None)
    if row is None:
        raise HTTPException(404, "provider not found")
    if body.enabled and row.implementation_status != "implemented":
        raise HTTPException(409, "placeholder providers cannot be enabled: the integration is not implemented")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(row, k, v)
    audit(session, user.username, "provider.configure", "provider", row.id, **body.model_dump(exclude_none=True))
    return _provider_dict(row)


@router.post("/providers/{name}/health", tags=["providers"])
def check_provider_health(name: str, session: SessionDep, user: User = Depends(analyst)):
    row = next((p for p in sync_providers(session) if p.name == name), None)
    if row is None:
        raise HTTPException(404, "provider not found")
    h = get_provider(name).health_check(ProviderContext(settings=get_settings()))
    row.last_health, row.last_health_at = {"status": h.status, "detail": h.detail}, utcnow()
    return _provider_dict(row)


@router.get("/providers/{name}/usage", tags=["providers"], dependencies=[Depends(viewer)])
def provider_usage(name: str, session: SessionDep):
    row = next((p for p in sync_providers(session) if p.name == name), None)
    if row is None:
        raise HTTPException(404, "provider not found")
    from datetime import timedelta

    from sqlalchemy import func

    now = utcnow()
    day = session.scalar(select(func.count()).select_from(ProviderUsage).where(ProviderUsage.provider_id == row.id, ProviderUsage.at > now - timedelta(days=1))) or 0
    minute = session.scalar(select(func.count()).select_from(ProviderUsage).where(ProviderUsage.provider_id == row.id, ProviderUsage.at > now - timedelta(minutes=1))) or 0
    return {"provider": name, "last_minute": minute, "last_24h": day, "rate_limit_per_minute": row.rate_limit_per_minute, "daily_quota": row.daily_quota}


# ---------- source policies ----------
class PolicyIn(BaseModel):
    scope: str = Field(pattern="^(category|domain|provider)$")
    pattern: str = Field(min_length=1, max_length=300)
    action: PolicyAction
    reason: str = ""


@router.get("/policies", tags=["policies"], dependencies=[Depends(viewer)])
def list_policies(session: SessionDep):
    return [{"id": p.id, "scope": p.scope, "pattern": p.pattern, "action": p.action, "reason": p.reason, "created_by": p.created_by}
            for p in session.scalars(select(SourcePolicy).order_by(SourcePolicy.scope, SourcePolicy.pattern))]


@router.put("/policies", tags=["policies"])
def upsert_policy(body: PolicyIn, session: SessionDep, user: User = Depends(admin)):
    row = session.scalar(select(SourcePolicy).where(SourcePolicy.scope == body.scope, SourcePolicy.pattern == body.pattern))
    if row is None:
        row = SourcePolicy(scope=body.scope, pattern=body.pattern, action=body.action.value, reason=body.reason, created_by=user.username)
        session.add(row)
    else:
        row.action, row.reason = body.action.value, body.reason
    session.flush()
    audit(session, user.username, "policy.upsert", "source_policy", row.id, scope=body.scope, pattern=body.pattern, action=body.action.value)
    return {"id": row.id, "scope": row.scope, "pattern": row.pattern, "action": row.action}


@router.delete("/policies/{policy_id}", tags=["policies"])
def delete_policy(policy_id: str, session: SessionDep, user: User = Depends(admin)):
    row = session.get(SourcePolicy, policy_id)
    if row is None:
        raise HTTPException(404, "policy not found")
    session.delete(row)
    audit(session, user.username, "policy.delete", "source_policy", policy_id)
    return {"deleted": policy_id}


# ---------- investigations ----------
def _inv_dict(i: Investigation) -> dict:
    return {"id": i.id, "name": i.name, "description": i.description, "status": i.status, "scope": i.scope, "created_by": i.created_by,
            "created_at": i.created_at.isoformat(), "updated_at": i.updated_at.isoformat()}


class ScopeIn(BaseModel):
    max_depth: int = Field(default=1, ge=0, le=5)
    allowed_providers: list[str] | None = None
    allowed_domains: list[str] | None = None


class InvestigationIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    seed: str | None = Field(default=None, max_length=2000, description="URL, @handle, wallet address, tx hash, or domain (optional)")
    full_name: str | None = Field(default=None, max_length=200, description="Full name: a parameter of the investigation, creates no entity")
    login: str | None = Field(default=None, max_length=64, description="Platform-less login/username (checked by Sherlock on many sites)")
    description: str = ""
    platform_hint: str | None = None
    scope: ScopeIn = ScopeIn()
    run_now: bool = False


@router.post("/investigations", tags=["investigations"])
def create_investigation(body: InvestigationIn, session: SessionDep, user: User = Depends(analyst)):
    try:
        inv, ref, tasks = disc.start_investigation(
            session, name=body.name, seed=body.seed, actor=user.username, scope=body.scope.model_dump(),
            description=body.description, platform_hint=body.platform_hint, full_name=body.full_name, login=body.login,
        )
    except InvalidReference as exc:
        raise HTTPException(422, str(exc)) from exc
    ran = disc.run_pending(session, investigation_id=inv.id, actor=user.username) if body.run_now else []
    ent = ent_svc.find_entity(session, ref.canonical_id) if ref is not None else None
    return {
        "investigation": _inv_dict(inv),
        "seed": None if ref is None else {
            "canonical_identifier": ref.canonical_id, "entity_type": ref.entity_type,
            "entity_id": ent_svc.resolve_id(session, ent.id) if ent else None},
        "tasks": [_task_dict(t) for t in tasks], "executed": [_task_dict(t) for t in ran],
    }


@router.get("/investigations", tags=["investigations"], dependencies=[Depends(viewer)])
def list_investigations(session: SessionDep, limit: int = 50, offset: int = 0):
    return [_inv_dict(i) for i in session.scalars(select(Investigation).order_by(Investigation.created_at.desc()).limit(min(limit, 200)).offset(offset))]


@router.get("/investigations/{inv_id}", tags=["investigations"], dependencies=[Depends(viewer)])
def get_investigation(inv_id: str, session: SessionDep):
    inv = session.get(Investigation, inv_id)
    if inv is None:
        raise HTTPException(404, "investigation not found")
    items = session.scalars(select(InvestigationItem).where(InvestigationItem.investigation_id == inv.id)).all()
    by_origin: dict[str, dict[str, int]] = {}
    for it in items:
        by_origin.setdefault(it.item_type, {}).setdefault(it.origin, 0)
        by_origin[it.item_type][it.origin] += 1
    tasks = session.scalars(select(DiscoveryTask).where(DiscoveryTask.investigation_id == inv.id).order_by(DiscoveryTask.depth, DiscoveryTask.created_at)).all()
    ent_ids = [i.item_id for i in items if i.item_type == "entity"]
    entities = session.scalars(select(Entity).where(Entity.id.in_(ent_ids or [""]))).all()
    return {
        **_inv_dict(inv), "notes": inv.notes, "item_counts": by_origin,
        "knowledge_reuse": {
            "reused_observations": by_origin.get("observation", {}).get("reused", 0),
            "new_observations": by_origin.get("observation", {}).get("discovered", 0) + by_origin.get("observation", {}).get("manual", 0),
            "reused_tasks": sum(1 for t in tasks if t.status == "reused"),
            "executed_tasks": sum(1 for t in tasks if t.status == "completed"),
        },
        "tasks": [_task_dict(t) for t in tasks],
        "entities": [prof.entity_dict(e) | {"origin": next((i.origin for i in items if i.item_type == "entity" and i.item_id == e.id), None)} for e in entities],
    }


class NotesIn(BaseModel):
    notes: str


@router.put("/investigations/{inv_id}/notes", tags=["investigations"])
def update_notes(inv_id: str, body: NotesIn, session: SessionDep, user: User = Depends(analyst)):
    inv = session.get(Investigation, inv_id)
    if inv is None:
        raise HTTPException(404, "investigation not found")
    inv.notes = body.notes
    audit(session, user.username, "investigation.notes", "investigation", inv.id)
    return {"id": inv.id, "notes": inv.notes}


@router.get("/investigations/{inv_id}/export", tags=["investigations"])
def export_investigation(inv_id: str, session: SessionDep, user: User = Depends(analyst)):
    try:
        return gov.export_investigation(session, inv_id, user.username)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/investigations/{inv_id}/changes", tags=["investigations"], dependencies=[Depends(viewer)])
def investigation_changes(inv_id: str, session: SessionDep, since: datetime | None = None):
    inv = session.get(Investigation, inv_id)
    if inv is None:
        raise HTTPException(404, "investigation not found")
    return prof.what_changed_since(session, since or inv.created_at)


class ImportIn(BaseModel):
    batch_id: str | None = None
    authorization_note: str
    records: list[dict]


@router.post("/investigations/{inv_id}/import", tags=["investigations"])
def import_records(inv_id: str, body: ImportIn, session: SessionDep, user: User = Depends(analyst)):
    inv = session.get(Investigation, inv_id)
    if inv is None:
        raise HTTPException(404, "investigation not found")
    outcome = disc.import_observations(session, inv, body.model_dump(), actor=user.username)
    if outcome.error:
        raise HTTPException(422, outcome.error)
    return {"tool_run_id": outcome.tool_run.id, "stats": outcome.stats}


class ViewIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    view_state: dict


@router.get("/investigations/{inv_id}/views", tags=["investigations"], dependencies=[Depends(viewer)])
def list_views(inv_id: str, session: SessionDep):
    return [{"id": v.id, "name": v.name, "view_state": v.view_state, "created_by": v.created_by, "created_at": v.created_at.isoformat()}
            for v in session.scalars(select(InvestigationView).where(InvestigationView.investigation_id == inv_id).order_by(InvestigationView.created_at.desc()))]


@router.post("/investigations/{inv_id}/views", tags=["investigations"])
def save_view(inv_id: str, body: ViewIn, session: SessionDep, user: User = Depends(analyst)):
    if session.get(Investigation, inv_id) is None:
        raise HTTPException(404, "investigation not found")
    v = InvestigationView(investigation_id=inv_id, name=body.name, view_state=body.view_state, created_by=user.username)
    session.add(v)
    session.flush()
    return {"id": v.id, "name": v.name}


# ---------- discovery tasks ----------
def _task_dict(t: DiscoveryTask) -> dict:
    return {
        "id": t.id, "investigation_id": t.investigation_id, "provider": t.provider_name, "category": t.category, "target_type": t.target_type,
        "target": t.target_identifier, "target_entity_id": t.target_entity_id, "depth": t.depth, "status": t.status, "status_reason": t.status_reason,
        "attempts": t.attempts, "priority": t.priority, "parent_task_id": t.parent_task_id,
        "originating_observation_id": t.originating_observation_id, "created_at": t.created_at.isoformat(),
        "completed_at": t.completed_at.isoformat() if t.completed_at else None, "result_summary": t.result_summary, "decided_by": t.decided_by,
    }


@router.get("/discovery/tasks", tags=["discovery"], dependencies=[Depends(viewer)])
def list_tasks(session: SessionDep, investigation_id: str | None = None, status: str | None = None, limit: int = 100, offset: int = 0):
    stmt = select(DiscoveryTask)
    if investigation_id:
        stmt = stmt.where(DiscoveryTask.investigation_id == investigation_id)
    if status:
        stmt = stmt.where(DiscoveryTask.status.in_(csv_set(status) or set()))
    return [_task_dict(t) for t in session.scalars(stmt.order_by(DiscoveryTask.created_at.desc()).limit(min(limit, 300)).offset(offset))]


class TaskDecisionIn(BaseModel):
    approve: bool
    reason: str = ""


@router.post("/discovery/tasks/{task_id}/decision", tags=["discovery"])
def decide_task(task_id: str, body: TaskDecisionIn, session: SessionDep, user: User = Depends(analyst)):
    t = session.get(DiscoveryTask, task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    try:
        disc.decide_task(session, t, body.approve, user.username, body.reason)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _task_dict(t)


class RunIn(BaseModel):
    investigation_id: str | None = None
    limit: int = Field(default=25, ge=1, le=200)


@router.post("/discovery/run", tags=["discovery"])
def run_discovery(body: RunIn, session: SessionDep, user: User = Depends(analyst)):
    """Synchronous drain of due tasks (the background worker does the same loop continuously)."""
    done = disc.run_pending(session, limit=body.limit, investigation_id=body.investigation_id, actor=user.username)
    return {"executed": [_task_dict(t) for t in done]}


@router.post("/discovery/tasks/{task_id}/retry", tags=["discovery"])
def retry_dead_task(task_id: str, session: SessionDep, user: User = Depends(analyst)):
    t = session.get(DiscoveryTask, task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    if t.status != "dead":
        raise HTTPException(409, "only dead-lettered tasks can be retried manually")
    t.status, t.attempts, t.next_attempt_at, t.completed_at = "queued", 0, None, None
    audit(session, user.username, "discovery.retry", "discovery_task", t.id)
    return _task_dict(t)


# ---------- graph ----------
def _filters(entity_types, relationship_types, statuses, source_id, since, until, include_sources, investigation_id):
    from app.api.deps import parse_graph_filters

    return parse_graph_filters(entity_types, relationship_types, statuses, source_id, since, until, include_sources, investigation_id)


@router.get("/graph/view", tags=["graph"], dependencies=[Depends(viewer)])
def graph_view(
    session: SessionDep, roots: str | None = None, investigation_id: str | None = None, entity_types: str | None = None,
    relationship_types: str | None = None, statuses: str | None = None, source_id: str | None = None, since: datetime | None = None,
    until: datetime | None = None, include_sources: bool = False, limit: int = 100,
):
    f = _filters(entity_types, relationship_types, statuses, source_id, since, until, include_sources, investigation_id)
    return graph_svc.initial_view(session, f, list(csv_set(roots) or []), limit).as_dict()


@router.get("/graph/neighbors/{entity_id}", tags=["graph"], dependencies=[Depends(viewer)])
def graph_neighbors(
    entity_id: str, session: SessionDep, entity_types: str | None = None, relationship_types: str | None = None, statuses: str | None = None,
    source_id: str | None = None, since: datetime | None = None, until: datetime | None = None, include_sources: bool = False,
    investigation_id: str | None = None, limit: int = 50,
):
    if session.get(Entity, entity_id) is None:
        raise HTTPException(404, "entity not found")
    f = _filters(entity_types, relationship_types, statuses, source_id, since, until, include_sources, investigation_id)
    return graph_svc.neighbors(session, ent_svc.resolve_id(session, entity_id), f, limit).as_dict()


@router.get("/graph/path", tags=["graph"], dependencies=[Depends(viewer)])
def graph_path(session: SessionDep, start: str, end: str, max_depth: int = 4, relationship_types: str | None = None, statuses: str | None = None):
    for eid in (start, end):
        if session.get(Entity, eid) is None:
            raise HTTPException(404, f"entity {eid} not found")
    f = _filters(None, relationship_types, statuses, None, None, None, False, None)
    return graph_svc.find_path(session, start, end, f, max_depth)


@router.get("/graph/search", tags=["graph"], dependencies=[Depends(viewer)])
def graph_search(session: SessionDep, q: str, limit: int = 20):
    return graph_svc.search_nodes(session, q, min(limit, 50))


@router.get("/search", tags=["search"], dependencies=[Depends(viewer)])
def global_search(session: SessionDep, q: str, limit: int = 20):
    """Entities + observations text search (simple substring match; full-text indexing is PLANNED)."""
    from sqlalchemy import cast, func, String

    like = f"%{q.lower()}%"
    ents = graph_svc.search_nodes(session, q, min(limit, 50))
    obs = session.scalars(select(Observation).where(func.lower(cast(Observation.normalized_payload, String)).like(like)).limit(min(limit, 50))).all()
    return {"entities": ents, "observations": [prof.obs_summary(o) for o in obs]}


# ---------- LLM Wiki ----------
@router.get("/wiki/pages", tags=["wiki"], dependencies=[Depends(viewer)])
def wiki_pages(session: SessionDep):
    return wiki_svc.list_pages(session)


@router.get("/wiki/lint", tags=["wiki"], dependencies=[Depends(viewer)])
def wiki_lint(session: SessionDep):
    problems = wiki_svc.lint(session)
    return {"ok": not problems, "problems": problems}


@router.post("/wiki/sync", tags=["wiki"])
def wiki_sync(session: SessionDep, user: User = Depends(analyst)):
    return wiki_svc.sync_from_disk(session, user.username)


@router.get("/wiki/page", tags=["wiki"], dependencies=[Depends(viewer)])
def wiki_page(path: str, session: SessionDep):
    page = wiki_svc.get_page(session, path)
    if page is None:
        raise HTTPException(404, "wiki page not found (run POST /wiki/sync?)")
    return page


class WikiWriteIn(BaseModel):
    path: str
    content: str = Field(min_length=1, max_length=200_000)
    note: str = Field(min_length=3)


@router.put("/wiki/page", tags=["wiki"])
def wiki_write(body: WikiWriteIn, session: SessionDep, user: User = Depends(admin)):
    try:
        return wiki_svc.write_page(session, body.path, body.content, user.username, body.note)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
