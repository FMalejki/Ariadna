"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { label } from "@/lib/i18n";
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
    mutationFn: (v: { id: string; approve: boolean }) => api(`/discovery/tasks/${v.id}/decision`, { body: { approve: v.approve, reason: v.approve ? "zatwierdzono w interfejsie" : "odrzucono w interfejsie" } }),
    onSuccess: refresh,
  });
  const retry = useMutation({ mutationFn: (id: string) => api(`/discovery/tasks/${id}/retry`, { method: "POST" }), onSuccess: refresh });
  const run = useMutation({ mutationFn: () => api("/discovery/run", { body: { limit: 25 } }), onSuccess: refresh });

  return (
    <Page title="Rozpoznanie" sub="Zacznij od imienia i nazwiska, loginu lub elementu początkowego (wystarczy jeden). Każdy wynik może zaproponować kolejne zadania, a kategorie wrażliwe czekają na zatwierdzenie.">
      <div className="card">
        <h2>Nowa sprawa</h2>
        <div className="row">
          <input placeholder="Nazwa sprawy (opcjonalnie)" aria-label="Nazwa sprawy" value={name} onChange={(e) => setName(e.target.value)} />
          <input placeholder="Imię i nazwisko" aria-label="Imię i nazwisko" autoComplete="off" value={fullName} onChange={(e) => setFullName(e.target.value)} />
          <input placeholder="Login" aria-label="Login" autoComplete="off" spellCheck={false} autoCapitalize="none" value={login} onChange={(e) => setLogin(e.target.value)} />
        </div>
        <div className="row">
          <input className="grow" placeholder="Element początkowy (opcjonalnie): adres URL, @uchwyt, adres portfela, hash transakcji lub domena" aria-label="Element początkowy" spellCheck={false} value={seed} onChange={(e) => setSeed(e.target.value)} />
          <label>głębokość <input type="number" min={0} max={5} value={depth} onChange={(e) => setDepth(Number(e.target.value))} style={{ width: 60 }} /></label>
          <label><input type="checkbox" checked={runNow} onChange={(e) => setRunNow(e.target.checked)} /> wykonaj zatwierdzone zadania od razu</label>
          <button disabled={!can("analyst") || !hasInput || start.isPending} onClick={() => start.mutate()}>Rozpocznij</button>
        </div>
        <div className="muted">
          Login: Sherlock sprawdza wiele serwisów (nie zakłada żadnej platformy). Trafienie oznacza tylko zgodność ciągu znaków z nazwą użytkownika i nie dowodzi tożsamości.
          Imię i nazwisko nie tworzy encji. Służy wyłącznie jako parametr sprawy i jej wyszukiwań.
        </div>
        {!can("analyst") && <div className="muted">Rola obserwatora nie pozwala rozpoczynać spraw.</div>}
        <ErrorLine error={start.error} />
        {created && (
          <div className="notice">
            {created.seed ? (
              <>Element początkowy rozpoznano jako <code>{created.seed.canonical_identifier}</code> ({label(created.seed.entity_type)}). </>
            ) : (
              <>Nie utworzono encji (podano tylko imię i nazwisko, które jest parametrem sprawy). </>
            )}
            Zaplanowane zadania: {created.tasks.length}, wykonane: {created.executed.length}.{" "}
            <Link href={`/investigations?id=${created.investigation.id}`}>Otwórz sprawę</Link>
            {created.seed?.entity_id && <> <Link href={`/graph?root=${created.seed.entity_id}`}>Pokaż w grafie</Link></>}
          </div>
        )}
      </div>

      <h3>Kolejka zadań</h3>
      <div className="row">
        <select aria-label="Filtr statusu zadań" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">wszystkie statusy</option>
          {["pending_approval", "queued", "running", "completed", "reused", "failed", "dead", "rejected", "skipped"].map((s) => <option key={s} value={s}>{label(s)}</option>)}
        </select>
        <button className="ghost" disabled={!can("analyst") || run.isPending} onClick={() => run.mutate()}>Wykonaj zadania do uruchomienia</button>
      </div>
      <ErrorLine error={tasks.error || decide.error || retry.error || run.error} />
      <div className="card">
        {tasks.data && tasks.data.length === 0 ? <Empty>Brak zadań. Rozpocznij sprawę powyżej.</Empty> : (
          <table>
            <thead><tr><th>Dostawca</th><th>Cel</th><th>Głębokość</th><th>Status</th><th>Powód</th><th>Utworzono</th><th /></tr></thead>
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
                        <button className="good sm" onClick={() => decide.mutate({ id: t.id, approve: true })}>Zatwierdź</button>
                        <button className="danger sm" onClick={() => decide.mutate({ id: t.id, approve: false })}>Odrzuć</button>
                      </>
                    )}
                    {t.status === "dead" && can("analyst") && <button className="ghost sm" onClick={() => retry.mutate(t.id)}>Ponów</button>}
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
