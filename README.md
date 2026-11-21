# Ariadna

A persistent, evidence-first OSINT knowledge graph for the crypto-trading ecosystem. It sits above existing tools and remembers what was found, where it came from, and how reliable it is. A second investigation reuses the first one's knowledge.

**Status: working prototype.** See [implementation status](docs/LLM_WIKI/IMPLEMENTATION_STATUS.md) for what is verified and what is not. Start with [`docs/LLM_WIKI/START_HERE.md`](docs/LLM_WIKI/START_HERE.md). AI assistants must also follow [`AGENTS.md`](AGENTS.md).

## Principles

Evidence over assumptions · conservative entity resolution (no merging on similar names, no wallet-ownership attribution) · append-only observations and reversible merges · relationship status derived from evidence, no confidence score · LLM output is only an unverified claim · no private data, no doxxing, no Telegram scraping.

## Run locally (without Docker)

```bash
# backend (SQLite, no external services)
cd backend
python3.12 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
export OSINT_ADMIN_PASSWORD=change-me OSINT_JWT_SECRET=dev-secret-dev-secret-dev-secret-123456
uvicorn app.main:app --reload          # http://localhost:8000/docs
python -m app.worker                   # optional, second terminal

# frontend
cd frontend && npm install && npm run dev   # http://localhost:3000, sign in as admin
```

## Run with Docker Compose

```bash
cp .env.example .env     # set OSINT_ADMIN_PASSWORD and OSINT_JWT_SECRET
docker compose build     # verified: images build
docker compose up        # NOT yet verified; report problems
```

Services: postgres, backend (runs `alembic upgrade head`), worker, frontend. Redis starts but nothing uses it yet. MinIO is not in Compose.

## Optional providers

Blockchain verification needs `OSINT_EVM_RPC_URLS` / `OSINT_SOLANA_RPC_URL`. Sherlock needs the `sherlock` binary (the backend Docker image installs `sherlock-project` from PyPI; this install is unverified until you rebuild). Without configuration, providers report "unavailable"; nothing is fabricated. The LLM layer is off unless `OSINT_LLM_PROVIDER=openai_compatible`.

## Tests

```bash
cd backend && pytest -q                 # offline, SQLite
cd frontend && npx tsc --noEmit && npm run build
```

## Demo (MVP)

1. Sign in, open **Discovery**, start an investigation from a seed (for example a URL you are authorised to use). Approve pending tasks, or import authorised records under **Investigations**.
2. Open **Knowledge Graph**, expand nodes, click an edge to see its evidence chain.
3. Start a second investigation with overlapping sources. **Investigations** shows `knowledge_reuse`: reused observations and runs versus new ones.

## Layout

`backend/` FastAPI app, migrations, tests · `frontend/` Next.js UI · `docs/LLM_WIKI/` project wiki and ADRs · `docker-compose.yml`
