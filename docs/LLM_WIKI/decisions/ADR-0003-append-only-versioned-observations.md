# ADR-0003: Append-only, versioned observations

STATUS: IMPLEMENTED

**Context.** Sources change over time, and re-collecting must neither duplicate data nor erase history.

**Decision.** Observations are versioned by `seq` per (source, observation_type). An unchanged `payload_hash` is a no-op (idempotent). A changed payload appends `seq + 1`. Relationships supported only by older sequence numbers become `historical` with `valid_to` set. Batch and authorization metadata for imports live on the tool run, not in the payload, so identical content imported twice stays a no-op. The only sanctioned removal is the audited source-deletion workflow.

**Consequences.** Full history and a clear repeat-ingestion story. Storage grows with source churn.

Status: Accepted. Tests: `test_ingest.py`.
