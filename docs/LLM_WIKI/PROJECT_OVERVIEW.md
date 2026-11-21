# Project overview

STATUS: PARTIALLY_IMPLEMENTED

## What this is

A persistent memory and evidence layer that sits above existing OSINT tools (Sherlock, blockchain RPC nodes, manual imports; others are placeholders). It is not another crawler. Its job is to remember what was found, where it came from, how it was obtained and how much it can be trusted, so a second investigation reuses the first one's knowledge instead of starting over.

First domain: the crypto-trading ecosystem (X accounts and posts, Telegram channels by link only, websites, domains, wallets, tokens, contracts, transactions).

## What it does

- Starts from a seed (URL, @handle, address, tx hash, domain) and normalizes it into a canonical identifier.
- Runs permitted providers through a policy gate, with human approval for sensitive categories.
- Stores every result as an append-only, versioned observation tied to a source, a tool run and (when available) a raw artifact.
- Derives relationships and their status from evidence. Shows the evidence behind every edge.
- Lets analysts review match candidates, relationships and LLM claims. Merges can be reversed.

## What it deliberately does not do

- Merge entities because names look similar, or attribute wallet ownership.
- Scrape Telegram, or collect private data, or deanonymize people.
- Treat LLM output as fact.
- Run a graph database, Kubernetes or a custom blockchain explorer.

## Stack

Backend: Python 3.12, FastAPI, Pydantic, SQLAlchemy 2, Alembic, PostgreSQL (JSONB) in production, SQLite in tests. Frontend: Next.js, React, TypeScript, TanStack Query, Cytoscape.js. Artifacts: local filesystem store. Orchestration: Docker Compose (untested, see IMPLEMENTATION_STATUS).

## Where to look next

[ARCHITECTURE](ARCHITECTURE.md) · [DATA_MODEL](DATA_MODEL.md) · [IMPLEMENTATION_STATUS](IMPLEMENTATION_STATUS.md) · [ROADMAP](ROADMAP.md)
