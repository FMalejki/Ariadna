# Roadmap

STATUS: PLANNED (nothing here exists unless IMPLEMENTATION_STATUS says so)

## Near term (validation of what exists)

- Run the stack under Docker Compose and fix whatever breaks.
- Execute the Alembic migration on a real PostgreSQL.
- Browser end-to-end test of the frontend, including the Cytoscape graph.
- Run `sherlock` and the RPC providers against real endpoints (with approval and rate limits).
- Evaluate the LLM layer against a real model.

## Next

- S3/MinIO artifact backend behind `ArtifactStore`.
- Redis-backed queue (RQ or Celery) for multiple workers; scheduled re-collection of stale sources.
- Full-text search (PostgreSQL `tsvector`) replacing substring search.
- httpOnly-cookie sessions, login throttling, user management UI.
- Authenticated, official-API collectors for X (no scraping) and public web pages with robots/ToS checks.

## Later

- Maigret and SpiderFoot adapters with field-level allowlists.
- More chains and richer token-transfer decoding.
- Saved graph views in the UI (the API exists).
- Pagination and virtualization for very large tables.
