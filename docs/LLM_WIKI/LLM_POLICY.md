# LLM policy

STATUS: PARTIALLY_IMPLEMENTED (code and tests exist; tested only with a scripted fake client, **never against a live model**)

## Rules

1. LLM output is an **unverified claim**. It is never promoted to fact, never creates an entity or relationship, and never changes a relationship status.
2. Deterministic code handles everything it can (URL parsing, address validation, blockchain checks). The LLM is used only to extract statements from free text and to write cited summaries.
3. External text is **untrusted data**. Prompts wrap it in `<untrusted_source_text>` tags and tell the model never to follow instructions inside it (`app/llm/prompts.py`). A test checks the delimiting; it cannot prove a model resists injection.
4. Output must be JSON matching a Pydantic schema. Malformed output is rejected wholesale; there is no best-effort repair. Fenced JSON is accepted, prose wrapping is not.
5. Every claim's `supporting_text` must be a verbatim excerpt of the observation (case and whitespace-insensitive). Claims that fail are rejected and reported.
6. Every claim stores model provider, model name and prompt version (`claims-v1`). Re-running the same model and prompt is idempotent. A different model or prompt supersedes older claims but keeps them.
7. Accepting a claim in the UI records a human review only. It does not set `verification_status` to verified and does not create a relationship.
8. Summaries (`POST /entities/{id}/summary`) must cite observation ids that were actually supplied.

## Configuration

`OSINT_LLM_PROVIDER` is `none` (default) or `openai_compatible` with `OSINT_LLM_BASE_URL`, `OSINT_LLM_MODEL`, `OSINT_LLM_API_KEY`. With `none`, the API returns 503 for LLM endpoints and the UI says extraction is unavailable. Nothing is faked.

## Tests

`backend/tests/test_llm.py` (17 tests): claim storage, schema failure, non-verbatim rejection, idempotency, supersession, injection delimiting, disabled client, citation check.

## Not done

Live-model evaluation, prompt-injection red-teaming against a real model, cost controls, per-claim confidence (intentionally absent).
