# System components

STATUS: IMPLEMENTED

## Backend modules

| Module | Responsibility | Tests |
|---|---|---|
| `normalize.py` | URL and identifier normalization, classification into canonical references. | `test_normalize.py` |
| `services/ingest.py` | Idempotent, versioned ingestion of provider output. | `test_ingest.py` |
| `services/entities.py` | Entity get-or-create, aliases, match candidates, soft merges and reversal. | `test_ingest.py`, `test_relationships_and_governance.py` |
| `services/relationships.py` | Evidence strength table, status derivation, review. | `test_relationships_and_governance.py` |
| `services/discovery.py` | Tasks, policy, reuse, approval, execution, imports. | `test_discovery.py` |
| `services/graph.py` | Bounded subgraph, neighbors, path, search. | `test_api.py` |
| `services/profile.py` | Entity profile, timeline, relationship detail. | `test_api.py` |
| `services/governance.py` | Source deletion, entity deletion, corrections (audited). | `test_relationships_and_governance.py` |
| `services/wiki.py` | Wiki mirror, safe writes, lint. | `test_wiki.py` |
| `providers/*` | Provider adapters. See [PROVIDER_CATALOG](PROVIDER_CATALOG.md). | `test_providers.py` |
| `llm/*` | Claim extraction and cited summaries. See [LLM_POLICY](LLM_POLICY.md). | `test_llm.py` |
| `artifacts.py` | Content-addressed raw artifact store (local filesystem). | `test_providers.py`, `test_api.py` |
| `security.py`, `audit.py` | JWT, scrypt hashes, roles, audit log. | `test_api.py` |
| `worker.py` | Polling loop that drains due tasks. | `test_discovery.py` (via `run_pending`) |

## Frontend

Next.js App Router, one page per workspace section under `frontend/src/app/`: Overview `/`, Discovery, Knowledge Graph, Entities, Sources, Investigations, Providers, Evidence Center, Intelligence KB, Settings. Shared pieces in `src/components/` (`Shell`, `ui.tsx` with `Badge`, `Drawer`, `RelationshipDrawer`). API client in `src/lib/api.ts`. Server state through TanStack Query.

The frontend compiles (`npm run build`) and type-checks. It has **not** been exercised in a browser against a running backend in this repository's history; see IMPLEMENTATION_STATUS.

## Infrastructure files

`docker-compose.yml`, `backend/Dockerfile`, `frontend/Dockerfile`, `.env.example`. Not yet run (Docker daemon was unavailable).
