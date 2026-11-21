# START HERE

STATUS: IMPLEMENTED (this page describes the process; the project it describes is PARTIALLY_IMPLEMENTED, see IMPLEMENTATION_STATUS.md)

This is the entry point for any AI model or engineer. The Project Wiki explains the **project itself**. It is not the Intelligence Knowledge Base, which is the PostgreSQL database that holds observations, entities and relationships.

## Reading order

1. `/AGENTS.md` (repository root) — the 12 mandatory rules.
2. This page.
3. [PROJECT_OVERVIEW](PROJECT_OVERVIEW.md) and [IMPLEMENTATION_STATUS](IMPLEMENTATION_STATUS.md).
4. The page for the subsystem you will touch (see the map below).
5. [decisions/README.md](decisions/README.md) before changing anything architectural.

## Status markers

Every page that makes a claim about the system uses one of these markers, written as `STATUS: <marker>`:

| Marker | Meaning |
|---|---|
| IMPLEMENTED | Code exists and tests in the repository exercise it. |
| PARTIALLY_IMPLEMENTED | Some of it exists; the page says what is missing. |
| PLANNED | Agreed direction, no code yet. |
| PROPOSED | An idea under discussion, not agreed. |
| UNVERIFIED | Believed true but not confirmed by code, tests or official docs. |
| DEPRECATED | Kept for history; do not build on it. |

`wiki.lint` (`GET /api/v1/wiki/lint`, `tests/test_wiki.py`) fails on unknown markers, missing required pages, or a missing `decisions/README.md`.

## Kinds of statement (keep them separate)

Confirmed implementation (cite file and test) · verified external behavior (cite the official docs) · architectural decision (cite the ADR) · proposed design · assumption · open question · deprecated behavior.

## Model-switching protocol

When you pick up this project, including after a model change:

1. Read `AGENTS.md`.
2. Read START_HERE, PROJECT_OVERVIEW and IMPLEMENTATION_STATUS.
3. Inspect the repository (`backend/app`, `backend/tests`, `frontend/src`, `backend/migrations`).
4. Compare the documentation with the code.
5. Report every discrepancy to the user before continuing, and fix the docs or the code.
6. Do not continue from memory of an earlier conversation.

## Subsystem map

| Subsystem | Code | Read first |
|---|---|---|
| Identifier normalization | `backend/app/normalize.py` | [ENTITY_TYPES](ENTITY_TYPES.md), ADR-0001 |
| Ingestion, entities, relationships | `backend/app/services/{ingest,entities,relationships}.py` | [EVIDENCE_MODEL](EVIDENCE_MODEL.md), [DATA_MODEL](DATA_MODEL.md) |
| Providers | `backend/app/providers/` | [PROVIDER_CATALOG](PROVIDER_CATALOG.md) |
| Discovery, queue, worker | `backend/app/services/discovery.py`, `backend/app/worker.py` | [DISCOVERY_ENGINE](DISCOVERY_ENGINE.md), [SOURCE_POLICIES](SOURCE_POLICIES.md) |
| Graph | `backend/app/services/graph.py`, `frontend/src/app/graph` | [KNOWLEDGE_GRAPH](KNOWLEDGE_GRAPH.md) |
| LLM layer | `backend/app/llm/` | [LLM_POLICY](LLM_POLICY.md) |
| API | `backend/app/api/` | [ARCHITECTURE](ARCHITECTURE.md); OpenAPI at `/docs` |
| Frontend | `frontend/src/` | [SYSTEM_COMPONENTS](SYSTEM_COMPONENTS.md) |
| Security | `backend/app/security.py`, `artifacts.py`, `audit.py` | [SECURITY_AND_PRIVACY](SECURITY_AND_PRIVACY.md) |

## Updating this wiki

Edit the markdown file in `docs/LLM_WIKI/` (the file is the source of truth). `POST /api/v1/wiki/sync` mirrors files into the `wiki_pages`/`wiki_revisions` tables so revisions are queryable. Keep edits small. Add a session note in `sessions/` after each milestone.
