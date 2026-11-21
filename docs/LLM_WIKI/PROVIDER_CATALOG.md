# Provider catalog

STATUS: PARTIALLY_IMPLEMENTED

A provider is an adapter that takes a normalized reference and returns `NormalizedObservation`s plus a raw artifact. Providers never write to the database. Registered in `backend/app/providers/registry.py`; rows are synced into the `providers` table at startup.

| Provider | Category | Status | What is actually verified |
|---|---|---|---|
| `manual_import` | `import` | IMPLEMENTED | Fully tested (`test_discovery.py`, `test_providers.py`). Never auto-routed. No network. |
| `evm_rpc` | `blockchain_verification` | IMPLEMENTED against **mocked** JSON-RPC | Tested with mocked responses only. Not run against a live node. Needs `OSINT_EVM_RPC_URLS`. Official docs: https://ethereum.org/en/developers/docs/apis/json-rpc/ |
| `solana_rpc` | `blockchain_verification` | IMPLEMENTED against **mocked** JSON-RPC | Same caveat. Needs `OSINT_SOLANA_RPC_URL`. |
| `sherlock` | `username_lookup` | PARTIALLY_IMPLEMENTED | CLI wrapper and output parser tested with fixtures. The `sherlock` binary was **not installed** during development, so it was never run live. Hits are `proposed` only and create a match candidate, never an alias or merge. https://github.com/sherlock-project/sherlock |
| `maigret` | `username_lookup` | PLANNED (placeholder) | No code. Disabled, never routed. Enabling returns HTTP 409. Reason: its profile "dossier" extraction can exceed the platform's privacy scope. |
| `spiderfoot` | `domain_footprint` | PLANNED (placeholder) | No code. Needs a pinned passive-module allowlist. |
| `maltego` | n/a | PLANNED (placeholder) | No code. |
| `web_search` | `web_collection` | PLANNED (placeholder) | No code. |

## Contract (`providers/base.py`)

`supports(ref)`, `validate_input`, `execute` → `RawResult`, `parse` → `NormalizedObservation[]`, `health_check`, plus `category`, `evidence_class`, `capabilities`, `implementation_status`, `docs_url`. Failures raise `ProviderUnavailable` / `ProviderInputError` and become a failed `tool_run`; a provider failure never corrupts stored knowledge (`test_providers.py`, `test_discovery.py`).

## Rate limits and quotas

Per-provider `rate_limit_per_minute` and `daily_quota` (defaults from settings) are enforced against `provider_usage` before execution. Editable by admins (`PATCH /providers/{name}`).

## Adding a provider

1. Subclass `IntelligenceProvider`, set `implementation_status` honestly.
2. Register in `registry.py`. 3. Add tests with mocked I/O. 4. Update this page and IMPLEMENTATION_STATUS. 5. Add an ADR if it changes policy.
