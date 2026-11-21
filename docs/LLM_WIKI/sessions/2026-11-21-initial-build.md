# Session 2026-11-21: initial build

STATUS: IMPLEMENTED

## Done

- Backend modular monolith: models, Alembic migration, normalization, ingestion, entity resolution, relationships, discovery, providers, LLM layer, governance, API, worker.
- Frontend: Next.js app with the 10 workspace sections, Cytoscape graph, evidence drawer.
- Project wiki, 9 ADRs, `AGENTS.md`, `README.md`, Dockerfiles, Compose file, `.env.example`, `.dockerignore`.

## Commands run and observed results

| Command | Result |
|---|---|
| `pytest -q` (backend, SQLite) | 119 passed, 1 failed before this page existed; the failure was the wiki lint test reporting missing `IMPLEMENTATION_STATUS.md`. |
| `alembic upgrade/downgrade/upgrade` on SQLite | succeeded |
| `npx tsc --noEmit`, `npm run build` | succeeded, 11 routes |
| `docker compose config -q` | succeeded |
| `docker compose build` | backend, worker, frontend images built (213 s). First attempts were interrupted; the build context was 450 MB because `node_modules` was sent, fixed with `.dockerignore`. |

| Live uvicorn + curl demo (SQLite, synthetic) | Overlapping second investigation: 1 duplicate observation, 1 new, 0 new relationships; wiki lint ok. |
| `pytest -q` after wiki completed | 120 passed |

## Not run

`docker compose up`, a real PostgreSQL migration, browser testing, live RPC nodes, `sherlock`, a real LLM.

## Decisions

ADR-0001 to ADR-0009.

## Next

Run the stack, fix what breaks, then the browser walkthrough of the MVP demo (investigation, then an overlapping second one).
