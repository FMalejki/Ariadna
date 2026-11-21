# Glossary

STATUS: IMPLEMENTED

| Term | Meaning |
|---|---|
| Observation | An append-only statement a source made or a provider returned, with times, parser version and provenance. |
| Source | A place information lives (URL, profile, address). Has reliability and collection status. |
| Entity | A canonical thing (account, channel, website, domain, address, transaction…) with a unique canonical identifier. |
| Relationship | A typed edge between entities whose status is derived from evidence. |
| Evidence | A row linking a relationship to an observation, marked supporting or contradicting. |
| Tool run | One execution of a provider, with input key, status and raw artifact. |
| Artifact | The raw captured payload, content-addressed by SHA-256. |
| Provider | An adapter around a tool or API. Never writes to the database. |
| Claim | An LLM-extracted, unverified statement awaiting analyst review. |
| Match candidate | A possible identity match proposed by a deterministic rule. Not a merge. |
| Merge | A soft, reversible combination of two entities recorded in `entity_merges`. |
| Verified | Checked deterministically against a blockchain provider. Nothing else is called verified. |
| Seed | The starting reference of an investigation. |
| Task | A planned provider execution in the durable `discovery_tasks` queue. |
| Reuse | Satisfying a task from an earlier successful run instead of calling the provider. |
| Project Wiki | `docs/LLM_WIKI`: engineering memory for humans and AI assistants. |
| Intelligence KB | The database of entities, observations and relationships. Separate from the Project Wiki. |
| ADR | Architecture Decision Record in `decisions/`. |
