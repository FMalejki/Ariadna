"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { label } from "@/lib/i18n";
import { Badge, Drawer, Empty, ErrorLine, Page, short, when } from "@/components/ui";

function SourceDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["source", id], queryFn: () => api<any>(`/sources/${id}`) });
  const s = q.data;
  return (
    <Drawer title="Źródło" onClose={onClose}>
      <ErrorLine error={q.error} />
      {s && (
        <>
          <div className="row"><Badge value={s.collection_status} /><Badge value={s.source_type} /><span className="muted">wiarygodność: {s.reliability}</span></div>
          <p className="mono" translate="no">{s.canonical_url ?? s.canonical_key}</p>
          {s.collection_restrictions && <div className="warnbox">{s.collection_restrictions}</div>}
          <div className="muted">Odkryto: {when(s.first_discovered_at)}, ostatnia obserwacja: {when(s.last_observed_at)}, przez: {s.provider ?? "—"} ({s.access_method})</div>
          <h3>Historia obserwacji ({s.observations.length})</h3>
          {s.observations.map((o: any) => (
            <div key={o.id} className="ev"><b>{o.observation_type}</b> nr {o.seq}, {when(o.observed_at)}<pre className="json">{JSON.stringify(o.payload, null, 2).slice(0, 800)}</pre></div>
          ))}
        </>
      )}
    </Drawer>
  );
}

export default function Sources() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  const list = useQuery({ queryKey: ["sources", q, status], queryFn: () => api<any[]>("/sources", { params: { q, collection_status: status, limit: 100 } }) });
  const stale = useQuery({ queryKey: ["sources-stale"], queryFn: () => api<any[]>("/sources/stale") });
  return (
    <Page title="Źródła" sub="Skąd pochodzi każda obserwacja i czy może być już nieaktualna.">
      {stale.data && stale.data.length > 0 && <div className="warnbox">Liczba źródeł nieaktualnych, ograniczonych lub niedostępnych: {stale.data.length}. Ich obserwacje mogą już nie odzwierciedlać rzeczywistości.</div>}
      <div className="row">
        <input className="grow" placeholder="Szukaj źródeł…" aria-label="Szukaj źródeł" value={q} onChange={(e) => setQ(e.target.value)} />
        <select aria-label="Status źródła" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">wszystkie statusy</option>{["available", "unavailable", "restricted", "stale"].map((s) => <option key={s} value={s}>{label(s)}</option>)}</select>
      </div>
      <ErrorLine error={list.error} />
      <div className="card" style={{ marginTop: 8 }}>
        {list.data && list.data.length === 0 ? <Empty>Brak źródeł.</Empty> : (
          <table>
            <thead><tr><th>Źródło</th><th>Typ</th><th>Status</th><th>Obserwacje</th><th>Dostawca</th><th>Ostatnia obserwacja</th></tr></thead>
            <tbody>{list.data?.map((s) => <tr key={s.id} className="click" onClick={() => setSel(s.id)}><td>{short(s.display_name ?? s.canonical_key, 50)}</td><td>{s.source_type}</td><td><Badge value={s.collection_status} /></td><td>{s.observation_count}</td><td>{s.provider}</td><td>{when(s.last_observed_at)}</td></tr>)}</tbody>
          </table>
        )}
      </div>
      {sel && <SourceDetail id={sel} onClose={() => setSel(null)} />}
    </Page>
  );
}
