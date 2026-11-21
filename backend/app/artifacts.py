"""Content-addressed artifact store (S3-compatible in production; local filesystem backend implemented).

STATUS: local filesystem backend IMPLEMENTED. S3/MinIO backend PLANNED (same `ArtifactStore` interface).
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import utcnow
from app.models import Artifact


class ArtifactStore(Protocol):
    def put(self, content_hash: str, data: bytes) -> str: ...
    def get(self, location: str) -> bytes: ...
    def delete(self, location: str) -> None: ...


class LocalArtifactStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def put(self, content_hash: str, data: bytes) -> str:
        rel = Path(content_hash[:2]) / content_hash
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        return f"file://{rel.as_posix()}"

    def _path(self, location: str) -> Path:
        if not location.startswith("file://"):
            raise ValueError("unsupported artifact location")
        path = (self.root / location.removeprefix("file://")).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("artifact path escapes store root")
        return path

    def get(self, location: str) -> bytes:
        return self._path(location).read_bytes()

    def delete(self, location: str) -> None:
        self._path(location).unlink(missing_ok=True)


_store: ArtifactStore | None = None


def get_store() -> ArtifactStore:
    global _store
    if _store is None:
        _store = LocalArtifactStore(get_settings().artifact_dir)
    return _store


def set_store(store: ArtifactStore | None) -> None:
    global _store
    _store = store


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_artifact(
    session: Session,
    data: bytes,
    *,
    source_id: str | None,
    content_type: str = "application/json",
    capture_method: str = "provider",
    metadata: dict | None = None,
) -> Artifact:
    """Store bytes content-addressed; identical content returns the existing Artifact (deduplicated)."""
    digest = sha256_hex(data)
    existing = session.scalar(select(Artifact).where(Artifact.content_hash == digest))
    if existing is not None:
        return existing
    location = get_store().put(digest, data)
    days = get_settings().artifact_retention_days
    art = Artifact(
        source_id=source_id,
        content_hash=digest,
        storage_location=location,
        content_type=content_type,
        capture_method=capture_method,
        size=len(data),
        retention_policy="default" if days is None else f"{days}d",
        retain_until=None if days is None else utcnow() + timedelta(days=days),
        meta=metadata or {},
    )
    session.add(art)
    session.flush()
    return art


def purge_expired(session: Session) -> int:
    """Delete raw bytes of artifacts past retention. Metadata + hash are kept so provenance stays auditable."""
    now = utcnow()
    n = 0
    for art in session.scalars(select(Artifact).where(Artifact.retain_until.is_not(None), Artifact.purged_at.is_(None))):
        if art.retain_until <= now:
            get_store().delete(art.storage_location)
            art.purged_at = now
            n += 1
    return n
