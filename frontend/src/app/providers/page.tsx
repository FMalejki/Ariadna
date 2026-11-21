"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Badge, ErrorLine, Page, when } from "@/components/ui";

export default function Providers() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["providers"], queryFn: () => api<any[]>("/providers") });
  const refresh = () => qc.invalidateQueries({ queryKey: ["providers"] });
  const patch = useMutation({ mutationFn: (v: { name: string; body: object }) => api(`/providers/${v.name}`, { method: "PATCH", body: v.body }), onSuccess: refresh });
  const health = useMutation({ mutationFn: (name: string) => api(`/providers/${name}/health`, { method: "POST" }), onSuccess: refresh });
  return (
    <Page title="Dostawcy danych" sub="Narzędzia są wymienialnymi adapterami. Zaślepki są wymienione wprost i nie można ich włączyć.">
      <ErrorLine error={q.error || patch.error || health.error} />
      <div className="grid g2">
        {q.data?.map((p) => {
          const placeholder = p.implementation_status === "placeholder";
          return (
            <div key={p.name} className="card">
              <div className="row"><h2 className="grow">{p.name} <span className="muted">v{p.version}</span></h2><Badge value={p.implementation_status} /><Badge value={p.enabled ? "active" : "skipped"} title={p.enabled ? "włączony" : "wyłączony"} /></div>
              <div>{p.description}</div>
              <div className="muted">rodzaj: {p.kind}, kategoria: {p.category}, klasa dowodu: {p.evidence_class}</div>
              <div className="muted">możliwości: {p.capabilities.join(", ") || "—"}</div>
              {placeholder && <div className="warnbox">Zaślepka: kod integracji nie istnieje. Zapytania nigdy do niej nie trafiają i nie można jej włączyć.</div>}
              <div className="muted">Stan: {p.last_health ? <Badge value={p.last_health.status ?? String(p.last_health)} /> : "nigdy nie sprawdzano"} {p.last_health_at && `(${when(p.last_health_at)})`}</div>
              <div className="muted">Limit zapytań: {p.rate_limit_per_minute ?? "—"}/min, dzienny limit: {p.daily_quota ?? "—"}</div>
              <div className="row" style={{ marginTop: 6 }}>
                <button className="ghost sm" disabled={!can("analyst") || placeholder} onClick={() => health.mutate(p.name)}>Sprawdź stan</button>
                <button className="ghost sm" disabled={!can("admin") || placeholder} onClick={() => patch.mutate({ name: p.name, body: { enabled: !p.enabled } })}>{p.enabled ? "Wyłącz" : "Włącz"}</button>
                {p.docs_url && <a href={p.docs_url} target="_blank" rel="noreferrer">Dokumentacja</a>}
              </div>
            </div>
          );
        })}
      </div>
    </Page>
  );
}
