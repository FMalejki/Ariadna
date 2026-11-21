"""Shared API helpers: role dependencies, pagination, graph filter parsing."""

from __future__ import annotations

from datetime import datetime

from app.db import SessionDep
from app.security import admin, analyst, current_user, viewer
from app.services.graph import GraphFilters

__all__ = ["SessionDep", "admin", "analyst", "viewer", "current_user", "parse_graph_filters", "csv_set"]


def csv_set(value: str | None) -> set[str] | None:
    items = {v.strip() for v in (value or "").split(",") if v.strip()}
    return items or None


def parse_graph_filters(
    entity_types: str | None = None,
    relationship_types: str | None = None,
    statuses: str | None = None,
    source_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    include_sources: bool = False,
    investigation_id: str | None = None,
) -> GraphFilters:
    return GraphFilters(
        entity_types=csv_set(entity_types), relationship_types=csv_set(relationship_types), statuses=csv_set(statuses),
        source_id=source_id, since=since, until=until, include_sources=include_sources, investigation_id=investigation_id,
    )
