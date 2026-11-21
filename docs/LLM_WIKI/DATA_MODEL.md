# Data model

STATUS: IMPLEMENTED (`backend/app/models.py`; migration `backend/migrations/versions/0001_initial_schema.py`; `tests/test_migrations.py` checks the migration matches the models)

JSON columns use JSONB on PostgreSQL and JSON elsewhere. Timestamps are timezone-aware (UTC).

## Tables (23)

| Table | Purpose |
|---|---|
| `users` | Accounts with role (`viewer`, `analyst`, `admin`) and scrypt password hash. |
| `providers` | Registered providers: version, kind, capabilities, `implementation_status`, enabled, quotas, last health. |
| `provider_usage` | Per-run usage rows used for rate limits and quotas. |
| `sources` | Where information lives (URL/key), type, reliability, collection status, discovery and last-observed times. |
| `artifacts` | Raw captured payloads: content hash, type, size, storage key. |
| `tool_runs` | One provider execution: input key, parameters, status, error, raw artifact, produced observation ids. |
| `observations` | Append-only statements from a source. Fields include `seq`, `payload_hash`, `normalized_payload`, `observed_at`, `retrieved_at`, `parser_version`, `evidence_type`. |
| `entities` | Canonical entities. `merged_into_id` for soft merges, `status`, `resolution_status`. |
| `entity_aliases` | Alternate identifiers with basis and confirmer. |
| `observation_entities` | Links observations to entities with a role (`subject` or `mentioned`). |
| `relationships` | Typed edges with derived `status`, `review_status`, validity window. |
| `relationship_evidence` | Evidence rows (supports or contradicts) pointing at observations. |
| `entity_merges` | Merge log with method, reason, actor, reversal fields. |
| `match_candidates` | Possible matches awaiting review, with rule and explanation. |
| `investigations`, `investigation_items`, `investigation_views` | Scope, membership and saved graph views. |
| `discovery_tasks` | The durable queue: provider, target, depth, status, attempts, parent, originating observation. |
| `source_policies` | `allow` / `require_approval` / `deny` by category, provider or domain. |
| `claims` | LLM-extracted statements with model, prompt version, review and verification status. |
| `wiki_pages`, `wiki_revisions` | Mirror of the project wiki with revisions. Not intelligence data. |
| `audit_logs` | Who did what, when. |

## Invariants

- Ingestion never updates or deletes observations. A changed source payload adds `seq + 1`. The single sanctioned exception is the admin-only, audited source-deletion workflow (`governance.delete_source`), which removes a source's observations, claims and evidence and marks relationships that lost all support `redacted`.
- Every relationship has at least one `relationship_evidence` row referencing a real observation.
- Entities are matched only on exact canonical identifier. `entities.canonical_identifier` is unique (a database constraint).
- `tool_runs.task_id` is intentionally not a foreign key.
- Deletion workflows (`governance.py`) are admin-only, require a reason and write an audit entry. Relationships that lose all support become `redacted`.

## Schema drift

`tests/test_migrations.py` runs Alembic upgrade/downgrade on SQLite and asserts no model/migration drift. The migration has been rendered as PostgreSQL SQL (`alembic upgrade head --sql`) but **never executed against a real PostgreSQL server**.
