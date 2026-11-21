# Discovery engine

STATUS: IMPLEMENTED (`backend/app/services/discovery.py`, `backend/app/worker.py`; `tests/test_discovery.py`)

## Task lifecycle

```
planned → pending_approval ─approve→ queued → running → completed
                            └reject→ rejected              ├→ failed (retryable, backoff) → … → dead
                                                           ├→ skipped (provider unavailable, e.g. no RPC configured)
                                                           └→ queued again, deferred (rate limit / quota, attempt not counted)
planned → skipped (denied by policy; target entity gone)
queued  → reused  (satisfied by an earlier successful tool run)
(no capable/enabled/implemented provider, or depth over the cap → no task is created)
```

## Rules

- **Deduplication.** `dedup_key = provider|entity_type|canonical_id`, unique per investigation, so the same provider/target is never planned twice within one investigation.
- **Reuse.** A successful `tool_run` with the same `input_key` within `OSINT_REUSE_WINDOW_HOURS` (default 168) satisfies the task as `reused`; the provider is not called.
- **Loops are bounded.** Follow-ups carry `depth + 1` and are dropped past the effective max depth. Cycles cannot repeat because of dedup and reuse. Tested in `test_discovery.py`.
- **Retry.** Retryable failures are retried up to `OSINT_TASK_MAX_ATTEMPTS` with linear backoff (`OSINT_TASK_RETRY_BACKOFF_SECONDS × attempts`); then the task is `dead` (dead-letter) and can be retried manually (`POST /discovery/tasks/{id}/retry`, dead tasks only; others return 409). Non-retryable failures go straight to `dead`.
- **Rate limits.** When a provider's per-minute rate limit or daily quota is hit, the task is deferred and re-queued without counting an attempt.
- **Only implemented, enabled providers** that `supports()` the reference get tasks. Placeholders never do.
- **Sherlock hits** do not spawn follow-ups.
- **Worker.** `python -m app.worker` polls the `discovery_tasks` table. `POST /discovery/run` drains tasks synchronously.

## Not implemented

Redis/RQ or Celery queues (PLANNED, ADR-0006). Scheduled re-collection of stale sources (PLANNED). Multi-worker locking beyond database row status has not been load tested.
