# ADR-0006: Database-backed task queue

STATUS: IMPLEMENTED (Redis-backed queue is PLANNED)

**Context.** The spec lists Redis plus a task queue, but the MVP must be correct and runnable with few moving parts.

**Decision.** `discovery_tasks` is the durable queue with retry, backoff and dead-letter. `python -m app.worker` polls it. Redis is provisioned in Docker Compose for the planned RQ/Celery step but **is not used by any code today**.

**Consequences.** No broker required for correctness; tasks survive restarts. Polling latency and multi-worker contention have not been load tested. Adopting Redis requires a new ADR superseding this one.

Status: Accepted.
