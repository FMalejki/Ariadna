"""Provider interface (spec 3.1.C). Providers are external-intelligence adapters; they never write to the
database. Their output is `NormalizedObservation`s that enter the staging -> ingestion pipeline
(app/services/ingest.py), which owns all persistence.
"""

from __future__ import annotations

import abc
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from app.config import Settings
from app.domain import EvidenceType
from app.normalize import Reference


class ProviderError(Exception):
    """Execution failed; may be retried."""


class ProviderUnavailable(ProviderError):
    """Provider cannot run (not installed / not configured / access blocked). Not retried; reported honestly."""


class ProviderTimeout(ProviderError):
    pass


class ProviderInputError(ValueError):
    """Input rejected by validate_input; never retried."""


def cursor_digest(cursor) -> str:
    """Short stable digest of a pagination cursor (identifies one history page; not a secret)."""
    return hashlib.sha256(json.dumps(cursor, sort_keys=True, default=str).encode()).hexdigest()[:12]


@dataclass
class ProviderInput:
    reference: Reference | None  # the thing to investigate (None for pure imports)
    payload: dict = field(default_factory=dict)  # provider-specific (e.g. import records)

    @property
    def target(self) -> str:
        if self.reference:
            return self.reference.canonical_id
        return str(self.payload.get("target") or self.payload.get("batch_id") or "import")[:580]

    @property
    def run_key(self) -> str:
        """ToolRun.input_key. A history page ("load more", payload["cursor"]) gets its own key so it is never mistaken
        for - or reused as - the first page of the same address."""
        cursor = self.payload.get("cursor") if self.payload else None
        if cursor is None:
            return self.target
        return f"{self.target}#page:{cursor_digest(cursor)}"[:580]


@dataclass
class ProviderContext:
    settings: Settings
    http: httpx.Client | None = None  # injectable for tests
    now: datetime | None = None

    def client(self) -> httpx.Client:
        if self.http is not None:
            return self.http
        return httpx.Client(timeout=self.settings.provider_http_timeout_seconds, follow_redirects=False)


@dataclass
class RawResult:
    raw: bytes  # preserved verbatim as an Artifact
    content_type: str = "application/json"
    exit_code: int | None = None
    log: str = ""
    parsed: Any = None  # provider-native parse used by normalize_result
    parameters: dict = field(default_factory=dict)


@dataclass
class SourceRef:
    source_type: str
    canonical_key: str
    canonical_url: str | None = None
    external_identifier: str | None = None
    display_name: str = ""
    access_method: str = "provider"


@dataclass
class Link:
    """A reference found in/with an observation. If `relationship_type` is None the ingest layer derives one
    from the (subject type, referenced type) rule table; if no rule applies, no relationship is created."""

    reference: Reference
    relationship_type: str | None = None
    direction: str = "out"  # out: subject -> reference ; in: reference -> subject
    explanation: str = ""
    evidence_type: str | None = None  # override of the observation's evidence class
    basis: str = ""  # which deterministic method justified it
    follow_up: bool = True  # eligible for discovery follow-up tasks
    match_candidate_rule: str | None = None  # also queue a pending MatchCandidate (never auto-merged)


@dataclass
class EntityTypeUpdate:
    reference: Reference
    new_type: str
    meta: dict = field(default_factory=dict)


@dataclass
class NetworkResolution:
    """Deterministic: provider proved `from_ref` (network unknown) exists on a concrete network."""

    from_identifier: str
    to_reference: Reference
    reason: str


@dataclass
class NormalizedObservation:
    source: SourceRef
    subject: Reference
    observation_type: str
    observed_at: datetime
    payload: dict
    external_id: str | None = None
    published_at: datetime | None = None
    text_fields: list[str] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    entity_type_updates: list[EntityTypeUpdate] = field(default_factory=list)
    network_resolutions: list[NetworkResolution] = field(default_factory=list)
    snapshot: bool = False  # profile/page snapshot: links absent from a newer snapshot become historical
    platform_hint: str | None = None
    source_unavailable: bool = False  # negative result: mark source unavailable rather than inventing data
    evidence_type: str | None = None  # override of provider evidence class


@dataclass
class HealthStatus:
    status: str  # healthy | degraded | unavailable
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "healthy"


class IntelligenceProvider(abc.ABC):
    name: str
    version: str
    kind: str  # import | cli | http_api | blockchain_rpc | placeholder
    description: str = ""
    category: str  # policy category: import | username_lookup | blockchain_verification | web_collection
    capabilities: list[str]
    evidence_class: EvidenceType
    implementation_status: str = "implemented"  # implemented | placeholder
    docs_url: str | None = None

    @abc.abstractmethod
    def supports(self, ref: Reference) -> bool:
        """Can this provider investigate this deterministic reference (used by discovery routing)?"""

    def validate_input(self, data: ProviderInput) -> None:
        if data.reference is None or not self.supports(data.reference):
            raise ProviderInputError(f"{self.name} does not support this input")

    @abc.abstractmethod
    def execute(self, data: ProviderInput, ctx: ProviderContext) -> RawResult: ...

    @abc.abstractmethod
    def normalize_result(self, raw: RawResult, data: ProviderInput, ctx: ProviderContext) -> list[NormalizedObservation]: ...

    @abc.abstractmethod
    def health_check(self, ctx: ProviderContext) -> HealthStatus: ...

    def describe(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "kind": self.kind,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "evidence_class": self.evidence_class.value,
            "implementation_status": self.implementation_status,
            "category": self.category,
            "docs_url": self.docs_url,
        }


class PlaceholderProvider(IntelligenceProvider):
    """Honest stand-in for a documented-but-unimplemented integration. Never executes, never returns data."""

    kind = "placeholder"
    implementation_status = "placeholder"
    category = "username_lookup"
    evidence_class = EvidenceType.TOOL_GENERATED_DISCOVERY
    note: str = ""

    def supports(self, ref: Reference) -> bool:
        return False  # never routed by the discovery engine

    def validate_input(self, data: ProviderInput) -> None:
        raise ProviderInputError(f"{self.name} is a placeholder, not an implemented integration")

    def execute(self, data: ProviderInput, ctx: ProviderContext) -> RawResult:
        raise ProviderUnavailable(f"{self.name} integration is not implemented: {self.note}")

    def normalize_result(self, raw, data, ctx):  # pragma: no cover - never reached
        return []

    def health_check(self, ctx: ProviderContext) -> HealthStatus:
        return HealthStatus("unavailable", f"Not implemented. {self.note}")
