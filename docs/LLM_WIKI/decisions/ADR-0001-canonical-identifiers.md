# ADR-0001: Canonical identifier formats

STATUS: IMPLEMENTED

**Context.** Deduplication needs one stable string per real-world object, derived without guessing.

**Decision.** Deterministic formats in `backend/app/normalize.py`: `x:<handle>`, `x:post:<id>`, `telegram:<name>`, `website:<host/path>`, `domain:<host>`, `addr:<network>:<address>` (wallets, tokens and contracts share one id space because an address is one on-chain object per network), `tx:<network>:<hash>`, plus profile forms for other platforms. Network `evm` means "an EVM chain, not yet determined" and is reconciled to a concrete network by a reversible `deterministic_network_resolution` merge (ADR-0002). `PARSER_VERSION` is stored on every observation.

**Consequences.** Exact-match dedup is trivial and safe. A type can change for an address (wallet → token) without a new identity. Normalization changes require a parser version bump.

Status: Accepted. Details: [ENTITY_TYPES](../ENTITY_TYPES.md).
