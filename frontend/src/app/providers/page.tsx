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
    <Page title="Providers" sub="Tools are interchangeable adapters. Placeholders are listed honestly and cannot be enabled.">
      <ErrorLine error={q.error || patch.error || health.error} />
      <div className="grid g2">
        {q.data?.map((p) => {
          const placeholder = p.implementation_status === "placeholder";
          return (
            <div key={p.name} className="card">
              <div className="row"><h2 className="grow">{p.name} <span className="muted">v{p.version}</span></h2><Badge value={p.implementation_status} /><Badge value={p.enabled ? "active" : "skipped"} title={p.enabled ? "enabled" : "disabled"} /></div>
              <div>{p.description}</div>
              <div className="muted">kind {p.kind} · category {p.category} · evidence class {p.evidence_class}</div>
              <div className="muted">capabilities: {p.capabilities.join(", ") || "—"}</div>
              {placeholder && <div className="warnbox">Placeholder: no integration code exists. It is never routed and cannot be enabled.</div>}
              <div className="muted">Health: {p.last_health ? <Badge value={p.last_health.status ?? String(p.last_health)} /> : "never checked"} {p.last_health_at && `· ${when(p.last_health_at)}`}</div>
              <div className="muted">Rate limit {p.rate_limit_per_minute ?? "—"}/min · daily quota {p.daily_quota ?? "—"}</div>
              <div className="row" style={{ marginTop: 6 }}>
                <button className="ghost sm" disabled={!can("analyst") || placeholder} onClick={() => health.mutate(p.name)}>Check health</button>
                <button className="ghost sm" disabled={!can("admin") || placeholder} onClick={() => patch.mutate({ name: p.name, body: { enabled: !p.enabled } })}>{p.enabled ? "Disable" : "Enable"}</button>
                {p.docs_url && <a href={p.docs_url} target="_blank" rel="noreferrer">Docs</a>}
              </div>
            </div>
          );
        })}
      </div>
    </Page>
  );
}
