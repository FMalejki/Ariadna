"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
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
  if (q.isLoading) return <div className="muted">Loading…</div>;
  if (!i) return <ErrorLine error={q.error} />;
  const kr = i.knowledge_reuse;
  return (
    <div>
      <div className="row"><h2 className="grow">{i.name}</h2><Badge value={i.status} /><button className="ghost sm" onClick={exportJson} disabled={!can("analyst")}>Export JSON</button></div>
      <div className="muted">Created {when(i.created_at)} by {i.created_by}, scope depth {i.scope?.max_depth}</div>
      <h3>Knowledge reuse</h3>
      <div className="grid g4">
        <div className="card stat">{kr.reused_observations}<small>observations reused</small></div>
        <div className="card stat">{kr.new_observations}<small>new observations</small></div>
        <div className="card stat">{kr.reused_tasks}<small>provider runs reused</small></div>
        <div className="card stat">{kr.executed_tasks}<small>provider runs executed</small></div>
      </div>
      {changes.data && <div className="muted" style={{ marginTop: 6 }}>Since this investigation started: {changes.data.new_observations} observations, {changes.data.new_entities} entities, {changes.data.new_relationships} relationships were added.</div>}
      <h3>Entities in scope ({i.entities.length})</h3>
      <div className="row">{i.entities.slice(0, 40).map((e: any) => <Link key={e.id} className="badge" href={`/entities?id=${e.id}`}>{short(e.display_name ?? e.canonical_identifier, 28)}</Link>)}</div>
      <h3>Tasks ({i.tasks.length})</h3>
      <table><tbody>{i.tasks.map((t: any) => <tr key={t.id}><td>{t.provider}</td><td className="mono">{short(t.target, 44)}</td><td>d{t.depth}</td><td><Badge value={t.status} /></td></tr>)}</tbody></table>
      <h3>Notes</h3>
      <textarea rows={4} value={notes ?? i.notes ?? ""} onChange={(e) => setNotes(e.target.value)} disabled={!can("analyst")} />
      <button className="ghost sm" disabled={notes === null || !can("analyst")} onClick={() => saveNotes.mutate()}>Save notes</button>
      <h3>Import analyst-provided records</h3>
      <div className="notice">Records are stored as <b>unverified</b> observations from a manual import provider. Declare that you are authorised to use this data; it is recorded with the batch.</div>
      <textarea rows={7} value={records} onChange={(e) => setRecords(e.target.value)} />
      <div className="row">
        <input className="grow" placeholder="Authorisation note (required)" value={auth} onChange={(e) => setAuth(e.target.value)} />
        <button disabled={!can("analyst") || auth.length < 3 || doImport.isPending} onClick={() => doImport.mutate()}>Import</button>
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
    <Page title="Investigations" sub="Each investigation reuses what the platform already knows and records what was new.">
      <ErrorLine error={list.error} />
      <div className="grid" style={{ gridTemplateColumns: "minmax(260px, 1fr) 2fr" }}>
        <div className="card">
          {list.data && list.data.length === 0 ? <Empty>No investigations. Start one in Discovery.</Empty> : (
            <table><tbody>{list.data?.map((i) => <tr key={i.id} className={`click ${sel === i.id ? "sel" : ""}`} onClick={() => setSel(i.id)}><td>{i.name}<div className="muted">{when(i.created_at)}</div></td><td><Badge value={i.status} /></td></tr>)}</tbody></table>
          )}
        </div>
        <div className="card">{sel ? <Detail id={sel} key={sel} /> : <Empty>Select an investigation.</Empty>}</div>
      </div>
    </Page>
  );
}
