# Runbooks

STATUS: PARTIALLY_IMPLEMENTED

Operational procedures. Steps marked "verified" were run; others are untested.

## Run locally (verified on SQLite)

```bash
cd backend && . .venv/bin/activate
OSINT_ADMIN_PASSWORD=... OSINT_JWT_SECRET=... uvicorn app.main:app --port 8000
```

Log in: `POST /api/v1/auth/login {"username":"admin","password":"..."}`. Interactive docs at `/docs`.

## Run the MVP demo through the API (verified with curl, SQLite, synthetic data)

1. Create investigation A: `POST /investigations {"name","seed":"https://x.com/example_handle","scope":{"max_depth":1}}`.
2. Import authorised records: `POST /investigations/{id}/import` (schema in [../schemas/import-payload.md](../schemas/import-payload.md)).
3. Create investigation B with the same seed and import overlapping records plus one new one.
4. `GET /investigations/{B}` → `knowledge_reuse` shows reused versus new observations. Observed in the 2026-11-21 run: 1 duplicate observation, 1 new, 0 new relationships.

## Migrate PostgreSQL (UNVERIFIED)

`cd backend && OSINT_DATABASE_URL=postgresql+psycopg://... alembic upgrade head`. The container entrypoint runs this. Never executed against PostgreSQL so far; report failures and fix `migrations/versions/0001_initial_schema.py`.

## Start the stack (UNVERIFIED)

`docker compose build` is verified. `docker compose up` is not.

## Update the wiki

Edit the markdown, run `pytest tests/test_wiki.py`, then `POST /api/v1/wiki/sync`. Add a note under `sessions/`.

## Recover from a wrong merge

Intelligence KB → Merges → Reverse (or `POST /merges/{id}/reverse`). Nothing is deleted by a merge.

## Dead-lettered tasks

Discovery → filter `dead` → Retry (or `POST /discovery/tasks/{id}/retry`). Read `status_reason` first.
