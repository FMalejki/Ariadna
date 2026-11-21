# Implementation status

STATUS: PARTIALLY_IMPLEMENTED

Honest inventory. "Verified" means a command was actually run and its result observed. Last updated in session [2026-11-21](sessions/2026-11-21-initial-build.md).

## Verified by running

| Area | Evidence |
|---|---|
| Backend logic and API | `cd backend && pytest -q` on SQLite: all tests pass, including the wiki lint test once this page exists. No test touches the network. |
| Migration | `alembic upgrade head`, `downgrade base`, `upgrade head` on SQLite; model/migration drift test passes (`tests/test_migrations.py`). |
| Frontend | `npx tsc --noEmit` and `npm run build` succeed (11 routes). |
| Docker images | `docker compose build` finished for backend, worker and frontend (reported from the user's terminal). |
| MVP demo via API | Live `uvicorn` on SQLite with curl and synthetic data: investigation A import, overlapping investigation B import → `knowledge_reuse` = 1 reused, 1 new observation, 0 new relationships; `/wiki/lint` ok; CORS preflight 200. |
| Compose file syntax | `docker compose config -q` succeeded. |

## Implemented and tested (backend)

URL/identifier normalization · idempotent, versioned ingestion · conservative entity resolution with reversible merges · evidence-derived relationship status incl. contradictions and historical edges · discovery with policy, approval, reuse, depth cap, retry, dead-letter · bounded graph, path and search endpoints · entity profile and timeline · governance (deletion, correction, export) · JWT auth and roles · audit log · wiki mirror and lint · LLM claim layer (with a scripted fake client) · manual import · REST API with OpenAPI at `/docs`.

## Implemented, not verified end to end

| Area | Gap |
|---|---|
| Frontend runtime | Built and type-checked only. Not opened in a browser against a live backend. Graph interactions (expand, edge drawer, path) are untested. |
| Containers running | Images build. `docker compose up`, the Alembic upgrade on PostgreSQL inside the container, worker start and frontend-to-backend connectivity have **not** been verified. |
| PostgreSQL | Migration rendered as PostgreSQL SQL offline; never executed on a PostgreSQL server. Tests run on SQLite. JSONB behavior is untested. |
| EVM and Solana RPC | Tested against mocked JSON-RPC only. Never run against a real node. |
| Sherlock | Wrapper and parser tested with fixtures. The `sherlock` binary was not installed, so it was never run. |
| LLM layer | Tested only with a scripted fake client. Never run against a real model. |

## Placeholders (no integration code)

`maigret`, `spiderfoot`, `maltego`, `web_search`. Listed in the UI as placeholders; cannot be enabled (HTTP 409).

## Provisioned but unused

Redis is in `docker-compose.yml`. No code uses it. MinIO was removed from Compose (its image was not pullable and nothing uses it). The worker polls the database (ADR-0006) and artifacts go to the local filesystem (ADR-0007).

## Not built

S3/MinIO artifact backend · Redis queue · scheduled re-collection · full-text search · official-API collectors for X or web crawling · login throttling and cookie sessions · user management UI · pagination and virtualization for very large result sets · relationship-review audit entries.

## Known minor issues

- `app/api/knowledge.py` re-exports a few unused names through `__all__`.
- Alembic emits a `path_separator` deprecation warning in tests.
- The graph "shortest path" UI needs two clicks (set start, then select end node).
