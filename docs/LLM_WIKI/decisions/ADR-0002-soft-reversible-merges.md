# ADR-0002: Soft, reversible entity merges

STATUS: IMPLEMENTED

**Context.** Entity resolution will sometimes be wrong; destroying data on merge makes mistakes permanent.

**Decision.** A merge sets `entities.merged_into_id` on the loser and writes an `entity_merges` row (method, reason, actor). Read paths use `resolve_id` and `cluster_ids`. Reversal reactivates the loser, clears the pointer, stamps `reversed_at/by` and removes only the alias the merge created. No observation or relationship is rewritten or deleted.

**Consequences.** Every merge is auditable and undoable. Queries must resolve clusters, which adds a small cost. Tested in `test_ingest.py` and `test_api.py`.

Status: Accepted.
