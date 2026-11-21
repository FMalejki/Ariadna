"use client";

import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Core, ElementDefinition } from "cytoscape";
import { api, type GraphEdge, type GraphNode, type Subgraph } from "@/lib/api";
import { label } from "@/lib/i18n";
import { Badge, ErrorLine, Page, RelationshipDrawer, short } from "@/components/ui";
import { explorerLinks, safeHttpUrl } from "@/lib/links";

const TYPE_COLOR: Record<string, string> = {
  social_account: "#2b3a9e", public_profile: "#4f6bd6", post: "#7e92e0", channel: "#0f6e7d", website: "#7a4fb0", domain: "#a98fd0",
  wallet: "#94590a", token: "#b87410", contract: "#c4692a", transaction: "#7d8a96", organization: "#2f8f6b", document: "#aab4bd", source: "#5b6773",
};
const STATUSES = ["proposed", "supported", "corroborated", "verified", "contradicted", "historical", "rejected"];

function toElements(nodes: GraphNode[], edges: GraphEdge[]): ElementDefinition[] {
  const ids = new Set(nodes.map((n) => n.id));
  return [
    ...nodes.map((n) => ({ group: "nodes" as const, data: { ...n, label: short(n.label, 28) } })),
    ...edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ group: "edges" as const, data: { ...e } })),
  ];
}

/** Links so the analyst can check the page or the address on a public explorer themselves. */
function NodeLinks({ node }: { node: GraphNode }) {
  const isTx = node.entity_type === "transaction";
  const chain = node.entity_type === "wallet" || node.entity_type === "contract" || node.entity_type === "token" || isTx
    ? explorerLinks(node.network, isTx ? "tx" : "address", isTx ? node.hash : node.address)
    : [];
  const page = safeHttpUrl(node.url);
  if (chain.length === 0 && !page) return null;
  return (
    <div>
      {page && <div><a href={page} target="_blank" rel="noopener noreferrer">Otwórz stronę</a> <span className="muted">{short(page, 50)}</span></div>}
      {chain.length > 0 && (
        <div className="row">
          <span className="muted">Zobacz w eksploratorze:</span>
          {chain.map((l) => <a key={l.url} href={l.url} target="_blank" rel="noopener noreferrer">{l.label}</a>)}
        </div>
      )}
      {chain.length > 0 && node.network === "evm" && <div className="muted">Sieć nie jest ustalona dla tego adresu, więc pokazano każdy znany łańcuch.</div>}
      <div className="muted">Linki zewnętrzne służą do własnej weryfikacji i nie są dowodem.</div>
    </div>
  );
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
  const [busy, setBusy] = useState(false);
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

  const expand = useCallback(async (id: string, type?: string) => {
    setError(null);
    setMsg(null);
    try {
      if (type === "wallet") {
        // Wallet: make sure its on-chain history is stored (a stored lookup is reused), link the top counterparties, one step.
        setBusy(true);
        const r = await api<any>("/leads/graph-expand", { method: "POST", body: { entity_id: id } });
        setMsg(
          r.lookup?.unavailable || (r.lookup?.status && r.lookup.status !== "succeeded")
            ? `Zapytanie do blockchaina nie powiodło się: ${r.lookup?.error ?? r.lookup?.status}. Pokazano tylko to, co jest już zapisane.`
            : `${r.lookup?.reused_prior_run ? "Użyto zapisanego zapytania do blockchaina" : "Odpytano węzeł"}; ` +
              `powiązano nowych kontrahentów: ${r.linked_new}, już obecnych: ${r.added.length - r.linked_new}` +
              `${r.skipped_over_limit ? `, niepowiązano kolejnych: ${r.skipped_over_limit} (limit)` : ""}. ${r.note}`,
        );
      }
      merge(await api<Subgraph>(`/graph/neighbors/${id}`, { params: { statuses: statusParam, limit: 40 } }));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
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
          { selector: "node", style: { label: "data(label)", color: "#17212b", "font-size": 10, "text-valign": "bottom", "text-margin-y": 4, width: 22, height: 22, "background-color": (n: any) => TYPE_COLOR[n.data("entity_type")] ?? "#7d8a96", "border-width": 1, "border-color": "#ffffff" } },
          { selector: "node[?verified]", style: { "border-width": 3, "border-color": "#17724a" } },
          { selector: "node[resolution_status = 'ambiguous'], node[resolution_status = 'unresolved']", style: { "border-style": "dashed", "border-color": "#94590a", "border-width": 2 } },
          { selector: "node[kind = 'source']", style: { shape: "round-rectangle", width: 18, height: 14 } },
          { selector: "edge", style: { width: 1.5, "line-color": "#7d8a96", "target-arrow-color": "#7d8a96", "target-arrow-shape": "triangle", "curve-style": "bezier", label: "data(relationship_type)", "font-size": 7, color: "#566472", "text-rotation": "autorotate", "text-background-color": "#ffffff", "text-background-opacity": 0.85 } },
          { selector: "edge[status = 'proposed']", style: { "line-style": "dashed", "line-color": "#5b6773", "target-arrow-color": "#5b6773" } },
          { selector: "edge[status = 'supported']", style: { "line-color": "#3d5f82", "target-arrow-color": "#3d5f82" } },
          { selector: "edge[status = 'corroborated']", style: { "line-color": "#0f6e7d", "target-arrow-color": "#0f6e7d", width: 2.5 } },
          { selector: "edge[status = 'verified']", style: { "line-color": "#17724a", "target-arrow-color": "#17724a", width: 3 } },
          { selector: "edge[status = 'contradicted']", style: { "line-color": "#b3261e", "target-arrow-color": "#b3261e", width: 3 } },
          { selector: "edge[status = 'historical']", style: { "line-style": "dotted", "line-color": "#94590a", "target-arrow-color": "#94590a" } },
          { selector: "edge[status = 'rejected']", style: { opacity: 0.3, "line-style": "dotted" } },
          { selector: ":selected", style: { "overlay-color": "#2b3a9e", "overlay-opacity": 0.25 } },
          { selector: ".path", style: { "line-color": "#7a4fb0", "target-arrow-color": "#7a4fb0", "background-color": "#7a4fb0", width: 4 } },
        ],
      });
      cy.on("tap", "node", (ev) => { setEdgeId(null); setSelectedNode(ev.target.data() as GraphNode); });
      cy.on("dbltap", "node", (ev) => expandRef.current(ev.target.id(), ev.target.data("entity_type")));
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
      if (!res.found) { setMsg("Nie znaleziono ścieżki w granicach limitu głębokości."); return; }
      const sg = await api<Subgraph>("/graph/view", { params: { roots: res.node_ids.join(","), limit: 100 } });
      merge(sg);
      res.node_ids.forEach((id: string) => cy.getElementById(id).addClass("path"));
      res.edge_ids.forEach((id: string) => cy.getElementById(id).addClass("path"));
      setMsg(`Wyróżniono ścieżkę o długości ${res.length}.`);
    } catch (e) {
      setError(e);
    }
  }

  return (
    <Page title="Graf wiedzy" sub="Widok ograniczony. Kliknij dwukrotnie węzeł, aby rozwinąć jego sąsiadów. Kliknij krawędź, aby zobaczyć jej dowody.">
      <div className="row">
        <input placeholder="Szukaj encji (min. 2 znaki)…" aria-label="Szukaj encji" spellCheck={false} value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 280 }} />
        <select multiple size={1} value={statuses} onChange={(e) => setStatuses(Array.from(e.target.selectedOptions).map((o) => o.value))} aria-label="Filtr statusów krawędzi" title="Filtr statusów krawędzi (Ctrl+kliknięcie wybiera kilka)" style={{ width: 160 }}>
          {STATUSES.map((s) => <option key={s} value={s}>{label(s)}</option>)}
        </select>
        <button className="ghost" onClick={() => setStatuses([])}>Wszystkie statusy</button>
        <span className="muted">węzły: {counts.n}, krawędzie: {counts.e}</span>
        <button className="ghost" onClick={() => { cyRef.current?.elements().remove(); setCounts({ n: 0, e: 0 }); setTruncated(false); setSelectedNode(null); }}>Wyczyść</button>
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
      {search.data && search.data.length === 0 && q.length >= 2 && <div className="muted">Żadna encja nie pasuje do „{q}”.</div>}
      <ErrorLine error={error} />
      {truncated && <div className="warnbox">Widok został skrócony dla czytelności. Rozwiń wybrane węzły lub przefiltruj po statusie, aby zobaczyć więcej.</div>}
      {msg && <div className="notice">{msg}</div>}
      <div className="graphwrap" ref={box} />
      {counts.n === 0 && <div className="muted">Wyszukaj encję lub otwórz element początkowy z sekcji Rozpoznanie, aby wypełnić graf.</div>}
      <div className="legend" style={{ marginTop: 8 }}>
        {([
          ["zweryfikowane w blockchainie (gruba, ciągła)", "#17724a", ""],
          ["potwierdzone niezależnie (ciągła)", "#0f6e7d", ""],
          ["poparte dowodem (cienka, ciągła)", "#3d5f82", ""],
          ["wstępne, niezweryfikowany trop (kreskowana)", "#5b6773", "dashed"],
          ["sprzeczne (gruba, ciągła)", "#b3261e", ""],
          ["historyczne (kropkowana)", "#94590a", "dotted"],
        ] as const).map(([label, c, style]) => (
          <span key={label} style={{ color: c }}><i className={style} /><span style={{ color: "var(--muted)" }}>{label}</span></span>
        ))}
        <span>Obramowanie węzła: zielone = zweryfikowany w blockchainie, kreskowane bursztynowe = nierozpoznany lub niejednoznaczny.</span>
      </div>
      {selectedNode && (
        <aside className="drawer" role="dialog" aria-label="Szczegóły węzła">
          <div className="row"><h2 className="grow">{selectedNode.label}</h2><button type="button" className="ghost sm" onClick={() => setSelectedNode(null)}>Zamknij</button></div>
          <div className="row"><Badge value={selectedNode.entity_type} /><Badge value={selectedNode.verified ? "verified" : "unverified"} /><Badge value={selectedNode.resolution_status} /></div>
          <p className="mono" translate="no">{selectedNode.canonical_identifier}</p>
          <NodeLinks node={selectedNode} />
          <div className="row">
            <button disabled={busy} onClick={() => expand(selectedNode.id, selectedNode.entity_type)}>{busy ? "Rozwijanie…" : "Rozwiń sąsiadów"}</button>
            <a href={`/entities?id=${selectedNode.id}`}>Otwórz profil</a>
          </div>
          <h3>Najkrótsza ścieżka</h3>
          <div className="row">
            <button className="ghost" onClick={() => setPathStart(selectedNode)}>Ustaw jako początek ścieżki</button>
            <button className="ghost" onClick={findPath} disabled={!pathStart || pathStart.id === selectedNode.id}>Znajdź ścieżkę od początku do tego węzła</button>
          </div>
          <div className="muted">{pathStart ? `Początek: ${short(pathStart.label, 36)}` : "Wybierz węzeł początkowy, a następnie zaznacz węzeł końcowy."}</div>
        </aside>
      )}
      {edgeId && <RelationshipDrawer id={edgeId} onClose={() => setEdgeId(null)} />}
    </Page>
  );
}
