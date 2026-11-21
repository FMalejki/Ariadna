# Data flows

STATUS: IMPLEMENTED

## 1. Investigation from a seed

1. `POST /api/v1/investigations` normalizes the seed (`normalize.py`). Invalid input returns 422.
2. The seed entity is created or reused. `discovery.create_tasks_for_reference` plans one task per capable, enabled provider.
3. Each task goes through policy: `allow` → `queued`, `require_approval` → `pending_approval`, `deny` → `skipped`.
4. `run_now=true` (or the worker) executes queued tasks. A recent successful `tool_run` with the same `input_key` inside `reuse_window_hours` is **reused** (status `reused`) instead of re-run.
5. Provider output (`NormalizedObservation`) goes to `ingest`. New facts append observations; unchanged payloads are no-ops.
6. Observations may spawn follow-up tasks, bounded by `max_discovery_depth` and the investigation scope.

## 2. Ingestion (`services/ingest.py`)

```
NormalizedObservation
  → source get-or-create (canonical key)
  → payload_hash compare with latest seq for (source, observation_type)
      same  → no-op (idempotent)
      new   → append observation seq+1
  → entities get-or-create (exact canonical id only)
  → relationships get-or-create; add evidence rows
  → status recomputed from evidence (relationships.compute_status)
  → older seq no longer shows a relationship → evidence stale → HISTORICAL
  → conflicting exclusive relationships → both CONTRADICTED
```

## 3. Second, overlapping investigation (MVP demo)

Run investigation A, then B with overlapping sources. B's response and `GET /investigations/{id}` report `knowledge_reuse` (`reused_observations`, `new_observations`, `reused_tasks`, `executed_tasks`). Covered by `tests/test_discovery.py` and `tests/test_api.py`.

## 4. Manual import

`POST /investigations/{id}/import` with an `authorization_note` and records. Processed by the `manual_import` provider, never auto-routed. Batch and authorization metadata go on the tool run, not in the observation payload, so re-importing the same records in a new batch is a no-op.

## 5. LLM claim extraction

`POST /observations/{id}/extract-claims` → validated JSON → `claims` rows (`review_status=pending`, `verification_status=unverified`). No relationship or entity is created. See [LLM_POLICY](LLM_POLICY.md).
