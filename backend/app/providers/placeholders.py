"""Honest placeholders for evaluated-but-not-implemented integrations (spec section 4 / Phase 3).

Evaluation notes are based on upstream documentation read during development (links in `docs_url`). None of
these execute anything; health_check reports `unavailable` with the reason. Do not mark them implemented until
code + tests prove they work.
"""

from __future__ import annotations

from app.providers.base import PlaceholderProvider


class MaigretPlaceholder(PlaceholderProvider):
    name = "maigret"
    version = "n/a"
    description = "Username search across many sites (placeholder)."
    capabilities = ["username_lookup"]
    docs_url = "https://github.com/soxoj/maigret"
    note = (
        "Maigret has a CLI with --json (simple|ndjson) and --csv reports, and also extracts profile attributes "
        "('dossier') which risks collecting personal data beyond this platform's scope (spec 1.3). Needs a "
        "field-level allowlist for extracted attributes and a verified report schema before an adapter is added. "
        "Reuse the sherlock adapter pattern (app/providers/cli.py)."
    )


class SpiderFootPlaceholder(PlaceholderProvider):
    name = "spiderfoot"
    version = "n/a"
    description = "Broad OSINT module orchestration (placeholder)."
    capabilities = ["domain_footprint", "username_lookup"]
    docs_url = "https://github.com/smicallef/spiderfoot"
    note = (
        "SpiderFoot (MIT) offers a CLI (sf.py), an embedded web server with REST API and CSV/JSON export, and 200+ "
        "modules, many needing API keys and several actively probing targets. An adapter needs a pinned module "
        "allowlist (passive modules only), a running SpiderFoot instance to call, and an export schema check."
    )


class MaltegoPlaceholder(PlaceholderProvider):
    name = "maltego"
    version = "n/a"
    description = "Link analysis / transforms (placeholder)."
    capabilities = ["graph_import"]
    docs_url = "https://www.maltego.com/"
    note = (
        "Maltego is commercial software. Practical integration is (a) importing exported graphs/entities that the "
        "analyst is licensed to export, or (b) serving this platform's data as a transform server. Neither is "
        "implemented; the manual_import provider can ingest analyst-prepared exports in the meantime."
    )
