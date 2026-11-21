"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge, Empty, ErrorLine, Page, RelationshipDrawer, short, when } from "@/components/ui";

const TYPES = ["social_account", "public_profile", "channel", "post", "website", "domain", "wallet", "token", "contract", "transaction", "organization", "document"];

function Profile({ id, onOpen }: { id: string; onOpen: (id: string) => void }) {
  const [tab, setTab] = useState("overview");
  const [rel, setRel] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["profile", id], queryFn: () => api<any>(`/entities/${id}`) });
  const p = q.data;
  if (q.isLoading) return <div className="muted">Loading…</div>;
  if (q.error) return <ErrorLine error={q.error} />;
  if (!p) return null;
  const e = p.entity;
  return (
    <div>
      <div className="row">
        <h2 className="grow">{e.display_name ?? e.canonical_identifier}</h2>
        <Badge value={e.entity_type} /><Badge value={e.resolution_status} />
        <Link href={`/graph?root=${e.id}`}>Open in graph</Link>
      </div>
      <div className="mono muted">{e.canonical_identifier}</div>
      <div className="notice">{p.evidence_notice}</div>
      <div className="tabs">
        {["overview", "relationships", "observations", "sources", "claims", "timeline"].map((t) => (
          <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>

      {tab === "overview" && (
        <>
          <div className="grid g3">
            <div className="card stat">{p.observations.length}<small>observations</small></div>
            <div className="card stat">{p.relationships.length}<small>relationships</small></div>
            <div className="card stat">{p.sources.length}<small>sources</small></div>
          </div>
          <div className="muted">First observed {when(e.first_observed_at)}, last observed {when(e.last_observed_at)}, last update {when(p.last_update)}</div>
          <h3>Verified on-chain events ({p.verified_onchain_events.length})</h3>
          {p.verified_onchain_events.length === 0 ? <div className="muted">None. Nothing about this entity has been verified against a blockchain provider.</div> : (
            <table><tbody>{p.verified_onchain_events.map((o: any) => <tr key={o.id}><td><Badge value="verified" /></td><td>{o.source}</td><td>{when(o.observed_at)}</td></tr>)}</tbody></table>
          )}
          {p.failed_verifications.length > 0 && (
            <div className="warnbox">{p.failed_verifications.length} blockchain lookup(s) failed. Failure is not evidence either way.</div>
          )}
          <h3>Known identifiers & aliases</h3>
          <div className="row">{p.known_identifiers.map((i: string) => <code key={i} className="badge">{i}</code>)}</div>
          {p.aliases.length > 0 && <table><tbody>{p.aliases.map((a: any) => <tr key={a.alias}><td className="mono">{a.alias}</td><td>{a.type}</td><td className="muted">{a.basis}</td></tr>)}</tbody></table>}
          {p.contradictions.length > 0 && <div className="warnbox">{p.contradictions.length} contradicted relationship(s). See the relationships tab.</div>}
          {p.match_candidates.length > 0 && <div className="warnbox">{p.match_candidates.length} possible match(es) await review in Intelligence KB. They are not merged.</div>}
          {p.pending_discovery_tasks.length > 0 && <div className="notice">{p.pending_discovery_tasks.length} discovery task(s) open for this entity.</div>}
        </>
      )}

      {tab === "relationships" && (
        p.relationships.length === 0 ? <Empty>No relationships recorded.</Empty> : (
          <table>
            <thead><tr><th>Type</th><th>Other entity</th><th>Status</th><th>Review</th><th>Last observed</th></tr></thead>
            <tbody>
              {p.relationships.map((r: any) => (
                <tr key={r.id} className="click" onClick={() => setRel(r.id)}>
                  <td>{r.direction === "in" ? "incoming " : "outgoing "}<code>{r.relationship_type}</code></td>
                  <td><a href="#" onClick={(ev) => { ev.preventDefault(); ev.stopPropagation(); onOpen(r.other.id); }}>{short(r.other.label ?? r.other.canonical_identifier, 36)}</a><div className="muted">{r.other.entity_type}</div></td>
                  <td><Badge value={r.status} /></td><td><Badge value={r.review_status} /></td><td>{when(r.last_observed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      )}

      {tab === "observations" && (
        p.observations.length === 0 ? <Empty>No observations.</Empty> : (
          <table>
            <thead><tr><th>Type</th><th>Source</th><th>Seq</th><th>Observed</th><th>Evidence</th></tr></thead>
            <tbody>{p.observations.map((o: any) => <tr key={o.id}><td>{o.observation_type}</td><td>{short(o.source, 40)}</td><td>{o.seq}</td><td>{when(o.observed_at)}</td><td><Badge value={o.evidence_type === "blockchain" ? "verified" : "unverified"} title={o.evidence_type} /> {o.evidence_type}</td></tr>)}</tbody>
          </table>
        )
      )}

      {tab === "sources" && (
        p.sources.length === 0 ? <Empty>No sources.</Empty> : (
          <table>
            <thead><tr><th>Source</th><th>Type</th><th>Status</th><th>Observations</th><th>Last observed</th></tr></thead>
            <tbody>{p.sources.map((s: any) => <tr key={s.id}><td>{short(s.label, 50)}{s.stale && <> <Badge value="historical" title="stale" /></>}</td><td>{s.source_type}</td><td><Badge value={s.collection_status} /></td><td>{s.observation_count}</td><td>{when(s.last_observed_at)}</td></tr>)}</tbody>
          </table>
        )
      )}

      {tab === "claims" && (
        <>
          <div className="warnbox">Claims are LLM-extracted statements. They are never facts until a person reviews them, and even then they remain claims.</div>
          {p.claims.length === 0 ? <Empty>No claims.</Empty> : (
            <table>
              <thead><tr><th>Claim</th><th>Review</th><th>Verification</th></tr></thead>
              <tbody>{p.claims.map((c: any) => <tr key={c.id}><td>{c.subject_reference} <code>{c.predicate}</code> {c.object_reference}<div className="muted">“{short(c.supporting_text, 120)}” ({c.model})</div></td><td><Badge value={c.review_status} /></td><td><Badge value={c.verification_status} /></td></tr>)}</tbody>
            </table>
          )}
        </>
      )}

      {tab === "timeline" && (
        p.timeline.length === 0 ? <Empty>No events.</Empty> : (
          <table><tbody>{p.timeline.map((t: any, i: number) => <tr key={i}><td>{when(t.at)}</td><td><Badge value={t.kind.replace(/_/g, " ")} /></td><td>{t.title}</td><td>{t.status && <Badge value={t.status} />}{t.verified_onchain && <Badge value="verified" />}</td></tr>)}</tbody></table>
        )
      )}
      {rel && <RelationshipDrawer id={rel} onClose={() => setRel(null)} />}
    </div>
  );
}

export default function Entities() {
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  useEffect(() => { const id = new URLSearchParams(window.location.search).get("id"); if (id) setSel(id); }, []);
  const list = useQuery({ queryKey: ["entities", q, type], queryFn: () => api<any[]>("/entities", { params: { q, entity_type: type, limit: 100 } }) });
  return (
    <Page title="Entities" sub="Canonical entities. Similar names are never merged automatically.">
      <div className="grid" style={{ gridTemplateColumns: "minmax(280px, 1fr) 2fr" }}>
        <div>
          <div className="row">
            <input className="grow" placeholder="Search identifier or name…" value={q} onChange={(e) => setQ(e.target.value)} />
            <select value={type} onChange={(e) => setType(e.target.value)}><option value="">all types</option>{TYPES.map((t) => <option key={t}>{t}</option>)}</select>
          </div>
          <ErrorLine error={list.error} />
          <div className="card" style={{ marginTop: 8, maxHeight: "75vh", overflow: "auto" }}>
            {list.data && list.data.length === 0 ? <Empty>No entities match.</Empty> : (
              <table><tbody>
                {list.data?.map((e) => (
                  <tr key={e.id} className={`click ${sel === e.id ? "sel" : ""}`} onClick={() => setSel(e.id)}>
                    <td>{short(e.display_name ?? e.canonical_identifier, 34)}<div className="mono muted">{short(e.canonical_identifier, 40)}</div></td>
                    <td><Badge value={e.entity_type} /></td>
                  </tr>
                ))}
              </tbody></table>
            )}
          </div>
        </div>
        <div className="card">{sel ? <Profile id={sel} onOpen={setSel} /> : <Empty>Select an entity to see its profile, evidence and timeline.</Empty>}</div>
      </div>
    </Page>
  );
}
