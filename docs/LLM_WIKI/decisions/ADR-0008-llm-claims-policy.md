# ADR-0008: LLM output is an unverified claim

STATUS: IMPLEMENTED

**Context.** LLMs hallucinate and can be steered by hostile text.

**Decision.** LLM output is stored only as `claims`, schema-validated, with verbatim supporting text, model and prompt version. It never creates relationships, never changes status, and human acceptance does not mark it verified. External text is delimited as untrusted data.

**Consequences.** Safe against silent promotion. Extraction value depends on analyst review time.

Status: Accepted. See [LLM_POLICY](../LLM_POLICY.md).
