"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, downloadArtifact } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { label } from "@/lib/i18n";
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
    <Page title="Centrum dowodów" sub="Każde uruchomienie dostawcy wraz z surowym artefaktem, który wytworzyło. Artefakty są pobierane, nigdy wyświetlane.">
      <div className="row">
        <input placeholder="dostawca" aria-label="Dostawca" spellCheck={false} value={provider} onChange={(e) => setProvider(e.target.value)} />
        <select aria-label="Status uruchomienia" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">wszystkie statusy</option>{["succeeded", "failed", "timed_out", "unavailable", "running"].map((s) => <option key={s} value={s}>{label(s)}</option>)}</select>
      </div>
      <ErrorLine error={runs.error || err} />
      <div className="card" style={{ marginTop: 8 }}>
        {runs.data && runs.data.length === 0 ? <Empty>Brak zapisanych uruchomień dostawców.</Empty> : (
          <table>
            <thead><tr><th>Dostawca</th><th>Cel</th><th>Status</th><th>Obserwacje</th><th>Rozpoczęto</th><th>Artefakt</th></tr></thead>
            <tbody>
              {runs.data?.map((r) => (
                <tr key={r.id}>
                  <td>{r.provider} <span className="muted">{r.provider_version}</span></td><td className="mono">{short(r.target, 46)}</td>
                  <td><Badge value={r.status} />{r.error && <div className="err">{short(r.error, 80)}</div>}</td>
                  <td>{r.observation_count}</td><td>{when(r.started_at)}</td>
                  <td>{r.raw_artifact_id ? <a href="#" onClick={(e) => { e.preventDefault(); setArt(r.raw_artifact_id); }}>{short(r.raw_artifact_id, 10)}</a> : <span className="muted">brak</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {art && (
        <Drawer title="Surowy artefakt" onClose={() => setArt(null)}>
          <ErrorLine error={meta.error} />
          {meta.data && (
            <>
              <div className="mono">sha256 {meta.data.content_hash}</div>
              <div className="muted">{meta.data.content_type}, {meta.data.size} B, zapisano {when(meta.data.captured_at)}</div>
              <div className="notice">Zawartość to niezaufany materiał zewnętrzny. Jest udostępniana wyłącznie do pobrania.</div>
              <button disabled={!can("analyst")} onClick={() => downloadArtifact(art).catch(setErr)}>Pobierz</button>
              {!can("analyst") && <div className="muted">Pobieranie wymaga roli analityka.</div>}
            </>
          )}
        </Drawer>
      )}
    </Page>
  );
}
