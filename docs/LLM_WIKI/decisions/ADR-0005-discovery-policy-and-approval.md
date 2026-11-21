# ADR-0005: Discovery policy and approval

STATUS: IMPLEMENTED

**Context.** Automatic follow-ups can run intrusive or out-of-scope lookups.

**Decision.** Every planned task passes `allow | require_approval | deny` policy (domain > provider > category). Only `blockchain_verification` and `import` are auto-approved by default; all other categories (for example `username_lookup`) wait for an analyst. The server caps discovery depth. Dedup and reuse keep loops finite.

**Consequences.** Safe by default, at the cost of analyst clicks. Policies are editable by admins and audited.

Status: Accepted. See [SOURCE_POLICIES](../SOURCE_POLICIES.md).
