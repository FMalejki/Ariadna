# ADR-0007: Artifact store interface with local backend

STATUS: PARTIALLY_IMPLEMENTED

**Context.** Raw provider payloads must be preserved for provenance, ideally in S3-compatible storage.

**Decision.** `ArtifactStore` protocol (`put/get/delete`) with a content-addressed `LocalArtifactStore` (implemented). An S3/MinIO backend is PLANNED behind the same interface. Metadata (hash, type, size, retention) lives in the `artifacts` table; retention purge deletes bytes but keeps metadata.

**Consequences.** Works without MinIO. MinIO is not in Compose until the backend exists.

Status: Accepted.
