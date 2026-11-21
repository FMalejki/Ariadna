"""Authorized manual import provider - the reliable, always-available ingestion path.

STATUS: IMPLEMENTED. Input is user-supplied material the analyst is authorized to provide (e.g. an export of
their own channel, a saved public page, a platform-API export). It makes no network requests.

Payload schema (also validated here):
  {
    "batch_id": "optional string",
    "authorization_note": "REQUIRED: who authorized this data / where it came from",
    "records": [ {
        "url": "https://x.com/example/status/123",   # REQUIRED, deterministic classification decides the type
        "text": "...", "bio": "...", "display_name": "...",
        "links": ["https://..."],                       # outbound links shown on the profile/page
        "published_at": "ISO-8601", "observed_at": "ISO-8601", "external_id": "..."
    } ]
  }
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.domain import EntityType, EvidenceType, ObservationType, RelationshipType
from app.normalize import InvalidReference, Reference, SkippedReference, classify_url
from app.providers.base import (
    HealthStatus,
    IntelligenceProvider,
    Link,
    NormalizedObservation,
    ProviderContext,
    ProviderInput,
    ProviderInputError,
    RawResult,
    SourceRef,
)

MAX_RECORDS = 500
MAX_TEXT = 20_000


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProviderInputError(f"invalid ISO-8601 timestamp {value!r}") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class ManualImportProvider(IntelligenceProvider):
    name = "manual_import"
    version = "1.0.0"
    kind = "import"
    category = "import"
    description = "Import authorized, user-supplied observations (JSON). No network access."
    capabilities = ["import:profile", "import:post", "import:page"]
    evidence_class = EvidenceType.DIRECT_SOURCE_STATEMENT

    def supports(self, ref: Reference) -> bool:
        return False  # imports are analyst-initiated, never auto-routed by discovery

    def validate_input(self, data: ProviderInput) -> None:
        p = data.payload
        if not isinstance(p.get("authorization_note"), str) or not p["authorization_note"].strip():
            raise ProviderInputError("authorization_note is required for imports")
        records = p.get("records")
        if not isinstance(records, list) or not records:
            raise ProviderInputError("records must be a non-empty list")
        if len(records) > MAX_RECORDS:
            raise ProviderInputError(f"at most {MAX_RECORDS} records per import")
        for i, rec in enumerate(records):
            if not isinstance(rec, dict) or not isinstance(rec.get("url"), str):
                raise ProviderInputError(f"record {i}: 'url' is required")
            for key in ("text", "bio"):
                if len(str(rec.get(key) or "")) > MAX_TEXT:
                    raise ProviderInputError(f"record {i}: {key} exceeds {MAX_TEXT} characters")
            _parse_dt(rec.get("published_at"))
            _parse_dt(rec.get("observed_at"))

    def execute(self, data: ProviderInput, ctx: ProviderContext) -> RawResult:
        raw = json.dumps(data.payload, sort_keys=True, ensure_ascii=False).encode()
        return RawResult(
            raw=raw, parsed=data.payload, log=f"{len(data.payload['records'])} records received",
            parameters={"batch_id": data.payload.get("batch_id"), "authorization_note": data.payload["authorization_note"]},
        )

    def normalize_result(self, raw: RawResult, data: ProviderInput, ctx: ProviderContext) -> list[NormalizedObservation]:
        payload = raw.parsed
        retrieved = ctx.now or datetime.now(UTC)
        out: list[NormalizedObservation] = []
        skipped: list[str] = []
        for rec in payload["records"]:
            ref = classify_url(rec["url"])
            if isinstance(ref, SkippedReference):
                skipped.append(f"{rec['url']}: {ref.reason}")
                continue
            etype = ref.entity_type
            if etype in (EntityType.SOCIAL_ACCOUNT.value, EntityType.PUBLIC_PROFILE.value, EntityType.CHANNEL.value):
                otype, snapshot = ObservationType.SOCIAL_PROFILE.value, True
            elif etype == EntityType.POST.value:
                otype, snapshot = ObservationType.SOCIAL_POST.value, False
            else:
                otype, snapshot = ObservationType.WEB_PAGE.value, True
            links_in = [str(u) for u in (rec.get("links") or []) if isinstance(u, str)]
            norm_payload = {
                "kind": otype,
                "url": ref.url,
                "display_name": rec.get("display_name"),
                "text": rec.get("text"),
                "bio": rec.get("bio"),
                "links": links_in,
            }  # import metadata (batch_id, authorization_note) lives on the ToolRun + raw artifact, NOT here: it must not change the content hash
            links: list[Link] = []
            if etype == EntityType.POST.value:
                author = _author_reference(ref)
                if author is not None:
                    rel = (
                        RelationshipType.CHANNEL_PUBLISHED_POST.value
                        if author.entity_type == EntityType.CHANNEL.value
                        else RelationshipType.ACCOUNT_PUBLISHED_POST.value
                    )
                    links.append(
                        Link(
                            author, rel, direction="in",
                            explanation=f"Post URL {ref.url} is published under {author.display_name}",
                            basis="deterministic URL parse (author segment of post URL)", follow_up=True,
                        )
                    )
            out.append(
                NormalizedObservation(
                    source=SourceRef(
                        ref.source_type, ref.canonical_id, ref.url, ref.canonical_id,
                        rec.get("display_name") or ref.display_name, access_method="authorized_import",
                    ),
                    subject=ref,
                    observation_type=otype,
                    observed_at=_parse_dt(rec.get("observed_at")) or retrieved,
                    published_at=_parse_dt(rec.get("published_at")),
                    payload=norm_payload,
                    external_id=rec.get("external_id") or ref.canonical_id,
                    text_fields=[str(rec.get("text") or ""), str(rec.get("bio") or ""), *links_in],
                    links=links,
                    snapshot=snapshot,
                    platform_hint=ref.platform,
                )
            )
        raw.log += ("; skipped: " + " | ".join(skipped)) if skipped else ""
        return out

    def health_check(self, ctx: ProviderContext) -> HealthStatus:
        return HealthStatus("healthy", "local import; no external dependency")


def _author_reference(post: Reference) -> Reference | None:
    if post.platform == "x" and post.attrs.get("author_handle"):
        h = post.attrs["author_handle"]
        return Reference(
            EntityType.SOCIAL_ACCOUNT.value, f"x:{h}", f"@{h}", "social_account", f"https://x.com/{h}",
            platform="x", url=f"https://x.com/{h}", attrs={"handle": h},
        )
    if post.platform == "telegram" and post.attrs.get("channel"):
        n = post.attrs["channel"]
        return Reference(
            EntityType.CHANNEL.value, f"telegram:{n}", f"t.me/{n}", "channel", f"https://t.me/{n}",
            platform="telegram", url=f"https://t.me/{n}", attrs={"name": n},
        )
    return None


# InvalidReference is re-exported for callers validating seeds
__all__ = ["ManualImportProvider", "InvalidReference"]
