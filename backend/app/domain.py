"""Controlled vocabularies. Kept as plain string constants so they map 1:1 onto DB text columns.

Changing a value here is a data-model change: update docs/LLM_WIKI/ENTITY_TYPES.md /
RELATIONSHIP_TYPES.md / EVIDENCE_MODEL.md and add a migration if stored data is affected.
"""

from __future__ import annotations

from enum import StrEnum


class EntityType(StrEnum):
    SOCIAL_ACCOUNT = "social_account"
    PUBLIC_PROFILE = "public_profile"
    CHANNEL = "channel"
    WEBSITE = "website"
    DOMAIN = "domain"
    WALLET = "wallet"
    TOKEN = "token"
    TRANSACTION = "transaction"
    CONTRACT = "contract"
    POST = "post"
    ORGANIZATION = "organization"
    DOCUMENT = "document"


class EntityStatus(StrEnum):
    ACTIVE = "active"
    MERGED = "merged"  # soft-merged into another entity; reversible
    DELETED = "deleted"  # soft-deleted by a correction / deletion workflow


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"  # deterministic canonical identifier
    CANDIDATE = "candidate"  # exists, but identity-level matches are pending review
    NEEDS_REVIEW = "needs_review"


class SourceType(StrEnum):
    SOCIAL_ACCOUNT = "social_account"
    SOCIAL_POST = "social_post"
    CHANNEL = "channel"
    WEBSITE = "website"
    PUBLIC_PROFILE = "public_profile"
    PUBLIC_API = "public_api"
    BLOCKCHAIN_ADDRESS = "blockchain_address"
    BLOCKCHAIN_TRANSACTION = "blockchain_transaction"
    EXPLORER_PAGE = "explorer_page"
    DOCUMENT = "document"
    OSINT_TOOL = "osint_tool"
    MANUAL_URL = "manual_url"
    IMPORTED_FILE = "imported_file"


class CollectionStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    UNAVAILABLE = "unavailable"  # provider could not access it; NOT silently replaced by scraping
    RESTRICTED = "restricted"  # collection not permitted by policy
    STALE = "stale"


class ObservationType(StrEnum):
    SOCIAL_PROFILE = "social_profile"
    SOCIAL_POST = "social_post"
    WEB_PAGE = "web_page"
    USERNAME_HIT = "username_hit"
    BLOCKCHAIN_TRANSACTION = "blockchain_transaction"
    BLOCKCHAIN_ADDRESS = "blockchain_address"
    BLOCKCHAIN_LOOKUP_FAILED = "blockchain_lookup_failed"  # negative result is evidence too
    IMPORT_RECORD = "import_record"


class ProcessingStatus(StrEnum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


class EvidenceType(StrEnum):
    """Evidence categories (spec section 6.1). Strength ordering lives in services/relationships.py."""

    DIRECT_SOURCE_STATEMENT = "direct_source_statement"
    OFFICIAL_PLATFORM_DATA = "official_platform_data"
    DETERMINISTIC_BLOCKCHAIN = "deterministic_blockchain_verification"
    INDEPENDENT_CORROBORATION = "independent_corroboration"
    TOOL_GENERATED_DISCOVERY = "tool_generated_discovery"
    LLM_EXTRACTED_CLAIM = "llm_extracted_claim"
    ANALYST_CONFIRMED = "analyst_confirmed"
    UNVERIFIED_CANDIDATE = "unverified_candidate"
    CONTRADICTORY = "contradictory_evidence"


class SupportStatus(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"


class RelationshipStatus(StrEnum):
    PROPOSED = "proposed"
    SUPPORTED = "supported"
    CORROBORATED = "corroborated"
    VERIFIED = "verified"  # by a specific deterministic method
    CONTRADICTED = "contradicted"
    REJECTED = "rejected"
    HISTORICAL = "historical"  # valid_to is set
    REDACTED = "redacted"  # all supporting observations removed by a source-deletion workflow


class ReviewStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class RelationshipType(StrEnum):
    ACCOUNT_LINKS_TO_CHANNEL = "ACCOUNT_LINKS_TO_CHANNEL"
    ACCOUNT_LINKS_TO_WEBSITE = "ACCOUNT_LINKS_TO_WEBSITE"
    ACCOUNT_MENTIONS_ACCOUNT = "ACCOUNT_MENTIONS_ACCOUNT"
    ACCOUNT_PUBLISHED_POST = "ACCOUNT_PUBLISHED_POST"
    CHANNEL_PUBLISHED_POST = "CHANNEL_PUBLISHED_POST"
    USERNAME_MATCHES_PROFILE = "USERNAME_MATCHES_PROFILE"  # same username string only; NOT an identity claim
    ACCOUNT_PROFILE_REFERENCES_ADDRESS = "ACCOUNT_PROFILE_REFERENCES_ADDRESS"
    POST_MENTIONS_TOKEN = "POST_MENTIONS_TOKEN"
    POST_CONTAINS_WALLET = "POST_CONTAINS_WALLET"
    POST_REFERENCES_TRANSACTION = "POST_REFERENCES_TRANSACTION"
    POST_MENTIONS_ACCOUNT = "POST_MENTIONS_ACCOUNT"
    POST_LINKS_TO_WEBSITE = "POST_LINKS_TO_WEBSITE"
    POST_LINKS_TO_CHANNEL = "POST_LINKS_TO_CHANNEL"
    PAGE_REFERENCES_ENTITY = "PAGE_REFERENCES_ENTITY"
    WALLET_EXECUTED_TRANSACTION = "WALLET_EXECUTED_TRANSACTION"
    WALLET_TRANSACTED_WITH = "WALLET_TRANSACTED_WITH"  # on-chain fact: both addresses appear in transfer(s); NOT shared ownership
    TRANSACTION_SWAPPED_TOKEN = "TRANSACTION_SWAPPED_TOKEN"
    TRANSACTION_TRANSFERRED_TOKEN = "TRANSACTION_TRANSFERRED_TOKEN"
    WEBSITE_HAS_DOMAIN = "WEBSITE_HAS_DOMAIN"
    # Derived (virtual) edges produced by the graph layer from observation provenance:
    SOURCE_DESCRIBES_ENTITY = "SOURCE_DESCRIBES_ENTITY"
    ENTITY_REFERENCED_BY_SOURCE = "ENTITY_REFERENCED_BY_SOURCE"


class ObservationEntityRole(StrEnum):
    SUBJECT = "subject"  # what the observation is about
    MENTIONED = "mentioned"  # referenced inside it


class TaskStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    REUSED = "reused"  # satisfied from earlier knowledge, provider not re-run
    FAILED = "failed"  # will be retried while attempts remain
    DEAD = "dead"  # dead-letter: attempts exhausted
    REJECTED = "rejected"
    SKIPPED = "skipped"  # no capable provider / depth exceeded / denied by policy


class ToolRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    UNAVAILABLE = "unavailable"


class MatchStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class Role(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class PolicyAction(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class ClaimReview(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ClaimVerification(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"  # only ever set from a deterministic verification, never from an LLM
    CONTRADICTED = "contradicted"
