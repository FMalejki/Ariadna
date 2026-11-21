"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { label } from "@/lib/i18n";
import { Badge, Empty, ErrorLine, Page, Repeatability, short, when } from "@/components/ui";

const NET_LABEL: Record<string, string> = { evm: "EVM", solana: "Solana", bitcoin: "Bitcoin" };
const STORE = "osint.leads.investigation";
const MAX_EXPAND = 20; // mirrors the backend limit per expansion

function Step({ n, title, children, hint }: { n: number; title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <h2>
        <span className="badge b-info" style={{ marginRight: 8 }}>krok {n}</span>
        {title}
      </h2>
      {hint && <div className="muted" style={{ marginBottom: 8 }}>{hint}</div>}
      {children}
    </div>
  );
}

/** Honest one-line result of a provider run (never hides unavailable / failed). */
function RunLine({ r }: { r: any }) {
  if (!r) return null;
  return (
    <div className="row" style={{ margin: "6px 0" }}>
      <Badge value={r.status} />
      <span className="muted">{r.provider}, uruchomienie {short(r.tool_run_id, 8)}</span>
      {r.error && <span className={r.unavailable ? "muted" : "err"}>{r.error}</span>}
    </div>
  );
}

export default function Leads() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const canAct = can("analyst");

  const [subject, setSubject] = useState("");
  const [extra, setExtra] = useState("");
  const [inv, setInv] = useState<string | null>(null);
  useEffect(() => { setInv(window.localStorage.getItem(STORE)); }, []);
  const setInvStored = (id: string | null) => {
    setInv(id);
    if (id) window.localStorage.setItem(STORE, id); else window.localStorage.removeItem(STORE);
  };

  const cfg = useQuery({ queryKey: ["lead-config"], queryFn: () => api<any>("/leads/config") });
  const state = useQuery({ queryKey: ["lead-state", inv], queryFn: () => api<any>(`/leads/sessions/${inv}`), enabled: !!inv, retry: false });
  useEffect(() => { if (state.error && inv) setInvStored(null); /* stale id after a DB reset */ }, [state.error]); // eslint-disable-line react-hooks/exhaustive-deps
  const refresh = () => qc.invalidateQueries({ queryKey: ["lead-state", inv] });

  // ---- step 1: proposed queries
  const [queries, setQueries] = useState<{ query: string; purpose: string }[]>([]);
  const propose = useMutation({
    mutationFn: () => api<any>("/leads/queries/propose", { body: { subject, extra_terms: extra.split(",").map((s) => s.trim()).filter(Boolean) } }),
    onSuccess: (r) => setQueries(r.queries),
  });
  const begin = useMutation({
    mutationFn: () => api<any>("/leads/sessions", { body: { subject } }),
    onSuccess: (r) => { setInvStored(r.investigation_id); setSearches({}); setPage(null); setOnchain({}); setMorePages({}); },
  });

  // ---- step 2: search (analyst approves each query; backend explicit)
  const [backend, setBackend] = useState("auto");
  const [searches, setSearches] = useState<Record<string, any>>({});
  const [activeQuery, setActiveQuery] = useState<string | null>(null);
  const search = useMutation({
    mutationFn: (q: string) => api<any>("/leads/search", { body: { investigation_id: inv, query: q, backend } }),
    onSuccess: (r) => { setSearches((s) => ({ ...s, [r.query]: r })); setActiveQuery(r.query); refresh(); },
  });

  // ---- step 3: fetch one selected page
  const [manualUrl, setManualUrl] = useState("");
  const [page, setPage] = useState<any>(null);
  const fetchPage = useMutation({
    mutationFn: (url: string) => api<any>("/leads/fetch", { body: { investigation_id: inv, url } }),
    onSuccess: (r) => { setPage(r); refresh(); },
  });

  // ---- step 4: track decision (rationale mandatory)
  const [rationale, setRationale] = useState<Record<string, string>>({});
  const [tracked, setTracked] = useState<Record<string, any>>({});
  const track = useMutation({
    mutationFn: (c: any) =>
      api<any>("/leads/track", {
        body: { investigation_id: inv, fetch_run_id: page.tool_run_id, network: c.network, address: c.address, rationale: rationale[`${c.network}:${c.address}`] ?? "" },
      }),
    onSuccess: (r, c) => { setTracked((t) => ({ ...t, [`${c.network}:${c.address}`]: r })); refresh(); },
  });

  // ---- step 5: on-chain lookup
  const [onchain, setOnchain] = useState<Record<string, any>>({});
  const lookup = useMutation({
    mutationFn: (v: { entity_id: string; refresh: boolean }) => api<any>("/leads/onchain", { body: { investigation_id: inv, ...v } }),
    onSuccess: (r) => { setOnchain((o) => ({ ...o, [r.entity_id]: r })); setMorePages((m) => ({ ...m, [r.entity_id]: [] })); refresh(); },
  });

  // ---- "load more": further history pages, each a separate audited provider run (kept per wallet, oldest first)
  const [morePages, setMorePages] = useState<Record<string, any[]>>({});
  const loadMore = useMutation({
    mutationFn: (v: { entity_id: string; lookup_run_id: string }) => api<any>("/leads/onchain/more", { body: { investigation_id: inv, ...v } }),
    onSuccess: (r) => { setMorePages((m) => ({ ...m, [r.entity_id]: [...(m[r.entity_id] ?? []), r] })); refresh(); },
  });

  // ---- step 6: expand selected counterparties
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [expanded, setExpanded] = useState<Record<string, any>>({});
  const expand = useMutation({
    mutationFn: (v: { entity_id: string; lookup_run_id: string; list: any[] }) =>
      api<any>("/leads/expand", { body: { investigation_id: inv, entity_id: v.entity_id, lookup_run_id: v.lookup_run_id, counterparties: v.list } }),
    onSuccess: (r, v) => { setExpanded((e) => ({ ...e, [v.entity_id]: r })); setPicked({}); refresh(); },
  });

  const searchRuns = (state.data?.runs ?? []).filter((r: any) => r.provider === "web_search");
  const results = activeQuery ? searches[activeQuery] : null;
  const loadPastRun = async (runId: string) => {
    const d = await api<any>(`/leads/runs/${runId}`);
    setSearches((s) => ({ ...s, [d.query]: { ...d, query: d.query } }));
    setActiveQuery(d.query);
  };
  const wallets: any[] = state.data?.wallets ?? [];

  return (
    <Page
      title="Wyszukiwanie tropów: od nazwiska do portfela i faktów z blockchaina"
      sub="Znajdź miejsca, w których publiczny adres portfela występuje obok osoby lub tematu, a następnie sprawdź go w blockchainie. Każdy krok to Twoja decyzja. Wzmianka nie jest dowodem własności."
    >
      <div className="warnbox">
        Wyniki wyszukiwania to <b>tropy</b>, a nie dowody. Strona, która pokazuje adres obok nazwiska, dowodzi jedynie tego, że strona tak twierdzi. Nic tutaj nie łączy
        osoby z portfelem. Kontrahenci to fakty transakcyjne, a nie tożsamość. Dane kontaktowe (e-mail, telefon) są odrzucane, zachowywany jest tylko adres, krótki kontekst i adres URL.
      </div>
      {cfg.data && (
        <div className="notice">
          Wyszukiwanie: Tavily {cfg.data.tavily_configured ? "skonfigurowany (oficjalne API)" : "NIESKONFIGUROWANY (ustaw OSINT_TAVILY_API_KEY)"}. ddgs to{" "}
          <b>nieoficjalne pobieranie wyników, w szarej strefie regulaminów</b>, bez klucza API. Jest używane tylko po jawnym wyborze albo jako zapisany w dzienniku zapas. Blockchain: Bitcoin{" "}
          {cfg.data.btc_esplora_configured ? "skonfigurowany" : "nieskonfigurowany"}. Eksploratory historii EVM: {cfg.data.evm_explorer_networks.join(", ") || "brak"}.
        </div>
      )}
      {!canAct && <div className="muted">Obserwator może czytać wyniki, ale nie może wykonywać kroków.</div>}

      <Step n={1} title="Osoba lub temat: proponowane zapytania" hint="Podpowiedzi są deterministyczne (bez AI), a nic nie jest wysyłane, dopóki nie zatwierdzisz zapytania w kroku 2. Możesz je dowolnie edytować.">
        <div className="row">
          <input className="grow" placeholder='Osoba lub temat, np. Jan Kowalski' aria-label="Osoba lub temat" value={subject} onChange={(e) => setSubject(e.target.value)} />
          <input placeholder="dodatkowe frazy, po przecinku" aria-label="Dodatkowe frazy" value={extra} onChange={(e) => setExtra(e.target.value)} />
          <button disabled={!subject.trim() || propose.isPending} onClick={() => propose.mutate()}>Zaproponuj zapytania</button>
          <button className="ghost" disabled={!canAct || !subject.trim() || begin.isPending} onClick={() => begin.mutate()}>
            {inv ? "Rozpocznij nową sesję" : "Rozpocznij sesję"}
          </button>
        </div>
        <ErrorLine error={propose.error || begin.error} />
        {inv && state.data && (
          <div className="notice">
            Sesja <code>{short(inv, 8)}</code>: {state.data.investigation.name}. <Link href={`/investigations?id=${inv}`}>Otwórz sprawę</Link>
          </div>
        )}
        {!inv && queries.length > 0 && <div className="warnbox">Rozpocznij sesję, aby uruchamiać wyszukiwania (grupuje Twoje kroki i ślad audytowy).</div>}
        {queries.length > 0 && (
          <table>
            <thead><tr><th>Zapytanie (można edytować)</th><th>Cel</th><th /></tr></thead>
            <tbody>
              {queries.map((q, i) => (
                <tr key={q.purpose + i}>
                  <td>
                    <input className="mono" style={{ width: "100%" }} aria-label="Zapytanie wyszukiwarki" spellCheck={false} value={q.query}
                      onChange={(e) => setQueries((all) => all.map((x, j) => (j === i ? { ...x, query: e.target.value } : x)))} />
                  </td>
                  <td className="muted">{q.purpose}</td>
                  <td><button className="sm" disabled={!canAct || !inv || search.isPending} onClick={() => search.mutate(q.query)}>Zatwierdź i szukaj</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="row" style={{ marginTop: 8 }}>
          <label>silnik wyszukiwania{" "}
            <select value={backend} onChange={(e) => setBackend(e.target.value)}>
              <option value="auto">automatyczny (Tavily, z zapisanym przejściem na ddgs)</option>
              <option value="tavily">tylko Tavily</option>
              <option value="ddgs">tylko ddgs (nieoficjalny)</option>
            </select>
          </label>
        </div>
        <ErrorLine error={search.error} />
      </Step>

      <Step n={2} title="Wyniki wyszukiwania" hint="Wybierz strony warte przeczytania. Pobierane są tylko strony, które wskażesz.">
        {searchRuns.length > 0 && (
          <div className="row" style={{ marginBottom: 6 }}>
            <span className="muted">Wcześniejsze wyszukiwania:</span>
            {searchRuns.slice(0, 8).map((r: any) => (
              <button key={r.tool_run_id} className="ghost sm" onClick={() => loadPastRun(r.tool_run_id)} title={when(r.started_at)}>{short(r.query ?? r.target, 40)}</button>
            ))}
          </div>
        )}
        {!results ? <Empty>Nie wykonano jeszcze wyszukiwania. Zatwierdź zapytanie w kroku 1.</Empty> : (
          <>
            <RunLine r={results} />
            <div className="row">
              <span className="muted">użyty silnik:</span> <Badge value={results.backend_used} />
              {results.unofficial_backend && <span className="warnbox" style={{ margin: 0 }}>nieoficjalne pobieranie wyników przez ddgs (szara strefa regulaminów, może przestać działać)</span>}
              {results.fallback && <span className="warnbox" style={{ margin: 0 }}>przejście z {results.fallback.from}: {results.fallback.reason}</span>}
            </div>
            {results.results?.length === 0 && <Empty>Wyszukiwanie nie zwróciło wyników.</Empty>}
            <table>
              <tbody>
                {results.results?.map((r: any) => (
                  <tr key={r.url}>
                    <td style={{ width: 28 }}>{r.rank}</td>
                    <td>
                      <b>{r.title || r.domain}</b>
                      <div className="mono muted">{r.url}</div>
                      <div>{r.snippet}</div>
                    </td>
                    <td><button className="sm" disabled={!canAct || fetchPage.isPending} onClick={() => fetchPage.mutate(r.url)}>Pobierz stronę</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        <div className="row" style={{ marginTop: 8 }}>
          <input className="grow mono" placeholder="…albo wklej adres strony, który już masz (np. ogłoszenie lub wpis)" aria-label="Adres strony" spellCheck={false} value={manualUrl} onChange={(e) => setManualUrl(e.target.value)} />
          <button className="ghost" disabled={!canAct || !inv || !manualUrl.trim() || fetchPage.isPending} onClick={() => fetchPage.mutate(manualUrl.trim())}>Pobierz stronę</button>
        </div>
        <ErrorLine error={fetchPage.error} />
      </Step>

      <Step n={3} title="Pobrana strona: kandydaci na adresy" hint="Deterministyczne wyodrębnianie adresów Solana, EVM i Bitcoin wraz z krótkim kontekstem. Plik robots.txt jest respektowany, a X, Telegram, Instagram, Facebook, TikTok i LinkedIn nigdy nie są pobierane.">
        {!page ? <Empty>Nie pobrano jeszcze żadnej strony.</Empty> : (
          <>
            <RunLine r={page} />
            {page.page && (
              <>
                <div>
                  <b>{page.page.title ?? "(brak tytułu)"}</b> <span className="mono muted">{page.page.final_url}</span>
                </div>
                <div className="muted">
                  {page.page.bytes_read} B{page.page.truncated ? " (obcięto na limicie rozmiaru, dalsza treść nie została przeczytana)" : ""}. Treść strony nie jest zapisywana.
                </div>
                {page.page.redirects?.length > 0 && <div className="muted">przekierowania: {page.page.redirects.join(", następnie ")}</div>}
                {page.page.address_candidates.length === 0 && <Empty>Na tej stronie nie znaleziono adresu blockchain.</Empty>}
                {page.page.address_candidates.map((c: any) => {
                  const key = `${c.network}:${c.address}`;
                  const t = tracked[key];
                  const r = rationale[key] ?? "";
                  return (
                    <div key={key} className="ev">
                      <div className="row">
                        <Badge value={NET_LABEL[c.network] ?? c.network} />
                        <code translate="no">{c.address}</code>
                        <span className="muted">{c.occurrences}× na stronie</span>
                      </div>
                      <div><Repeatability r={c.repeatability} /></div>
                      {c.contexts.map((ctx: string, i: number) => <div key={i} className="muted" style={{ fontStyle: "italic" }}>“{ctx}”</div>)}
                      <div className="mono muted">{page.page.final_url}</div>
                      {t ? (
                        <div className="notice">
                          Zapisano jako portfel. Relacja <code>{t.relationship_type}</code> <Badge value={t.relationship_status} />. {t.note}
                        </div>
                      ) : (
                        <div className="row" style={{ marginTop: 6 }}>
                          <input className="grow" placeholder="Dlaczego śledzić ten adres? (wymagane, min. 10 znaków; zapisywane w dzienniku audytu)" aria-label="Uzasadnienie śledzenia adresu" value={r}
                            onChange={(e) => setRationale((x) => ({ ...x, [key]: e.target.value }))} />
                          <button className="sm" disabled={!canAct || r.trim().length < 10 || track.isPending} onClick={() => track.mutate(c)}>Śledź adres</button>
                        </div>
                      )}
                    </div>
                  );
                })}
                <ErrorLine error={track.error} />
              </>
            )}
          </>
        )}
      </Step>

      <Step n={4} title="Śledzone portfele: zapytanie do blockchaina" hint="Saldo, ostatnie transakcje i kontrahenci ze skonfigurowanego węzła lub eksploratora. Niedostępne źródła są zgłaszane i nigdy nie są zastępowane scrapingiem.">
        {wallets.length === 0 ? <Empty>Brak śledzonych portfeli. Zacznij śledzić adres w kroku 3.</Empty> : wallets.map((w) => {
          const o = onchain[w.entity_id];
          return (
            <div key={w.entity_id} className="ev">
              <div className="row">
                <Badge value={NET_LABEL[w.network] ?? w.network} /> <code>{w.address}</code>
                <Link href={`/graph?root=${w.entity_id}`}>graf</Link>
                <span className="grow" />
                <button className="sm" disabled={!canAct || lookup.isPending} onClick={() => lookup.mutate({ entity_id: w.entity_id, refresh: false })}>Sprawdź w blockchainie</button>
                {o && <button className="ghost sm" disabled={!canAct || lookup.isPending} onClick={() => lookup.mutate({ entity_id: w.entity_id, refresh: true })}>Odśwież</button>}
              </div>
              {w.repeatability && <div><Repeatability r={w.repeatability} /></div>}
              {o && (
                <>
                  <RunLine r={o} />
                  {o.reused_prior_run && <div className="muted">Użyto niedawnego zapisanego zapytania (pobrano {when(o.retrieved_at)}). Użyj „Odśwież”, aby ponownie odpytać węzeł.</div>}
                  {o.view?.networks.map((n: any, ni: number) => {
                    const pages = (morePages[w.entity_id] ?? []).map((p: any) => ({ run: p.lookup_run_id, n: p.view?.networks?.find((x: any) => x.network === n.network) })).filter((p: any) => p.n);
                    const all = [{ run: o.lookup_run_id, n }, ...pages];
                    const last = all[all.length - 1];
                    const txs: any[] = []; const seen = new Set<string>();
                    all.forEach((pg) => (pg.n.recent_transactions ?? []).forEach((t: any) => { if (!seen.has(t.hash)) { seen.add(t.hash); txs.push(t); } }));
                    const cps = new Map<string, any>();  // merged across loaded pages; the first page a counterparty appeared in is cited as evidence
                    all.forEach((pg) => (pg.n.counterparties ?? []).forEach((c: any) => {
                      const key = `${c.address.toLowerCase()}|${c.direction}`;
                      const cur = cps.get(key);
                      if (cur) cur.tx_count += c.tx_count; else cps.set(key, { ...c, run: pg.run });
                    }));
                    const rows = Array.from(cps.values());
                    const keyOf = (c: any) => `${w.entity_id}|${n.network}|${c.address}|${c.direction}|${c.run}`;
                    const selectable = rows.filter((c) => !c.is_contract);
                    const mine = Object.keys(picked).filter((k) => picked[k] && k.startsWith(`${w.entity_id}|${n.network}|`));
                    const selectAll = () => {
                      const next: Record<string, boolean> = { ...picked };
                      mine.forEach((k) => delete next[k]);
                      selectable.slice(0, MAX_EXPAND).forEach((c) => { next[keyOf(c)] = true; });
                      setPicked(next);
                    };
                    return (
                    <div key={n.network + ni} style={{ marginTop: 6 }}>
                      <div className="row">
                        <b>{n.network}</b>
                        {n.found === false && <span className="muted">adres nie występuje w tej sieci</span>}
                        {n.balance && <span>saldo: <b>{n.balance}</b></span>}
                        {n.tx_count != null && <span className="muted">liczba transakcji / nonce: {n.tx_count}</span>}
                        {n.has_code && <Badge value="contract" />}
                        {n.explorer_flags?.is_scam && <Badge value="explorer flags scam" title="eksplorator oznacza adres jako oszustwo" />}
                      </div>
                      {n.error && <div className="err">{n.error}</div>}
                      {n.history_error && <div className="muted">historia: {n.history_error}</div>}
                      {txs.length > 0 && (
                        <div style={{ maxHeight: 280, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 6 }}>
                          <table>
                            <thead style={{ position: "sticky", top: 0, background: "var(--panel)" }}><tr><th>Transakcja</th><th>Czas</th><th>Kierunek / kwota</th></tr></thead>
                            <tbody>
                              {txs.map((t: any) => (
                                <tr key={t.hash}>
                                  <td className="mono">{short(t.hash, 18)}</td>
                                  <td>{when(t.time)}</td>
                                  <td>{t.direction ?? (t.failed ? "nieudana" : "")} {t.net_btc ? `${t.net_btc} BTC` : ""} {t.value_wei ? `${t.value_wei} wei` : ""}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                      {txs.length > 0 && <div className="muted">Wczytane transakcje: {txs.length}{all.length > 1 ? ` (z ${all.length} stron)` : ""}.</div>}
                      {last.n.has_more ? (
                        <div className="row" style={{ marginTop: 4 }}>
                          <button className="ghost sm" disabled={!canAct || loadMore.isPending} onClick={() => loadMore.mutate({ entity_id: w.entity_id, lookup_run_id: last.run })}>
                            {loadMore.isPending ? "Wczytywanie…" : "Wczytaj więcej"}
                          </button>
                          <span className="muted">pobiera kolejną stronę z węzła lub eksploratora jako osobne, audytowane uruchomienie</span>
                        </div>
                      ) : last.n.history_truncated ? (
                        <div className="muted">Historia jest obcięta i nie można zażądać kolejnej strony (brak kursora).</div>
                      ) : txs.length > 0 ? <div className="muted">Osiągnięto koniec historii.</div> : null}
                      {rows.length > 0 && (
                        <>
                          <h3>Kontrahenci (wybierasz sam)</h3>
                          <div className="notice">Inne adresy widoczne w tych samych transakcjach. Giełdy i kontrakty pojawiają się przy wielu niezwiązanych stronach, a to nie jest dowód własności. Liczby są sumowane po wczytanych stronach, a dowodem dla kontrahenta jest pierwsza strona, na której się pojawił.</div>
                          <div className="row">
                            <button className="ghost sm" disabled={selectable.length === 0} onClick={selectAll}>
                              Zaznacz wszystkie{selectable.length > MAX_EXPAND ? ` (pierwsze ${MAX_EXPAND})` : ""}
                            </button>
                            <button className="ghost sm" disabled={mine.length === 0} onClick={() => { const next = { ...picked }; mine.forEach((k) => delete next[k]); setPicked(next); }}>Wyczyść</button>
                            <span className={mine.length >= MAX_EXPAND ? "err" : "muted"}>zaznaczono {mine.length} z {MAX_EXPAND}</span>
                            <span className="muted">Kontrakty ({rows.length - selectable.length}) nie są zaznaczane przez „Zaznacz wszystkie”</span>
                          </div>
                          <div style={{ maxHeight: 320, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 6 }}>
                            <table>
                              <tbody>
                                {rows.map((c: any) => {
                                  const k = keyOf(c);
                                  const checked = !!picked[k];
                                  return (
                                    <tr key={k}>
                                      <td style={{ width: 28 }}>
                                        <input type="checkbox" aria-label={`Wybierz ${c.address}`} checked={checked} disabled={!checked && mine.length >= MAX_EXPAND}
                                          onChange={(e) => setPicked((p) => { const next = { ...p }; if (e.target.checked) next[k] = true; else delete next[k]; return next; })} />
                                      </td>
                                      <td className="mono">{c.address}</td>
                                      <td>{c.direction.replace(/_/g, " ")}, {c.tx_count} tx {c.is_contract ? <Badge value="contract" /> : null}</td>
                                    </tr>
                                  );
                                })}
                              </tbody>
                            </table>
                          </div>
                          <div className="row" style={{ marginTop: 4 }}>
                            <button className="sm" disabled={!canAct || expand.isPending || mine.length === 0}
                              onClick={() => {
                                const list = mine.map((k) => {
                                  const [, network, address, direction, lookup_run_id] = k.split("|");
                                  return { network, address, direction, lookup_run_id };
                                });
                                expand.mutate({ entity_id: w.entity_id, lookup_run_id: o.lookup_run_id, list });
                              }}>Dodaj zaznaczone jako powiązane portfele</button>
                            <span className="muted">tworzy relację WALLET_TRANSACTED_WITH (fakt transakcyjny), a nie powiązanie własności</span>
                          </div>
                        </>
                      )}
                    </div>
                    );
                  })}
                  {expanded[w.entity_id] && (
                    <div className="notice">
                      Dodane portfele: {expanded[w.entity_id].added.length}{expanded[w.entity_id].skipped.length ? `, pominięte: ${expanded[w.entity_id].skipped.length}` : ""}.{" "}
                      {expanded[w.entity_id].note}
                    </div>
                  )}
                </>
              )}
            </div>
          );
        })}
        <ErrorLine error={lookup.error || expand.error || loadMore.error} />
      </Step>

      {state.data && state.data.relationships.length > 0 && (
        <Step n={5} title="Relacje utworzone w tej sesji" hint="Przejdź do grafu wiedzy, aby zobaczyć dowody i pochodzenie danych.">
          <table>
            <thead><tr><th>Typ</th><th>Status</th></tr></thead>
            <tbody>
              {state.data.relationships.map((r: any) => (
                <tr key={r.id}><td><code>{r.type}</code></td><td><Badge value={r.status} /></td></tr>
              ))}
            </tbody>
          </table>
        </Step>
      )}
    </Page>
  );
}
