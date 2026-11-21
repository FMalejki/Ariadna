# Entity types and canonical identifiers

STATUS: IMPLEMENTED (`backend/app/domain.py` `EntityType`; `backend/app/normalize.py`; `tests/test_normalize.py`)

## Types

`social_account`, `public_profile`, `channel`, `website`, `domain`, `wallet`, `token`, `transaction`, `contract`, `post`, `organization`, `document`.

## Canonical identifier formats (ADR-0001)

| Kind | Canonical id | Notes |
|---|---|---|
| X account | `x:<handle>` | Lowercased, `@` stripped, 1–15 chars `[A-Za-z0-9_]`. |
| X post | `x:post:<id>` | Numeric status id. |
| Telegram | `telegram:<name>`, `telegram:<name>/<msgid>` | Only from links (`t.me/...`); invite/share links are skipped. Telegram is never scraped. |
| Other public profiles | `github:<user>`, `instagram:<user>`, `tiktok:<user>`, `reddit:<user>`, `medium:<user>`, `warpcast:<user>`, `youtube:<@handle or channel-id>` | Classified from URLs (type `public_profile` or `channel`). Classification only: no provider collects from these platforms. |
| Website | `website:<host>/<path>[?query]` | Scheme and `www.` removed, `utm_*` and known tracking params dropped, host IDNA-encoded. |
| Domain | `domain:<host>` | Lowercase host, leading `www.` removed. |
| Address | `addr:<network>:<address>` | Wallets, tokens and contracts share this id space. EVM addresses and hashes lowercased on EVM networks; Solana kept case-sensitive. |
| Transaction | `tx:<network>:<hash>` | EVM `0x` + 64 hex; Solana base58 signature (64 bytes). Parser version is stored on every observation (`PARSER_VERSION`). |

## Network `evm`

When only a `0x…` address is known, the chain is not determined, so the network is `evm`. When a provider confirms a concrete chain, the entity is reconciled by a **reversible** `deterministic_network_resolution` merge, never silently rewritten.

## Validation

Invalid or unsupported input raises `InvalidReference` (API: 422). Non-collectable references (for example Telegram content URLs) become `SkippedReference` with a reason. No identifier is invented. Wallet ownership is never inferred from any of these.
