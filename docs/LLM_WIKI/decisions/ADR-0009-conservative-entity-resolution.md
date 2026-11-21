# ADR-0009: Conservative entity resolution

STATUS: IMPLEMENTED

**Context.** Similar usernames and addresses on different platforms are common and misleading.

**Decision.** Entities are matched only on exact canonical identifiers. Similarity (for example the same username on another platform from Sherlock) produces a `proposed` `USERNAME_MATCHES_PROFILE` relationship and a pending `match_candidate`, never an alias or merge. Accepting a cross-type candidate records a confirmed alias, not a merge. Wallet ownership is never attributed.

**Consequences.** Low false-merge risk; more analyst review.

Status: Accepted.
