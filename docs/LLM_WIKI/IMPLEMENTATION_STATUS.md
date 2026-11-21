# Implementation status

STATUS: PARTIALLY_IMPLEMENTED

Honest inventory. "Verified" means a command was actually run and its result observed. The only session note so far is [2026-11-21](sessions/2026-11-21-initial-build.md); it predates the Lead Search, Automation, redesign and Polish-UI work listed below (a newer session note is still to be written).

## One-paragraph summary

Ariadna is a persistent OSINT intelligence and knowledge-graph platform for crypto analysis (hackathon project for KAS / Ministry of Finance). An analyst starts from a name, a login, a URL, an address or a transaction hash. The system collects observations through replaceable providers, stores them append-only with raw artifacts, and derives relationship status only from evidence (there is no confidence score). A wallet mention on a page is never treated as ownership. LLM output is stored as an unverified claim. The backend (FastAPI + SQLAlchemy + Alembic) and the Polish-language web UI (Next.js) are built and covered by automated tests; end-to-end runs against real external services are mostly unverified (see below).

## Verified by running

| Area | Evidence |
|---|---|
| Backend logic and API | `cd backend && pytest -q` on SQLite: **317 tests pass** (last run after the Polish backend texts). No test touches the network. Includes the wiki lint test and the repeatability, automation, leads and web-provider tests. |
| Migrations | Two Alembic revisions (`0001_initial_schema`, `0002_automation_runs`). Upgrade/downgrade and model/migration drift are covered by `tests/test_migrations.py` on SQLite. |
| Frontend build | `npx tsc --noEmit` and `npm run build` succeed (12 routes: Overview, Leads, Automation, Discovery, Graph, Entities, Sources, Investigations, Evidence, KB, Providers, Settings). |
| Docker images | An earlier `docker compose build` of backend, worker and frontend finished (reported from the user's terminal). Images were rebuilt by the user after the later changes; not re-observed here. |
| MVP demo via API (earlier) | Live `uvicorn` on SQLite with curl and synthetic data: investigation A import, overlapping investigation B import gave `knowledge_reuse` = 1 reused, 1 new observation, 0 new relationships; `/wiki/lint` ok; CORS preflight 200. |

## Implemented and tested (backend)

URL/identifier normalization · idempotent, versioned ingestion · conservative entity resolution with reversible merges · evidence-derived relationship status incl. contradictions and historical edges · discovery with policy, approval, reuse, depth cap, retry, dead-letter · bounded graph, path and search endpoints · entity profile and timeline · governance (deletion, correction, export) · JWT auth and roles (viewer / analyst / admin) · audit log · wiki mirror and lint · LLM claim layer (scripted fake client) · manual import · REST API with OpenAPI at `/docs`.

### Lead Search workflow (tested with mocked HTTP and fixtures)

- `POST /leads/queries/propose`: deterministic query suggestions (no AI); nothing runs until the analyst approves a query.
- `POST /leads/search`: web search via **Tavily** (official keyed API) with **ddgs** as an **unofficial, ToS-gray** fallback; a fallback is always reported, never silent. Results are `tool_generated_discovery` leads only.
- `POST /leads/fetch`: fetches only pages the analyst picked. Respects `robots.txt`, SSRF protection, size limit, no-scrape domain list (X, Telegram, Instagram, Facebook, TikTok, LinkedIn). Extracts Solana / EVM / Bitcoin address candidates with short context; contact data is redacted.
- `POST /leads/track`: records an address as a wallet with a `PAGE_REFERENCES_ENTITY` relation. This is a page mention, not ownership; the analyst must give a reason (audited).
- `POST /leads/onchain`, `/leads/onchain/more`: on-chain facts through `evm_rpc`, `solana_rpc`, `bitcoin_esplora`; "load more" fetches the next history page as a separate provider run with a cursor.
- `POST /leads/expand`, `/leads/graph-expand`: one-step counterparty expansion, at most 20 counterparties, idempotent, creates `WALLET_TRANSACTED_WITH` (an on-chain fact, not shared ownership). Reuses stored lookups; no new node request if data exists.
- **Repeatability level** of a mention: shown in words only (low / medium / high), derived from the number of distinct URLs, never as a percentage, does not change relationship status, always labelled "not ownership; different URLs may copy the same content".

### Automation (stages A to E, tested; background worker)

- Runs are stored in the database and advanced by the worker in time-boxed chunks, so they survive restarts. Endpoints: `GET /automation/config`, `POST/GET /automation/runs`, `GET /automation/runs/{id}`, `POST /automation/runs/{id}/abort`.
- Stages, each switched on independently: **A** Sherlock (login to profile URLs, username-string matches only), **B** web search then fetch then address candidates, **C** track only addresses that have the full name or login within ±120 characters on the same page, **D** on-chain lookup of the tracked wallets, **E** one expansion step.
- Hard limits always apply (queries, pages, addresses, seconds), every step is logged, runs can be aborted, and the result states why it stopped.
- Choosing stage A is recorded as the analyst's explicit approval.

### Discovery input

A full name and a login (stored as a username without a platform) are accepted as discovery inputs besides URL / handle / address / hash.

## Frontend (built, type-checked, not seen in a browser)

- **Polish UI** (`<html lang="pl">`): all pages, navigation, sign-in, drawers, placeholders and aria-labels. API values stay in English (`verified`, `proposed`, `pending_approval`); only labels are translated through `frontend/src/lib/i18n.ts`. Dates use `Intl` with pl-PL.
- **Ariadna design**: light theme, single brand colour (indigo), self-hosted IBM Plex fonts, grouped navigation with a "thread" line. Status colours are reserved for evidence status and are always paired with a glyph and border style, so "proposed" never looks "verified".
- Pages: Overview, Lead Search, Automation, Discovery, Knowledge graph, Entities, Sources, Investigations, Evidence center, Knowledge base, Providers, Settings.
- All warnings are kept in Polish: mention is not ownership, ddgs is unofficial, Sherlock results are username-string matches, repeatability is not a confidence score.
- Texts that still arrive from the backend in English: automation log details, stop reasons and outcomes, error messages (`HTTPException`, `LeadError`), JSON keys in the settings panel. Already-stored runs keep the English texts they were saved with.

## Implemented, not verified end to end

| Area | Gap |
|---|---|
| Frontend runtime | Never opened in a browser by the assistant. Neither the redesign nor the Polish translation has been visually checked. Graph interactions (expand, edge drawer, path, link panel) are untested. |
| Containers running | `docker compose up`, Alembic upgrade on PostgreSQL inside the container, worker start and frontend-to-backend connectivity were not verified by the assistant. |
| PostgreSQL | Migrations rendered as PostgreSQL SQL offline; never executed on a PostgreSQL server. Tests run on SQLite. JSONB behavior is untested. |
| EVM, Solana, Bitcoin | Tested against mocked JSON-RPC / Esplora / Blockscout responses only. No real-node smoke test has been run. |
| Web search and fetch | Tavily and ddgs are tested with mocked responses. Live behavior (rate limits, ddgs breakage, real pages) is not covered by tests. |
| Sherlock | Wrapper and parser tested with fixtures. The `sherlock` binary was not installed, so it was never run. Known issue: a profile URL such as `website:discord.com/` is not normalised cleanly. |
| Automation under load | Time boxes and limits are tested with fakes. Behavior against public nodes returning HTTP 429 is not observed. |
| LLM layer | Tested only with a scripted fake client. Never run against a real model. |

## Placeholders (no integration code)

`maigret`, `spiderfoot`, `maltego`. Listed in the UI as placeholders; cannot be enabled (HTTP 409). (`web_search` and `web_fetch` are no longer placeholders; they are implemented providers.)

## Provisioned but unused

Redis is in `docker-compose.yml`. No code uses it. MinIO was removed from Compose (its image was not pullable and nothing uses it). The worker polls the database (ADR-0006) and artifacts go to the local filesystem (ADR-0007).

## Not built

S3/MinIO artifact backend · Redis queue · scheduled re-collection · full-text search · official-API collectors for X or web crawling · login throttling and cookie sessions · user management UI · pagination and virtualization for very large result sets · relationship-review audit entries · stablecoin payout analysis (facts only) · translation of backend log and error texts.

## Documentation debt

The wiki has not yet been brought up to date with the Lead Search and Automation work. Still to do: `PROVIDER_CATALOG`, `DISCOVERY_ENGINE`, `DATA_FLOWS`, `SECURITY_AND_PRIVACY`, `RELATIONSHIP_TYPES` (`WALLET_TRANSACTED_WITH`, `PAGE_REFERENCES_ENTITY`), `EVIDENCE_MODEL` (repeatability is not a confidence score), `ROADMAP`, `runbooks`; ADR-0010 and later (web search and lead workflow, automation, Polish UI and redesign); a new session note; `OSINT_*` variable names (no values) in `.env.example`.

## Known minor issues

- `app/api/knowledge.py` re-exports a few unused names through `__all__`.
- Alembic emits a `path_separator` deprecation warning in tests.
- The graph "shortest path" UI needs two clicks (set start, then select end node).
- The Automation page does not show how many URLs are left before a run starts.
- Open question: the same network may appear as both `evm` and `ethereum`, which can create duplicate entities.
