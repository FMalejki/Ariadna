"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { label } from "@/lib/i18n";
import { Badge, Empty, ErrorLine, Page, short, when } from "@/components/ui";

const SAMPLE = JSON.stringify(
  [{ url: "https://x.com/example_handle", display_name: "Example", bio: "Analyst-provided bio text", links: ["https://example.org"] }],
  null, 2,
);

function Detail({ id }: { id: string }) {
  const qc = useQueryClient();
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["investigation", id], queryFn: () => api<any>(`/investigations/${id}`) });
  const changes = useQuery({ queryKey: ["inv-changes", id], queryFn: () => api<any>(`/investigations/${id}/changes`) });
  const [notes, setNotes] = useState<string | null>(null);
  const [records, setRecords] = useState(SAMPLE);
  const [auth, setAuth] = useState("");
  const [result, setResult] = useState<any>(null);
  const saveNotes = useMutation({ mutationFn: () => api(`/investigations/${id}/notes`, { method: "PUT", body: { notes: notes ?? "" } }), onSuccess: () => qc.invalidateQueries({ queryKey: ["investigation", id] }) });
  const doImport = useMutation({
    mutationFn: () => api<any>(`/investigations/${id}/import`, { body: { authorization_note: auth, records: JSON.parse(records) } }),
    onSuccess: (r) => { setResult(r); qc.invalidateQueries({ queryKey: ["investigation", id] }); },
  });
  const i = q.data;
  async function exportJson() {
    const data = await api<any>(`/investigations/${id}/export`);
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url; a.download = `investigation-${id}.json`; a.click(); URL.revokeObjectURL(url);
  }
  if (q.isLoading) return <div className="muted">Wczytywanie…</div>;
  if (!i) return <ErrorLine error={q.error} />;
  const kr = i.knowledge_reuse;
  return (
    <div>
      <div className="row"><h2 className="grow">{i.name}</h2><Badge value={i.status} /><button className="ghost sm" onClick={exportJson} disabled={!can("analyst")}>Eksportuj JSON</button></div>
      <div className="muted">Utworzono: {when(i.created_at)}, autor: {i.created_by}, głębokość zakresu: {i.scope?.max_depth}</div>
      <h3>Ponowne wykorzystanie wiedzy</h3>
      <div className="grid g4">
        <div className="card stat">{kr.reused_observations}<small>obserwacje wykorzystane ponownie</small></div>
        <div className="card stat">{kr.new_observations}<small>nowe obserwacje</small></div>
        <div className="card stat">{kr.reused_tasks}<small>uruchomienia dostawców wykorzystane ponownie</small></div>
        <div className="card stat">{kr.executed_tasks}<small>uruchomienia dostawców wykonane</small></div>
      </div>
      {changes.data && <div className="muted" style={{ marginTop: 6 }}>Od rozpoczęcia sprawy dodano: obserwacje {changes.data.new_observations}, encje {changes.data.new_entities}, relacje {changes.data.new_relationships}.</div>}
      <h3>Encje w zakresie sprawy ({i.entities.length})</h3>
      <div className="row">{i.entities.slice(0, 40).map((e: any) => <Link key={e.id} className="badge" href={`/entities?id=${e.id}`}>{short(e.display_name ?? e.canonical_identifier, 28)}</Link>)}</div>
      <h3>Zadania ({i.tasks.length})</h3>
      <table><tbody>{i.tasks.map((t: any) => <tr key={t.id}><td>{t.provider}</td><td className="mono">{short(t.target, 44)}</td><td>d{t.depth}</td><td><Badge value={t.status} /></td></tr>)}</tbody></table>
      <h3>Notatki</h3>
      <textarea rows={4} aria-label="Notatki" value={notes ?? i.notes ?? ""} onChange={(e) => setNotes(e.target.value)} disabled={!can("analyst")} />
      <button className="ghost sm" disabled={notes === null || !can("analyst")} onClick={() => saveNotes.mutate()}>Zapisz notatki</button>
      <h3>Import rekordów dostarczonych przez analityka</h3>
      <div className="notice">Rekordy są zapisywane jako <b>niezweryfikowane</b> obserwacje z dostawcy importu ręcznego. Oświadcz, że masz uprawnienia do wykorzystania tych danych. Oświadczenie jest zapisywane razem z partią.</div>
      <textarea rows={7} aria-label="Rekordy do importu (JSON)" spellCheck={false} value={records} onChange={(e) => setRecords(e.target.value)} />
      <div className="row">
        <input className="grow" placeholder="Podstawa uprawnienia (wymagana)" aria-label="Podstawa uprawnienia" value={auth} onChange={(e) => setAuth(e.target.value)} />
        <button disabled={!can("analyst") || auth.length < 3 || doImport.isPending} onClick={() => doImport.mutate()}>Importuj</button>
      </div>
      <ErrorLine error={doImport.error || saveNotes.error} />
      {result && <pre className="json">{JSON.stringify(result.stats, null, 2)}</pre>}
    </div>
  );
}

export default function Investigations() {
  const [sel, setSel] = useState<string | null>(null);
  useEffect(() => { const id = new URLSearchParams(window.location.search).get("id"); if (id) setSel(id); }, []);
  const list = useQuery({ queryKey: ["investigations"], queryFn: () => api<any[]>("/investigations") });
  return (
    <Page title="Sprawy" sub="Każda sprawa wykorzystuje to, co system już wie, i zapisuje, co było nowe.">
      <ErrorLine error={list.error} />
      <div className="grid" style={{ gridTemplateColumns: "minmax(260px, 1fr) 2fr" }}>
        <div className="card">
          {list.data && list.data.length === 0 ? <Empty>Brak spraw. Rozpocznij sprawę w sekcji Rozpoznanie.</Empty> : (
            <table><tbody>{list.data?.map((i) => <tr key={i.id} className={`click ${sel === i.id ? "sel" : ""}`} onClick={() => setSel(i.id)}><td>{i.name}<div className="muted">{when(i.created_at)}</div></td><td><Badge value={i.status} /></td></tr>)}</tbody></table>
          )}
        </div>
        <div className="card">{sel ? <Detail id={sel} key={sel} /> : <Empty>Wybierz sprawę z listy.</Empty>}</div>
      </div>
    </Page>
  );
}
