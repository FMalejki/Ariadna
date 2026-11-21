"""Sherlock adapter (username discovery via the official CLI).

STATUS: IMPLEMENTED against Sherlock 0.16.x CLI (flags and CSV columns verified from upstream source:
sherlock_project/sherlock.py -> `--csv`, `--folderoutput`, `--print-found`, `--timeout`, `--no-color`; CSV header
username,name,url_main,url_user,exists,http_status,response_time_s; status enum Claimed/Available/Unknown/
Illegal/WAF). Sherlock is MIT licensed. Sherlock performs live HTTP checks against many sites.

A "Claimed" result only means a page exists at a URL derived from the SAME USERNAME STRING. It is
tool-generated discovery, never identity evidence: ingestion creates a PROPOSED USERNAME_MATCHES_PROFILE
relationship plus a pending MatchCandidate - never an alias or merge.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, datetime

from app.domain import EntityType, EvidenceType, ObservationType, RelationshipType, SourceType
from app.normalize import Reference, SkippedReference, classify_url
from app.providers.base import (
    HealthStatus,
    IntelligenceProvider,
    Link,
    NormalizedObservation,
    ProviderContext,
    ProviderError,
    ProviderInput,
    ProviderInputError,
    ProviderUnavailable,
    RawResult,
    SourceRef,
)
from app.providers.cli import cleanup, resolve_binary, run_controlled

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,32}$")  # strict; also blocks option-like strings (no leading '-')
_EXPECTED_HEADER = ["username", "name", "url_main", "url_user", "exists", "http_status", "response_time_s"]


class SherlockProvider(IntelligenceProvider):
    name = "sherlock"
    version = "0.16.x-adapter-1"
    kind = "cli"
    category = "username_lookup"
    description = "Username presence check across many sites via the Sherlock CLI (tool-generated discovery)."
    capabilities = ["username_lookup"]
    evidence_class = EvidenceType.TOOL_GENERATED_DISCOVERY
    docs_url = "https://github.com/sherlock-project/sherlock"

    def _username(self, ref: Reference) -> str | None:
        handle = ref.attrs.get("handle") or ref.attrs.get("name")
        if isinstance(handle, str) and handle.startswith("@"):
            handle = handle[1:]
        return handle if isinstance(handle, str) and USERNAME_RE.match(handle) and not handle.startswith(("-", ".")) else None

    def supports(self, ref: Reference) -> bool:
        return ref.entity_type in (EntityType.SOCIAL_ACCOUNT.value, EntityType.PUBLIC_PROFILE.value) and self._username(ref) is not None

    def validate_input(self, data: ProviderInput) -> None:
        if data.reference is None or self._username(data.reference) is None:
            raise ProviderInputError("sherlock needs a social account/profile with a valid username (3-32 chars)")

    def execute(self, data: ProviderInput, ctx: ProviderContext) -> RawResult:
        username = self._username(data.reference)  # type: ignore[arg-type]
        s = ctx.settings
        binary = resolve_binary(s.sherlock_binary)
        # workdir is created inside run_controlled; we need it as folderoutput, so output goes to a sub-path of cwd
        argv = [
            binary, "--no-color", "--print-found", "--csv", "--folderoutput", "out",
            "--timeout", str(min(max(int(s.sherlock_timeout_seconds // 6), 5), 30)), "--", username,
        ]
        res = run_controlled(argv, timeout=s.sherlock_timeout_seconds)
        try:
            csv_path = res.workdir / "out" / f"{username}.csv"
            if res.exit_code != 0 and not csv_path.exists():
                raise ProviderError(f"sherlock exited with {res.exit_code}: {res.stderr[-500:] or res.stdout[-500:]}")
            if not csv_path.exists():
                raise ProviderError("sherlock produced no CSV output")
            raw = csv_path.read_bytes()
            return RawResult(
                raw=raw, content_type="text/csv", exit_code=res.exit_code,
                log=(res.stdout[-1500:] or "")[:1500], parameters={"username": username, "argv0": "sherlock"},
                parsed={"username": username},
            )
        finally:
            cleanup(res)

    def normalize_result(self, raw: RawResult, data: ProviderInput, ctx: ProviderContext) -> list[NormalizedObservation]:
        return [parse_sherlock_csv(raw.raw.decode("utf-8", "replace"), data.reference, self.version, ctx.now)]  # type: ignore[arg-type]

    def health_check(self, ctx: ProviderContext) -> HealthStatus:
        try:
            binary = resolve_binary(ctx.settings.sherlock_binary)
        except ProviderUnavailable as exc:
            return HealthStatus("unavailable", str(exc))
        try:
            res = run_controlled([binary, "--version"], timeout=20)
        except ProviderError as exc:
            return HealthStatus("degraded", str(exc))
        try:
            ok = res.exit_code == 0
            return HealthStatus("healthy" if ok else "degraded", (res.stdout or res.stderr).strip()[:200])
        finally:
            cleanup(res)


def parse_sherlock_csv(text: str, subject: Reference, tool_version: str, now: datetime | None = None) -> NormalizedObservation:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != _EXPECTED_HEADER:
        raise ProviderError(f"unexpected sherlock CSV header: {reader.fieldnames}")
    username = subject.attrs.get("handle") or subject.attrs.get("name") or ""
    claimed: list[dict] = []
    other_status = 0
    for row in reader:
        if row["exists"] == "Claimed":
            claimed.append({"site": row["name"], "url": row["url_user"], "http_status": row["http_status"]})
        else:
            other_status += 1
    claimed.sort(key=lambda c: (c["site"], c["url"]))
    links: list[Link] = []
    for c in claimed:
        ref = classify_url(c["url"])
        if isinstance(ref, SkippedReference):
            continue
        if ref.canonical_id == subject.canonical_id:
            continue
        links.append(
            Link(
                ref, RelationshipType.USERNAME_MATCHES_PROFILE.value,
                explanation=f"Sherlock reports a page for username '{username}' on {c['site']} (same username string; identity NOT established)",
                basis="sherlock: status Claimed",
                match_candidate_rule="same_username_string",
                follow_up=False,
            )
        )
    payload = {
        "tool": "sherlock",
        "username": username,
        "claimed": claimed,
        "claimed_count": len(claimed),
        "note": "Wynik „claimed” to wyłącznie dopasowanie ciągu znaków nazwy użytkownika; nie dowodzi, że te konta kontroluje ta sama osoba.",
    }
    return NormalizedObservation(
        source=SourceRef(
            SourceType.OSINT_TOOL.value, f"sherlock:{username.lower()}", None, f"sherlock:{username.lower()}",
            f"Sherlock username search '{username}'", access_method="cli",
        ),
        subject=subject,
        observation_type=ObservationType.USERNAME_HIT.value,
        observed_at=now or datetime.now(UTC),
        payload=payload,
        external_id=f"sherlock:{username.lower()}",
        links=links,
    )
