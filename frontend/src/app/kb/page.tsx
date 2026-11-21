"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { label } from "@/lib/i18n";
import { Badge, Empty, ErrorLine, Page, RelationshipDrawer, short, when } from "@/components/ui";

function Candidates() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["candidates"], queryFn: () => api<any[]>("/match-candidates") });
  const m = useMutation({
    mutationFn: (v: { id: string; decision: string }) => api(`/match-candidates/${v.id}/decide`, { body: { decision: v.decision } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["candidates"] }); qc.invalidateQueries({ queryKey: ["merges"] }); },
  });
  return (
    <>
      <div className="notice">Kandydaci to możliwe dopasowania zaproponowane przez deterministyczne reguły. Nic nie jest łączone, dopóki analityk tego nie zaakceptuje, a każde połączenie można cofnąć.</div>
      <ErrorLine error={q.error || m.error} />
      {q.data && q.data.length === 0 ? <Empty>Brak kandydatów do połączenia.</Empty> : (
        <table>
          <thead><tr><th>Encja A</th><th>Encja B</th><th>Reguła</th><th>Uzasadnienie</th><th /></tr></thead>
          <tbody>{q.data?.map((c) => (
            <tr key={c.id}>
              <td>{short(c.entity_a?.canonical_identifier, 34)}</td><td>{short(c.entity_b?.canonical_identifier, 34)}</td><td><code>{c.rule}</code></td><td className="muted">{short(c.explanation, 90)}</td>
              <td className="row">{can("analyst") && <><button className="good sm" onClick={() => m.mutate({ id: c.id, decision: "accepted" })}>Zaakceptuj połączenie</button><button className="danger sm" onClick={() => m.mutate({ id: c.id, decision: "rejected" })}>Odrzuć</button></>}</td>
            </tr>))}</tbody>
        </table>
      )}
    </>
  );
}

function Merges() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["merges"], queryFn: () => api<any[]>("/merges", { params: { include_reversed: true } }) });
  const rev = useMutation({ mutationFn: (id: string) => api(`/merges/${id}/reverse`, { body: { note: "cofnięto w interfejsie" } }), onSuccess: () => qc.invalidateQueries({ queryKey: ["merges"] }) });
  return (
    <>
      <ErrorLine error={q.error || rev.error} />
      {q.data && q.data.length === 0 ? <Empty>Brak zapisanych połączeń.</Empty> : (
        <table>
          <thead><tr><th>Encja docelowa</th><th>Encja włączona</th><th>Metoda</th><th>Powód</th><th>Kiedy</th><th /></tr></thead>
          <tbody>{q.data?.map((m) => (
            <tr key={m.id}><td className="mono">{short(m.winner_id, 10)}</td><td className="mono">{short(m.loser_id, 10)}</td><td>{m.method}</td><td className="muted">{short(m.reason, 70)}</td><td>{when(m.merged_at)}</td>
              <td>{m.reversed_at ? <Badge value="historical" title={`cofnięto ${when(m.reversed_at)}`} /> : can("analyst") && <button className="ghost sm" onClick={() => rev.mutate(m.id)}>Cofnij połączenie</button>}</td></tr>))}</tbody>
        </table>
      )}
    </>
  );
}

function Claims() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const [status, setStatus] = useState("pending");
  const q = useQuery({ queryKey: ["claims", status], queryFn: () => api<any[]>("/claims", { params: { review_status: status } }) });
  const llm = useQuery({ queryKey: ["llm"], queryFn: () => api<any>("/llm/status") });
  const m = useMutation({ mutationFn: (v: { id: string; decision: string }) => api(`/claims/${v.id}/review`, { body: { decision: v.decision } }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["claims"] }); qc.invalidateQueries({ queryKey: ["overview"] }); } });
  return (
    <>
      <div className="warnbox">Wynik modelu językowego jest niezweryfikowanym twierdzeniem. Zaakceptowanie twierdzenia zapisuje wyłącznie Twój przegląd: nie weryfikuje go i nie tworzy relacji.</div>
      <div className="muted">Model językowy: {llm.data?.enabled ? `${llm.data.provider}/${llm.data.model}` : "nieskonfigurowany (wyodrębnianie twierdzeń niedostępne)"}</div>
      <div className="row"><select aria-label="Status przeglądu" value={status} onChange={(e) => setStatus(e.target.value)}>{["pending", "accepted", "rejected"].map((s) => <option key={s} value={s}>{label(s)}</option>)}</select></div>
      <ErrorLine error={q.error || m.error} />
      {q.data && q.data.length === 0 ? <Empty>Brak twierdzeń o statusie „{label(status)}”.</Empty> : (
        <table>
          <thead><tr><th>Twierdzenie</th><th>Tekst źródłowy</th><th>Model</th><th /></tr></thead>
          <tbody>{q.data?.map((c) => (
            <tr key={c.id}><td>{c.subject_reference} <code>{c.predicate}</code> {c.object_reference}</td><td className="muted">“{short(c.supporting_text, 120)}”</td><td className="muted">{c.model}, wersja promptu {c.prompt_version}</td>
              <td className="row">{c.review_status === "pending" && can("analyst") ? <><button className="good sm" onClick={() => m.mutate({ id: c.id, decision: "accepted" })}>Zaakceptuj</button><button className="danger sm" onClick={() => m.mutate({ id: c.id, decision: "rejected" })}>Odrzuć</button></> : <Badge value={c.review_status} />}</td></tr>))}</tbody>
        </table>
      )}
    </>
  );
}

function Relationships() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const [status, setStatus] = useState("contradicted");
  const [open, setOpen] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["rels", status], queryFn: () => api<any[]>("/relationships", { params: { status, limit: 100 } }) });
  const m = useMutation({ mutationFn: (v: { id: string; decision: string }) => api(`/relationships/${v.id}/review`, { body: { decision: v.decision } }), onSuccess: () => qc.invalidateQueries({ queryKey: ["rels"] }) });
  return (
    <>
      <div className="row"><select aria-label="Status relacji" value={status} onChange={(e) => setStatus(e.target.value)}>{["proposed", "supported", "corroborated", "verified", "contradicted", "historical", "rejected"].map((s) => <option key={s} value={s}>{label(s)}</option>)}</select></div>
      <ErrorLine error={q.error || m.error} />
      {q.data && q.data.length === 0 ? <Empty>Brak relacji o statusie „{label(status)}”.</Empty> : (
        <table>
          <thead><tr><th>Typ</th><th>Status</th><th>Przegląd</th><th>Ostatnia obserwacja</th><th /></tr></thead>
          <tbody>{q.data?.map((r) => (
            <tr key={r.id} className="click" onClick={() => setOpen(r.id)}><td><code>{r.relationship_type}</code></td><td><Badge value={r.status} /></td><td><Badge value={r.review_status} /></td><td>{when(r.last_observed_at)}</td>
              <td className="row" onClick={(e) => e.stopPropagation()}>{can("analyst") && <><button className="good sm" onClick={() => m.mutate({ id: r.id, decision: "accepted" })}>Zaakceptuj</button><button className="danger sm" onClick={() => m.mutate({ id: r.id, decision: "rejected" })}>Odrzuć</button></>}</td></tr>))}</tbody>
        </table>
      )}
      {open && <RelationshipDrawer id={open} onClose={() => setOpen(null)} />}
    </>
  );
}

function Observations() {
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const list = useQuery({ queryKey: ["kb-search", term], queryFn: () => (term ? api<any>("/search", { params: { q: term } }) : Promise.resolve(null)), enabled: true });
  const recent = useQuery({ queryKey: ["obs-recent"], queryFn: () => api<any[]>("/observations", { params: { limit: 50 } }) });
  return (
    <>
      <form className="row" onSubmit={(e) => { e.preventDefault(); setTerm(q); }}>
        <input className="grow" placeholder="Szukaj encji i obserwacji…" aria-label="Szukaj encji i obserwacji" value={q} onChange={(e) => setQ(e.target.value)} />
        <button>Szukaj</button>
      </form>
      <ErrorLine error={list.error || recent.error} />
      {term && list.data && (
        <>
          <h3>Encje ({list.data.entities.length})</h3>
          <div className="row">{list.data.entities.map((e: any) => <a key={e.id} className="badge" href={`/entities?id=${e.id}`}>{short(e.label, 30)}</a>)}</div>
          <h3>Obserwacje ({list.data.observations.length})</h3>
        </>
      )}
      {!term && <h3>Najnowsze obserwacje</h3>}
      {(() => { const rows = term ? list.data?.observations : recent.data; return rows && rows.length > 0 ? (
        <table>
          <thead><tr><th>Typ</th><th>Źródło</th><th>Nr</th><th>Zaobserwowano</th><th>Dowód</th></tr></thead>
          <tbody>{rows.map((o: any) => <tr key={o.id}><td>{o.observation_type}</td><td>{short(o.source, 40)}</td><td>{o.seq}</td><td>{when(o.observed_at)}</td><td>{o.evidence_type}</td></tr>)}</tbody>
        </table>
      ) : <Empty>Brak obserwacji.</Empty>; })()}
    </>
  );
}

const TABS = [["observations", "Obserwacje i wyszukiwanie"], ["relationships", "Relacje"], ["claims", "Twierdzenia modelu"], ["candidates", "Kandydaci do połączenia"], ["merges", "Połączenia"]] as const;

export default function KB() {
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("observations");
  return (
    <Page title="Baza wiedzy" sub="Zgromadzona wiedza: obserwacje, przejrzane relacje, twierdzenia i rozpoznawanie encji. Osobna od wiki projektu.">
      <div className="tabs">{TABS.map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}</div>
      {tab === "observations" && <Observations />}
      {tab === "relationships" && <Relationships />}
      {tab === "claims" && <Claims />}
      {tab === "candidates" && <Candidates />}
      {tab === "merges" && <Merges />}
    </Page>
  );
}
