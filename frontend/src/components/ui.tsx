"use client";

import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { api } from "@/lib/api";
import { safeHttpUrl } from "@/lib/links";
import { label, pl, when } from "@/lib/i18n";

export { when };

/** Map any backend status string to a badge colour class. Unknown values render as neutral. */
const BADGE: Record<string, string> = {
  verified: "b-verified", corroborated: "b-corroborated", supported: "b-supported", proposed: "b-proposed",
  contradicted: "b-contradicted", rejected: "b-rejected", historical: "b-historical", redacted: "b-redacted",
  pending: "b-pending", pending_approval: "b-pending", unverified: "b-unverified", unreviewed: "b-unverified",
  completed: "b-ok", succeeded: "b-ok", success: "b-ok", timed_out: "b-bad", healthy: "b-ok", ok: "b-ok", degraded: "b-pending", approved: "b-ok", confirmed: "b-ok", reused: "b-info", queued: "b-info", running: "b-info",
  failed: "b-bad", dead: "b-bad", error: "b-bad", unavailable: "b-bad", skipped: "b-unverified", skipped_policy: "b-unverified", stopped_by_limit: "b-pending", aborted: "b-unverified", placeholder: "b-pending",
  implemented: "b-ok", partial: "b-pending", active: "b-ok",
};

export function Badge({ value, title }: { value: string | null | undefined; title?: string }) {
  if (!value) return null;
  return (
    <span className={`badge ${BADGE[value.toLowerCase()] ?? ""}`} title={title ?? `wartość: ${value}`}>
      {label(value)}
    </span>
  );
}

/** Mention repeatability as WORDS only (never a percentage or a colour scale that reads as "verified"). Always annotated. */
const LEVEL_PL: Record<string, string> = { low: "niska", medium: "średnia", high: "wysoka" };
export function Repeatability({ r }: { r: { distinct_urls: number; pages_with_address: number; level: string | null; disclaimer: string } | null | undefined }) {
  if (!r) return null;
  const pages = (n: number) => `${n} ${pl(n, "strona", "strony", "stron")}`;
  return (
    <span className="muted" title={r.disclaimer}>
      {r.level ? (
        <>
          powtarzalność wzmianki: <b>{LEVEL_PL[r.level] ?? r.level}</b> ({pages(r.distinct_urls)} z imieniem i nazwiskiem lub loginem obok tego adresu
          {r.pages_with_address > r.distinct_urls ? `, adres widoczny na ${pages(r.pages_with_address)}` : ""})
        </>
      ) : (
        <>żadna strona nie umieszcza imienia i nazwiska ani loginu obok tego adresu (adres widoczny na: {pages(r.pages_with_address)})</>
      )}
      {" "}<i>To nie jest dowód własności. Różne adresy URL mogą kopiować tę samą treść.</i>
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
      {sub && <p className="sub">{sub}</p>}
      {children}
    </div>
  );
}

export function Drawer({ onClose, title, children }: { onClose: () => void; title: string; children: ReactNode }) {
  return (
    <aside className="drawer" role="dialog" aria-label={title} onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}>
      <div className="row">
        <h2 className="grow">{title}</h2>
        <button type="button" className="ghost sm" onClick={onClose}>Zamknij</button>
      </div>
      {children}
    </aside>
  );
}

export function short(s: string | null | undefined, n = 40): string {
  if (!s) return "";
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

/** Edge detail: answers “why does the system believe this?” with every evidence item and its provenance. */
export function RelationshipDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["relationship", id], queryFn: () => api<any>(`/relationships/${id}`) });
  const r = q.data;
  return (
    <Drawer onClose={onClose} title="Dowody dla relacji">
      {q.isLoading && <div className="muted">Wczytywanie…</div>}
      <ErrorLine error={q.error} />
      {r && (
        <>
          <div className="row">
            <Badge value={r.status} />
            <code>{r.relationship_type}</code>
            <Badge value={r.review_status} />
          </div>
          <p>
            <b>{r.source_entity?.display_name}</b> <span className="muted" translate="no">({r.source_entity?.canonical_identifier})</span>
            <br />powiązane z <b>{r.target_entity?.display_name}</b> <span className="muted" translate="no">({r.target_entity?.canonical_identifier})</span>
          </p>
          {r.status === "proposed" && (
            <div className="warnbox">Relacja tylko wstępna: pojedynczy niezweryfikowany trop, a nie potwierdzony fakt.</div>
          )}
          {r.has_contradiction && <div className="warnbox">Źródła są ze sobą sprzeczne co do tej relacji.</div>}
          {r.is_stale && <div className="warnbox">Wszystkie dowody pochodzą ze starszej wersji źródła.</div>}
          <div className="muted">
            Pierwsza obserwacja: {when(r.first_observed_at)}, ostatnia: {when(r.last_observed_at)}, metoda: {r.resolution_method ?? "—"}
          </div>
          <h3>Dowody ({r.evidence.length})</h3>
          {r.evidence.map((e: any) => (
            <div key={e.id} className={`ev ${e.support_status === "contradicts" ? "contra" : ""}`}>
              <div className="row">
                <Badge value={e.support_status} />
                <code>{e.evidence_type}</code>
                <span className="muted">siła dowodu: {e.evidence_strength ?? "—"}</span>
                {e.stale && <Badge value="historical" title="zastąpione nowszą obserwacją" />}
              </div>
              <div>{e.explanation}</div>
              <div className="muted">
                Źródło: {e.observation.source ?? "nieznane"}, obserwacja nr {e.observation.seq}, zaobserwowano {when(e.observation.observed_at)}.{" "}
                Dostawca: {e.provider ? `${e.provider.name}@${e.provider.version}` : "—"}, wiarygodność źródła: {e.source_reliability}.
              </div>
              {e.observation.source_url && (
                <div className="mono" translate="no">
                  {short(e.observation.source_url, 70)}{" "}
                  {safeHttpUrl(e.observation.source_url) && (
                    <a href={safeHttpUrl(e.observation.source_url)!} target="_blank" rel="noopener noreferrer">Otwórz stronę źródłową</a>
                  )}
                </div>
              )}
              <div className="muted">podstawa: {e.confidence_basis}</div>
              {e.observation.raw_artifact_id && <div className="muted">surowy artefakt {short(e.observation.raw_artifact_id, 12)} (zob. Centrum dowodów)</div>}
            </div>
          ))}
        </>
      )}
    </Drawer>
  );
}
