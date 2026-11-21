"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { label } from "@/lib/i18n";
import { Badge, Empty, ErrorLine, Page, short, when } from "@/components/ui";

function Policies() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["policies"], queryFn: () => api<any[]>("/policies") });
  const [scope, setScope] = useState("category");
  const [pattern, setPattern] = useState("");
  const [action, setAction] = useState("allow");
  const refresh = () => qc.invalidateQueries({ queryKey: ["policies"] });
  const add = useMutation({ mutationFn: () => api("/policies", { method: "PUT", body: { scope, pattern, action, reason: "ustawiono w interfejsie" } }), onSuccess: () => { setPattern(""); refresh(); } });
  const del = useMutation({ mutationFn: (id: string) => api(`/policies/${id}`, { method: "DELETE" }), onSuccess: refresh });
  return (
    <div className="card">
      <h2>Polityki źródeł</h2>
      <div className="muted">Określają, które kategorie rozpoznania, domeny lub dostawcy działają automatycznie, wymagają zatwierdzenia albo są zablokowani.</div>
      <ErrorLine error={q.error || add.error || del.error} />
      {q.data && q.data.length === 0 ? <Empty>Brak własnych polityk. Obowiązują ustawienia domyślne.</Empty> : (
        <table><tbody>{q.data?.map((p) => <tr key={p.id}><td>{p.scope}</td><td className="mono">{p.pattern}</td><td><Badge value={p.action} /></td><td className="muted">{short(p.reason, 40)}</td><td>{can("admin") && <button className="ghost sm" onClick={() => del.mutate(p.id)}>Usuń</button>}</td></tr>)}</tbody></table>
      )}
      {can("admin") && (
        <div className="row" style={{ marginTop: 8 }}>
          <select aria-label="Zakres polityki" value={scope} onChange={(e) => setScope(e.target.value)}>{[["category", "kategoria"], ["domain", "domena"], ["provider", "dostawca"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          <input placeholder="wzorzec" aria-label="Wzorzec polityki" spellCheck={false} value={pattern} onChange={(e) => setPattern(e.target.value)} />
          <select aria-label="Działanie polityki" value={action} onChange={(e) => setAction(e.target.value)}>{["allow", "require_approval", "deny"].map((s) => <option key={s} value={s}>{label(s)}</option>)}</select>
          <button disabled={!pattern} onClick={() => add.mutate()}>Zapisz politykę</button>
        </div>
      )}
    </div>
  );
}

function Wiki() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const pages = useQuery({ queryKey: ["wiki-pages"], queryFn: () => api<any[]>("/wiki/pages") });
  const lint = useQuery({ queryKey: ["wiki-lint"], queryFn: () => api<any>("/wiki/lint") });
  const [path, setPath] = useState<string | null>(null);
  const page = useQuery({ queryKey: ["wiki-page", path], queryFn: () => api<any>("/wiki/page", { params: { path: path! } }), enabled: !!path });
  const sync = useMutation({ mutationFn: () => api("/wiki/sync", { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["wiki-pages"] }).then(() => qc.invalidateQueries({ queryKey: ["wiki-lint"] })) });
  return (
    <div className="card">
      <h2>Wiki projektu (LLM_WIKI)</h2>
      <div className="muted">Dokumentacja samego projektu, dla ludzi i asystentów AI. To nie jest baza wiedzy wywiadowczej.</div>
      <div className="row">
        {lint.data && <Badge value={lint.data.ok ? "ok" : "failed"} title="kontrola spójności wiki" />}
        <span className="muted">{lint.data ? (lint.data.ok ? "kontrola spójności zakończona powodzeniem" : `liczba problemów: ${lint.data.problems.length}`) : ""}</span>
        <button className="ghost sm" disabled={!can("analyst") || sync.isPending} onClick={() => sync.mutate()}>Synchronizuj z dysku</button>
      </div>
      <ErrorLine error={pages.error || lint.error || sync.error} />
      {lint.data && lint.data.problems.length > 0 && <div className="warnbox">{lint.data.problems.map((p: any, i: number) => <div key={i}>{typeof p === "string" ? p : JSON.stringify(p)}</div>)}</div>}
      <div className="grid" style={{ gridTemplateColumns: "1fr 2fr" }}>
        <div style={{ maxHeight: 360, overflow: "auto" }}>
          {pages.data && pages.data.length === 0 ? <Empty>Brak zaindeksowanych stron. Użyj „Synchronizuj z dysku”.</Empty> : (
            <table><tbody>{pages.data?.map((p) => <tr key={p.path} className={`click ${path === p.path ? "sel" : ""}`} onClick={() => setPath(p.path)}><td>{p.path}</td><td className="muted">{when(p.updated_at)}</td></tr>)}</tbody></table>
          )}
        </div>
        <pre className="json" style={{ maxHeight: 360, overflow: "auto", whiteSpace: "pre-wrap" }}>{page.data?.content ?? "Wybierz stronę."}</pre>
      </div>
    </div>
  );
}

function Audit() {
  const q = useQuery({ queryKey: ["audit"], queryFn: () => api<any[]>("/audit", { params: { limit: 100 } }), retry: false });
  return (
    <div className="card">
      <h2>Dziennik audytu</h2>
      {q.error ? <div className="muted">Wymagana rola administratora.</div> : q.data && q.data.length === 0 ? <Empty>Brak wpisów w dzienniku audytu.</Empty> : (
        <table><thead><tr><th>Kiedy</th><th>Wykonawca</th><th>Działanie</th><th>Cel</th></tr></thead><tbody>{q.data?.map((a: any) => <tr key={a.id}><td>{when(a.created_at ?? a.at)}</td><td>{a.actor}</td><td>{a.action}</td><td className="mono">{short(`${a.target_type ?? ""}:${a.target_id ?? ""}`, 40)}</td></tr>)}</tbody></table>
      )}
    </div>
  );
}

export default function Settings() {
  const s = useQuery({ queryKey: ["settings"], queryFn: () => api<any>("/settings") });
  const { session } = useAuth();
  return (
    <Page title="Ustawienia" sub={`Zalogowano jako ${session?.username} (${label(session?.role)}). Sekrety nigdy nie są tu pokazywane.`}>
      <ErrorLine error={s.error} />
      <div className="grid g2">
        <div className="card"><h2>Konfiguracja uruchomieniowa</h2><pre className="json">{JSON.stringify(s.data, null, 2)}</pre></div>
        <Policies />
      </div>
      <h3>Dokumentacja</h3>
      <Wiki />
      <h3>Administracja</h3>
      <Audit />
    </Page>
  );
}
