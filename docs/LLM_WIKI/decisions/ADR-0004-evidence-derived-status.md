# ADR-0004: Relationship status derived from evidence; no confidence score

STATUS: IMPLEMENTED

**Context.** Free-floating confidence numbers look precise and hide their basis.

**Decision.** Status is computed from evidence rows by a fixed precedence (`services/relationships.py`), with an explainable strength ordering per evidence category. Conflicts between exclusive sources keep both sides and mark `contradicted`. Only deterministic blockchain checks can yield `verified`.

**Consequences.** Every status can be explained from visible evidence. There is no ranking by score; analysts review instead.

Status: Accepted. See [EVIDENCE_MODEL](../EVIDENCE_MODEL.md).
