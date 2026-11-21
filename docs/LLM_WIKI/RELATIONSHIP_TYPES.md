# Relationship types

STATUS: IMPLEMENTED (`backend/app/domain.py` `RelationshipType`)

| Type | Meaning | Exclusive source? |
|---|---|---|
| `ACCOUNT_LINKS_TO_CHANNEL` / `ACCOUNT_LINKS_TO_WEBSITE` | A profile or page links to a channel or website. | no |
| `ACCOUNT_MENTIONS_ACCOUNT` | An account's text mentions another account. | no |
| `ACCOUNT_PUBLISHED_POST` / `CHANNEL_PUBLISHED_POST` | Authorship of a post. | **yes** (one author) |
| `USERNAME_MATCHES_PROFILE` | The same username string exists on another platform. **Not an identity claim.** Always `proposed` from tool discovery. | no |
| `ACCOUNT_PROFILE_REFERENCES_ADDRESS` | A profile text contains an address. Says nothing about ownership. | no |
| `POST_MENTIONS_TOKEN`, `POST_CONTAINS_WALLET`, `POST_REFERENCES_TRANSACTION`, `POST_MENTIONS_ACCOUNT`, `POST_LINKS_TO_WEBSITE`, `POST_LINKS_TO_CHANNEL` | A post references something. | no |
| `PAGE_REFERENCES_ENTITY` | A web page references an entity. | no |
| `WALLET_EXECUTED_TRANSACTION` | The sender recorded on-chain. | **yes** (one sender) |
| `TRANSACTION_SWAPPED_TOKEN`, `TRANSACTION_TRANSFERRED_TOKEN` | Token movement observed in a verified transaction. | no |
| `WEBSITE_HAS_DOMAIN` | Structural containment. | no |
| `SOURCE_DESCRIBES_ENTITY`, `ENTITY_REFERENCED_BY_SOURCE` | **Derived** edges computed by the graph layer from provenance. Not stored in `relationships`. | n/a |

When two sources disagree about an exclusive relationship, both are kept and marked `contradicted`.

## Status values

`proposed`, `supported`, `corroborated`, `verified`, `contradicted`, `rejected`, `historical`, `redacted`. See [EVIDENCE_MODEL](EVIDENCE_MODEL.md).
