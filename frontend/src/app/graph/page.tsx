"use client";

import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Core, ElementDefinition } from "cytoscape";
import { api, type GraphEdge, type GraphNode, type Subgraph } from "@/lib/api";
import { Badge, ErrorLine, Page, RelationshipDrawer, short } from "@/components/ui";

const TYPE_COLOR: Record<string, string> = {
  social_account: "#4c8dff", public_profile: "#60a5fa", post: "#7aa7ff", channel: "#38bdf8", website: "#a78bfa", domain: "#c4b5fd",
  wallet: "#f5b84a", token: "#f59e0b", contract: "#fb923c", transaction: "#94a3b8", organization: "#34d399", document: "#cbd5e1", source: "#475569",
};
const STATUSES = ["proposed", "supported", "corroborated", "verified", "contradicted", "historical", "rejected"];

function toElements(nodes: GraphNode[], edges: GraphEdge[]): ElementDefinition[] {
  const ids = new Set(nodes.map((n) => n.id));
  return [
    ...nodes.map((n) => ({ group: "nodes" as const, data: { ...n, label: short(n.label, 28) } })),
    ...edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ group: "edges" as const, data: { ...e } })),
  ];
}

export default function GraphPage() {
  const box = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const [roots, setRoots] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [statuses, setStatuses] = useState<string[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [edgeId, setEdgeId] = useState<string | null>(null);
  const [pathStart, setPathStart] = useState<GraphNode | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [counts, setCounts] = useState({ n: 0, e: 0 });
  const statusParam = statuses.join(",") || undefined;

  const search = useQuery({
    queryKey: ["graph-search", q],
    queryFn: () => api<GraphNode[]>("/graph/search", { params: { q } }),
    enabled: q.length >= 2,
  });

  const merge = useCallback((sg: Subgraph) => {
    const cy = cyRef.current;
    if (!cy) return;
    const have = new Set(cy.elements().map((el) => el.id()));
    const fresh = toElements(sg.nodes, sg.edges).filter((el) => !have.has(el.data.id as string));
    cy.add(fresh);
    setTruncated((t) => t || sg.truncated);
    setCounts({ n: cy.nodes().length, e: cy.edges().length });
    cy.layout({ name: "cose", animate: false, nodeDimensionsIncludeLabels: true, padding: 30 }).run();
  }, []);

  const loadRoots = useCallback(async (ids: string[]) => {
    const cy = cyRef.current;
    if (!cy || ids.length === 0) return;
    setError(null);
    setMsg(null);
    try {
      cy.elements().remove();
      setTruncated(false);
      merge(await api<Subgraph>("/graph/view", { params: { roots: ids.join(","), statuses: statusParam, limit: 100 } }));
    } catch (e) {
      setError(e);
    }
  }, [merge, statusParam]);

  const expand = useCallback(async (id: string) => {
    try {
      merge(await api<Subgraph>(`/graph/neighbors/${id}`, { params: { statuses: statusParam, limit: 40 } }));
    } catch (e) {
      setError(e);
    }
  }, [merge, statusParam]);

  // Initialise Cytoscape once (client only; dynamic import keeps it out of SSR).
  useEffect(() => {
    let destroyed = false;
    (async () => {
      const cytoscape = (await import("cytoscape")).default;
      if (destroyed || !box.current) return;
      const cy = cytoscape({
        container: box.current,
        minZoom: 0.2,
        maxZoom: 3,
        style: [
          { selector: "node", style: { label: "data(label)", color: "#d7dce6", "font-size": 10, "text-valign": "bottom", "text-margin-y": 4, width: 22, height: 22, "background-color": (n: any) => TYPE_COLOR[n.data("entity_type")] ?? "#64748b", "border-width": 1, "border-color": "#0b0e14" } },
          { selector: "node[?verified]", style: { "border-width": 3, "border-color": "#3ecf8e" } },
          { selector: "node[resolution_status = 'ambiguous'], node[resolution_status = 'unresolved']", style: { "border-style": "dashed", "border-color": "#f5b84a", "border-width": 2 } },
          { selector: "node[kind = 'source']", style: { shape: "round-rectangle", width: 18, height: 14 } },
          { selector: "edge", style: { width: 1.5, "line-color": "#64748b", "target-arrow-color": "#64748b", "target-arrow-shape": "triangle", "curve-style": "bezier", label: "data(relationship_type)", "font-size": 7, color: "#8a94a8", "text-rotation": "autorotate", "text-background-color": "#0a0d13", "text-background-opacity": 0.8 } },
          { selector: "edge[status = 'proposed']", style: { "line-style": "dashed", "line-color": "#475569", "target-arrow-color": "#475569" } },
          { selector: "edge[status = 'supported']", style: { "line-color": "#3b82a8", "target-arrow-color": "#3b82a8" } },
          { selector: "edge[status = 'corroborated']", style: { "line-color": "#38bdf8", "target-arrow-color": "#38bdf8", width: 2.5 } },
          { selector: "edge[status = 'verified']", style: { "line-color": "#3ecf8e", "target-arrow-color": "#3ecf8e", width: 3 } },
          { selector: "edge[status = 'contradicted']", style: { "line-color": "#ef5b6b", "target-arrow-color": "#ef5b6b", width: 3 } },
          { selector: "edge[status = 'historical']", style: { "line-style": "dotted", "line-color": "#f5b84a", "target-arrow-color": "#f5b84a" } },
          { selector: "edge[status = 'rejected']", style: { opacity: 0.3, "line-style": "dotted" } },
          { selector: ":selected", style: { "overlay-color": "#4c8dff", "overlay-opacity": 0.25 } },
          { selector: ".path", style: { "line-color": "#a78bfa", "target-arrow-color": "#a78bfa", "background-color": "#a78bfa", width: 4 } },
        ],
      });
      cy.on("tap", "node", (ev) => { setEdgeId(null); setSelectedNode(ev.target.data() as GraphNode); });
      cy.on("dbltap", "node", (ev) => expandRef.current(ev.target.id()));
      cy.on("tap", "edge", (ev) => { setSelectedNode(null); setEdgeId(ev.target.id()); });
      cy.on("tap", (ev) => { if (ev.target === cy) { setSelectedNode(null); } });
      cyRef.current = cy;
      const root = new URLSearchParams(window.location.search).get("root");
      if (root) setRoots([root]);
    })();
    return () => { destroyed = true; cyRef.current?.destroy(); cyRef.current = null; };
  }, []);

  const expandRef = useRef(expand);
  expandRef.current = expand;

  useEffect(() => { if (roots.length) void loadRoots(roots); }, [roots, loadRoots]);

  async function findPath() {
    const cy = cyRef.current;
    if (!cy || !selectedNode || !pathStart) return;
    try {
      const res = await api<any>("/graph/path", { params: { start: pathStart.id, end: selectedNode.id, statuses: statusParam } });
      cy.elements().removeClass("path");
      if (!res.found) { setMsg("No path found within the depth limit."); return; }
      const sg = await api<Subgraph>("/graph/view", { params: { roots: res.node_ids.join(","), limit: 100 } });
      merge(sg);
      res.node_ids.forEach((id: string) => cy.getElementById(id).addClass("path"));
      res.edge_ids.forEach((id: string) => cy.getElementById(id).addClass("path"));
      setMsg(`Path of length ${res.length} highlighted.`);
    } catch (e) {
      setError(e);
    }
  }

  return (
    <Page title="Knowledge Graph" sub="Bounded view. Double-click a node to expand its neighbours; click an edge to see its evidence.">
      <div className="row">
        <input placeholder="Search entity (min 2 chars)…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 280 }} />
        <select multiple size={1} value={statuses} onChange={(e) => setStatuses(Array.from(e.target.selectedOptions).map((o) => o.value))} title="Filter edges by status (ctrl-click for multiple)" style={{ width: 160 }}>
          {STATUSES.map((s) => <option key={s}>{s}</option>)}
        </select>
        <button className="ghost" onClick={() => setStatuses([])}>All statuses</button>
        <span className="muted">{counts.n} nodes · {counts.e} edges</span>
        <button className="ghost" onClick={() => { cyRef.current?.elements().remove(); setCounts({ n: 0, e: 0 }); setTruncated(false); setSelectedNode(null); }}>Clear</button>
      </div>
      {search.data && search.data.length > 0 && (
        <div className="card row">
          {search.data.map((n) => (
            <button key={n.id} className="ghost sm" onClick={() => { setRoots([n.id]); setQ(""); }}>
              {short(n.label, 30)} <span className="muted">{n.entity_type}</span>
            </button>
          ))}
        </div>
      )}
      {search.data && search.data.length === 0 && q.length >= 2 && <div className="muted">No entity matches “{q}”.</div>}
      <ErrorLine error={error} />
      {truncated && <div className="warnbox">The view was truncated to stay readable. Expand specific nodes or filter by status to see more.</div>}
      {msg && <div className="notice">{msg}</div>}
      <div className="graphwrap" ref={box} />
      {counts.n === 0 && <div className="muted">Search for an entity or open a seed from Discovery to populate the graph.</div>}
      <div className="legend" style={{ marginTop: 8 }}>
        {Object.entries({ "solid green = verified on-chain": "#3ecf8e", "cyan = corroborated": "#38bdf8", "blue = supported": "#3b82a8", "dashed = proposed (unverified lead)": "#475569", "red = contradicted": "#ef5b6b", "dotted amber = historical": "#f5b84a" }).map(([k, c]) => (
          <span key={k}><i style={{ background: c }} />{k}</span>
        ))}
        <span>green node border = on-chain verified · dashed amber border = unresolved/ambiguous</span>
      </div>
      {selectedNode && (
        <aside className="drawer">
          <div className="row"><h2 className="grow">{selectedNode.label}</h2><button className="ghost sm" onClick={() => setSelectedNode(null)}>Close</button></div>
          <div className="row"><Badge value={selectedNode.entity_type} /><Badge value={selectedNode.verified ? "verified" : "unverified"} /><Badge value={selectedNode.resolution_status} /></div>
          <p className="mono">{selectedNode.canonical_identifier}</p>
          <div className="row">
            <button onClick={() => expand(selectedNode.id)}>Expand neighbours</button>
            <a href={`/entities?id=${selectedNode.id}`}>Open profile</a>
          </div>
          <h3>Shortest path</h3>
          <div className="row">
            <button className="ghost" onClick={() => setPathStart(selectedNode)}>Set as path start</button>
            <button className="ghost" onClick={findPath} disabled={!pathStart || pathStart.id === selectedNode.id}>Find path from start to here</button>
          </div>
          <div className="muted">{pathStart ? `Start: ${short(pathStart.label, 36)}` : "Pick a start node, then select the end node."}</div>
        </aside>
      )}
      {edgeId && <RelationshipDrawer id={edgeId} onClose={() => setEdgeId(null)} />}
    </Page>
  );
}
