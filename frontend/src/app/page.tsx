"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api } from "@/lib/api";
import { Badge, Empty, ErrorLine, Page, short, when } from "@/components/ui";
import { label } from "@/lib/i18n";

const TOTALS: Record<string, string> = { entities: "encje", sources: "źródła", observations: "obserwacje", relationships: "relacje" };

const ATTENTION: [string, string, string][] = [
  ["tasks_pending_approval", "Zadania rozpoznania czekające na zatwierdzenie", "/discovery"],
  ["match_candidates_pending", "Kandydaci do połączenia encji do przejrzenia", "/kb"],
  ["claims_pending_review", "Twierdzenia modelu językowego czekające na przegląd", "/kb"],
  ["contradicted_relationships", "Relacje sprzeczne", "/kb"],
  ["stale_or_unavailable_sources", "Źródła nieaktualne lub niedostępne", "/sources"],
  ["dead_tasks", "Zadania, których nie udało się wykonać mimo ponowień", "/discovery"],
];

export default function Overview() {
  const q = useQuery({ queryKey: ["overview"], queryFn: () => api<any>("/stats/overview") });
  const d = q.data;
  return (
    <Page title="Przegląd" sub="Ariadna zachowuje każdą obserwację i pokazuje dowody stojące za każdym powiązaniem. Ta strona pokazuje, co system zawiera i co czeka na przegląd.">
      <ErrorLine error={q.error} />
      {q.isLoading && <div className="muted">Wczytywanie…</div>}
      {d && (
        <>
          <dl className="figures" aria-label="Sumy">
            {Object.entries(d.totals).map(([k, v]) => (
              <div key={k}><dt>{TOTALS[k] ?? k}</dt><dd>{String(v)}</dd></div>
            ))}
          </dl>
          <p className="muted">
            W ciągu ostatnich 24 godzin: obserwacje {d.last_24h.new_observations}, encje {d.last_24h.new_entities},
            {" "}relacje {d.last_24h.new_relationships} (nowe).
          </p>
          <h3>Wymaga uwagi analityka</h3>
          <ul className="attention">
            {ATTENTION.map(([key, label, href]) => {
              const n = d.attention[key] ?? 0;
              return (
                <li key={key}>
                  <Link href={href} className={n ? "open" : ""}>
                    <span className="n">{n}</span>
                    <span>{label}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
          <div className="grid g2">
            <div>
              <h3>Encje według typu</h3>
              <div className="card">
                {Object.keys(d.entities_by_type).length === 0 ? <Empty>Brak encji. Rozpocznij sprawę w sekcji Rozpoznanie.</Empty> : (
                  <table><tbody>{Object.entries(d.entities_by_type).map(([k, v]) => <tr key={k}><td>{label(k)}</td><td>{String(v)}</td></tr>)}</tbody></table>
                )}
              </div>
            </div>
            <div>
              <h3>Relacje według statusu dowodowego</h3>
              <div className="card">
                {Object.keys(d.relationships_by_status).length === 0 ? <Empty>Brak relacji.</Empty> : (
                  <table><tbody>{Object.entries(d.relationships_by_status).map(([k, v]) => <tr key={k}><td><Badge value={k} /></td><td>{String(v)}</td></tr>)}</tbody></table>
                )}
              </div>
            </div>
          </div>
          <h3>Ostatnie uruchomienia dostawców</h3>
          <div className="card">
            {d.recent_runs.length === 0 ? <Empty>Brak uruchomień dostawców.</Empty> : (
              <table>
                <thead><tr><th>Dostawca</th><th>Cel</th><th>Status</th><th>Rozpoczęto</th></tr></thead>
                <tbody>
                  {d.recent_runs.map((r: any) => (
                    <tr key={r.id}><td>{r.provider}</td><td className="mono" translate="no">{short(r.target, 50)}</td><td><Badge value={r.status} /></td><td>{when(r.started_at)}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <h3>Dostawcy danych</h3>
          <div className="row">
            {d.providers.map((p: any) => (
              <span key={p.name} className="badge" title={`stan: ${p.last_health ? JSON.stringify(p.last_health) : "nieznany"}`}>
                {p.name}: {label(p.implementation_status)}{p.enabled ? "" : ", wyłączony"}
              </span>
            ))}
          </div>
        </>
      )}
    </Page>
  );
}
