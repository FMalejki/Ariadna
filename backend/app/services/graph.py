"""Graph query layer (spec 7). Backed by the relational schema; every query is bounded.

Nodes are canonical entities (merged entities are folded into their survivor). Edges are Relationship rows
with evidence counts/status/provenance, plus *derived* provenance edges (SOURCE_DESCRIBES_ENTITY /
ENTITY_REFERENCED_BY_SOURCE) computed from Observation/ObservationEntity - these are marked `derived: true`
and carry no evidence rows of their own beyond the observation they come from.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain import EntityStatus, ObservationEntityRole, RelationshipType, SupportStatus
from app.models import (
    Entity,
    InvestigationItem,
    Observation,
    ObservationEntity,
    Relationship,
    RelationshipEvidence,
    Source,
)
from app.services import entities as ent_svc


@dataclass
class GraphFilters:
    entity_types: set[str] | None = None
    relationship_types: set[str] | None = None
    statuses: set[str] | None = None  # relationship status
    source_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    include_sources: bool = False
    investigation_id: str | None = None


@dataclass
class Subgraph:
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: dict[str, dict] = field(default_factory=dict)
    truncated: bool = False

    def as_dict(self) -> dict:
        return {"nodes": list(self.nodes.values()), "edges": list(self.edges.values()), "truncated": self.truncated}


def _node(ent: Entity, degree: int | None = None) -> dict:
    return {
        "id": ent.id, "kind": "entity", "label": ent.display_name or ent.canonical_identifier, "entity_type": ent.entity_type,
        "canonical_identifier": ent.canonical_identifier, "status": ent.status, "resolution_status": ent.resolution_status,
        "first_observed_at": ent.first_observed_at.isoformat() if ent.first_observed_at else None,
        "last_observed_at": ent.last_observed_at.isoformat() if ent.last_observed_at else None,
        "degree": degree,
        "verified": bool((ent.meta or {}).get("verification", {}).get("status") == "verified"),
        # Pointers the UI uses for "open the source page / open in an explorer" links (no new facts).
        "network": (ent.meta or {}).get("network"), "address": (ent.meta or {}).get("address"),
        "hash": (ent.meta or {}).get("hash"), "url": (ent.meta or {}).get("url"),
    }


def _edge_stats(session: Session, rel_ids: list[str]) -> dict[str, dict]:
    if not rel_ids:
        return {}
    rows = session.execute(
        select(
            RelationshipEvidence.relationship_id,
            RelationshipEvidence.support_status,
            func.count(),
            func.count(func.distinct(Observation.source_id)),
        )
        .join(Observation, Observation.id == RelationshipEvidence.observation_id)
        .where(RelationshipEvidence.relationship_id.in_(rel_ids))
        .group_by(RelationshipEvidence.relationship_id, RelationshipEvidence.support_status)
    ).all()
    stats: dict[str, dict] = {}
    for rid, support, n, sources in rows:
        s = stats.setdefault(rid, {"evidence_count": 0, "supporting": 0, "contradicting": 0, "distinct_sources": 0})
        s["evidence_count"] += n
        s["supporting" if support == SupportStatus.SUPPORTS.value else "contradicting"] += n
        s["distinct_sources"] = max(s["distinct_sources"], sources)
    return stats


def _edge(rel: Relationship, stats: dict, stale_days: int) -> dict:
    last = rel.last_observed_at
    stale = bool(last and (datetime.now(last.tzinfo) - last).days > stale_days) or rel.status == "historical"
    s = stats.get(rel.id, {"evidence_count": 0, "supporting": 0, "contradicting": 0, "distinct_sources": 0})
    return {
        "id": rel.id, "source": rel.source_entity_id, "target": rel.target_entity_id, "relationship_type": rel.relationship_type,
        "status": rel.status, "review_status": rel.review_status, "derived": False,
        "first_observed_at": rel.first_observed_at.isoformat() if rel.first_observed_at else None,
        "last_observed_at": last.isoformat() if last else None,
        "valid_from": rel.valid_from.isoformat() if rel.valid_from else None,
        "valid_to": rel.valid_to.isoformat() if rel.valid_to else None,
        "stale": stale, "resolution_method": rel.resolution_method, **s,
    }


def _fold(session: Session, rel: Relationship, cache: dict[str, str]) -> tuple[str, str]:
    def r(i: str) -> str:
        if i not in cache:
            cache[i] = ent_svc.resolve_id(session, i)
        return cache[i]
    return r(rel.source_entity_id), r(rel.target_entity_id)


def neighbors(session: Session, entity_id: str, f: GraphFilters, limit: int | None = None) -> Subgraph:
    """One-hop expansion around an entity (all members of its merge cluster), bounded and filtered."""
    settings = get_settings()
    limit = min(limit or settings.graph_max_nodes, settings.graph_max_nodes)
    cluster = ent_svc.cluster_ids(session, entity_id)
    root = cluster[0]
    stmt = select(Relationship).where(
        (Relationship.source_entity_id.in_(cluster)) | (Relationship.target_entity_id.in_(cluster))
    )
    if f.relationship_types:
        stmt = stmt.where(Relationship.relationship_type.in_(f.relationship_types))
    if f.statuses:
        stmt = stmt.where(Relationship.status.in_(f.statuses))
    if f.since:
        stmt = stmt.where(Relationship.last_observed_at >= f.since)
    if f.until:
        stmt = stmt.where(Relationship.first_observed_at <= f.until)
    if f.source_id:
        stmt = stmt.where(
            Relationship.id.in_(
                select(RelationshipEvidence.relationship_id)
                .join(Observation, Observation.id == RelationshipEvidence.observation_id)
                .where(Observation.source_id == f.source_id)
            )
        )
    rels = list(session.scalars(stmt.order_by(Relationship.last_observed_at.desc().nullslast()).limit(limit * 3)))
    cache: dict[str, str] = {}
    sg = Subgraph()
    root_ent = session.get(Entity, root)
    if root_ent is None or root_ent.status == EntityStatus.DELETED.value:
        return sg
    sg.nodes[root] = _node(root_ent)
    stats = _edge_stats(session, [r.id for r in rels])
    for rel in rels:
        s, t = _fold(session, rel, cache)
        if s == t:
            continue
        other = t if s == root else s
        if other not in sg.nodes:
            ent = session.get(Entity, other)
            if ent is None or ent.status == EntityStatus.DELETED.value:
                continue
            if f.entity_types and ent.entity_type not in f.entity_types:
                continue
            if len(sg.nodes) >= limit:
                sg.truncated = True
                continue
            sg.nodes[other] = _node(ent)
        e = _edge(rel, stats, settings.stale_after_days)
        e["source"], e["target"] = s, t
        sg.edges[rel.id] = e
    if f.include_sources:
        _add_source_edges(session, root, cluster, sg, limit)
    _fill_degrees(session, sg)
    return sg


def _add_source_edges(session: Session, root: str, cluster: list[str], sg: Subgraph, limit: int) -> None:
    rows = session.execute(
        select(Source, ObservationEntity.role, func.count(Observation.id), func.min(Observation.observed_at), func.max(Observation.observed_at))
        .join(Observation, Observation.source_id == Source.id)
        .join(ObservationEntity, ObservationEntity.observation_id == Observation.id)
        .where(ObservationEntity.entity_id.in_(cluster))
        .group_by(Source.id, ObservationEntity.role)
        .limit(limit)
    ).all()
    for src, role, n, first, last in rows:
        nid = f"source:{src.id}"
        if nid not in sg.nodes:
            if len(sg.nodes) >= limit:
                sg.truncated = True
                continue
            sg.nodes[nid] = {
                "id": nid, "kind": "source", "label": src.display_name or src.canonical_key, "entity_type": "source",
                "canonical_identifier": src.canonical_key, "status": src.collection_status, "resolution_status": None,
                "first_observed_at": None, "last_observed_at": src.last_observed_at.isoformat() if src.last_observed_at else None,
                "degree": None, "verified": False, "source_id": src.id, "url": src.canonical_url,
            }
        subject = role == ObservationEntityRole.SUBJECT.value
        etype = RelationshipType.SOURCE_DESCRIBES_ENTITY.value if subject else RelationshipType.ENTITY_REFERENCED_BY_SOURCE.value
        eid = f"derived:{src.id}:{root}:{etype}"
        sg.edges[eid] = {
            "id": eid, "source": nid, "target": root, "relationship_type": etype, "status": "supported", "review_status": "unreviewed",
            "derived": True, "first_observed_at": first.isoformat() if first else None, "last_observed_at": last.isoformat() if last else None,
            "valid_from": None, "valid_to": None, "stale": False, "resolution_method": "observation_provenance",
            "evidence_count": n, "supporting": n, "contradicting": 0, "distinct_sources": 1,
        }


def _fill_degrees(session: Session, sg: Subgraph) -> None:
    ent_ids = [n["id"] for n in sg.nodes.values() if n["kind"] == "entity"]
    if not ent_ids:
        return
    deg: dict[str, int] = {i: 0 for i in ent_ids}
    for col in (Relationship.source_entity_id, Relationship.target_entity_id):
        for eid, n in session.execute(select(col, func.count()).where(col.in_(ent_ids)).group_by(col)):
            deg[eid] += n
    for i, n in deg.items():
        sg.nodes[i]["degree"] = n


def initial_view(session: Session, f: GraphFilters, root_ids: list[str] | None, limit: int | None = None) -> Subgraph:
    """Starting point: given roots (or an investigation's entities) plus their immediate neighborhood - never the whole DB."""
    settings = get_settings()
    limit = min(limit or settings.graph_max_nodes, settings.graph_max_nodes)
    roots = list(root_ids or [])
    if not roots and f.investigation_id:
        roots = list(
            session.scalars(
                select(InvestigationItem.item_id).where(InvestigationItem.investigation_id == f.investigation_id, InvestigationItem.item_type == "entity")
            )
        )
    sg = Subgraph()
    seen: set[str] = set()
    for rid in roots[:limit]:
        survivor = ent_svc.resolve_id(session, rid)
        if survivor in seen:
            continue
        seen.add(survivor)
        sub = neighbors(session, survivor, f, limit)
        for k, v in sub.nodes.items():
            if len(sg.nodes) < limit or k in sg.nodes:
                sg.nodes[k] = v
            else:
                sg.truncated = True
        sg.edges.update({k: v for k, v in sub.edges.items() if v["source"] in sg.nodes and v["target"] in sg.nodes})
        sg.truncated = sg.truncated or sub.truncated
    return sg


def search_nodes(session: Session, q: str, limit: int = 20) -> list[dict]:
    like = f"%{q.lower()}%"
    rows = session.scalars(
        select(Entity)
        .where(Entity.status == EntityStatus.ACTIVE.value, (func.lower(Entity.canonical_identifier).like(like)) | (func.lower(Entity.display_name).like(like)))
        .limit(limit)
    )
    return [_node(e) for e in rows]


def find_path(session: Session, start_id: str, end_id: str, f: GraphFilters, max_depth: int | None = None) -> dict:
    """Shortest path (BFS) over entity relationships; excludes rejected edges and respects filters."""
    settings = get_settings()
    max_depth = min(max_depth or settings.graph_max_path_depth, settings.graph_max_path_depth)
    start, end = ent_svc.resolve_id(session, start_id), ent_svc.resolve_id(session, end_id)
    if start == end:
        return {"found": True, "node_ids": [start], "edge_ids": [], "length": 0}
    prev: dict[str, tuple[str, str] | None] = {start: None}
    frontier = deque([(start, 0)])
    visited_budget = settings.graph_max_nodes * 20
    cache: dict[str, str] = {}
    while frontier and visited_budget > 0:
        cur, d = frontier.popleft()
        if d >= max_depth:
            continue
        cluster = ent_svc.cluster_ids(session, cur)
        stmt = select(Relationship).where(
            ((Relationship.source_entity_id.in_(cluster)) | (Relationship.target_entity_id.in_(cluster))), Relationship.status != "rejected"
        )
        if f.relationship_types:
            stmt = stmt.where(Relationship.relationship_type.in_(f.relationship_types))
        if f.statuses:
            stmt = stmt.where(Relationship.status.in_(f.statuses))
        for rel in session.scalars(stmt.limit(500)):
            s, t = _fold(session, rel, cache)
            nxt = t if s == cur else s
            if nxt == cur or nxt in prev:
                continue
            prev[nxt] = (cur, rel.id)
            visited_budget -= 1
            if nxt == end:
                nodes, edges, node = [end], [], end
                while prev[node] is not None:
                    p, e = prev[node]  # type: ignore[misc]
                    edges.append(e)
                    nodes.append(p)
                    node = p
                return {"found": True, "node_ids": nodes[::-1], "edge_ids": edges[::-1], "length": len(edges)}
            frontier.append((nxt, d + 1))
    return {"found": False, "node_ids": [], "edge_ids": [], "length": None, "searched_depth": max_depth}
