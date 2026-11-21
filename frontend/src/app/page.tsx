"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api } from "@/lib/api";
import { Badge, Empty, ErrorLine, Page, short, when } from "@/components/ui";

const ATTENTION: [string, string, string][] = [
  ["tasks_pending_approval", "Discovery tasks awaiting approval", "/discovery"],
  ["match_candidates_pending", "Entity match candidates to review", "/kb"],
  ["claims_pending_review", "LLM claims awaiting review", "/kb"],
  ["contradicted_relationships", "Contradicted relationships", "/kb"],
  ["stale_or_unavailable_sources", "Stale or unavailable sources", "/sources"],
  ["dead_tasks", "Dead-lettered tasks", "/discovery"],
];

export default function Overview() {
  const q = useQuery({ queryKey: ["overview"], queryFn: () => api<any>("/stats/overview") });
  const d = q.data;
  return (
    <Page title="Overview" sub="What the platform knows, what changed, and what needs an analyst.">
      <ErrorLine error={q.error} />
      {q.isLoading && <div className="muted">Loading…</div>}
      {d && (
        <>
          <div className="grid g4">
            {Object.entries(d.totals).map(([k, v]) => (
              <div key={k} className="card stat">{String(v)}<small>{k}</small></div>
            ))}
          </div>
          <h3>Needs attention</h3>
          <div className="grid g3">
            {ATTENTION.map(([key, label, href]) => (
              <Link key={key} href={href} className={`card ${d.attention[key] ? "attn" : ""}`}>
                <div className="stat">{d.attention[key] ?? 0}<small>{label}</small></div>
              </Link>
            ))}
          </div>
          <h3>New in the last 24 hours</h3>
          <div className="card row">
            <span>{d.last_24h.new_observations} observations</span>
            <span>{d.last_24h.new_entities} entities</span>
            <span>{d.last_24h.new_relationships} relationships</span>
          </div>
          <div className="grid g2">
            <div>
              <h3>Entities by type</h3>
              <div className="card">
                {Object.keys(d.entities_by_type).length === 0 ? <Empty>No entities yet. Start an investigation in Discovery.</Empty> : (
                  <table><tbody>{Object.entries(d.entities_by_type).map(([k, v]) => <tr key={k}><td>{k}</td><td>{String(v)}</td></tr>)}</tbody></table>
                )}
              </div>
            </div>
            <div>
              <h3>Relationships by evidence status</h3>
              <div className="card">
                {Object.keys(d.relationships_by_status).length === 0 ? <Empty>No relationships yet.</Empty> : (
                  <table><tbody>{Object.entries(d.relationships_by_status).map(([k, v]) => <tr key={k}><td><Badge value={k} /></td><td>{String(v)}</td></tr>)}</tbody></table>
                )}
              </div>
            </div>
          </div>
          <h3>Recent provider runs</h3>
          <div className="card">
            {d.recent_runs.length === 0 ? <Empty>No provider runs yet.</Empty> : (
              <table>
                <thead><tr><th>Provider</th><th>Target</th><th>Status</th><th>Started</th></tr></thead>
                <tbody>
                  {d.recent_runs.map((r: any) => (
                    <tr key={r.id}><td>{r.provider}</td><td className="mono">{short(r.target, 50)}</td><td><Badge value={r.status} /></td><td>{when(r.started_at)}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <h3>Providers</h3>
          <div className="row">
            {d.providers.map((p: any) => (
              <span key={p.name} className="badge" title={`health: ${p.last_health ?? "unknown"}`}>
                {p.name} · {p.implementation_status}{p.enabled ? "" : " · disabled"}
              </span>
            ))}
          </div>
        </>
      )}
    </Page>
  );
}
