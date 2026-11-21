"""Discovery Engine (spec 3.1.B, 15).

Seed -> tasks -> provider runs -> new references -> controlled follow-up tasks.

Controls (all enforced here, all tested):
  * depth limit            min(investigation.scope.max_depth, settings.max_discovery_depth)
  * deduplication          UNIQUE(investigation_id, dedup_key)  => loops and repeats are impossible
  * provider routing       only enabled, implemented providers whose `supports(ref)` is true
  * policy                 SourcePolicy rows (category/domain/provider): allow | require_approval | deny;
                           default for unknown categories is REQUIRE_APPROVAL unless in settings allowlist
  * scope allowlists       investigation.scope.allowed_providers / allowed_domains
  * knowledge reuse        a recent successful ToolRun for the same (provider, target) satisfies a task
                           without re-running the provider (status REUSED) - the "memory" in action
  * rate limits / quotas   enforced per provider in runner.check_limits; exceeded => task re-queued, not dropped
  * retries                bounded attempts with backoff; exhausted => DEAD (dead-letter)
A discovered URL is a candidate, not permission to collect everything behind it.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import audit
from app.config import get_settings
from app.db import utcnow
from app.domain import EntityType, PolicyAction, TaskStatus, ToolRunStatus
from app.models import DiscoveryTask, Entity, Investigation, ObservationEntity, Provider, SourcePolicy, ToolRun
from app.normalize import InvalidReference, Reference, classify_seed
from app.providers.base import ProviderContext, ProviderInput
from app.providers.registry import all_providers, get_provider, sync_providers
from app.services import entities as ent_svc
from app.services import ingest as ingest_svc
from app.services import runner

log = logging.getLogger("osint.discovery")

DEFAULT_SCOPE = {"max_depth": 1, "allowed_providers": None, "allowed_domains": None}
TERMINAL = {
    TaskStatus.COMPLETED.value, TaskStatus.REUSED.value, TaskStatus.DEAD.value, TaskStatus.REJECTED.value, TaskStatus.SKIPPED.value,
}


def effective_max_depth(inv: Investigation) -> int:
    scope = {**DEFAULT_SCOPE, **(inv.scope or {})}
    return min(int(scope["max_depth"]), get_settings().max_discovery_depth)


def reference_from_entity(ent: Entity) -> Reference | None:
    """Rebuild a Reference from a stored entity (for routing follow-up tasks)."""
    meta = ent.meta or {}
    attrs = {k: v for k, v in meta.items() if k not in ("platform", "network", "url")}
    return Reference(
        entity_type=ent.entity_type, canonical_id=ent.canonical_identifier, display_name=ent.display_name,
        source_type="", raw=ent.canonical_identifier, platform=meta.get("platform"), network=meta.get("network"),
        url=meta.get("url"), attrs=attrs,
    )


def _policy_for(session: Session, provider_name: str, category: str, domain: str | None) -> tuple[str, str]:
    """Most specific policy wins: domain > provider > category. Returns (action, reason)."""
    rows = list(session.scalars(select(SourcePolicy)))
    def find(scope: str, pattern: str | None):
        if pattern is None:
            return None
        return next((r for r in rows if r.scope == scope and r.pattern == pattern), None)
    for scope, pattern in (("domain", domain), ("provider", provider_name), ("category", category)):
        r = find(scope, pattern)
        if r is not None:
            return r.action, f"policy {scope}:{pattern} -> {r.action}"
    if category in get_settings().auto_approve_categories or category == "import":
        return PolicyAction.ALLOW.value, f"category {category} is auto-approved by configuration"
    return PolicyAction.REQUIRE_APPROVAL.value, f"category {category} has no allow policy; manual approval required"


def dedup_key(provider_name: str, ref: Reference) -> str:
    return f"{provider_name}|{ref.entity_type}|{ref.canonical_id}"


def _domain_of(ref: Reference) -> str | None:
    return ref.attrs.get("host") if ref.entity_type in (EntityType.WEBSITE.value, EntityType.DOMAIN.value) else None


def create_tasks_for_reference(
    session: Session,
    inv: Investigation,
    ref: Reference,
    *,
    depth: int,
    originating_observation_id: str | None,
    parent_task_id: str | None,
    target_entity_id: str | None,
    actor: str,
) -> list[DiscoveryTask]:
    """Route one reference to capable providers and create (deduplicated, policy-checked) tasks."""
    if depth > effective_max_depth(inv):
        return []
    scope = {**DEFAULT_SCOPE, **(inv.scope or {})}
    created: list[DiscoveryTask] = []
    rows = {p.name: p for p in sync_providers(session)}
    for prov in all_providers():
        row = rows[prov.name]
        if prov.implementation_status != "implemented" or not row.enabled or not prov.supports(ref):
            continue
        if scope["allowed_providers"] is not None and prov.name not in scope["allowed_providers"]:
            continue
        domain = _domain_of(ref)
        if scope["allowed_domains"] is not None and domain is not None and domain not in scope["allowed_domains"]:
            continue
        key = dedup_key(prov.name, ref)
        if session.scalar(select(DiscoveryTask.id).where(DiscoveryTask.investigation_id == inv.id, DiscoveryTask.dedup_key == key)):
            continue  # already planned / done in this investigation -> no loops, no repeats
        action, reason = _policy_for(session, prov.name, prov.category, domain)
        status = {
            PolicyAction.ALLOW.value: TaskStatus.QUEUED.value,
            PolicyAction.REQUIRE_APPROVAL.value: TaskStatus.PENDING_APPROVAL.value,
            PolicyAction.DENY.value: TaskStatus.SKIPPED.value,
        }[action]
        task = DiscoveryTask(
            investigation_id=inv.id, dedup_key=key, target_type=ref.entity_type, target_identifier=ref.canonical_id,
            target_entity_id=target_entity_id, originating_observation_id=originating_observation_id,
            parent_task_id=parent_task_id, provider_id=row.id, provider_name=prov.name, category=prov.category,
            priority=max(0, 50 - depth * 10), depth=depth, status=status, status_reason=reason,
            scheduled_at=utcnow() if status == TaskStatus.QUEUED.value else None,
        )
        try:
            with session.begin_nested():
                session.add(task)
                session.flush()
        except IntegrityError:
            continue
        created.append(task)
    return created


def start_investigation(
    session: Session, *, name: str, seed: str, actor: str, scope: dict | None = None, description: str = "", platform_hint: str | None = None
) -> tuple[Investigation, Reference, list[DiscoveryTask]]:
    ref = classify_seed(seed, platform_hint)  # raises InvalidReference
    inv = Investigation(name=name, description=description, scope={**DEFAULT_SCOPE, **(scope or {})}, created_by=actor)
    session.add(inv)
    session.flush()
    audit(session, actor, "investigation.create", "investigation", inv.id, seed=ref.canonical_id)
    ent, created = ent_svc.get_or_create_entity(session, ref, None)  # step 1: reuse existing, else create
    ingest_svc._add_item(session, inv.id, "entity", ent.id, "seed" if created else "reused")
    tasks = create_tasks_for_reference(
        session, inv, ref, depth=0, originating_observation_id=None, parent_task_id=None, target_entity_id=ent.id, actor=actor
    )
    return inv, ref, tasks


def decide_task(session: Session, task: DiscoveryTask, approve: bool, actor: str, reason: str = "") -> DiscoveryTask:
    if task.status != TaskStatus.PENDING_APPROVAL.value:
        raise ValueError(f"task is {task.status}, not pending approval")
    task.status = TaskStatus.QUEUED.value if approve else TaskStatus.REJECTED.value
    task.decided_by = actor
    task.status_reason = f"{'approved' if approve else 'rejected'} by {actor}. {reason}".strip()
    if approve:
        task.scheduled_at = utcnow()
    else:
        task.completed_at = utcnow()
    audit(session, actor, "discovery.approve" if approve else "discovery.reject", "discovery_task", task.id, provider=task.provider_name, target=task.target_identifier)
    return task


def _find_reusable_run(session: Session, provider_name: str, target: str) -> ToolRun | None:
    window = utcnow() - timedelta(hours=get_settings().reuse_window_hours)
    return session.scalar(
        select(ToolRun)
        .where(
            ToolRun.provider_name == provider_name, ToolRun.input_key == target,
            ToolRun.status == ToolRunStatus.SUCCEEDED.value, ToolRun.finished_at > window,
        )
        .order_by(ToolRun.finished_at.desc())
        .limit(1)
    )


def execute_task(session: Session, task: DiscoveryTask, *, ctx: ProviderContext | None = None, actor: str = "worker") -> DiscoveryTask:
    """Run one queued task end-to-end and schedule controlled follow-ups. Safe to call repeatedly (idempotent)."""
    if task.status not in (TaskStatus.QUEUED.value, TaskStatus.FAILED.value):
        return task
    inv = session.get(Investigation, task.investigation_id)
    assert inv is not None
    row = session.get(Provider, task.provider_id)
    assert row is not None
    ent = session.get(Entity, task.target_entity_id) if task.target_entity_id else ent_svc.find_entity(session, task.target_identifier)
    ref = reference_from_entity(ent) if ent else None
    if ref is None:
        task.status, task.status_reason, task.completed_at = TaskStatus.SKIPPED.value, "target entity no longer exists", utcnow()
        return task

    reusable = _find_reusable_run(session, task.provider_name, task.target_identifier)
    if reusable is not None:
        task.status = TaskStatus.REUSED.value
        task.completed_at = utcnow()
        task.result_summary = {"reused_tool_run_id": reusable.id, "reused_observations": len(reusable.observation_ids or []), "provider_executed": False}
        task.status_reason = f"satisfied by earlier run {reusable.id} (within reuse window); provider not called"
        for oid in reusable.observation_ids or []:
            ingest_svc._add_item(session, inv.id, "observation", oid, "reused")
        ingest_svc._add_item(session, inv.id, "tool_run", reusable.id, "reused")
        ingest_svc._add_item(session, inv.id, "entity", ent.id, "reused")
        _spawn_follow_ups_from_observations(session, inv, task, reusable.observation_ids or [], actor)
        return task

    task.status = TaskStatus.RUNNING.value
    task.attempts += 1
    session.flush()
    try:
        outcome = runner.run_provider(
            session, row, ProviderInput(reference=ref), ctx=ctx, investigation_id=inv.id, task_id=task.id, actor=actor
        )
    except runner.RateLimited as exc:
        task.attempts -= 1  # rate limiting is not a failure
        task.status = TaskStatus.QUEUED.value
        task.next_attempt_at = utcnow() + timedelta(seconds=exc.retry_after_seconds)
        task.status_reason = f"deferred: {exc}"
        return task
    ingest_svc._add_item(session, inv.id, "tool_run", outcome.tool_run.id, "discovered")

    if outcome.error is None:
        task.status, task.completed_at = TaskStatus.COMPLETED.value, utcnow()
        task.result_summary = {"tool_run_id": outcome.tool_run.id, "provider_executed": True, **outcome.stats}
        task.status_reason = None
        if outcome.ingest is not None:
            _spawn_follow_ups(session, inv, task, outcome.ingest, actor)
    elif outcome.unavailable:
        task.status, task.completed_at = TaskStatus.SKIPPED.value, utcnow()
        task.status_reason = f"provider unavailable: {outcome.error}"
        task.result_summary = {"tool_run_id": outcome.tool_run.id, "provider_executed": False, "unavailable": True}
    else:
        settings = get_settings()
        task.status_reason = outcome.error
        task.result_summary = {"tool_run_id": outcome.tool_run.id, "error": outcome.error}
        if outcome.retryable and task.attempts < settings.task_max_attempts:
            task.status = TaskStatus.FAILED.value
            task.next_attempt_at = utcnow() + timedelta(seconds=settings.task_retry_backoff_seconds * task.attempts)
        else:
            task.status, task.completed_at = TaskStatus.DEAD.value, utcnow()  # dead-letter
    return task


def _spawn_follow_ups(session: Session, inv: Investigation, task: DiscoveryTask, result: ingest_svc.IngestResult, actor: str) -> None:
    for d in result.discovered:
        create_tasks_for_reference(
            session, inv, d.reference, depth=task.depth + 1, originating_observation_id=d.observation.id,
            parent_task_id=task.id, target_entity_id=d.entity.id, actor=actor,
        )


def _spawn_follow_ups_from_observations(session: Session, inv: Investigation, task: DiscoveryTask, obs_ids: list[str], actor: str) -> None:
    """When a task was satisfied from memory, follow-ups still derive from the remembered observations."""
    for oid in obs_ids:
        for oe in session.scalars(select(ObservationEntity).where(ObservationEntity.observation_id == oid, ObservationEntity.role == "mentioned")):
            ent = session.get(Entity, ent_svc.resolve_id(session, oe.entity_id))
            ref = reference_from_entity(ent) if ent else None
            if ref is not None:
                create_tasks_for_reference(
                    session, inv, ref, depth=task.depth + 1, originating_observation_id=oid, parent_task_id=task.id,
                    target_entity_id=ent.id, actor=actor,
                )
            ingest_svc._add_item(session, inv.id, "entity", oe.entity_id, "reused")


def run_pending(session: Session, *, limit: int = 50, investigation_id: str | None = None, ctx: ProviderContext | None = None, actor: str = "worker") -> list[DiscoveryTask]:
    """Drain due tasks (queued, or failed with backoff elapsed) in priority order. Follow-ups created during the
    drain are picked up in the same call; depth limits + dedup keep it finite."""
    done: list[DiscoveryTask] = []
    for _ in range(limit):
        now = utcnow()
        stmt = select(DiscoveryTask).where(
            DiscoveryTask.status.in_([TaskStatus.QUEUED.value, TaskStatus.FAILED.value]),
            (DiscoveryTask.next_attempt_at.is_(None)) | (DiscoveryTask.next_attempt_at <= now),
        )
        if investigation_id:
            stmt = stmt.where(DiscoveryTask.investigation_id == investigation_id)
        task = session.scalar(stmt.order_by(DiscoveryTask.priority.desc(), DiscoveryTask.created_at).limit(1))
        if task is None:
            break
        execute_task(session, task, ctx=ctx, actor=actor)
        session.flush()
        done.append(task)
        if task.status == TaskStatus.QUEUED.value and task.next_attempt_at and task.next_attempt_at > now:
            continue  # deferred by a rate limit; next loop skips it until due
    return done


def import_observations(
    session: Session, inv: Investigation, payload: dict, *, actor: str, ctx: ProviderContext | None = None
) -> runner.RunOutcome:
    """Authorized manual import (acceptance step 3 alternative). Imported references seed controlled follow-ups."""
    row = next(p for p in sync_providers(session) if p.name == "manual_import")
    outcome = runner.run_provider(session, row, ProviderInput(reference=None, payload=payload), ctx=ctx, investigation_id=inv.id, actor=actor)
    ingest_svc._add_item(session, inv.id, "tool_run", outcome.tool_run.id, "manual")
    audit(session, actor, "import.run", "tool_run", outcome.tool_run.id, investigation=inv.id, status=outcome.tool_run.status)
    if outcome.ingest is not None:
        task = DiscoveryTask(  # synthetic parent so follow-ups carry depth/lineage
            investigation_id=inv.id, dedup_key=f"manual_import|{outcome.tool_run.id}", target_type="import", target_identifier=payload.get("batch_id") or outcome.tool_run.id,
            provider_id=row.id, provider_name="manual_import", category="import", depth=0, status=TaskStatus.COMPLETED.value, completed_at=utcnow(),
            result_summary={"tool_run_id": outcome.tool_run.id, **outcome.stats}, status_reason="analyst-initiated import",
        )
        session.add(task)
        session.flush()
        _spawn_follow_ups(session, inv, task, outcome.ingest, actor)
    return outcome


__all__ = [
    "create_tasks_for_reference", "start_investigation", "decide_task", "execute_task", "run_pending", "import_observations",
    "InvalidReference", "get_provider",
]
