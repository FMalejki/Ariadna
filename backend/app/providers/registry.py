"""Provider registry. Code defines providers; the `providers` table mirrors them for config/health/audit."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Provider
from app.providers.base import IntelligenceProvider
from app.providers.blockchain import EvmRpcProvider, SolanaRpcProvider
from app.providers.manual_import import ManualImportProvider
from app.providers.onchain import BitcoinEsploraProvider
from app.providers.placeholders import MaigretPlaceholder, MaltegoPlaceholder, SpiderFootPlaceholder
from app.providers.sherlock import SherlockProvider
from app.providers.webfetch import WebFetchProvider
from app.providers.websearch import WebSearchProvider

_REGISTRY: dict[str, IntelligenceProvider] = {}


def _defaults() -> list[IntelligenceProvider]:
    return [
        ManualImportProvider(),
        SherlockProvider(),
        EvmRpcProvider(),
        SolanaRpcProvider(),
        BitcoinEsploraProvider(),
        WebSearchProvider(),
        WebFetchProvider(),
        MaigretPlaceholder(),
        SpiderFootPlaceholder(),
        MaltegoPlaceholder(),
    ]


def register(provider: IntelligenceProvider) -> None:
    _REGISTRY[provider.name] = provider


def reset_registry(extra: list[IntelligenceProvider] | None = None) -> None:
    _REGISTRY.clear()
    for p in _defaults():
        register(p)
    for p in extra or []:
        register(p)


def get_provider(name: str) -> IntelligenceProvider:
    if not _REGISTRY:
        reset_registry()
    return _REGISTRY[name]


def all_providers() -> list[IntelligenceProvider]:
    if not _REGISTRY:
        reset_registry()
    return list(_REGISTRY.values())


def sync_providers(session: Session) -> list[Provider]:
    """Upsert DB rows for registered providers; preserves operator settings (enabled, limits, config)."""
    s = get_settings()
    rows: dict[str, Provider] = {p.name: p for p in session.scalars(select(Provider))}
    for prov in all_providers():
        row = rows.get(prov.name)
        placeholder = prov.implementation_status != "implemented"
        if row is None:
            row = Provider(
                name=prov.name, version=prov.version, kind=prov.kind, description=prov.description,
                capabilities=list(prov.capabilities), evidence_class=prov.evidence_class.value,
                implementation_status=prov.implementation_status, enabled=not placeholder,
                rate_limit_per_minute=s.default_rate_limit_per_minute, daily_quota=s.default_quota_per_day,
            )
            session.add(row)
            rows[prov.name] = row
        else:
            was_placeholder = row.implementation_status != "implemented"
            row.version, row.kind, row.description = prov.version, prov.kind, prov.description
            row.capabilities = list(prov.capabilities)
            row.evidence_class = prov.evidence_class.value
            row.implementation_status = prov.implementation_status
            if placeholder:
                row.enabled = False
            elif was_placeholder:
                row.enabled = True  # a former placeholder became a real integration; an operator can still disable it afterwards
    session.flush()
    return list(rows.values())
