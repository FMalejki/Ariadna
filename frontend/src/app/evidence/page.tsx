"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, downloadArtifact } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Badge, Drawer, Empty, ErrorLine, Page, short, when } from "@/components/ui";

export default function Evidence() {
  const { can } = useAuth();
  const [provider, setProvider] = useState("");
  const [status, setStatus] = useState("");
  const [art, setArt] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const runs = useQuery({ queryKey: ["runs", provider, status], queryFn: () => api<any[]>("/evidence/tool-runs", { params: { provider, status, limit: 100 } }) });
  const meta = useQuery({ queryKey: ["artifact", art], queryFn: () => api<any>(`/evidence/artifacts/${art}`), enabled: !!art });
  return (
    <Page title="Evidence Center" sub="Every provider run, with the raw artifact it produced. Artifacts are downloaded, never rendered.">
      <div className="row">
        <input placeholder="provider" value={provider} onChange={(e) => setProvider(e.target.value)} />
        <select value={status} onChange={(e) => setStatus(e.target.value)}><option value="">all statuses</option>{["succeeded", "failed", "timed_out", "unavailable", "running"].map((s) => <option key={s}>{s}</option>)}</select>
      </div>
      <ErrorLine error={runs.error || err} />
      <div className="card" style={{ marginTop: 8 }}>
        {runs.data && runs.data.length === 0 ? <Empty>No provider runs recorded.</Empty> : (
          <table>
            <thead><tr><th>Provider</th><th>Target</th><th>Status</th><th>Obs.</th><th>Started</th><th>Artifact</th></tr></thead>
            <tbody>
              {runs.data?.map((r) => (
                <tr key={r.id}>
                  <td>{r.provider} <span className="muted">{r.provider_version}</span></td><td className="mono">{short(r.target, 46)}</td>
                  <td><Badge value={r.status} />{r.error && <div className="err">{short(r.error, 80)}</div>}</td>
                  <td>{r.observation_count}</td><td>{when(r.started_at)}</td>
                  <td>{r.raw_artifact_id ? <a href="#" onClick={(e) => { e.preventDefault(); setArt(r.raw_artifact_id); }}>{short(r.raw_artifact_id, 10)}</a> : <span className="muted">none</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {art && (
        <Drawer title="Raw artifact" onClose={() => setArt(null)}>
          <ErrorLine error={meta.error} />
          {meta.data && (
            <>
              <div className="mono">sha256 {meta.data.content_hash}</div>
              <div className="muted">{meta.data.content_type}, {meta.data.size} bytes, captured {when(meta.data.captured_at)}</div>
              <div className="notice">Contents are untrusted external material. They are served as a download only.</div>
              <button disabled={!can("analyst")} onClick={() => downloadArtifact(art).catch(setErr)}>Download</button>
              {!can("analyst") && <div className="muted">Analyst role required to download.</div>}
            </>
          )}
        </Drawer>
      )}
    </Page>
  );
}
