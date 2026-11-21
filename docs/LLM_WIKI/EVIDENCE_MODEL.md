# Evidence model

STATUS: IMPLEMENTED (`backend/app/services/relationships.py`; `tests/test_relationships_and_governance.py`, `tests/test_ingest.py`)

There is **no confidence score**. A relationship's status is a function of its evidence rows and an analyst review.

## Evidence categories (strength)

| Category | Strength |
|---|---|
| `deterministic_blockchain_verification` | 6 |
| `analyst_confirmed` | 6 |
| `official_platform_data` | 5 |
| `independent_corroboration` | 4 |
| `direct_source_statement` | 3 |
| `tool_generated_discovery` | 2 |
| `llm_extracted_claim` | 1 |
| `unverified_candidate` | lowest |
| `contradictory_evidence` | marks a contradiction |

Strength is an explainable ordering, shown with each evidence item. It is not a probability.

## Status precedence (first match wins)

1. Analyst rejected → `rejected`.
2. Contradicting evidence present → `contradicted` (supporting and contradicting evidence are both preserved and shown).
3. Supporting evidence exists but every supporting observation was superseded by a newer observation of the same source and type that no longer shows it → `historical` (`valid_to` set).
4. At least one deterministic blockchain verification → `verified`.
5. Supporting evidence from at least two distinct sources among strong categories → `corroborated`.
6. At least one supporting strong-category evidence → `supported`.
7. Otherwise (tool discovery, LLM, unverified only) → `proposed`.

## Verification boundary

Only blockchain RPC checks produce `verified`. A found transaction proves it exists, who sent it and what the receipt shows. It does not prove who controls an address. A failed lookup is stored as an observation (`blockchain_lookup_failed`) and is not evidence either way.

## Review

`POST /relationships/{id}/review` with `accepted` adds `analyst_confirmed` evidence anchored to an existing observation. `rejected` sets status without deleting evidence.

## Provenance chain shown in the UI

relationship → evidence → observation (seq, observed/published/retrieved times, parser version) → source (reliability, collection status) → tool run → provider@version → raw artifact (hash, download for analysts).
