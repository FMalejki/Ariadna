# Source policies

STATUS: IMPLEMENTED (`backend/app/services/discovery.py` `_policy_for`; `tests/test_discovery.py`)

A policy decides whether a planned discovery task may run.

| Action | Effect |
|---|---|
| `allow` | Task is `queued` and runs. |
| `require_approval` | Task is `pending_approval` until an analyst approves or rejects it. |
| `deny` | Task is `skipped` with the reason recorded. |

## Resolution order

Most specific wins: **domain > provider > category**. With no matching policy:

- Categories in `OSINT_AUTO_APPROVE_CATEGORIES` (default `blockchain_verification`) and category `import` → `allow`.
- Everything else (for example `username_lookup`) → `require_approval`.

## Other guards

- Server-side `max_discovery_depth` caps per-investigation scope depth. Scopes can only narrow it.
- Investigation scope can restrict providers and domains.
- Telegram content is never collected; Telegram references are classified only.
- Observations record `collection_status` (`active`, `unavailable`, `restricted`, `stale`). A source a provider cannot reach is marked `unavailable`; the platform never silently falls back to scraping.

Managed in the UI at Settings → Source policies and via `GET/PUT/DELETE /policies` (admin to change).
