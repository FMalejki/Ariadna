"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Badge, Empty, ErrorLine, Page, short, when } from "@/components/ui";

function Policies() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["policies"], queryFn: () => api<any[]>("/policies") });
  const [scope, setScope] = useState("category");
  const [pattern, setPattern] = useState("");
  const [action, setAction] = useState("allow");
  const refresh = () => qc.invalidateQueries({ queryKey: ["policies"] });
  const add = useMutation({ mutationFn: () => api("/policies", { method: "PUT", body: { scope, pattern, action, reason: "set in UI" } }), onSuccess: () => { setPattern(""); refresh(); } });
  const del = useMutation({ mutationFn: (id: string) => api(`/policies/${id}`, { method: "DELETE" }), onSuccess: refresh });
  return (
    <div className="card">
      <h2>Source policies</h2>
      <div className="muted">Control which discovery categories, domains or providers run automatically, need approval, or are denied.</div>
      <ErrorLine error={q.error || add.error || del.error} />
      {q.data && q.data.length === 0 ? <Empty>No custom policies. Defaults apply.</Empty> : (
        <table><tbody>{q.data?.map((p) => <tr key={p.id}><td>{p.scope}</td><td className="mono">{p.pattern}</td><td><Badge value={p.action} /></td><td className="muted">{short(p.reason, 40)}</td><td>{can("admin") && <button className="ghost sm" onClick={() => del.mutate(p.id)}>Remove</button>}</td></tr>)}</tbody></table>
      )}
      {can("admin") && (
        <div className="row" style={{ marginTop: 8 }}>
          <select value={scope} onChange={(e) => setScope(e.target.value)}>{["category", "domain", "provider"].map((s) => <option key={s}>{s}</option>)}</select>
          <input placeholder="pattern" value={pattern} onChange={(e) => setPattern(e.target.value)} />
          <select value={action} onChange={(e) => setAction(e.target.value)}>{["allow", "require_approval", "deny"].map((s) => <option key={s}>{s}</option>)}</select>
          <button disabled={!pattern} onClick={() => add.mutate()}>Save policy</button>
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
      <h2>Project wiki (LLM_WIKI)</h2>
      <div className="muted">Documentation about the project itself, for humans and AI assistants. It is not the intelligence knowledge base.</div>
      <div className="row">
        {lint.data && <Badge value={lint.data.ok ? "ok" : "failed"} title="wiki lint" />}
        <span className="muted">{lint.data ? (lint.data.ok ? "lint passed" : `${lint.data.problems.length} problem(s)`) : ""}</span>
        <button className="ghost sm" disabled={!can("analyst") || sync.isPending} onClick={() => sync.mutate()}>Sync from disk</button>
      </div>
      <ErrorLine error={pages.error || lint.error || sync.error} />
      {lint.data && lint.data.problems.length > 0 && <div className="warnbox">{lint.data.problems.map((p: any, i: number) => <div key={i}>{typeof p === "string" ? p : JSON.stringify(p)}</div>)}</div>}
      <div className="grid" style={{ gridTemplateColumns: "1fr 2fr" }}>
        <div style={{ maxHeight: 360, overflow: "auto" }}>
          {pages.data && pages.data.length === 0 ? <Empty>No pages indexed. Use “Sync from disk”.</Empty> : (
            <table><tbody>{pages.data?.map((p) => <tr key={p.path} className={`click ${path === p.path ? "sel" : ""}`} onClick={() => setPath(p.path)}><td>{p.path}</td><td className="muted">{when(p.updated_at)}</td></tr>)}</tbody></table>
          )}
        </div>
        <pre className="json" style={{ maxHeight: 360, overflow: "auto", whiteSpace: "pre-wrap" }}>{page.data?.content ?? "Select a page."}</pre>
      </div>
    </div>
  );
}

function Audit() {
  const q = useQuery({ queryKey: ["audit"], queryFn: () => api<any[]>("/audit", { params: { limit: 100 } }), retry: false });
  return (
    <div className="card">
      <h2>Audit log</h2>
      {q.error ? <div className="muted">Admin role required.</div> : q.data && q.data.length === 0 ? <Empty>No audit entries.</Empty> : (
        <table><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Target</th></tr></thead><tbody>{q.data?.map((a: any) => <tr key={a.id}><td>{when(a.created_at ?? a.at)}</td><td>{a.actor}</td><td>{a.action}</td><td className="mono">{short(`${a.target_type ?? ""}:${a.target_id ?? ""}`, 40)}</td></tr>)}</tbody></table>
      )}
    </div>
  );
}

export default function Settings() {
  const s = useQuery({ queryKey: ["settings"], queryFn: () => api<any>("/settings") });
  const { session } = useAuth();
  return (
    <Page title="Settings" sub={`Signed in as ${session?.username} (${session?.role}). Secrets are never shown here.`}>
      <ErrorLine error={s.error} />
      <div className="grid g2">
        <div className="card"><h2>Runtime configuration</h2><pre className="json">{JSON.stringify(s.data, null, 2)}</pre></div>
        <Policies />
      </div>
      <h3>Documentation</h3>
      <Wiki />
      <h3>Administration</h3>
      <Audit />
    </Page>
  );
}
