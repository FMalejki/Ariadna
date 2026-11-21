"""Relational schema - the platform's source of truth (spec section 5).

Design notes (see docs/LLM_WIKI/DATA_MODEL.md):
* Observations are append-only; idempotent ingestion is enforced by a unique content hash per source.
* Entity canonical identifiers are namespaced strings and globally unique ("x:handle",
  "ethereum:0xabc...", "evm:0xabc..." when the chain is unknown, "solana:<base58>", ...).
* Relationships are unique per (source, target, type); every one needs RelationshipEvidence rows.
* Entity merges are soft and reversible (EntityMerge); aliases are stored separately.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, JSONType, UTCDateTime, new_id, utcnow
from app.domain import (
    ClaimReview,
    ClaimVerification,
    CollectionStatus,
    EntityStatus,
    MatchStatus,
    ProcessingStatus,
    RelationshipStatus,
    ResolutionStatus,
    ReviewStatus,
    Role,
    TaskStatus,
    ToolRunStatus,
)


def _pk() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True, default=new_id)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = _pk()
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default=Role.ANALYST.value)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Provider(Base):
    """Configuration/health mirror of a code-defined provider (see app/providers)."""

    __tablename__ = "providers"
    id: Mapped[str] = _pk()
    name: Mapped[str] = mapped_column(String(100), unique=True)
    version: Mapped[str] = mapped_column(String(50))
    kind: Mapped[str] = mapped_column(String(50))  # import | cli | http_api | blockchain_rpc | placeholder
    description: Mapped[str] = mapped_column(Text, default="")
    capabilities: Mapped[list] = mapped_column(JSONType, default=list)
    evidence_class: Mapped[str] = mapped_column(String(50))  # maps to EvidenceType
    implementation_status: Mapped[str] = mapped_column(String(30))  # implemented | placeholder
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=20)
    daily_quota: Mapped[int] = mapped_column(Integer, default=500)
    last_health: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    last_health_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("source_type", "canonical_key", name="uq_source_identity"),)
    id: Mapped[str] = _pk()
    source_type: Mapped[str] = mapped_column(String(40), index=True)
    canonical_key: Mapped[str] = mapped_column(String(600))  # canonical URL or external identifier
    canonical_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    external_identifier: Mapped[str | None] = mapped_column(String(600), nullable=True)
    display_name: Mapped[str] = mapped_column(String(300), default="")
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    access_method: Mapped[str] = mapped_column(String(50), default="manual")
    first_discovered_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    collection_status: Mapped[str] = mapped_column(String(20), default=CollectionStatus.PENDING.value)
    collection_restrictions: Mapped[str | None] = mapped_column(Text, nullable=True)
    reliability: Mapped[dict] = mapped_column(JSONType, default=dict)
    meta: Mapped[dict] = mapped_column("metadata", JSONType, default=dict)
    entity_id: Mapped[str | None] = mapped_column(ForeignKey("entities.id"), nullable=True)

    observations = relationship("Observation", back_populates="source")


class Artifact(Base):
    """Preserved raw evidence, content-addressed (sha256). Unique per content hash."""

    __tablename__ = "artifacts"
    id: Mapped[str] = _pk()
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    storage_location: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    capture_method: Mapped[str] = mapped_column(String(100), default="provider")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    retention_policy: Mapped[str] = mapped_column(String(50), default="default")
    retain_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    meta: Mapped[dict] = mapped_column("metadata", JSONType, default=dict)


class ToolRun(Base):
    __tablename__ = "tool_runs"
    id: Mapped[str] = _pk()
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"), index=True)
    provider_name: Mapped[str] = mapped_column(String(100))
    provider_version: Mapped[str] = mapped_column(String(50))
    # Deliberately not an FK: tool_runs -> discovery_tasks -> observations -> tool_runs would be a cycle.
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    input_key: Mapped[str] = mapped_column(String(600), default="", index=True)  # target canonical id (reuse lookup)

    input_refs: Mapped[dict] = mapped_column(JSONType, default=dict)
    parameters: Mapped[dict] = mapped_column(JSONType, default=dict)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=ToolRunStatus.RUNNING.value, index=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    log: Mapped[str | None] = mapped_column(Text, nullable=True)  # safe diagnostic output only
    raw_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"), nullable=True)
    observation_ids: Mapped[list] = mapped_column(JSONType, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Observation(Base):
    """Immutable record of something observed. Never updated after creation (except processing_status)."""

    __tablename__ = "observations"
    __table_args__ = (UniqueConstraint("source_id", "content_hash", name="uq_observation_idempotency"),)
    id: Mapped[str] = _pk()
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    provider_id: Mapped[str | None] = mapped_column(ForeignKey("providers.id"), nullable=True)
    tool_run_id: Mapped[str | None] = mapped_column(ForeignKey("tool_runs.id"), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    observation_type: Mapped[str] = mapped_column(String(40), index=True)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    raw_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"), nullable=True)
    normalized_payload: Mapped[dict] = mapped_column(JSONType)
    content_hash: Mapped[str] = mapped_column(String(64))  # sha256(payload_hash:seq); unique per source
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)  # sha256(type+payload+published_at)
    # Version number within (source, observation_type). A changed payload appends seq+1 (never overwrites);
    # an unchanged payload is a no-op (idempotent). A higher seq supersedes lower ones for "historical" logic.
    seq: Mapped[int] = mapped_column(Integer, default=1)
    parser_version: Mapped[str] = mapped_column(String(30))
    processing_status: Mapped[str] = mapped_column(String(20), default=ProcessingStatus.PENDING.value)
    evidence_type: Mapped[str] = mapped_column(String(50))  # evidence category this observation yields
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    source = relationship("Source", back_populates="observations")


class Entity(Base):
    __tablename__ = "entities"
    id: Mapped[str] = _pk()
    entity_type: Mapped[str] = mapped_column(String(30), index=True)
    canonical_identifier: Mapped[str] = mapped_column(String(600), unique=True)
    display_name: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=EntityStatus.ACTIVE.value, index=True)
    resolution_status: Mapped[str] = mapped_column(String(20), default=ResolutionStatus.RESOLVED.value)
    meta: Mapped[dict] = mapped_column("metadata", JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    first_observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    merged_into_id: Mapped[str | None] = mapped_column(ForeignKey("entities.id"), nullable=True)


class EntityAlias(Base):
    """Confirmed alternative identifiers. Never created from similarity alone."""

    __tablename__ = "entity_aliases"
    __table_args__ = (UniqueConstraint("entity_id", "alias", name="uq_alias"),)
    id: Mapped[str] = _pk()
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), index=True)
    alias: Mapped[str] = mapped_column(String(600), index=True)
    alias_type: Mapped[str] = mapped_column(String(40), default="identifier")
    basis: Mapped[str] = mapped_column(Text)  # why this alias is believed (deterministic rule / analyst)
    observation_id: Mapped[str | None] = mapped_column(ForeignKey("observations.id"), nullable=True)
    confirmed_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ObservationEntity(Base):
    """Which entities an observation is about / mentions (provenance index)."""

    __tablename__ = "observation_entities"
    __table_args__ = (UniqueConstraint("observation_id", "entity_id", "role", name="uq_obs_entity"),)
    id: Mapped[str] = _pk()
    observation_id: Mapped[str] = mapped_column(ForeignKey("observations.id"), index=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    extraction_method: Mapped[str] = mapped_column(String(60), default="deterministic_parser")


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint("source_entity_id", "target_entity_id", "relationship_type", name="uq_relationship"),
        Index("ix_rel_target", "target_entity_id"),
    )
    id: Mapped[str] = _pk()
    source_entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), index=True)
    target_entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"))
    relationship_type: Mapped[str] = mapped_column(String(60), index=True)
    status: Mapped[str] = mapped_column(String(20), default=RelationshipStatus.PROPOSED.value, index=True)
    review_status: Mapped[str] = mapped_column(String(20), default=ReviewStatus.UNREVIEWED.value)
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    first_observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    valid_from: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    resolution_method: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    evidence = relationship("RelationshipEvidence", back_populates="relationship_", cascade="all")


class RelationshipEvidence(Base):
    __tablename__ = "relationship_evidence"
    __table_args__ = (
        UniqueConstraint("relationship_id", "observation_id", "evidence_type", "support_status", name="uq_rel_evidence"),
    )
    id: Mapped[str] = _pk()
    relationship_id: Mapped[str] = mapped_column(ForeignKey("relationships.id"), index=True)
    observation_id: Mapped[str] = mapped_column(ForeignKey("observations.id"), index=True)
    evidence_type: Mapped[str] = mapped_column(String(50))
    explanation: Mapped[str] = mapped_column(Text)
    support_status: Mapped[str] = mapped_column(String(20))
    confidence_basis: Mapped[str] = mapped_column(Text)  # which deterministic method / rule justifies this
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    relationship_ = relationship("Relationship", back_populates="evidence")


class EntityMerge(Base):
    """Reversible merge record. Reversal marks reversed_at; the loser entity is reactivated."""

    __tablename__ = "entity_merges"
    id: Mapped[str] = _pk()
    winner_id: Mapped[str] = mapped_column(ForeignKey("entities.id"))
    loser_id: Mapped[str] = mapped_column(ForeignKey("entities.id"))
    reason: Mapped[str] = mapped_column(Text)
    evidence_observation_ids: Mapped[list] = mapped_column(JSONType, default=list)
    method: Mapped[str] = mapped_column(String(60))
    snapshot: Mapped[dict] = mapped_column(JSONType, default=dict)  # what moved, to undo it exactly
    merged_by: Mapped[str] = mapped_column(String(100))
    merged_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    reversed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    reversed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)


class MatchCandidate(Base):
    """Uncertain identity match awaiting human review. Never auto-merged."""

    __tablename__ = "match_candidates"
    __table_args__ = (UniqueConstraint("entity_a_id", "entity_b_id", "rule", name="uq_match_candidate"),)
    id: Mapped[str] = _pk()
    entity_a_id: Mapped[str] = mapped_column(ForeignKey("entities.id"))
    entity_b_id: Mapped[str] = mapped_column(ForeignKey("entities.id"))
    rule: Mapped[str] = mapped_column(String(80))
    explanation: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=MatchStatus.PENDING.value)
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Investigation(Base):
    __tablename__ = "investigations"
    id: Mapped[str] = _pk()
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open")
    scope: Mapped[dict] = mapped_column(JSONType, default=dict)  # max_depth, allowed_providers, allowed_domains
    notes: Mapped[str] = mapped_column(Text, default="")  # analyst-generated, separate from public observations
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class InvestigationItem(Base):
    __tablename__ = "investigation_items"
    __table_args__ = (UniqueConstraint("investigation_id", "item_type", "item_id", name="uq_inv_item"),)
    id: Mapped[str] = _pk()
    investigation_id: Mapped[str] = mapped_column(ForeignKey("investigations.id"), index=True)
    item_type: Mapped[str] = mapped_column(String(20))  # entity | observation | source | tool_run
    item_id: Mapped[str] = mapped_column(String(36))
    origin: Mapped[str] = mapped_column(String(30), default="discovered")  # seed | discovered | reused | manual
    added_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class InvestigationView(Base):
    """Saved graph view (spec 7.3)."""

    __tablename__ = "investigation_views"
    id: Mapped[str] = _pk()
    investigation_id: Mapped[str] = mapped_column(ForeignKey("investigations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    view_state: Mapped[dict] = mapped_column(JSONType)  # root ids, filters, expanded nodes, positions
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class DiscoveryTask(Base):
    __tablename__ = "discovery_tasks"
    __table_args__ = (UniqueConstraint("investigation_id", "dedup_key", name="uq_task_dedup"),)
    id: Mapped[str] = _pk()
    investigation_id: Mapped[str] = mapped_column(ForeignKey("investigations.id"), index=True)
    dedup_key: Mapped[str] = mapped_column(String(800))
    target_type: Mapped[str] = mapped_column(String(40))
    target_identifier: Mapped[str] = mapped_column(String(600))
    target_entity_id: Mapped[str | None] = mapped_column(ForeignKey("entities.id"), nullable=True)
    originating_observation_id: Mapped[str | None] = mapped_column(ForeignKey("observations.id"), nullable=True)
    parent_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"))
    provider_name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(50))  # policy category
    priority: Mapped[int] = mapped_column(Integer, default=50)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default=TaskStatus.QUEUED.value, index=True)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    result_summary: Mapped[dict | None] = mapped_column(JSONType, nullable=True)


class AutomationRun(Base):
    """Persistent record of one automated lead run (stages A-E, see services/automation.py). The worker advances it in
    bounded chunks; `log` is the analyst-facing step-by-step trail (what ran, outcome, error, policy skip)."""

    __tablename__ = "automation_runs"
    id: Mapped[str] = _pk()
    investigation_id: Mapped[str] = mapped_column(ForeignKey("investigations.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued|running|completed|stopped_by_limit|aborted|failed
    config: Mapped[dict] = mapped_column(JSONType, default=dict)  # stages, limits, subject, login, query plan (never mutated)
    progress: Mapped[dict] = mapped_column(JSONType, default=dict)  # executor cursor
    counters: Mapped[dict] = mapped_column(JSONType, default=dict)
    log: Mapped[list] = mapped_column(JSONType, default=list)
    stop_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    abort_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class SourcePolicy(Base):
    """Allowlist / approval / denial rules for discovery (spec 3.1.B)."""

    __tablename__ = "source_policies"
    __table_args__ = (UniqueConstraint("scope", "pattern", name="uq_policy"),)
    id: Mapped[str] = _pk()
    scope: Mapped[str] = mapped_column(String(20))  # category | domain | provider
    pattern: Mapped[str] = mapped_column(String(300))
    action: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Claim(Base):
    """Claim extracted from an observation (LLM or other). Never auto-promoted to fact."""

    __tablename__ = "claims"
    __table_args__ = (
        UniqueConstraint("observation_id", "claim_hash", "model_name", "prompt_version", name="uq_claim_version"),
    )
    id: Mapped[str] = _pk()
    observation_id: Mapped[str] = mapped_column(ForeignKey("observations.id"), index=True)
    claim_hash: Mapped[str] = mapped_column(String(64))
    claim_type: Mapped[str] = mapped_column(String(60))
    subject_reference: Mapped[str | None] = mapped_column(String(600), nullable=True)
    predicate: Mapped[str] = mapped_column(String(200))
    object_reference: Mapped[str | None] = mapped_column(String(600), nullable=True)
    supporting_text: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(30), default="llm")
    model_provider: Mapped[str] = mapped_column(String(100))
    model_name: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(50))
    review_status: Mapped[str] = mapped_column(String(20), default=ClaimReview.PENDING.value)
    verification_status: Mapped[str] = mapped_column(String(20), default=ClaimVerification.UNVERIFIED.value)
    superseded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class WikiPage(Base):
    """Project-wiki pages mirrored from docs/LLM_WIKI so edits are revisioned and queryable.

    This is engineering memory, NOT intelligence data (spec 10.6).
    """

    __tablename__ = "wiki_pages"
    id: Mapped[str] = _pk()
    path: Mapped[str] = mapped_column(String(300), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    current_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class WikiRevision(Base):
    __tablename__ = "wiki_revisions"
    __table_args__ = (UniqueConstraint("page_id", "content_hash", name="uq_wiki_revision"),)
    id: Mapped[str] = _pk()
    page_id: Mapped[str] = mapped_column(ForeignKey("wiki_pages.id"), index=True)
    revision_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    author: Mapped[str] = mapped_column(String(100))
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = _pk()
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100), index=True)
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)


class ProviderUsage(Base):
    """Per-provider execution counters used to enforce rate limits and daily quotas."""

    __tablename__ = "provider_usage"
    id: Mapped[str] = _pk()
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"), index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
