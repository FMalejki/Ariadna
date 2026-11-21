"""LLM-assisted claim extraction and summaries (spec 11). Strict validation; claims are never facts.

Contract:
  extract_claims(observation)  -> Claim rows (review=pending, verification=unverified, model+prompt version recorded)
  A re-run with a different model / prompt version adds NEW versioned claims and supersedes (never deletes) the old
  ones. Invalid LLM output is rejected wholesale and reported; nothing partial is stored for a rejected item.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.domain import ClaimReview, ClaimVerification
from app.llm import prompts
from app.llm.client import LLMClient, LLMDisabled, LLMError
from app.models import Claim, Observation

MAX_CLAIMS_PER_OBSERVATION = 20
MAX_TEXT_CHARS = 8000


class ClaimOut(BaseModel):
    claim_type: str
    subject_reference: str | None = Field(default=None, max_length=600)
    predicate: str = Field(min_length=1, max_length=200)
    object_reference: str | None = Field(default=None, max_length=600)
    supporting_text: str = Field(min_length=1, max_length=2000)

    @field_validator("claim_type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in prompts.CLAIM_TYPES:
            raise ValueError(f"unknown claim_type {v!r}")
        return v


class ClaimsEnvelope(BaseModel):
    claims: list[ClaimOut] = Field(max_length=MAX_CLAIMS_PER_OBSERVATION)


class SummaryEnvelope(BaseModel):
    summary: str = Field(min_length=1, max_length=4000)
    citations: list[str] = Field(min_length=1)


@dataclass
class ExtractionResult:
    observation_id: str
    created: list[Claim] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    error: str | None = None


def observation_text(obs: Observation) -> str:
    """All string values of the normalized payload, in stable order - this is what the model may quote from."""
    parts: list[str] = []

    def walk(v) -> None:
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            for k in sorted(v):
                walk(v[k])
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(obs.normalized_payload)
    return "\n".join(parts)[:MAX_TEXT_CHARS]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def parse_json_object(raw: str) -> dict:
    """Accept bare JSON or a single fenced block; nothing else. No best-effort repair of malformed output."""
    text = raw.strip()
    m = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def validate_claims(raw: str, source_text: str) -> tuple[list[ClaimOut], list[dict]]:
    """Returns (accepted, rejected). A claim whose supporting_text is not a verbatim excerpt of the source is rejected."""
    try:
        env = ClaimsEnvelope.model_validate(parse_json_object(raw))
    except (ValueError, ValidationError) as exc:
        raise ValueError(f"LLM output failed schema validation: {type(exc).__name__}") from exc
    haystack = _norm(source_text)
    accepted: list[ClaimOut] = []
    rejected: list[dict] = []
    for c in env.claims:
        if _norm(c.supporting_text) not in haystack:
            rejected.append({"claim": c.model_dump(), "reason": "supporting_text is not a verbatim excerpt of the observation"})
        else:
            accepted.append(c)
    return accepted, rejected


def _claim_hash(c: ClaimOut) -> str:
    key = "|".join([c.claim_type, c.subject_reference or "", c.predicate, c.object_reference or "", _norm(c.supporting_text)])
    return hashlib.sha256(key.encode()).hexdigest()


def extract_claims(session: Session, client: LLMClient, obs: Observation, actor: str = "llm") -> ExtractionResult:
    result = ExtractionResult(observation_id=obs.id)
    text = observation_text(obs)
    if not text.strip():
        result.error = "observation has no text to extract from"
        return result
    try:
        raw = client.complete(prompts.CLAIMS_SYSTEM, prompts.claims_user_prompt(obs.id, obs.observation_type, text))
    except LLMDisabled as exc:
        result.error = str(exc)
        return result
    except LLMError as exc:
        result.error = str(exc)
        return result
    try:
        accepted, rejected = validate_claims(raw, text)
    except ValueError as exc:
        result.error = str(exc)
        audit(session, actor, "llm.output_rejected", "observation", obs.id, model=client.model, prompt=prompts.CLAIMS_PROMPT_VERSION)
        return result
    result.rejected = rejected
    fresh_hashes: set[str] = set()
    for c in accepted:
        h = _claim_hash(c)
        fresh_hashes.add(h)
        exists = session.scalar(
            select(Claim.id).where(
                Claim.observation_id == obs.id, Claim.claim_hash == h,
                Claim.model_name == client.model, Claim.prompt_version == prompts.CLAIMS_PROMPT_VERSION,
            )
        )
        if exists:
            continue  # same model + prompt + claim: idempotent
        row = Claim(
            observation_id=obs.id, claim_hash=h, claim_type=c.claim_type, subject_reference=c.subject_reference,
            predicate=c.predicate, object_reference=c.object_reference, supporting_text=c.supporting_text,
            published_at=obs.published_at, extraction_method="llm", model_provider=client.provider, model_name=client.model,
            prompt_version=prompts.CLAIMS_PROMPT_VERSION,
            review_status=ClaimReview.PENDING.value, verification_status=ClaimVerification.UNVERIFIED.value,
        )
        session.add(row)
        result.created.append(row)
    session.flush()
    # Older versions (other model / prompt) are superseded, not deleted: history stays queryable.
    if result.created:
        newest = result.created[0].id
        for old in session.scalars(
            select(Claim).where(Claim.observation_id == obs.id, Claim.superseded_by.is_(None), Claim.id.not_in([r.id for r in result.created]))
        ):
            if (old.model_name, old.prompt_version) != (client.model, prompts.CLAIMS_PROMPT_VERSION):
                old.superseded_by = newest
    audit(session, actor, "llm.claims_extracted", "observation", obs.id, created=len(result.created), rejected=len(rejected), model=client.model, prompt=prompts.CLAIMS_PROMPT_VERSION)
    return result


def review_claim(session: Session, claim: Claim, decision: str, actor: str) -> Claim:
    """Analyst decision on a claim. Accepting does NOT make it verified and does NOT create a relationship."""
    if decision not in (ClaimReview.ACCEPTED.value, ClaimReview.REJECTED.value):
        raise ValueError("decision must be accepted | rejected")
    claim.review_status = decision
    audit(session, actor, f"claim.{decision}", "claim", claim.id)
    return claim


def summarize_entity(client: LLMClient, entity_label: str, observations: list[Observation]) -> dict:
    """Cited summary. Citations must reference observations that were actually supplied."""
    supplied = {o.id: o for o in observations[:25]}
    payload = [{"id": o.id, "observation_type": o.observation_type, "text": observation_text(o)[:1500]} for o in supplied.values()]
    raw = client.complete(prompts.SUMMARY_SYSTEM, prompts.summary_user_prompt(entity_label, payload))
    try:
        env = SummaryEnvelope.model_validate(parse_json_object(raw))
    except (ValueError, ValidationError) as exc:
        raise ValueError("LLM summary failed schema validation") from exc
    bad = [c for c in env.citations if c not in supplied]
    if bad:
        raise ValueError(f"LLM summary cites unknown observations: {bad}")
    return {
        "summary": env.summary, "citations": env.citations, "model": f"{client.provider}/{client.model}",
        "prompt_version": prompts.SUMMARY_PROMPT_VERSION,
        "notice": "AI-generated summary of stored observations. Not verified fact; check the cited observations.",
    }
