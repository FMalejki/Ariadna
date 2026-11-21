"""Provider execution: ToolRun bookkeeping, rate limits / quotas, artifact preservation, ingestion.

The only place that calls `IntelligenceProvider.execute`. Failures are recorded, never swallowed:
  ProviderUnavailable -> ToolRun UNAVAILABLE (not retried; the source is NOT replaced by unsupported scraping)
  ProviderTimeout     -> ToolRun TIMED_OUT (retryable)
  ProviderError       -> ToolRun FAILED    (retryable)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.artifacts import save_artifact
from app.config import get_settings
from app.db import utcnow
from app.domain import CollectionStatus, ToolRunStatus
from app.models import Provider, ProviderUsage, Source, ToolRun
from app.providers.base import (
    IntelligenceProvider,
    ProviderContext,
    ProviderError,
    ProviderInput,
    ProviderInputError,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.providers.registry import get_provider
from app.services import ingest as ingest_svc

log = logging.getLogger("osint.runner")


class RateLimited(Exception):
    def __init__(self, reason: str, retry_after_seconds: int):
        super().__init__(reason)
        self.retry_after_seconds = retry_after_seconds


@dataclass
class RunOutcome:
    tool_run: ToolRun
    ingest: ingest_svc.IngestResult | None = None
    error: str | None = None
    retryable: bool = False
    unavailable: bool = False
    stats: dict = field(default_factory=dict)


def check_limits(session: Session, row: Provider) -> None:
    now = utcnow()
    last_minute = session.scalar(
        select(func.count()).select_from(ProviderUsage).where(ProviderUsage.provider_id == row.id, ProviderUsage.at > now - timedelta(minutes=1))
    )
    if (last_minute or 0) >= row.rate_limit_per_minute:
        raise RateLimited(f"rate limit {row.rate_limit_per_minute}/min reached for {row.name}", 60)
    last_day = session.scalar(
        select(func.count()).select_from(ProviderUsage).where(ProviderUsage.provider_id == row.id, ProviderUsage.at > now - timedelta(days=1))
    )
    if (last_day or 0) >= row.daily_quota:
        raise RateLimited(f"daily quota {row.daily_quota} reached for {row.name}", 3600)


def run_provider(
    session: Session,
    row: Provider,
    data: ProviderInput,
    *,
    ctx: ProviderContext | None = None,
    investigation_id: str | None = None,
    task_id: str | None = None,
    impl: IntelligenceProvider | None = None,
    actor: str = "system",
) -> RunOutcome:
    impl = impl or get_provider(row.name)
    settings = get_settings()
    ctx = ctx or ProviderContext(settings=settings)
    if not row.enabled:
        raise ProviderInputError(f"provider {row.name} is disabled")
    check_limits(session, row)

    params = {"actor": actor, "investigation_id": investigation_id}
    run = ToolRun(
        provider_id=row.id, provider_name=impl.name, provider_version=impl.version, task_id=task_id,
        input_key=data.target,
        input_refs={
            "target": data.target,
            "entity_type": data.reference.entity_type if data.reference else None,
            "records": len(data.payload.get("records", [])) if data.payload else None,
        },
        parameters=params,
    )
    session.add(run)
    session.add(ProviderUsage(provider_id=row.id))
    session.flush()
    outcome = RunOutcome(run)
    try:
        impl.validate_input(data)
        raw = impl.execute(data, ctx)
        run.exit_code, run.log = raw.exit_code, (raw.log or "")[:4000]
        run.parameters = {**params, **raw.parameters}
        art = save_artifact(
            session, raw.raw, source_id=None, content_type=raw.content_type, capture_method=f"provider:{impl.name}",
            metadata={"provider": impl.name, "provider_version": impl.version, "tool_run_id": run.id},
        )
        run.raw_artifact_id = art.id
        observations = impl.normalize_result(raw, data, ctx)
        with session.begin_nested():  # a failed ingest rolls back its writes but not the ToolRun record
            result = ingest_svc.ingest(
                session, observations, provider=row, tool_run_id=run.id, raw_artifact_id=art.id,
                evidence_default=impl.evidence_class.value, investigation_id=investigation_id, retrieved_at=ctx.now or utcnow(), actor=actor,
            )
        run.observation_ids = list(result.touched_observation_ids)
        run.status = ToolRunStatus.SUCCEEDED.value
        outcome.ingest = result
        outcome.stats = result.stats.as_dict()
    except ProviderInputError as exc:
        run.status, run.error = ToolRunStatus.FAILED.value, f"invalid input: {exc}"
        outcome.error = run.error
    except ProviderUnavailable as exc:
        run.status, run.error = ToolRunStatus.UNAVAILABLE.value, str(exc)
        outcome.error, outcome.unavailable = str(exc), True
        _mark_source_unavailable(session, data)
    except ProviderTimeout as exc:
        run.status, run.error = ToolRunStatus.TIMED_OUT.value, str(exc)
        outcome.error, outcome.retryable = str(exc), True
    except ProviderError as exc:
        run.status, run.error = ToolRunStatus.FAILED.value, str(exc)
        outcome.error, outcome.retryable = str(exc), True
    except Exception as exc:  # recorded and surfaced; never silently swallowed
        log.exception("unexpected provider failure", extra={"provider": impl.name, "tool_run_id": run.id})
        run.status, run.error = ToolRunStatus.FAILED.value, f"unexpected {type(exc).__name__}: {exc}"
        outcome.error = run.error
    run.finished_at = utcnow()
    session.flush()
    return outcome


def _mark_source_unavailable(session: Session, data: ProviderInput) -> None:
    if data.reference is None:
        return
    src = session.scalar(
        select(Source).where(Source.source_type == data.reference.source_type, Source.canonical_key == data.reference.canonical_id)
    )
    if src is not None and src.collection_status == CollectionStatus.PENDING.value:
        src.collection_status = CollectionStatus.UNAVAILABLE.value
        src.last_checked_at = utcnow()
