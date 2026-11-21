"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Badge, Empty, ErrorLine, Page, short, when } from "@/components/ui";

type StageCfg = { id: string; label: string; requires: string | null };
const LIMIT_LABELS: [string, string][] = [
  ["max_queries", "max queries"], ["max_pages", "max pages"], ["max_addresses", "max addresses"], ["max_seconds", "max seconds"],
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
    <Page title="Automation" sub="Run the lead workflow in stages. Every step is limited, logged and abortable; nothing here establishes who owns a wallet.">
      <div className="warnbox">
        A page mention is not ownership. Tracking is rule-based (the full name or login appears near the address on the same page) and creates a
        page-mention link only. Counterparties are transaction facts. Sherlock hits are username-string matches, not identity.
        The web search backend ddgs, when used, is unofficial and may conflict with the engines' terms.
      </div>

      <div className="card">
        <h2>New run</h2>
        {!can("analyst") && <div className="muted">Viewer role can read runs but cannot start them.</div>}
        <div className="row">
          <input placeholder="Imię i nazwisko" value={fullName} onChange={(e) => setFullName(e.target.value)} />
          <input placeholder="Login" value={login} onChange={(e) => setLogin(e.target.value)} />
        </div>
        <div style={{ margin: "8px 0" }}>
          {stageCfg.map((s) => {
            const lacking = s.requires && !stages.includes(s.requires);
            return (
              <label key={s.id} style={{ display: "block", margin: "2px 0" }} title={lacking ? `requires stage ${s.requires}` : undefined}>
                <input type="checkbox" checked={stages.includes(s.id)} onChange={() => setStages(toggleStage(stages, s.id, stageCfg))} />{" "}
                <b>{s.id}</b> · {s.label}
                {s.requires && <span className="muted"> (requires {s.requires})</span>}
              </label>
            );
          })}
        </div>
        {stages.includes("B") && (
          <textarea
            className="mono" rows={3} style={{ width: "100%" }} value={queries} onChange={(e) => setQueries(e.target.value)}
            placeholder="Search queries, one per line (optional). Empty = generated suggestions based on the name or login, cut to the query limit."
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
          <button disabled={!can("analyst") || !hasInput || needsLogin || stages.length === 0 || start.isPending} onClick={() => start.mutate()}>Start run</button>
        </div>
        <div className="muted">
          Limits always apply: when one is reached the run stops as “stopped by limit” and the reason is stored.
          {stages.includes("A") && " Starting with stage A ticked is your approval to run Sherlock for this login (it checks many sites; it is audited)."}
          {needsLogin && " Stage A needs a login."}
        </div>
        <ErrorLine error={start.error} />
      </div>

      {run && (
        <div className="card">
          <div className="row">
            <h2 style={{ margin: 0 }}>Run {short(run.id, 8)}</h2>
            <Badge value={run.status} />
            <span className="muted">stages {run.stages?.join("")} · {run.subject ?? "—"}{run.login ? ` · login ${run.login}` : ""}</span>
            <span className="grow" />
            <Link href={`/leads`} onClick={() => window.localStorage.setItem("osint.leads.investigation", run.investigation_id)}>Lead Search</Link>
            <Link href={`/investigations?id=${run.investigation_id}`}>investigation</Link>
            {active && <button className="danger" disabled={!can("analyst") || abort.isPending || run.abort_requested} onClick={() => abort.mutate()}>{run.abort_requested ? "aborting…" : "Abort"}</button>}
          </div>
          <ErrorLine error={abort.error} />
          {run.stop_reason && <div className="notice">{run.status === "stopped_by_limit" ? "Stopped by limit: " : ""}{run.stop_reason}</div>}
          <div className="row">
            {Object.entries(run.counters ?? {}).map(([k, v]) => <span key={k} className="muted">{k}: <b>{String(v)}</b></span>)}
            <span className="muted">phase: {run.phase}</span>
            {run.started_at && <span className="muted">started {when(run.started_at)}</span>}
          </div>
          {active && <div className="muted">The worker advances this run in the background; this page refreshes by itself.</div>}

          <h3>Log</h3>
          {run.log?.length ? (
            <table>
              <thead><tr><th>Time</th><th>Stage</th><th>Step</th><th>Result</th><th>Target</th><th>Detail</th></tr></thead>
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
          ) : <Empty>No steps yet.</Empty>}

          {run.candidates?.length > 0 && (
            <>
              <h3>Address candidates ({run.candidates.length})</h3>
              <div className="muted">Only candidates with the full name or login in their context are tracked (stage C). Others stay candidates.</div>
              <table>
                <thead><tr><th>Network</th><th>Address</th><th>Pages</th><th>Name/login nearby</th><th>Tracked</th></tr></thead>
                <tbody>
                  {run.candidates.map((c: any) => (
                    <tr key={`${c.network}${c.address}`}>
                      <td>{c.network}</td><td className="mono">{c.address}</td><td>{c.pages}</td>
                      <td>{c.matched_term ?? <span className="muted">no</span>}</td>
                      <td>{c.tracked ? <Badge value="succeeded" title="page mention recorded; not ownership" /> : <span className="muted">no</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          {run.tracked?.length > 0 && (
            <div className="row" style={{ marginTop: 8 }}>
              <span className="muted">Open in the graph:</span>
              {run.tracked.map((w: any) => <Link key={w.entity_id} href={`/graph?root=${w.entity_id}`}>{short(w.address, 14)}</Link>)}
            </div>
          )}
          {run.profile_urls?.length > 0 && (
            <>
              <h3>Sherlock profile URLs ({run.profile_urls.length})</h3>
              <div className="muted">Pages that exist for the same username string. Not evidence that one person controls them.</div>
              <ul>{run.profile_urls.map((u: string) => <li key={u} className="mono">{u}</li>)}</ul>
            </>
          )}
        </div>
      )}

      <div className="card">
        <h2>Recent runs</h2>
        {list.data?.runs?.length ? (
          <table>
            <thead><tr><th>Started</th><th>Subject / login</th><th>Stages</th><th>Status</th><th>By</th><th /></tr></thead>
            <tbody>
              {list.data.runs.map((r: any) => (
                <tr key={r.id}>
                  <td className="muted">{when(r.created_at)}</td>
                  <td>{r.subject ?? "—"}{r.login ? ` / ${r.login}` : ""}</td>
                  <td>{r.stages?.join("")}</td>
                  <td><Badge value={r.status} title={r.stop_reason ?? undefined} /></td>
                  <td className="muted">{r.created_by}</td>
                  <td><button onClick={() => setRunId(r.id)}>Open</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty>No automation runs yet.</Empty>}
      </div>
    </Page>
  );
}
