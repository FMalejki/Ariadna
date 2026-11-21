"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Badge, Empty, ErrorLine, Page, short, when } from "@/components/ui";

export default function Discovery() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const [name, setName] = useState("");
  const [seed, setSeed] = useState("");
  const [fullName, setFullName] = useState("");
  const [login, setLogin] = useState("");
  const [depth, setDepth] = useState(1);
  const [runNow, setRunNow] = useState(true);
  const [filter, setFilter] = useState("");
  const [created, setCreated] = useState<any>(null);

  const tasks = useQuery({
    queryKey: ["tasks", filter],
    queryFn: () => api<any[]>("/discovery/tasks", { params: { status: filter || undefined, limit: 200 } }),
    refetchInterval: 5000,
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["tasks"] });
    qc.invalidateQueries({ queryKey: ["overview"] });
  };
  const hasInput = Boolean(seed.trim() || fullName.trim() || login.trim());
  const start = useMutation({
    mutationFn: () =>
      api<any>("/investigations", {
        body: {
          name: name.trim() || login.trim() || fullName.trim() || seed.trim().slice(0, 80),
          seed: seed.trim() || null,
          full_name: fullName.trim() || null,
          login: login.trim() || null,
          scope: { max_depth: depth },
          run_now: runNow,
        },
      }),
    onSuccess: (r) => { setCreated(r); refresh(); },
  });
  const decide = useMutation({
    mutationFn: (v: { id: string; approve: boolean }) => api(`/discovery/tasks/${v.id}/decision`, { body: { approve: v.approve, reason: v.approve ? "approved in UI" : "rejected in UI" } }),
    onSuccess: refresh,
  });
  const retry = useMutation({ mutationFn: (id: string) => api(`/discovery/tasks/${id}/retry`, { method: "POST" }), onSuccess: refresh });
  const run = useMutation({ mutationFn: () => api("/discovery/run", { body: { limit: 25 } }), onSuccess: refresh });

  return (
    <Page title="Discovery" sub="Start from a full name, a login or a seed (at least one). Each result may propose follow-up tasks; sensitive categories wait for approval.">
      <div className="card">
        <h2>New investigation</h2>
        <div className="row">
          <input placeholder="Investigation name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
          <input placeholder="Imię i nazwisko" value={fullName} onChange={(e) => setFullName(e.target.value)} />
          <input placeholder="Login" value={login} onChange={(e) => setLogin(e.target.value)} />
        </div>
        <div className="row">
          <input className="grow" placeholder="Seed (optional): URL, @handle, wallet address, tx hash or domain" value={seed} onChange={(e) => setSeed(e.target.value)} />
          <label>depth <input type="number" min={0} max={5} value={depth} onChange={(e) => setDepth(Number(e.target.value))} style={{ width: 60 }} /></label>
          <label><input type="checkbox" checked={runNow} onChange={(e) => setRunNow(e.target.checked)} /> run approved tasks now</label>
          <button disabled={!can("analyst") || !hasInput || start.isPending} onClick={() => start.mutate()}>Start</button>
        </div>
        <div className="muted">
          Login: Sherlock checks many sites (no platform is assumed); a hit is a username-string match, not proof of identity.
          The full name creates no entity; it only parameterises the investigation and its searches.
        </div>
        {!can("analyst") && <div className="muted">Viewer role cannot start investigations.</div>}
        <ErrorLine error={start.error} />
        {created && (
          <div className="notice">
            {created.seed ? (
              <>Seed resolved to <code>{created.seed.canonical_identifier}</code> ({created.seed.entity_type}). </>
            ) : (
              <>No entity created (full name only: it is a parameter of the investigation). </>
            )}
            {created.tasks.length} task(s) planned, {created.executed.length} executed.{" "}
            <Link href={`/investigations?id=${created.investigation.id}`}>Open investigation</Link>
            {created.seed?.entity_id && <> <Link href={`/graph?root=${created.seed.entity_id}`}>View in graph</Link></>}
          </div>
        )}
      </div>

      <h3>Task queue</h3>
      <div className="row">
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">all statuses</option>
          {["pending_approval", "queued", "running", "completed", "reused", "failed", "dead", "rejected", "skipped"].map((s) => <option key={s}>{s}</option>)}
        </select>
        <button className="ghost" disabled={!can("analyst") || run.isPending} onClick={() => run.mutate()}>Run due tasks</button>
      </div>
      <ErrorLine error={tasks.error || decide.error || retry.error || run.error} />
      <div className="card">
        {tasks.data && tasks.data.length === 0 ? <Empty>No tasks. Start an investigation above.</Empty> : (
          <table>
            <thead><tr><th>Provider</th><th>Target</th><th>Depth</th><th>Status</th><th>Reason</th><th>Created</th><th /></tr></thead>
            <tbody>
              {tasks.data?.map((t) => (
                <tr key={t.id}>
                  <td>{t.provider}<div className="muted">{t.category}</div></td>
                  <td className="mono">{short(t.target, 48)}</td>
                  <td>{t.depth}</td>
                  <td><Badge value={t.status} /></td>
                  <td className="muted">{short(t.status_reason, 60)}</td>
                  <td>{when(t.created_at)}</td>
                  <td className="row">
                    {t.status === "pending_approval" && can("analyst") && (
                      <>
                        <button className="good sm" onClick={() => decide.mutate({ id: t.id, approve: true })}>Approve</button>
                        <button className="danger sm" onClick={() => decide.mutate({ id: t.id, approve: false })}>Reject</button>
                      </>
                    )}
                    {t.status === "dead" && can("analyst") && <button className="ghost sm" onClick={() => retry.mutate(t.id)}>Retry</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Page>
  );
}
