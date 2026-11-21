"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Badge, Empty, ErrorLine, Page, short, when } from "@/components/ui";

type StageCfg = { id: string; label: string; requires: string | null };
const LIMIT_LABELS: [string, string][] = [
  ["max_queries", "maks. zapytań"], ["max_pages", "maks. stron"], ["max_addresses", "maks. adresów"], ["max_seconds", "maks. sekund"],
];
const STORE = "osint.automation.run";
const FINAL = ["completed", "stopped_by_limit", "aborted", "failed"];

/** Ticking a stage also ticks what it needs; unticking a stage also unticks what depends on it (the backend enforces the same rule). */
function toggleStage(sel: string[], id: string, cfg: StageCfg[]): string[] {
  const next = new Set(sel);
  if (next.has(id)) {
    next.delete(id);
    let changed = true;
    while (changed) {
      changed = false;
      for (const s of cfg) if (s.requires && !next.has(s.requires) && next.has(s.id)) { next.delete(s.id); changed = true; }
    }
  } else {
    let cur: string | null = id;
    while (cur) { next.add(cur); cur = cfg.find((s) => s.id === cur)?.requires ?? null; }
  }
  return cfg.map((s) => s.id).filter((x) => next.has(x));
}

export default function Automation() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const [fullName, setFullName] = useState("");
  const [login, setLogin] = useState("");
  const [stages, setStages] = useState<string[]>(["B"]);
  const [limits, setLimits] = useState<Record<string, string>>({});
  const [queries, setQueries] = useState("");
  const [runId, setRunIdState] = useState<string | null>(null);
  const setRunId = (id: string | null) => {
    setRunIdState(id);
    if (id) window.localStorage.setItem(STORE, id); else window.localStorage.removeItem(STORE);
  };
  useEffect(() => {
    const fromUrl = new URLSearchParams(window.location.search).get("run");
    setRunIdState(fromUrl || window.localStorage.getItem(STORE));
  }, []);

  const cfg = useQuery({ queryKey: ["automation-config"], queryFn: () => api<any>("/automation/config") });
  const list = useQuery({ queryKey: ["automation-runs"], queryFn: () => api<any>("/automation/runs"), refetchInterval: 5000 });
  const state = useQuery({
    queryKey: ["automation-run", runId],
    queryFn: () => api<any>(`/automation/runs/${runId}`),
    enabled: !!runId,
    refetchInterval: (q) => (q.state.data && FINAL.includes(q.state.data.status) ? false : 2000),
  });
  useEffect(() => { if (state.error && runId) setRunId(null); /* stale id after a DB reset */ }, [state.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const stageCfg: StageCfg[] = cfg.data?.stages ?? [];
  const hasInput = Boolean(fullName.trim() || login.trim());
  const needsLogin = stages.includes("A") && !login.trim();
  const body = () => ({
    full_name: fullName.trim() || null,
    login: login.trim() || null,
    stages,
    queries: stages.includes("B") ? queries.split("\n").map((q) => q.trim()).filter(Boolean) : [],
    limits: Object.fromEntries(Object.entries(limits).filter(([, v]) => v !== "").map(([k, v]) => [k, Number(v)])),
  });
  const start = useMutation({
    mutationFn: () => api<any>("/automation/runs", { body: body() }),
    onSuccess: (r) => { setRunId(r.id); qc.invalidateQueries({ queryKey: ["automation-runs"] }); },
  });
  const abort = useMutation({
    mutationFn: () => api<any>(`/automation/runs/${runId}/abort`, { method: "POST" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["automation-run", runId] }); qc.invalidateQueries({ queryKey: ["automation-runs"] }); },
  });

  const run = state.data;
  const active = run && !FINAL.includes(run.status);

  return (
    <Page title="Automatyzacja" sub="Uruchamia przepływ wyszukiwania tropów etapami. Każdy krok ma limit, jest zapisywany w dzienniku i można go przerwać. Nic tutaj nie ustala, kto jest właścicielem portfela.">
      <div className="warnbox">
        Wzmianka na stronie nie jest dowodem własności. Śledzenie opiera się na regule (imię i nazwisko lub login występuje obok adresu na tej samej stronie)
        i tworzy wyłącznie powiązanie typu „wzmianka na stronie”. Kontrahenci są faktami transakcyjnymi. Trafienia Sherlocka to zgodność ciągu znaków z nazwą użytkownika, a nie tożsamość.
        Silnik wyszukiwania ddgs, jeśli zostanie użyty, jest nieoficjalny i może naruszać regulaminy wyszukiwarek.
      </div>

      <div className="card">
        <h2>Nowe uruchomienie</h2>
        {!can("analyst") && <div className="muted">Obserwator może przeglądać uruchomienia, ale nie może ich rozpoczynać.</div>}
        <div className="row">
          <input placeholder="Imię i nazwisko" aria-label="Imię i nazwisko" autoComplete="off" value={fullName} onChange={(e) => setFullName(e.target.value)} />
          <input placeholder="Login" aria-label="Login" autoComplete="off" spellCheck={false} autoCapitalize="none" value={login} onChange={(e) => setLogin(e.target.value)} />
        </div>
        <div style={{ margin: "8px 0" }}>
          {stageCfg.map((s) => {
            const lacking = s.requires && !stages.includes(s.requires);
            return (
              <label key={s.id} style={{ display: "block", margin: "2px 0" }} title={lacking ? `wymaga etapu ${s.requires}` : undefined}>
                <input type="checkbox" checked={stages.includes(s.id)} onChange={() => setStages(toggleStage(stages, s.id, stageCfg))} />{" "}
                <b>{s.id}</b>: {s.label}
                {s.requires && <span className="muted"> (wymaga {s.requires})</span>}
              </label>
            );
          })}
        </div>
        {stages.includes("B") && (
          <textarea
            className="mono" rows={3} style={{ width: "100%" }} value={queries} onChange={(e) => setQueries(e.target.value)}
            placeholder="Zapytania wyszukiwarki, jedno w wierszu (opcjonalnie). Puste pole oznacza podpowiedzi wygenerowane na podstawie imienia i nazwiska lub loginu, przycięte do limitu zapytań." aria-label="Zapytania wyszukiwarki" spellCheck={false}
          />
        )}
        <div className="row" style={{ marginTop: 8 }}>
          {LIMIT_LABELS.map(([k, label]) => (
            <label key={k}>{label}{" "}
              <input type="number" min={1} max={cfg.data?.hard_caps?.[k]} style={{ width: 80 }}
                placeholder={String(cfg.data?.default_limits?.[k] ?? "")} value={limits[k] ?? ""}
                onChange={(e) => setLimits({ ...limits, [k]: e.target.value })} />
            </label>
          ))}
          <span className="grow" />
          <button disabled={!can("analyst") || !hasInput || needsLogin || stages.length === 0 || start.isPending} onClick={() => start.mutate()}>Rozpocznij uruchomienie</button>
        </div>
        <div className="muted">
          Limity obowiązują zawsze: po osiągnięciu któregoś z nich uruchomienie zatrzymuje się ze statusem „zatrzymane limitem”, a powód jest zapisywany.
          {stages.includes("A") && " Rozpoczęcie z zaznaczonym etapem A oznacza Twoją zgodę na uruchomienie Sherlocka dla tego loginu (sprawdza wiele serwisów, a działanie trafia do audytu)."}
          {needsLogin && " Etap A wymaga loginu."}
        </div>
        <ErrorLine error={start.error} />
      </div>

      {run && (
        <div className="card">
          <div className="row">
            <h2 style={{ margin: 0 }}>Uruchomienie {short(run.id, 8)}</h2>
            <Badge value={run.status} />
            <span className="muted">etapy: {run.stages?.join("")}, {run.subject ?? "—"}{run.login ? `, login ${run.login}` : ""}</span>
            <span className="grow" />
            <Link href={`/leads`} onClick={() => window.localStorage.setItem("osint.leads.investigation", run.investigation_id)}>Wyszukiwanie tropów</Link>
            <Link href={`/investigations?id=${run.investigation_id}`}>sprawa</Link>
            {active && <button className="danger" disabled={!can("analyst") || abort.isPending || run.abort_requested} onClick={() => abort.mutate()}>{run.abort_requested ? "Przerywanie…" : "Przerwij"}</button>}
          </div>
          <ErrorLine error={abort.error} />
          {run.stop_reason && <div className="notice">{run.status === "stopped_by_limit" ? "Zatrzymane limitem: " : ""}{run.stop_reason}</div>}
          <div className="row">
            {Object.entries(run.counters ?? {}).map(([k, v]) => <span key={k} className="muted">{k}: <b>{String(v)}</b></span>)}
            <span className="muted">faza: {run.phase}</span>
            {run.started_at && <span className="muted">rozpoczęto {when(run.started_at)}</span>}
          </div>
          {active && <div className="muted">Proces w tle prowadzi to uruchomienie dalej, a ta strona odświeża się sama.</div>}

          <h3>Dziennik</h3>
          {run.log?.length ? (
            <table>
              <thead><tr><th>Czas</th><th>Etap</th><th>Krok</th><th>Wynik</th><th>Cel</th><th>Szczegóły</th></tr></thead>
              <tbody>
                {run.log.map((e: any, i: number) => (
                  <tr key={i}>
                    <td className="muted">{when(e.at)}</td><td>{e.stage}</td><td>{e.action}</td>
                    <td><Badge value={e.outcome} /></td>
                    <td className="mono">{short(e.target, 60)}</td>
                    <td className="muted">{e.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty>Brak kroków.</Empty>}

          {run.candidates?.length > 0 && (
            <>
              <h3>Kandydaci na adresy ({run.candidates.length})</h3>
              <div className="muted">Śledzeni są tylko kandydaci, w których kontekście występuje imię i nazwisko lub login (etap C). Pozostali zostają kandydatami.</div>
              <table>
                <thead><tr><th>Sieć</th><th>Adres</th><th>Strony</th><th>Imię i nazwisko lub login obok</th><th>Śledzony</th></tr></thead>
                <tbody>
                  {run.candidates.map((c: any) => (
                    <tr key={`${c.network}${c.address}`}>
                      <td>{c.network}</td><td className="mono">{c.address}</td><td>{c.pages}</td>
                      <td>{c.matched_term ?? <span className="muted">nie</span>}</td>
                      <td>{c.tracked ? <Badge value="succeeded" title="zapisano wzmiankę na stronie; to nie jest dowód własności" /> : <span className="muted">nie</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          {run.tracked?.length > 0 && (
            <div className="row" style={{ marginTop: 8 }}>
              <span className="muted">Otwórz w grafie:</span>
              {run.tracked.map((w: any) => <Link key={w.entity_id} href={`/graph?root=${w.entity_id}`}>{short(w.address, 14)}</Link>)}
            </div>
          )}
          {run.profile_urls?.length > 0 && (
            <>
              <h3>Adresy profili znalezione przez Sherlocka ({run.profile_urls.length})</h3>
              <div className="muted">Strony istniejące dla tego samego ciągu znaków nazwy użytkownika. To nie dowodzi, że kontroluje je jedna osoba.</div>
              <ul>{run.profile_urls.map((u: string) => <li key={u} className="mono">{u}</li>)}</ul>
            </>
          )}
        </div>
      )}

      <div className="card">
        <h2>Ostatnie uruchomienia</h2>
        {list.data?.runs?.length ? (
          <table>
            <thead><tr><th>Rozpoczęto</th><th>Osoba / login</th><th>Etapy</th><th>Status</th><th>Autor</th><th /></tr></thead>
            <tbody>
              {list.data.runs.map((r: any) => (
                <tr key={r.id}>
                  <td className="muted">{when(r.created_at)}</td>
                  <td>{r.subject ?? "—"}{r.login ? ` / ${r.login}` : ""}</td>
                  <td>{r.stages?.join("")}</td>
                  <td><Badge value={r.status} title={r.stop_reason ?? undefined} /></td>
                  <td className="muted">{r.created_by}</td>
                  <td><button onClick={() => setRunId(r.id)}>Otwórz</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty>Brak uruchomień automatyzacji.</Empty>}
      </div>
    </Page>
  );
}
