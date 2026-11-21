"use client";

import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { api } from "@/lib/api";

/** Map any backend status string to a badge colour class. Unknown values render as neutral. */
const BADGE: Record<string, string> = {
  verified: "b-verified", corroborated: "b-corroborated", supported: "b-supported", proposed: "b-proposed",
  contradicted: "b-contradicted", rejected: "b-rejected", historical: "b-historical", redacted: "b-redacted",
  pending: "b-pending", pending_approval: "b-pending", unverified: "b-unverified", unreviewed: "b-unverified",
  completed: "b-ok", succeeded: "b-ok", success: "b-ok", timed_out: "b-bad", healthy: "b-ok", ok: "b-ok", degraded: "b-pending", approved: "b-ok", confirmed: "b-ok", reused: "b-info", queued: "b-info", running: "b-info",
  failed: "b-bad", dead: "b-bad", error: "b-bad", unavailable: "b-bad", skipped: "b-unverified", placeholder: "b-pending",
  implemented: "b-ok", partial: "b-pending", active: "b-ok",
};

export function Badge({ value, title }: { value: string | null | undefined; title?: string }) {
  if (!value) return null;
  return (
    <span className={`badge ${BADGE[value.toLowerCase()] ?? ""}`} title={title}>
      {value.replace(/_/g, " ")}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ErrorLine({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="err">{error instanceof Error ? error.message : String(error)}</div>;
}

export function Page({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  return (
    <div>
      <h1>{title}</h1>
      {sub && <div className="sub">{sub}</div>}
      {children}
    </div>
  );
}

export function Drawer({ onClose, title, children }: { onClose: () => void; title: string; children: ReactNode }) {
  return (
    <aside className="drawer" role="dialog" aria-label={title}>
      <div className="row">
        <h2 className="grow">{title}</h2>
        <button className="ghost sm" onClick={onClose}>Close</button>
      </div>
      {children}
    </aside>
  );
}

export function short(s: string | null | undefined, n = 40): string {
  if (!s) return "";
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

export function when(s: string | null | undefined): string {
  return s ? s.replace("T", " ").slice(0, 16) : "—";
}

/** Edge detail: answers “why does the system believe this?” with every evidence item and its provenance. */
export function RelationshipDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["relationship", id], queryFn: () => api<any>(`/relationships/${id}`) });
  const r = q.data;
  return (
    <Drawer onClose={onClose} title="Relationship evidence">
      {q.isLoading && <div className="muted">Loading…</div>}
      <ErrorLine error={q.error} />
      {r && (
        <>
          <div className="row">
            <Badge value={r.status} />
            <code>{r.relationship_type}</code>
            <Badge value={r.review_status} />
          </div>
          <p>
            <b>{r.source_entity?.display_name}</b> <span className="muted">({r.source_entity?.canonical_identifier})</span>
            <br />→ <b>{r.target_entity?.display_name}</b> <span className="muted">({r.target_entity?.canonical_identifier})</span>
          </p>
          {r.status === "proposed" && (
            <div className="warnbox">Proposed only: a single unverified lead. Not a confirmed fact.</div>
          )}
          {r.has_contradiction && <div className="warnbox">Sources disagree about this relationship.</div>}
          {r.is_stale && <div className="warnbox">All supporting evidence is from an older version of its source.</div>}
          <div className="muted">
            First observed {when(r.first_observed_at)} · last {when(r.last_observed_at)} · method {r.resolution_method ?? "—"}
          </div>
          <h3>Evidence ({r.evidence.length})</h3>
          {r.evidence.map((e: any) => (
            <div key={e.id} className={`ev ${e.support_status === "contradicts" ? "contra" : ""}`}>
              <div className="row">
                <Badge value={e.support_status} />
                <code>{e.evidence_type}</code>
                <span className="muted">strength {e.evidence_strength ?? "—"}</span>
                {e.stale && <Badge value="historical" title="superseded by a newer observation" />}
              </div>
              <div>{e.explanation}</div>
              <div className="muted">
                {e.observation.source ?? "source"} · seq {e.observation.seq} · observed {when(e.observation.observed_at)} ·{" "}
                provider {e.provider ? `${e.provider.name}@${e.provider.version}` : "—"} · reliability {e.source_reliability}
              </div>
              {e.observation.source_url && <div className="mono">{short(e.observation.source_url, 70)}</div>}
              <div className="muted">basis: {e.confidence_basis}</div>
              {e.observation.raw_artifact_id && <div className="muted">raw artifact {short(e.observation.raw_artifact_id, 12)} (see Evidence Center)</div>}
            </div>
          ))}
        </>
      )}
    </Drawer>
  );
}
