# Knowledge graph

STATUS: IMPLEMENTED (backend `services/graph.py`, frontend `src/app/graph/page.tsx`; backend tested in `test_api.py`; the frontend graph page has **not** been exercised in a browser)

There is no graph database. The graph is a view over `entities`, `relationships` and observation provenance, computed on demand and bounded by `OSINT_GRAPH_MAX_NODES` (default 200).

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /graph/view?roots=&investigation_id=&statuses=&limit=` | Initial bounded subgraph around root entities. |
| `GET /graph/neighbors/{entity_id}` | Expand one node. |
| `GET /graph/path?start=&end=` | Shortest path (BFS), excluding rejected edges. |
| `GET /graph/search?q=` | Entity lookup by identifier or name. |

Responses are `{nodes, edges, truncated}`. Merged entities resolve to their winner. Filters: entity types, relationship types, statuses, source, time range, investigation. Source nodes and `SOURCE_DESCRIBES_ENTITY` edges appear only when `include_sources=true`.

## UI behavior

Starts from a seed (search or `?root=`), double-click expands, edge click opens the evidence drawer (`GET /relationships/{id}`). Edge styling follows status (dashed = proposed, solid green = verified, red = contradicted, dotted amber = historical). A `truncated` warning is shown when the bound is hit.
