"""Project Wiki service (spec 10). Engineering memory only - never intelligence data.

Source of truth is the markdown files in docs/LLM_WIKI (versioned in git). The DB mirrors them into
WikiPage/WikiRevision so revisions are queryable from the API. `sync_from_disk` is idempotent (a revision is only
added when the content hash changes). Writes through the API write the file first, then the revision.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import WikiPage, WikiRevision

STATUS_MARKERS = ("IMPLEMENTED", "PARTIALLY_IMPLEMENTED", "PLANNED", "PROPOSED", "UNVERIFIED", "DEPRECATED")
_SAFE = re.compile(r"^[A-Za-z0-9_\-./]+\.md$")


def wiki_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        cand = p / "docs" / "LLM_WIKI"
        if cand.is_dir():
            return cand
    return here.parents[3] / "docs" / "LLM_WIKI"


def safe_path(rel: str) -> Path:
    if not _SAFE.match(rel) or ".." in Path(rel).parts:
        raise ValueError("invalid wiki path")
    root = wiki_root().resolve()
    path = (root / rel).resolve()
    if root not in path.parents:
        raise ValueError("path escapes wiki root")
    return path


def _title(content: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    return m.group(1).strip() if m else fallback


def _record(session: Session, rel: str, content: str, author: str, note: str) -> tuple[WikiPage, bool]:
    digest = hashlib.sha256(content.encode()).hexdigest()
    page = session.scalar(select(WikiPage).where(WikiPage.path == rel))
    if page is None:
        page = WikiPage(path=rel, title=_title(content, rel))
        session.add(page)
        session.flush()
    if session.scalar(select(WikiRevision.id).where(WikiRevision.page_id == page.id, WikiRevision.content_hash == digest)):
        return page, False
    n = (session.scalar(select(func.max(WikiRevision.revision_number)).where(WikiRevision.page_id == page.id)) or 0) + 1
    rev = WikiRevision(page_id=page.id, revision_number=n, content=content, content_hash=digest, author=author, change_note=note)
    session.add(rev)
    session.flush()
    page.current_revision_id, page.title = rev.id, _title(content, rel)
    return page, True


def sync_from_disk(session: Session, author: str = "sync") -> dict:
    root = wiki_root()
    added = unchanged = 0
    if root.is_dir():
        for f in sorted(root.rglob("*.md")):
            rel = f.relative_to(root).as_posix()
            _, new = _record(session, rel, f.read_text(encoding="utf-8"), author, "sync from disk")
            added += int(new)
            unchanged += int(not new)
    return {"root": str(root), "revisions_added": added, "unchanged": unchanged}


def list_pages(session: Session) -> list[dict]:
    return [
        {"path": p.path, "title": p.title, "updated_at": p.updated_at.isoformat(), "current_revision_id": p.current_revision_id}
        for p in session.scalars(select(WikiPage).order_by(WikiPage.path))
    ]


def get_page(session: Session, rel: str) -> dict | None:
    page = session.scalar(select(WikiPage).where(WikiPage.path == rel))
    if page is None or page.current_revision_id is None:
        return None
    rev = session.get(WikiRevision, page.current_revision_id)
    assert rev is not None
    revs = session.execute(
        select(WikiRevision.revision_number, WikiRevision.author, WikiRevision.created_at, WikiRevision.change_note).where(WikiRevision.page_id == page.id).order_by(WikiRevision.revision_number.desc())
    ).all()
    return {
        "path": page.path, "title": page.title, "content": rev.content, "revision": rev.revision_number,
        "statuses": status_markers(rev.content),
        "history": [{"revision": r[0], "author": r[1], "at": r[2].isoformat(), "note": r[3]} for r in revs],
    }


def write_page(session: Session, rel: str, content: str, author: str, note: str) -> dict:
    if not note.strip():
        raise ValueError("a change note is required")
    path = safe_path(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _record(session, rel, content, author, note)
    return get_page(session, rel)  # type: ignore[return-value]


def status_markers(content: str) -> dict[str, int]:
    return {m: len(re.findall(rf"STATUS:\s*{m}\b", content)) for m in STATUS_MARKERS if re.search(rf"STATUS:\s*{m}\b", content)}


def lint(session: Session | None = None) -> list[str]:
    """Wiki hygiene: required pages exist, status markers are valid. Returned as human-readable problems."""
    problems: list[str] = []
    root = wiki_root()
    required = [
        "START_HERE.md", "PROJECT_OVERVIEW.md", "PRODUCT_VISION.md", "ARCHITECTURE.md", "SYSTEM_COMPONENTS.md", "DATA_MODEL.md", "DATA_FLOWS.md",
        "ENTITY_TYPES.md", "RELATIONSHIP_TYPES.md", "PROVIDER_CATALOG.md", "SOURCE_POLICIES.md", "EVIDENCE_MODEL.md", "DISCOVERY_ENGINE.md",
        "KNOWLEDGE_GRAPH.md", "LLM_POLICY.md", "SECURITY_AND_PRIVACY.md", "IMPLEMENTATION_STATUS.md", "ROADMAP.md", "GLOSSARY.md",
    ]
    for r in required:
        if not (root / r).is_file():
            problems.append(f"missing required page {r}")
    if not (root / "decisions" / "README.md").is_file():
        problems.append("missing decisions/README.md")
    for f in root.rglob("*.md"):
        for m in re.findall(r"STATUS:\s*([A-Z_]+)", f.read_text(encoding="utf-8")):
            if m not in STATUS_MARKERS:
                problems.append(f"{f.relative_to(root)}: unknown status marker {m}")
    return problems
